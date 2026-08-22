"""外部插件发现与加载器 (plugin_loader) 专项测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbimagent.core.plugin import PluginRegistry, create_default_plugin_registry
from openbimagent.core.plugin_loader import (
    MANIFEST_FILENAME,
    PLUGINS_DIR_ENV,
    PluginLoaderError,
    load_external_plugins,
)

_REPO_PLUGINS_DIR = Path(__file__).parents[1] / "plugins"

_GOOD_MODULE = '''from openbimagent.core.plugin import BIMPlugin


class TmpPlugin(BIMPlugin):
    plugin_id = "plugin.external.tmp"
    name = "Tmp"
    provides_capabilities = ("tmp:cap",)

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self.register_handler("tmp:cap", lambda: "tmp-ok")


def create_plugin() -> BIMPlugin:
    return TmpPlugin()
'''

_GOOD_MANIFEST = '''[plugin]
plugin_id = "plugin.external.tmp"
name = "Tmp"
version = "1.0.0"
description = ""
capabilities = ["tmp:cap"]
'''


def _write_plugin(root: Path, name: str, *, module: str = _GOOD_MODULE, manifest: str | None = _GOOD_MANIFEST) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(module, encoding="utf-8")
    if manifest is not None:
        (plugin_dir / MANIFEST_FILENAME).write_text(manifest, encoding="utf-8")
    return plugin_dir


def test_load_repo_example_echo_plugin() -> None:
    """仓库内置示例外部插件经 manifest 核验后可被调度。"""
    registry = PluginRegistry()
    loaded = load_external_plugins(registry, _REPO_PLUGINS_DIR)

    assert "plugin.external.example_echo" in loaded
    assert registry.invoke("example:echo", "hi") == "echo: hi"


def test_env_var_discovery_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENBIMAGENT_PLUGINS_DIR 环境变量指定发现路径。"""
    _write_plugin(tmp_path, "tmp-plugin")
    monkeypatch.setenv(PLUGINS_DIR_ENV, str(tmp_path))

    registry = PluginRegistry()
    loaded = load_external_plugins(registry)

    assert loaded == ["plugin.external.tmp"]
    assert registry.invoke("tmp:cap") == "tmp-ok"


def test_default_registry_includes_external_when_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认注册表工厂在 env 配置时自动纳入外部插件。"""
    _write_plugin(tmp_path, "tmp-plugin")
    monkeypatch.setenv(PLUGINS_DIR_ENV, str(tmp_path))

    registry = create_default_plugin_registry()
    assert registry.has_capability("tmp:cap")
    assert registry.has_capability("solver:self_healing")


def test_missing_or_empty_dir_is_noop(tmp_path: Path) -> None:
    """目录不存在或为空时静默返回空列表。"""
    registry = PluginRegistry()
    assert load_external_plugins(registry, tmp_path / "nonexistent") == []
    assert load_external_plugins(registry, tmp_path) == []


def test_fail_closed_on_missing_manifest(tmp_path: Path) -> None:
    """缺 manifest 拒绝加载且注册表零污染。"""
    _write_plugin(tmp_path, "no-manifest", manifest=None)
    registry = PluginRegistry()

    with pytest.raises(PluginLoaderError, match="缺少 manifest"):
        load_external_plugins(registry, tmp_path)
    assert len(registry.list_plugins()) == 0


def test_fail_closed_on_manifest_mismatch(tmp_path: Path) -> None:
    """manifest 与运行时事实 (plugin_id / capabilities) 不符拒绝加载。"""
    registry = PluginRegistry()

    _write_plugin(
        tmp_path,
        "id-mismatch",
        manifest=_GOOD_MANIFEST.replace('plugin_id = "plugin.external.tmp"', 'plugin_id = "plugin.external.other"'),
    )
    with pytest.raises(PluginLoaderError, match="plugin_id"):
        load_external_plugins(registry, tmp_path)

    registry2 = PluginRegistry()
    _write_plugin(
        tmp_path,
        "cap-mismatch",
        manifest=_GOOD_MANIFEST.replace('capabilities = ["tmp:cap"]', 'capabilities = ["tmp:other"]'),
    )
    with pytest.raises(PluginLoaderError, match="capabilities"):
        load_external_plugins(registry2, tmp_path)
    assert len(registry2.list_plugins()) == 0


def test_fail_closed_on_bad_module(tmp_path: Path) -> None:
    """模块缺少 create_plugin 工厂或含多个 .py 文件时拒绝加载。"""
    registry = PluginRegistry()

    _write_plugin(tmp_path, "no-factory", module='X = 1\n')
    with pytest.raises(PluginLoaderError, match="create_plugin"):
        load_external_plugins(registry, tmp_path)

    registry2 = PluginRegistry()
    case2_dir = tmp_path / "case2"
    plugin_dir = _write_plugin(case2_dir, "two-modules")
    (plugin_dir / "extra.py").write_text("Y = 2\n", encoding="utf-8")
    with pytest.raises(PluginLoaderError, match="恰好包含一个"):
        load_external_plugins(registry2, case2_dir)


def test_two_phase_atomicity_keeps_good_plugins_grouped(tmp_path: Path) -> None:
    """两阶段加载：目录内任一插件核验失败则整批不注册（失败关闭）。"""
    _write_plugin(tmp_path, "good-one")
    _write_plugin(tmp_path, "bad-one", manifest=None)

    registry = PluginRegistry()
    with pytest.raises(PluginLoaderError):
        load_external_plugins(registry, tmp_path)
    assert len(registry.list_plugins()) == 0
