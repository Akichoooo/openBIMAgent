"""Subagent Runtime P1b 可变状态存储测试。"""

from datetime import datetime, timezone

import pytest

from openbimagent.orchestrator.contracts import (
    ExecutionMode,
    SubagentError,
    SubagentHandle,
    SubagentRequest,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.state import RuntimeStateCorruptionError, RuntimeStateStore


def _request() -> SubagentRequest:
    return SubagentRequest.create(
        parent_session_id="parent",
        role="worker",
        task="x",
        execution_mode=ExecutionMode.BACKGROUND,
    )


def _handle(request: SubagentRequest, status: SubagentStatus) -> SubagentHandle:
    return SubagentHandle(
        request_id=request.request_id,
        agent_id="agent",
        parent_session_id="parent",
        child_session_id="child",
        child_session_path="C:/tmp/child.jsonl",
        status=status,
        lineage_id=request.lineage_id,
        attempt_number=request.attempt_number,
        resumed_from_request_id=request.resumed_from_request_id,
    )


def test_state_store_atomic_roundtrip_and_no_secret_fields(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path / "runtime")
    request = _request()
    handle = _handle(request, SubagentStatus.QUEUED)
    record = store.write(
        request=request,
        handle=handle,
        status=SubagentStatus.QUEUED,
        phase="prepared",
    )
    loaded = store.load(request.request_id)
    assert loaded == record
    text = store.path_for(request.request_id).read_text(encoding="utf-8")
    assert "api_key" not in text.lower()
    assert "authorization" not in text.lower()
    assert list(store.root.glob("*.tmp")) == []


def test_state_store_corruption_fails_closed_with_path(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path / "runtime")
    broken = store.root / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeStateCorruptionError, match="broken.json"):
        store.load_all()


def test_state_store_terminal_result_roundtrip(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path / "runtime")
    request = _request()
    handle = _handle(request, SubagentStatus.FAILED)
    now = datetime.now(timezone.utc)
    error = SubagentError(code="RuntimeRestarted", message="restart", retryable=True)
    result = SubagentResultEnvelope(
        request_id=request.request_id,
        agent_id=handle.agent_id,
        parent_session_id=handle.parent_session_id,
        child_session_id=handle.child_session_id,
        child_session_path=handle.child_session_path,
        status=SubagentStatus.FAILED,
        summary="",
        hint="restart",
        manifest_path="C:/tmp/recovery-manifest.json",
        started_at=now,
        ended_at=now,
        error=error,
        receipt_id="receipt",
        lineage_id=request.lineage_id,
        attempt_number=request.attempt_number,
        resumed_from_request_id=request.resumed_from_request_id,
    )
    store.write(
        request=request,
        handle=handle,
        status=SubagentStatus.FAILED,
        phase="terminal",
        result=result,
    )
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].result == result
