"""Agent Core VectorworksMCPClient 单测：全程 fake MCP，无真实 VW。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openbimagent.assembly.vectorworks_plan import (
    ReceiptStatus,
    VectorworksBuilder,
    VectorworksExecutionReceipt,
)
from openbimagent.mcp_clients.vectorworks import VectorworksClientError, VectorworksMCPClient
from test_compiled_utility_ir import solved_payload


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


def test_execute_plan_sends_typed_payload_and_validates_receipt() -> None:
    async def run() -> None:
        plan = VectorworksBuilder().build(solved_payload())
        receipt = VectorworksExecutionReceipt(
            receipt_id=f"vw-receipt-{plan.canonical_sha256[:24]}",
            plan_id=plan.plan_id,
            idempotency_key=plan.idempotency_key,
            canonical_sha256=plan.canonical_sha256,
            status=ReceiptStatus.COMPLETED,
            output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx",
            state_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx.openbimagent.json",
        )
        client = VectorworksMCPClient(toolset="minimal")
        fake = FakeMCP({
            "execute_plan": result(
                structured={"result": receipt.model_dump(mode="json")}
            ),
        })
        client._mcp_client = fake

        out = await client.execute_plan(
            plan,
            output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx",
            approved=True,
        )

        assert out == receipt
        assert fake.calls == [
            (
                "execute_plan",
                {
                    "plan": plan.model_dump(mode="json"),
                    "output_path": r"D:\devloop\G6_Test\openbimagent_g6.vwx",
                    "approved": True,
                },
            )
        ]

    asyncio.run(run())


def test_execute_plan_accepts_server_typed_capabilities_envelope() -> None:
    async def run() -> None:
        plan = VectorworksBuilder().build(solved_payload())
        receipt = VectorworksExecutionReceipt(
            receipt_id=f"vw-receipt-{plan.canonical_sha256[:24]}",
            plan_id=plan.plan_id,
            idempotency_key=plan.idempotency_key,
            canonical_sha256=plan.canonical_sha256,
            status=ReceiptStatus.COMPLETED,
            output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx",
            state_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx.openbimagent.json",
        )
        client = VectorworksMCPClient(
            default_output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx"
        )
        client._mcp_client = FakeMCP({
            "execute_plan": result(structured={"result": receipt.model_dump(mode="json")}),
        })
        capabilities = {
            "server_version": "1.0.0-m1",
            "typed_execution": {
                "protocol_version": "1.0",
                "host_api_version": "2024",
                "units": ["m", "mm"],
                "operations": ["create_object", "set_record", "connect_topology"],
                "object_types": [
                    "utility_system", "manhole", "inlet", "outlet", "junction",
                    "valve", "equipment", "terminal", "distribution_port", "pipe_segment",
                ],
            },
        }
        out = await client.execute_plan(plan, approved=True, capabilities=capabilities)
        assert out == receipt

    asyncio.run(run())


def test_execute_plan_rejects_receipt_identity_tampering() -> None:
    async def run() -> None:
        plan = VectorworksBuilder().build(solved_payload())
        client = VectorworksMCPClient()
        client._mcp_client = FakeMCP({
            "execute_plan": result(structured={"result": {
                "receipt_id": "vw-receipt-tampered",
                "plan_id": plan.plan_id,
                "idempotency_key": plan.idempotency_key,
                "canonical_sha256": "0" * 64,
                "status": "completed",
                "output_path": r"D:\devloop\G6_Test\openbimagent_g6.vwx",
                "state_path": r"D:\devloop\G6_Test\openbimagent_g6.vwx.openbimagent.json",
                "applied_operations": [],
                "confirmed_object_ids": [],
                "semantic_snapshot": None,
                "compensations": [],
                "errors": [],
            }}),
        })
        with pytest.raises(VectorworksClientError, match="receipt plan/output/state identity"):
            await client.execute_plan(
                plan,
                output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx",
                approved=True,
            )

    asyncio.run(run())


def test_execute_plan_rejects_receipt_output_identity_tampering() -> None:
    async def run() -> None:
        plan = VectorworksBuilder().build(solved_payload())
        client = VectorworksMCPClient()
        client._mcp_client = FakeMCP({
            "execute_plan": result(structured={"result": {
                "receipt_id": f"vw-receipt-{plan.canonical_sha256[:24]}",
                "plan_id": plan.plan_id,
                "idempotency_key": plan.idempotency_key,
                "canonical_sha256": plan.canonical_sha256,
                "status": "completed",
                "output_path": r"D:\devloop\G6_Test\different.vwx",
                "state_path": r"D:\devloop\G6_Test\different.vwx.openbimagent.json",
                "applied_operations": [],
                "confirmed_object_ids": [],
                "semantic_snapshot": None,
                "compensations": [],
                "errors": [],
            }}),
        })
        with pytest.raises(VectorworksClientError, match="output/state identity"):
            await client.execute_plan(
                plan,
                output_path=r"D:\devloop\G6_Test\openbimagent_g6.vwx",
                approved=True,
            )

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
