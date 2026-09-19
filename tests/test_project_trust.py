"""F5 Project Trust 测试:项目本地插件未信任前不加载,approve 后加载。"""

from __future__ import annotations

import shutil
import warnings
from pathlib import Path

from openbimagent.core import plugin_loader
from openbimagent.core.plugin import PluginRegistry
from openbimagent.core.project_trust import approve, is_project_local, revoke


def _seed_project_plugins(tmp_path: Path) -> Path:
    src = Path("plugins/example-echo")
    dest = tmp_path / "proj-plugins" / "example_echo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    (tmp_path / ".git").mkdir()  # 使其成为项目本地
    return tmp_path / "proj-plugins"


def test_project_local_plugin_dir_blocked_until_trusted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENBIMAGENT_TRUST_FILE", str(tmp_path / "trust.json"))
    plugin_dir = _seed_project_plugins(tmp_path)
    assert is_project_local(plugin_dir)
    registry = PluginRegistry()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        loaded = plugin_loader.load_external_plugins(registry, plugin_dir=plugin_dir)
    assert loaded == [], "未信任的项目本地插件目录不应加载"
    approve(plugin_dir)
    loaded2 = plugin_loader.load_external_plugins(registry, plugin_dir=plugin_dir)
    assert "plugin.external.example_echo" in loaded2
    revoke(plugin_dir)


def test_non_project_dir_loads_without_trust(tmp_path: Path, monkeypatch) -> None:
    # 不含 .git 的目录非项目本地,无需信任
    monkeypatch.setenv("OPENBIMAGENT_TRUST_FILE", str(tmp_path / "trust.json"))
    plugin_dir = tmp_path / "non-project"
    shutil.copytree("plugins/example-echo", plugin_dir / "example_echo")
    registry = PluginRegistry()
    loaded = plugin_loader.load_external_plugins(registry, plugin_dir=plugin_dir)
    assert "plugin.external.example_echo" in loaded


def test_cli_trust_approve_list_check(tmp_path: Path, capsys) -> None:
    from openbimagent.cli import main

    trust_file = tmp_path / "trust.json"
    target = tmp_path / "proj"
    (target / ".git").mkdir(parents=True)
    code = main(["trust", "check", str(target), "--trust-file", str(trust_file)])
    assert code == 1  # 项目本地未信任 → 不允许
    assert "trusted=False" in capsys.readouterr().out
    code = main(["trust", "approve", str(target), "--trust-file", str(trust_file)])
    assert code == 0
    code = main(["trust", "check", str(target), "--trust-file", str(trust_file)])
    assert code == 0
    code = main(["trust", "list", "--trust-file", str(trust_file)])
    out = capsys.readouterr().out
    assert code == 0 and str(target) in out


def test_mcp_serve_tools_callable(tmp_path: Path, monkeypatch) -> None:
    """A5:四只读工具可直接调用(mcp 装饰器包装后取底层函数)。"""
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    from openbimagent.server import mcp_serve

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    from openbimagent.session.schema import EventType
    from openbimagent.session.store import SessionStore

    store = SessionStore(sessions / "s1.jsonl", title="mcp-test")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "hi"})
    import json as _json

    def _call(tool, *args):
        return getattr(tool, "fn", tool)(*args)

    listed = _json.loads(_call(mcp_serve.list_sessions))
    assert any(e["session_id"] == "s1" for e in listed)
    summary = _json.loads(_call(mcp_serve.session_summary, "s1"))
    assert summary["session_id"] == "s1"
    health = _json.loads(_call(mcp_serve.mcp_health))
    assert "vectorworks" in health["declared_hosts"]
    api = _json.loads(_call(mcp_serve.lookup_vs_api, "Wall", 2))
    assert api["matches"]
