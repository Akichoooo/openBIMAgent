"""M2 P4 SSE 网络端点测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from openbimagent.server.fastapi_app import build_m2_readonly_app
from openbimagent.server.readonly_http import M2ReadonlyHttpAdapter
from openbimagent.server.service import M2ReadOnlyService
from openbimagent.server.sse_endpoint import M2SseStreamBudget
from openbimagent.session.schema import CustomPayload, CustomType, EventType, SessionEvent
from openbimagent.session.store import SessionStore


class _MockReader:
    def list_attempts(self, **kw):
        return ()
    def get_attempt(self, _):
        raise ValueError("no runtime")
    def get_lineage(self, _):
        return ()
    def list_approvals(self, **kw):
        return ()


def test_sse_with_invalid_session_id_returns_event() -> None:
    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=Path("."))
    client = TestClient(app)
    resp = client.get("/api/v1/sessions/invalid-session/events/projection", headers={"X-Request-ID": "t-001"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_sse_unknown_session_returns_error() -> None:
    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=Path("."))
    client = TestClient(app)
    resp = client.get("/api/v1/sessions/nonexistent/events/projection", headers={"X-Request-ID": "t-002"})
    assert resp.status_code == 200
    lines = resp.text.split("\n")
    # Should return an error event
    assert any("event: error" in line for line in lines)


def test_sse_stream_budget_rejects_over_limit() -> None:
    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    budget = M2SseStreamBudget(max_active=0)
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=Path("."), sse_budget=budget)
    client = TestClient(app)
    resp = client.get("/api/v1/sessions/valid-id/events/projection", headers={"X-Request-ID": "t-003"})
    assert resp.status_code == 200
    lines = resp.text.split("\n")
    assert any("event: error" in line for line in lines)


def _make_subagent_event(
    event_id: str, request_id: str, agent_id: str, child_session_id: str, status: str
) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        parentId=None,
        timestamp=datetime.now(timezone.utc),
        type=EventType.CUSTOM,
        payload=CustomPayload(
            customType=CustomType.SUBAGENT_CREATED,
            request_id=request_id,
            agent_id=agent_id,
            child_session_id=child_session_id,
            status=status,
            lineage_id="lineage-001",
            attempt_number=1,
        ),
    )


def test_sse_with_real_session(tmp_path: Path) -> None:
    """Create a real session, add a subagent event, then verify SSE projects it."""
    store = SessionStore(tmp_path / "test-session.jsonl", title="test")
    store.append(_make_subagent_event("evt-001", "req-001", "agent-A", "child-001", "created"))

    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=tmp_path)
    client = TestClient(app)
    resp = client.get(
        f"/api/v1/sessions/{store.session_id}/events/projection",
        headers={"X-Request-ID": "t-004"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # Should contain at least one SSE event
    assert "event:" in resp.text
    assert "id:" in resp.text
    # Verify the event data is valid JSON
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            assert "event_type" in payload
            assert "session_id" in payload


def test_sse_replay_with_last_event_id(tmp_path: Path) -> None:
    """Create a session with multiple events, verify Last-Event-ID replay."""
    store = SessionStore(tmp_path / "test-replay.jsonl", title="test")

    for i in range(3):
        store.append(_make_subagent_event(
            f"evt-{i}", f"req-{i}", "agent-A", f"child-{i}", "created"
        ))
    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=tmp_path)
    client = TestClient(app)

    # First request: get all events
    resp1 = client.get(
        f"/api/v1/sessions/{store.session_id}/events/projection",
        headers={"X-Request-ID": "t-005", "Last-Event-ID": ""},
    )
    assert resp1.status_code == 200
    event_ids = []
    for line in resp1.text.split("\n"):
        if line.startswith("id: "):
            event_ids.append(line[4:])
    assert len(event_ids) > 0

    # Use the second-to-last event ID for replay
    if len(event_ids) >= 2:
        last_id = event_ids[-2]
        resp2 = client.get(
            f"/api/v1/sessions/{store.session_id}/events/projection",
            headers={"X-Request-ID": "t-006", "Last-Event-ID": last_id},
        )
        assert resp2.status_code == 200
        # Should have fewer events than the full list
        replay_ids = [line.split("id: ")[1] for line in resp2.text.split("\n") if line.startswith("id: ")]
        assert len(replay_ids) < len(event_ids)


def test_events_json_and_m2_projection_do_not_shadow(tmp_path: Path) -> None:
    """B5 回归：同一 app 传入 sessions_dir 时，workbench 的 JSON /events(runs.py)与
    M2 持久投影 SSE /events/projection(sse_endpoint.py)分流到不同 handler，互不遮蔽。

    修复前两者抢注 /events，FastAPI 先注册先匹配 → server 模式(传 sessions_dir)下
    workbench 的 JSON 读取端点被 M2 SSE 遮蔽，前端 getSessionEvents 拿不到 JSON。
    """
    service = M2ReadOnlyService(
        control_plane=_MockReader(), session_index_reader=lambda: [], artifact_lookup=lambda _: None
    )
    adapter = M2ReadonlyHttpAdapter(service)
    app = build_m2_readonly_app(adapter, sessions_dir=tmp_path)
    client = TestClient(app)

    # workbench JSON 读取端点命中 runs.py(content-type=application/json)，未被 M2 SSE 遮蔽
    json_resp = client.get("/api/v1/sessions/coexist-check/events")
    assert json_resp.headers["content-type"].startswith("application/json")

    # M2 持久投影端点走独立子路径，命中 sse_endpoint.py(content-type=text/event-stream)
    proj_resp = client.get(
        "/api/v1/sessions/coexist-check/events/projection",
        headers={"X-Request-ID": "t-coexist"},
    )
    assert proj_resp.headers["content-type"].startswith("text/event-stream")