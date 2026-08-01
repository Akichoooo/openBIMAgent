"""P1d 只读 Control Plane 与稳定 actor identity 测试。"""

from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from openbimagent.orchestrator.actor import ActorRef, ActorType, actor_ref
from openbimagent.orchestrator.approval import ApprovalBroker
from openbimagent.orchestrator.control_plane import ControlPlaneError, ReadOnlyControlPlane
from openbimagent.orchestrator.contracts import ExecutionMode, SubagentRequest, SubagentStatus
from openbimagent.orchestrator.runtime import ChildRunOutput, LocalSubagentRuntime
from openbimagent.session.schema import CustomType, EventType
from openbimagent.session.store import SessionStore


def _agents_dir(tmp_path: Path) -> Path:
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
    return agents


def _completed_lineage(tmp_path: Path):
    sessions = tmp_path / "sessions"
    artifacts = tmp_path / "artifacts"
    parent = SessionStore.create(sessions, title="parent")
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda request, *_: ChildRunOutput(summary=f"attempt {request.attempt_number}"),
    )
    first = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="first",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    runtime.submit(first, parent_session=parent)
    runtime.join(first.request_id, timeout_s=5)
    second, receipt = runtime.resume(
        first.request_id,
        instruction="continue safely",
        idempotency_key="test:control-plane:resume",
        requested_by=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN, display_name="JY"),
    )
    runtime.join(second.request_id, timeout_s=5)
    return runtime, parent, first, second, receipt


def test_actor_ref_normalizes_legacy_and_is_stable() -> None:
    ref = actor_ref("parent-agent")
    assert ref.actor_id == "agent:parent-agent"
    assert ref.actor_type is ActorType.AGENT
    legacy = ActorRef.legacy("old parent")
    assert legacy.actor_type is ActorType.LEGACY
    assert legacy.display_name == "old parent"


def test_control_plane_lists_attempts_and_resumes_without_task_or_instruction(tmp_path) -> None:
    runtime, _, first, second, receipt = _completed_lineage(tmp_path)
    plane = ReadOnlyControlPlane(tmp_path / "sessions")
    attempts = plane.get_lineage(first.lineage_id)
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert attempts[1].request_id == second.request_id
    assert attempts[1].status is SubagentStatus.COMPLETED
    assert "task" not in attempts[0].model_dump()
    resumes = plane.list_resumes(lineage_id=first.lineage_id)
    assert len(resumes) == 1
    assert resumes[0].receipt_id == receipt.receipt_id
    assert resumes[0].idempotency_key == "test:control-plane:resume"
    assert "instruction" not in resumes[0].model_dump()
    runtime.shutdown()


def test_control_plane_reads_while_runtime_holds_lease_and_filters(tmp_path) -> None:
    runtime, parent, first, _, _ = _completed_lineage(tmp_path)
    plane = ReadOnlyControlPlane(tmp_path / "sessions")
    assert len(plane.list_attempts(parent_session_id=parent.session_id)) == 2
    assert len(plane.list_attempts(status="completed")) == 2
    assert plane.get_attempt(first.request_id).lineage_id == first.lineage_id
    runtime.shutdown()


def test_control_plane_deduplicates_parent_child_facts_and_fails_on_conflict(tmp_path) -> None:
    runtime, parent, first, second, _ = _completed_lineage(tmp_path)
    plane = ReadOnlyControlPlane(tmp_path / "sessions")
    assert len(plane.list_resumes()) == 1
    child = SessionStore(Path(runtime.status(second.request_id).child_session_path))
    resume_event = next(
        event for event in child.load()
        if event.type is EventType.CUSTOM and event.payload.customType is CustomType.RESUME_REQUESTED
    )
    payload = resume_event.payload.model_dump(mode="json")
    payload["instruction"] = "tampered"
    parent.append_new(EventType.CUSTOM, payload)
    with pytest.raises(ControlPlaneError, match="审计事实.*冲突"):
        plane.list_resumes()
    runtime.shutdown()


def test_control_plane_projects_approval_and_steer_audit_facts(tmp_path) -> None:
    runtime, parent, first, _, _ = _completed_lineage(tmp_path)
    first_run = runtime._background[first.request_id]
    broker = ApprovalBroker(default_timeout_s=5)
    decision: list[bool] = []
    approval_thread = threading.Thread(
        target=lambda: decision.append(
            broker.request(
                request_id=first.request_id,
                agent_id=first_run.handle.agent_id,
                parent_session=parent,
                child_session=first_run.child_session,
                tool_name="write",
                permission_key="write",
                args={"path": "private.txt", "content": "TOP-SECRET"},
            )
        )
    )
    approval_thread.start()
    deadline = time.monotonic() + 5
    while not broker.pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    approval = broker.pending()[0]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        parent_has_request = any(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.APPROVAL_REQUESTED
            and event.payload.model_dump().get("approval_id") == approval.approval_id
            for event in parent.load()
        )
        child_has_request = any(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.APPROVAL_REQUESTED
            and event.payload.model_dump().get("approval_id") == approval.approval_id
            for event in first_run.child_session.load()
        )
        if parent_has_request and child_has_request:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("approval request 未完成父子 Session 对账")

    plane = ReadOnlyControlPlane(tmp_path / "sessions")
    pending = plane.list_approvals(request_id=first.request_id, pending_only=True)
    assert len(pending) == 1
    assert pending[0].approval_id == approval.approval_id
    assert pending[0].pending is True
    assert "TOP-SECRET" not in pending[0].args_summary
    assert "private.txt" not in pending[0].args_summary

    receipt = broker.decide(
        approval.approval_id,
        decision="approved",
        decided_by=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN, display_name="JY"),
    )
    approval_thread.join(timeout=5)
    settled = plane.list_approvals(request_id=first.request_id)
    assert decision == [True]
    assert settled[0].pending is False
    assert settled[0].receipt_id == receipt.receipt_id
    assert settled[0].decided_by.actor_id == "human:jy"

    directive = runtime.steer_queue.accept
    from openbimagent.orchestrator.control import SteerDirective, SteerStatus

    steer = SteerDirective.create(
        request_id=first.request_id,
        agent_id=first_run.handle.agent_id,
        child_session_id=first_run.handle.child_session_id,
        lineage_id=first.lineage_id,
        attempt_number=1,
        instruction="private steer instruction",
        requested_by=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN),
    )
    directive(
        steer,
        parent_session=parent,
        child_session=first_run.child_session,
    )
    runtime.steer_queue.settle(
        steer,
        status=SteerStatus.APPLIED,
        parent_session=parent,
        child_session=first_run.child_session,
        reason="applied in audit fixture",
    )
    steers = plane.list_steers(request_id=first.request_id)
    assert len(steers) == 1
    assert steers[0].statuses == (SteerStatus.ACCEPTED, SteerStatus.APPLIED)
    assert steers[0].latest_status is SteerStatus.APPLIED
    assert "instruction" not in steers[0].model_dump()
    runtime.shutdown()


def test_control_plane_unknown_identity_and_corrupt_state_fail_closed(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    plane = ReadOnlyControlPlane(sessions)
    with pytest.raises(ControlPlaneError, match="未知 request_id"):
        plane.get_attempt("missing")
    runtime_root = sessions / "_runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "broken.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(Exception, match="状态文件损坏"):
        plane.list_attempts()
