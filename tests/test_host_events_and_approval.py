"""最后一批测试:A6 宿主事件通道 + F4 审批 fail-closed 语义。"""

from __future__ import annotations

import json
from pathlib import Path

from openbimagent.core.loop import AgentLoop
from openbimagent.orchestrator.host_events import HostEvent, HostEventChannel
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


# ---------- A6 ----------

def test_host_event_channel_pushes_into_subscribed_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.jsonl", title="host-push")
    channel = HostEventChannel()
    channel.subscribe(store)
    channel.push(HostEvent(kind="render_complete", host="blender", payload={"frames": 4}))
    channel.push(HostEvent(kind="export_done", host="vectorworks", payload={"path": "a.vwx"}))
    events = [e for e in store.load() if e.type is EventType.CUSTOM]
    payloads = [e.payload.model_dump(mode="json") for e in events]
    assert [p["customType"] for p in payloads] == ["host_event", "host_event"]
    assert payloads[0]["kind"] == "render_complete" and payloads[0]["frames"] == 4
    assert payloads[1]["host"] == "vectorworks"
    # drain 与落盘解耦:取走清空,重复 drain 为空
    drained = channel.drain()
    assert [e.kind for e in drained] == ["render_complete", "export_done"]
    assert channel.drain() == []


def test_host_event_channel_unsubscribe_stops_pushes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.jsonl", title="unsub")
    channel = HostEventChannel()
    channel.subscribe(store)
    channel.unsubscribe(store)
    channel.push(HostEvent(kind="host_error", host="blender", payload={}))
    assert [e for e in store.load() if e.type is EventType.CUSTOM] == []
    assert len(channel.drain()) == 1


def test_host_event_custom_type_passes_schema_gate(tmp_path: Path) -> None:
    """host_event 必须过 session_event 门禁(枚举已同步扩展)。"""
    from openbimagent.schema_gate import gate as schema_gate

    store = SessionStore(tmp_path / "s.jsonl", title="gate")
    channel = HostEventChannel()
    channel.subscribe(store)
    channel.push(HostEvent(kind="batch_done", host="blender", payload={"batch": "主体"}))
    event = next(e for e in store.load() if e.type is EventType.CUSTOM)
    assert schema_gate.validate_artifact("session_event", event.model_dump(mode="json")) == []


# ---------- F4 ----------

def test_ask_without_approver_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """ASK 工具在无审批回调且 stdin 不可用(EOF)时必须拒绝,绝不静默放行。"""
    def _eof(prompt: str = "") -> str:
        raise EOFError("no stdin in headless mode")

    monkeypatch.setattr("builtins.input", _eof)
    session = SessionStore(tmp_path / "s.jsonl", title="approval")
    loop = AgentLoop(
        ["write"], session, chat_fn=lambda *a, **k: {"content": "x"}, workdir=tmp_path,
        permission_rules={"write": "ask"},  # 无 approval_callback → 走默认 _cli_approval(stdin)
    )
    result = loop._dispatch("write", {"path": str(tmp_path / "a.txt"), "content": "x"})
    assert result["status"] == "rejected"  # 审批不可用 → fail-closed 拒绝
    assert result["ui_view"]["permission"] == "approval_unavailable"
    assert not (tmp_path / "a.txt").exists(), "审批不可用时必须 fail-closed,不得落盘"


def test_ask_with_rejecting_approver_blocks_tool(tmp_path: Path) -> None:
    session = SessionStore(tmp_path / "s.jsonl", title="approval")
    loop = AgentLoop(
        ["write"], session, chat_fn=lambda *a, **k: {"content": "x"}, workdir=tmp_path,
        approval_callback=lambda name, args: False,
    )
    result = loop._dispatch("write", {"path": str(tmp_path / "b.txt"), "content": "x"})
    assert result["status"] == "rejected"
    assert not (tmp_path / "b.txt").exists()
