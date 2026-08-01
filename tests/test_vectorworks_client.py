"""Agent Core VectorworksMCPClient 单测：全程 fake MCP，无真实 VW。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openbimagent.mcp_clients.vectorworks import VectorworksClientError, VectorworksMCPClient


class FakeMCP:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.responses[name]


def result(*, structured=None, text=None, is_error=False):
    content = [SimpleNamespace(text=text)] if text is not None else []
    return SimpleNamespace(structured_content=structured, content=content, is_error=is_error)


def test_connect_health_and_capabilities() -> None:
    async def run() -> None:
        client = VectorworksMCPClient(toolset="modeling")
        fake = FakeMCP({
            "ping": result(text="pong"),
            "describe_capabilities": result(structured={"result": {"vectorworks_version": "2024", "toolset": "modeling"}}),
        })
        client._mcp_client = fake
        await client.connect()
        assert client.is_connected is True
        assert client.capabilities == {"vectorworks_version": "2024", "toolset": "modeling"}
        assert [c[0] for c in fake.calls] == ["ping", "describe_capabilities"]
        await client.close()
        assert client.is_connected is False

    asyncio.run(run())


def test_execute_code_success_and_approval_passthrough() -> None:
    async def run() -> None:
        client = VectorworksMCPClient(toolset="full")
        fake = FakeMCP({
            "execute_vs_code": result(structured={"result": '{"ok": true, "stdout": "done"}'}),
        })
        client._mcp_client = fake
        out = await client.execute_code("vs.Rect((0, 0), (10, 10))", approved=True)
        assert out["ok"] is True
        assert fake.calls == [
            ("execute_vs_code", {"code": "vs.Rect((0, 0), (10, 10))", "approved": True})
        ]

    asyncio.run(run())


def test_execute_code_turns_server_gate_failure_into_error() -> None:
    async def run() -> None:
        client = VectorworksMCPClient(toolset="full")
        client._mcp_client = FakeMCP({
            "execute_vs_code": result(structured={"result": {
                "ok": False,
                "error": "未审批",
                "gate_blocked": True,
            }}),
        })
        with pytest.raises(VectorworksClientError, match="approval_gate"):
            await client.execute_code("vs.IFC_ExportWithUI('x.ifc')")

    asyncio.run(run())


def test_call_tool_rejects_unknown_and_is_error() -> None:
    async def run() -> None:
        client = VectorworksMCPClient()
        client._mcp_client = FakeMCP({"ping": result(text="bad", is_error=True)})
        with pytest.raises(VectorworksClientError, match="未知"):
            await client.call_tool("delete_everything", {})
        with pytest.raises(VectorworksClientError, match="is_error"):
            await client.call_tool("ping", {})

    asyncio.run(run())


def test_invalid_toolset_rejected() -> None:
    with pytest.raises(ValueError, match="toolset"):
        VectorworksMCPClient(toolset="unsafe")
