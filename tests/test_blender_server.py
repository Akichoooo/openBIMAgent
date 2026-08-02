"""Blender MCP server typed tool tests; socket connection is fully mocked."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from openbimagent.assembly.blender_plan import BlenderBuilder
from test_compiled_utility_ir import solved_payload

_SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers"
    / "blender_mcp"
    / "server"
    / "server.py"
)


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("blender_server_test", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeConnection:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((command, params or {}))
        return self.response


def test_execute_plan_tool_forwards_structured_plan_without_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _load_server_module()
    plan = BlenderBuilder().build(solved_payload()).model_dump(mode="json")
    output = tmp_path / "case.blend"
    response = {
        "receipt_id": "receipt-1",
        "plan_id": plan["plan_id"],
        "idempotency_key": plan["idempotency_key"],
        "canonical_sha256": plan["canonical_sha256"],
        "status": "completed",
        "output_path": str(output.resolve()),
        "snapshot_path": None,
        "state_path": str(output.resolve()) + ".openbimagent.json",
        "applied_operations": [],
        "confirmed_object_ids": [],
        "semantic_snapshot": None,
        "errors": [],
    }
    fake = _FakeConnection(response)
    monkeypatch.setattr(server, "AUTHORIZED_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "get_blender_connection", lambda: fake)
    result = json.loads(server.execute_plan(None, plan, str(output), approved=True))
    assert result == response
    assert fake.calls == [
        (
            "execute_plan",
            {
                "plan": plan,
                "output_path": str(output.resolve()),
                "approved": True,
            },
        )
    ]
    assert "code" not in fake.calls[0][1]


def test_execute_plan_tool_rejects_missing_root_and_scope_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _load_server_module()
    plan = BlenderBuilder().build(solved_payload()).model_dump(mode="json")
    fake = _FakeConnection({})
    monkeypatch.setattr(server, "get_blender_connection", lambda: fake)
    monkeypatch.setattr(server, "AUTHORIZED_ROOT", "")
    with pytest.raises(Exception, match="AUTHORIZED_ROOT"):
        server.execute_plan(None, plan, str(tmp_path / "case.blend"), approved=True)
    monkeypatch.setattr(server, "AUTHORIZED_ROOT", str(tmp_path))
    with pytest.raises(Exception, match="escaped authorized root"):
        server.execute_plan(None, plan, str(tmp_path.parent / "outside.blend"), approved=True)
    assert fake.calls == []


def test_server_tool_budget_is_exactly_twelve() -> None:
    source = _SERVER_PATH.read_text(encoding="utf-8")
    assert source.count("@mcp.tool()") == 12
