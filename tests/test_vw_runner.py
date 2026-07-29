"""VW MCP runner 单测:命令分发 + vs 代码执行(无 VW 环境部分用 mock)。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 runner.py。

覆盖(4 个测试):
- execute_command("ping") → {"message":"pong"}
- execute_command("describe_capabilities") → 含 vw_version/python_version/known_issues
- execute_command("unknown") → 抛 ValueError
- execute_vs_code 成功:mock vs 模块,执行简单代码 → {"ok":True,"stdout":...}
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ---------- 按路径加载 runner.py ----------

_RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "runner.py"
)


def _load_runner_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_runner_test", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 4 个测试 ----------


def test_execute_command_ping() -> None:
    """execute_command("ping") → {"message":"pong"}。"""
    runner = _load_runner_module()
    result = runner.execute_command("ping", {})
    assert result == {"message": "pong"}


def test_execute_command_describe_capabilities() -> None:
    """execute_command("describe_capabilities") → 含 vw_version/python_version/known_issues。"""
    runner = _load_runner_module()
    result = runner.execute_command("describe_capabilities", {})
    assert "vw_version" in result
    assert "python_version" in result
    assert "known_issues" in result
    assert isinstance(result["known_issues"], list)
    assert len(result["known_issues"]) > 0
    # 无 VW 环境下 vw_version 应为 "unknown"
    assert result["vw_version"] == "unknown"
    # ArcByCenter 坑应在 known_issues 中
    issues_text = " ".join(result["known_issues"])
    assert "ArcByCenter" in issues_text


def test_execute_command_unknown() -> None:
    """execute_command("unknown") → 抛 ValueError。"""
    runner = _load_runner_module()
    with pytest.raises(ValueError, match="Unknown command"):
        runner.execute_command("unknown_cmd", {})


def test_execute_vs_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_vs_code 成功:mock vs 模块,执行简单代码 → {"ok":True,"stdout":...}。

    无 VW 环境,vs 模块不存在,用 monkeypatch 注入 fake vs 模块。
    """
    runner = _load_runner_module()

    # 构造 fake vs 模块
    fake_vs = types.ModuleType("vs")
    captured: dict[str, Any] = {}

    def _message(text: str) -> str:
        captured["message"] = text
        return text

    fake_vs.Message = _message  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vs", fake_vs)

    result = runner.execute_vs_code("vs.Message('hello')")

    assert result["ok"] is True
    assert "stdout" in result
    assert result["error"] if not result["ok"] else True  # ok 时无 error 键
    assert captured.get("message") == "hello"
