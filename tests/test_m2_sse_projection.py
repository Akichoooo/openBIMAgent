"""M2 P4 pre-G7 持久事实 SSE 纯函数投影与离线回放测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.orchestrator.approval import ApprovalDecision, ApprovalRequest, DecisionReceipt
from openbimagent.orchestrator.contracts import (
    ArtifactRecord,
    ArtifactStatus,
    SubagentRequest,
    SubagentStatus,
)
from openbimagent.orchestrator.runtime import ChildRunOutput, LocalSubagentRuntime
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.server.contracts import M2ErrorCode, M2SseCursor, M2SseEventType
from openbimagent.server.sse_projection import M2SseProjector, SseProjectionError
from openbimagent.session.schema import CustomPayload, CustomType, EventType, SessionEvent
from openbimagent.session.store import SessionStore

BASE = datetime(2026, 8, 4, tzinfo=timezone.utc)
PROJECTOR = M2SseProjector()


def _event(custom_type: CustomType, payload: dict, *, offset: int, event_id: str) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        parentId=None,
        timestamp=BASE + timedelta(seconds=offset),
        type=EventType.CUSTOM,
        payload=CustomPayload(customType=custom_type, **payload),
    )


def _lifecycle(custom_type: CustomType, *, status: str, offset: int, event_id: str, **extra) -> SessionEvent:
    return _event(
        custom_type,
        {
            "request_id": "request-1",
            "agent_id": "agent-1",
            "lineage_id": "lineage-1",
            "attempt_number": 1,
            "resumed_from_request_id": None,
            "parent_session_id": "parent-1",
            "child_session_id": "child-1",
            "child_session_path": "D:/private/child-1.jsonl",
            "status": status,
            **extra,
        },
        offset=offset,
        event_id=event_id,
    )


def _approval_request(*, offset: int = 2, event_id: str = "approval-request") -> SessionEvent:
    approval = ApprovalRequest(
        approval_id="approval-1",
        request_id="request-1",
        agent_id="agent-1",
        parent_session_id="parent-1",
        child_session_id="child-1",
        tool_name="write",
        permission_key="write",
        args_summary='{"content":"str","path":"str"}',
        args_sha256="a" * 64,
        requested_at=BASE + timedelta(seconds=2),
    )
    return _event(
        CustomType.APPROVAL_REQUESTED,
        approval.model_dump(mode="json"),
        offset=offset,
        event_id=event_id,
    )


def _approval_decision(*, offset: int = 3, event_id: str = "approval-decision") -> SessionEvent:
    receipt = DecisionReceipt(
        receipt_id="receipt-approval-1",
        approval_id="approval-1",
        request_id="request-1",
        agent_id="agent-1",
        decision=ApprovalDecision.APPROVED,
        decided_by=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN, display_name="JY"),
        reason="private reason",
        decided_at=BASE + timedelta(seconds=3),
    )
    return _event(
        CustomType.APPROVAL_DECIDED,
        receipt.model_dump(mode="json"),
        offset=offset,
        event_id=event_id,
    )


def _artifact(*, offset: int = 4, event_id: str = "artifact") -> SessionEvent:
    record = ArtifactRecord(
        artifact_id="artifact-1",
        kind="ifc",
        path="D:/private/result.ifc",
        relative_path="result.ifc",
        media_type="application/x-step",
        sha256="b" * 64,
        size_bytes=128,
        source_attempt_id="request-1",
        status=ArtifactStatus.COMPLETED,
    )
    return _event(
        CustomType.ARTIFACT_COMMITTED,
        {
            "request_id": "request-1",
            "agent_id": "agent-1",
            "artifact": record.model_dump(mode="json"),
        },
        offset=offset,
        event_id=event_id,
    )


def _facts() -> list[SessionEvent]:
    return [
        _lifecycle(CustomType.SUBAGENT_CREATED, status="created", offset=0, event_id="created", role="worker"),
        _lifecycle(CustomType.SUBAGENT_STARTED, status="running", offset=1, event_id="started", role="worker"),
        _approval_request(),
        _approval_decision(),
        _artifact(),
        _lifecycle(
            CustomType.SUBAGENT_COMPLETED,
            status="completed",
            offset=5,
            event_id="completed",
            receipt_id="receipt-attempt-1",
        ),
    ]


def test_projection_is_deterministic_and_terminal_is_explicit() -> None:
    first = PROJECTOR.project(session_id="parent-1", events=_facts())
    second = PROJECTOR.project(session_id="parent-1", events=reversed(_facts()))
    assert first == second
    assert [event.sequence for event in first] == list(range(1, 7))
    assert [event.event_type for event in first] == [
        M2SseEventType.ATTEMPT,
        M2SseEventType.ATTEMPT,
        M2SseEventType.APPROVAL,
        M2SseEventType.APPROVAL,
        M2SseEventType.ARTIFACT,
        M2SseEventType.TERMINAL,
    ]
    assert first[-1].terminal is True
    assert first[-1].data["status"] == "completed"
    for event in first:
        assert validate_artifact("m2_sse_event", event.model_dump(mode="json")) == []
    cursor = PROJECTOR.cursor_for(first[-1])
    assert validate_artifact("m2_sse_cursor", cursor.model_dump(mode="json")) == []


def test_parent_child_duplicate_facts_are_deduplicated_with_stable_identity() -> None:
    facts = _facts()
    duplicates = [
        event.model_copy(update={"id": f"child-{event.id}", "timestamp": event.timestamp + timedelta(milliseconds=1)})
        for event in facts
    ]
    baseline = PROJECTOR.project(session_id="parent-1", events=facts)
    projected = PROJECTOR.project(session_id="parent-1", events=[*duplicates, *facts])
    assert projected == baseline
    assert len({event.event_id for event in projected}) == len(projected)


def test_projection_whitelists_data_and_never_exposes_paths_or_private_text() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    serialized = str([event.model_dump(mode="json") for event in events])
    for forbidden in (
        "D:/private",
        "child_session_path",
        "args_summary",
        "private reason",
        "display_name",
        "human:jy",
        "actor_id",
        "relative_path",
        "result.ifc",
    ):
        assert forbidden not in serialized
    artifact = next(event for event in events if event.event_type is M2SseEventType.ARTIFACT)
    assert artifact.data["download_available"] is False
    approval = next(event for event in events if event.data.get("state") == "pending")
    assert approval.data["args_sha256"] == "a" * 64


@pytest.mark.parametrize("session_id", [".", "..", "session/escape", r"session\escape", "C:session", "tenant:session"])
def test_projection_rejects_invalid_stream_session_even_when_no_event_is_projected(session_id: str) -> None:
    with pytest.raises(SseProjectionError, match="session_id") as exc_info:
        PROJECTOR.project(session_id=session_id, events=[])
    assert exc_info.value.code is M2ErrorCode.INVALID_REQUEST


def test_non_whitelisted_session_events_are_ignored() -> None:
    message = SessionEvent(
        id="message",
        parentId=None,
        timestamp=BASE,
        type=EventType.MESSAGE,
        payload={"role": "user", "content": "private task"},
    )
    snapshot = _event(
        CustomType.SNAPSHOT,
        {"blend_file_path": "D:/private/a.blend", "hash": "c" * 64},
        offset=1,
        event_id="snapshot",
    )
    assert PROJECTOR.project(session_id="parent-1", events=[message, snapshot]) == ()


def test_approval_and_artifact_require_lifecycle_attempt_identity() -> None:
    for fact in (_approval_request(), _approval_decision(), _artifact()):
        with pytest.raises(SseProjectionError, match="缺少.*lifecycle") as exc_info:
            PROJECTOR.project(session_id="parent-1", events=[fact])
        assert exc_info.value.code is M2ErrorCode.CONFLICT


def test_conflicting_duplicate_facts_fail_closed() -> None:
    created = _lifecycle(CustomType.SUBAGENT_CREATED, status="created", offset=0, event_id="created")
    tampered = _lifecycle(
        CustomType.SUBAGENT_CREATED,
        status="created",
        offset=1,
        event_id="tampered",
        role="different-role",
    )
    with pytest.raises(SseProjectionError, match="持久事实冲突"):
        PROJECTOR.project(session_id="parent-1", events=[created, tampered])


@pytest.mark.parametrize("fact_factory", [_approval_request, _approval_decision, _artifact])
def test_child_fact_agent_must_match_lifecycle(fact_factory) -> None:
    created = _lifecycle(CustomType.SUBAGENT_CREATED, status="created", offset=0, event_id="created")
    fact = fact_factory()
    tampered = fact.model_copy(
        update={
            "payload": fact.payload.model_copy(update={"agent_id": "agent-other"}),
        }
    )
    with pytest.raises(SseProjectionError, match="agent_id.*lifecycle"):
        PROJECTOR.project(session_id="parent-1", events=[created, tampered])


def test_conflicting_attempt_identity_for_same_request_fails_closed() -> None:
    created = _lifecycle(CustomType.SUBAGENT_CREATED, status="created", offset=0, event_id="created")
    conflict = created.model_copy(
        update={
            "id": "conflict",
            "payload": created.payload.model_copy(update={"lineage_id": "lineage-2"}),
        }
    )
    with pytest.raises(SseProjectionError, match="冲突 attempt 身份"):
        PROJECTOR.project(session_id="parent-1", events=[created, conflict])


def test_lifecycle_status_must_match_custom_type() -> None:
    invalid = _lifecycle(CustomType.SUBAGENT_COMPLETED, status="running", offset=1, event_id="invalid")
    with pytest.raises(SseProjectionError, match="status.*冲突"):
        PROJECTOR.project(session_id="parent-1", events=[invalid])


def test_terminal_must_be_last_fact_for_attempt() -> None:
    completed = _lifecycle(CustomType.SUBAGENT_COMPLETED, status="completed", offset=1, event_id="completed")
    late_artifact = _artifact(offset=2)
    created = _lifecycle(CustomType.SUBAGENT_CREATED, status="created", offset=0, event_id="created")
    with pytest.raises(SseProjectionError, match="终态后"):
        PROJECTOR.project(session_id="parent-1", events=[created, completed, late_artifact])


def test_replay_without_cursor_and_limit_is_deterministic() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    assert PROJECTOR.replay(session_id="parent-1", events=events, limit=2) == events[:2]
    with pytest.raises(SseProjectionError, match="1..1000") as exc_info:
        PROJECTOR.replay(session_id="parent-1", events=events, limit=0)
    assert exc_info.value.code is M2ErrorCode.INVALID_REQUEST


def test_cursor_replays_strictly_after_last_acknowledged_event() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    cursor = PROJECTOR.cursor_for(events[2])
    assert PROJECTOR.replay(session_id="parent-1", events=events, cursor=cursor) == events[3:]
    assert PROJECTOR.replay(
        session_id="parent-1",
        events=events,
        cursor=PROJECTOR.cursor_for(events[-1]),
    ) == ()


def test_wrong_session_stale_and_mismatched_cursor_fail_closed() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    cursors = [
        M2SseCursor(session_id="other", last_event_id=events[0].event_id, last_sequence=1),
        M2SseCursor(session_id="parent-1", last_event_id="evt-" + "0" * 64, last_sequence=1),
        M2SseCursor(session_id="parent-1", last_event_id=events[0].event_id, last_sequence=2),
    ]
    for cursor in cursors:
        with pytest.raises(SseProjectionError) as exc_info:
            PROJECTOR.replay(session_id="parent-1", events=events, cursor=cursor)
        assert exc_info.value.code is M2ErrorCode.REPLAY_CURSOR_EXPIRED


def test_corrupt_stream_sequence_session_event_id_and_data_fail_closed() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    corruptions = [
        (events[0].model_copy(update={"sequence": 2}), *events[1:]),
        (events[0].model_copy(update={"session_id": "other"}), *events[1:]),
        (events[0], events[1].model_copy(update={"event_id": events[0].event_id}), *events[2:]),
        (
            events[0].model_copy(update={"data": {**events[0].data, "role": "tampered"}}),
            *events[1:],
        ),
    ]
    for stream in corruptions:
        with pytest.raises(SseProjectionError) as exc_info:
            PROJECTOR.replay(session_id="parent-1", events=stream)
        assert exc_info.value.code is M2ErrorCode.CONFLICT


def test_replay_rejects_reordered_or_post_terminal_facts() -> None:
    events = PROJECTOR.project(session_id="parent-1", events=_facts())
    reordered = (
        events[0],
        events[2].model_copy(update={"sequence": 2}),
        events[1].model_copy(update={"sequence": 3}),
        *events[3:],
    )
    with pytest.raises(SseProjectionError, match="事件顺序"):
        PROJECTOR.replay(session_id="parent-1", events=reordered)

    terminal = events[-1].model_copy(update={"sequence": 5})
    late_artifact = events[4].model_copy(
        update={
            "sequence": 6,
            "occurred_at": terminal.occurred_at + timedelta(seconds=1),
        }
    )
    post_terminal = (*events[:4], terminal, late_artifact)
    with pytest.raises(SseProjectionError, match="终态后"):
        PROJECTOR.replay(session_id="parent-1", events=post_terminal)


def test_projection_error_maps_to_safe_api_error() -> None:
    error = SseProjectionError(M2ErrorCode.REPLAY_CURSOR_EXPIRED, "SSE cursor 已过期")
    api_error = error.to_api_error("api-1")
    assert api_error.code is M2ErrorCode.REPLAY_CURSOR_EXPIRED
    assert api_error.request_id == "api-1"
    assert api_error.retryable is False
    assert api_error.details == {}


def test_real_runtime_parent_child_facts_project_to_one_stable_stream(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\n"
        "name: worker\n"
        "tools: []\n"
        "permissions: {}\n"
        "context_mode: isolated\n"
        "max_turns: 2\n"
        "artifact_contract: summary-v1\n"
        "nesting: false\n"
        "---\nworker",
        encoding="utf-8",
    )
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        child_runner=lambda *_: ChildRunOutput(summary="completed safely"),
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="private task",
    )
    try:
        result = runtime.run(request, parent_session=parent)
        assert result.status is SubagentStatus.COMPLETED
        child_events = SessionStore(Path(result.child_session_path)).load()
        projected = PROJECTOR.project(
            session_id=parent.session_id,
            events=[*parent.load(), *child_events],
        )
    finally:
        runtime.shutdown()
    assert [event.sequence for event in projected] == list(range(1, len(projected) + 1))
    assert sum(event.event_type is M2SseEventType.TERMINAL for event in projected) == 1
    assert projected[-1].event_type is M2SseEventType.TERMINAL
    assert any(event.event_type is M2SseEventType.ARTIFACT for event in projected)
    serialized = str([event.model_dump(mode="json") for event in projected])
    assert "private task" not in serialized
    assert "child_session_path" not in serialized


def test_projector_has_no_path_or_runtime_constructor_and_does_not_touch_files(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    projector = M2SseProjector()
    projector.project(session_id="parent-1", events=[])
    after = tuple(tmp_path.rglob("*"))
    assert before == after == ()
    assert not hasattr(projector, "start")
    assert not hasattr(projector, "listen")
    assert not hasattr(projector, "runtime")
