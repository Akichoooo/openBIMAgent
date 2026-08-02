"""VW MCP server 工具单测:3 个 @mcp.tool 调用(全程 mock FileIPCClient)。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 server.py,
用 set_client 注入 fake client,避免真实文件 IO。

覆盖(3 个测试):
- ping() 工具:mock send_command 返回 {"message":"pong"} → 返回 "pong"
- describe_capabilities() 工具:返回含 server_version/vectorworks_version
- execute_vs_code() 工具:mock send_command → 返回 {"ok":True}
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

# ---------- 按路径加载 server.py ----------

_SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "server" / "server.py"
)


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_server_test", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeClient:
    """fake FileIPCClient:按 command 返回 canned 结果。"""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((command, params or {}))
        return self.responses.get(command, {"error": f"no canned response for {command}"})


# ---------- 3 个测试 ----------


def test_ping_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """ping() 工具:mock send_command 返回 {"message":"pong"} → 返回 "pong"。"""
    server = _load_server_module()
    fake = _FakeClient({"ping": {"message": "pong"}})
    server.set_client(fake)  # type: ignore[attr-defined]
    try:
        result = server.ping()
        assert result == "pong"
        assert fake.calls == [("ping", {})]
    finally:
        # 重置全局 client,避免污染其他测试
        server._client = None  # type: ignore[attr-defined]


def test_describe_capabilities_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """describe_capabilities() 工具:返回含 server_version/vectorworks_version。"""
    server = _load_server_module()
    fake = _FakeClient({
        "describe_capabilities": {
            "vw_version": "VectorWorks 2024",
            "python_version": "3.13.0",
            "known_issues": ["ArcByCenter 已损坏"],
        }
    })
    server.set_client(fake)  # type: ignore[attr-defined]
    try:
        result = server.describe_capabilities()
        assert "server_version" in result
        assert result["server_version"] == "1.0.0-m1"
        assert result["vectorworks_version"] == "VectorWorks 2024"
        assert result["python_version"] == "3.13.0"
        assert result["file_ipc"] is True
        assert result["toolset"] == "minimal"
        assert "ArcByCenter 已损坏" in result["known_issues"]
        assert fake.calls == [("describe_capabilities", {})]
    finally:
        server._client = None  # type: ignore[attr-defined]


def test_execute_plan_tool_forwards_typed_payload_and_approval() -> None:
    """MCP 工具只转发结构化 plan，不得转译为 execute_code。"""
    server = _load_server_module()
    plan = {
        "plan_version": "1.0",
        "protocol_version": "1.0",
        "host_api_version": "2024",
        "plan_id": "vw-plan-test",
        "ir_id": "ir-test",
        "source_ir_sha256": "a" * 64,
        "units": "m",
        "operations": [{"operation_id": "create:node-1"}],
        "canonical_sha256": "b" * 64,
        "idempotency_key": f"vw-plan:{'b' * 64}",
    }
    fake = _FakeClient({"execute_plan": {"status": "completed"}})
    server.set_client(fake)
    server.DEFAULT_AUTHORIZED_ROOT = ""
    try:
        result = server.execute_plan(
            plan,
            r"D:\devloop\G6_Test\openbimagent_g6.vwx",
            approved=True,
        )
        assert result["status"] == "completed"
        assert fake.calls == [
            (
                "execute_plan",
                {
                    "plan": plan,
                    "output_path": r"D:\devloop\G6_Test\openbimagent_g6.vwx",
                    "_approved": True,
                },
            )
        ]
    finally:
        server._client = None


def test_execute_vs_code_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_vs_code() 工具:mock send_command → 返回 {"ok":True}。"""
    server = _load_server_module()
    fake = _FakeClient({
        "execute_code": {"ok": True, "stdout": "done", "stderr": ""}
    })
    server.set_client(fake)  # type: ignore[attr-defined]
    try:
        result = server.execute_vs_code("vs.Message('test')")
        assert result["ok"] is True
        assert result["stdout"] == "done"
        # 验证传入的 command 和 params
        assert len(fake.calls) == 1
        cmd, params = fake.calls[0]
        assert cmd == "execute_code"
        assert params == {"code": "vs.Message('test')"}

        approved_result = server.execute_vs_code("vs.IFC_ExportWithUI('x.ifc')", approved=True)
        assert approved_result["ok"] is True
        assert fake.calls[-1] == (
            "execute_code",
            {"code": "vs.IFC_ExportWithUI('x.ifc')", "_approved": True},
        )
    finally:
        server._client = None  # type: ignore[attr-defined]
