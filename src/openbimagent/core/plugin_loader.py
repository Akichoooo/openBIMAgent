"""外部插件发现与加载器 (External Plugin Discovery & Loader)。

约定（对标 DSH `dsh-plugin` topic 生态的最小机制占位）：
  - 外部插件 = 插件目录内的一个 Python 模块 + 同目录 `openbimagent-plugin.toml` manifest
  - 模块必须暴露 ``create_plugin() -> BIMPlugin`` 工厂
  - manifest ``[plugin]`` 表声明 plugin_id/name/version/capabilities，
    与插件运行时事实不符即拒绝加载（失败关闭）
  - 发现路径由 ``OPENBIMAGENT_PLUGINS_DIR`` 环境变量或显式参数指定
  - 生态索引约定：插件仓库打 GitHub topic ``openbimagent-plugin``

安全边界：加载外部 Python 模块即执行其代码，目录必须受信任；
加载器采用两阶段策略（先全部解析核验，再统一注册），单个坏插件不会污染注册表。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import tomllib
from pathlib import Path

from openbimagent.core.plugin import BIMPlugin, PluginRegistry

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "openbimagent-plugin.toml"
PLUGINS_DIR_ENV = "OPENBIMAGENT_PLUGINS_DIR"
TOPIC_CONVENTION = "openbimagent-plugin"
_MANIFEST_REQUIRED_FIELDS = ("plugin_id", "name", "version", "capabilities")


class PluginLoaderError(ValueError):
    """外部插件 manifest 缺失、字段不全或与插件运行时事实不符。"""


def discover_plugin_dirs(plugin_dir: Path) -> list[Path]:
    """列出插件目录下的候选子目录（跳过隐藏与下划线开头目录）。"""
    if not plugin_dir.is_dir():
        return []
    return sorted(
        p for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    )


def _parse_manifest(module_dir: Path) -> dict[str, object]:
    manifest_path = module_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise PluginLoaderError(
            f"外部插件 '{module_dir.name}' 缺少 manifest: {MANIFEST_FILENAME} (约定见 plugins/README.md)"
        )
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' manifest 解析失败: {exc}") from exc

    table = manifest.get("plugin")
    if not isinstance(table, dict):
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' manifest 必须包含 [plugin] 表")
    missing = [field for field in _MANIFEST_REQUIRED_FIELDS if field not in table]
    if missing:
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' manifest 缺少字段: {missing}")
    capabilities = table["capabilities"]
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' manifest capabilities 必须是字符串数组")
    return table


def _resolve_plugin(module_dir: Path) -> BIMPlugin:
    """解析单个外部插件：加载模块、执行工厂、manifest 事实核验。"""
    py_files = [f for f in module_dir.glob("*.py") if f.name != "__init__.py"]
    if len(py_files) != 1:
        raise PluginLoaderError(
            f"外部插件 '{module_dir.name}' 必须恰好包含一个 .py 模块文件 (发现 {len(py_files)} 个)"
        )

    table = _parse_manifest(module_dir)

    module_name = f"openbimagent_external.{module_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, py_files[0])
    if spec is None or spec.loader is None:
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' 模块 spec 创建失败: {py_files[0].name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' 模块执行失败: {exc}") from exc

    factory = getattr(module, "create_plugin", None)
    if not callable(factory):
        del sys.modules[module_name]
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' 必须暴露 create_plugin() -> BIMPlugin 工厂")

    plugin = factory()
    if not isinstance(plugin, BIMPlugin):
        del sys.modules[module_name]
        raise PluginLoaderError(f"外部插件 '{module_dir.name}' create_plugin() 返回类型不是 BIMPlugin")

    # manifest 与运行时事实核验 (失败关闭)
    if plugin.plugin_id != table["plugin_id"]:
        raise PluginLoaderError(
            f"外部插件 '{module_dir.name}' manifest plugin_id ({table['plugin_id']}) "
            f"与运行时 ({plugin.plugin_id}) 不一致"
        )
    manifest_caps = set(table["capabilities"])  # type: ignore[arg-type]
    actual_caps = set(plugin.provides_capabilities)
    if manifest_caps != actual_caps:
        raise PluginLoaderError(
            f"外部插件 '{module_dir.name}' manifest capabilities {sorted(manifest_caps)} "
            f"与运行时 provides_capabilities {sorted(actual_caps)} 不一致"
        )

    logger.info(
        "外部插件 manifest 核验通过: %s (v%s, capabilities=%s)",
        plugin.plugin_id,
        plugin.version,
        sorted(actual_caps),
    )
    return plugin


def load_external_plugins(
    registry: PluginRegistry,
    plugin_dir: Path | str | None = None,
) -> list[str]:
    """从外部插件目录发现并注册全部插件，返回注册成功的 plugin_id 列表。

    两阶段失败关闭：先解析核验全部插件（任一不符即抛错、注册表零污染），
    再统一注册（开启依赖校验，外部插件可声明对内核能力的 requires）。
    目录不存在或未配置时静默返回空列表（零行为变化）。
    """
    if plugin_dir is not None:
        directory = Path(plugin_dir)
    else:
        env_value = os.environ.get(PLUGINS_DIR_ENV, "")
        if not env_value:
            return []
        directory = Path(env_value)

    if not directory.is_dir():
        return []

    candidates = discover_plugin_dirs(directory)
    resolved = [_resolve_plugin(module_dir) for module_dir in candidates]

    registered: list[str] = []
    for plugin in resolved:
        registry.register(plugin, check_dependencies=True)
        registered.append(plugin.plugin_id)
    return registered
