"""P1b-B Approval Broker、child ask 转发与 decision receipt 测试。"""

import threading
import time

import pytest

from openbimagent.orchestrator.approval import (
    ApprovalBroker,
    ApprovalBrokerError,
    ApprovalDecision,
)
from openbimagent.schema_gate import gate
from openbimagent.session.schema import CustomType, EventType
from openbimagent.session.store import SessionStore


def _sessions(tmp_path):
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    child = SessionStore.create(tmp_path / "sessions", title="child")
    return parent, child


def _request_in_thread(broker, parent, child, *, args=None, cancel_event=None):
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(
            broker.request(
                request_id="request-1",
                agent_id="agent-1",
                parent_session=parent,
                child_session=child,
                tool_name="write",
                permission_key="write",
                args=args or {"path": "secret.txt", "content": "TOP-SECRET"},
                cancel_event=cancel_event,
            )
        )
    )
    thread.start()
    return thread, result


def _wait_pending(broker):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = broker.pending()
        if pending:
            return pending[0]
        time.sleep(0.01)
    raise AssertionError("approval request 未进入 pending")


def test_broker_forwards_child_ask_to_parent_and_approves(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    broker = ApprovalBroker(default_timeout_s=5)
    thread, result = _request_in_thread(broker, parent, child)
    request = _wait_pending(broker)
    assert request.tool_name == "write"
    assert "TOP-SECRET" not in request.args_summary
    assert "secret.txt" not in request.args_summary
    assert len(request.args_sha256) == 64

    receipt = broker.decide(
        request.approval_id,
        decision=ApprovalDecision.APPROVED,
        decided_by="parent-user",
        reason="reviewed",
    )
    thread.join(timeout=5)
    assert result == [True]
    assert receipt.approved is True
    assert broker.receipt(request.approval_id) == receipt

    for store in (parent, child):
        events = store.load()
        custom = [event for event in events if event.type is EventType.CUSTOM]
        assert [event.payload.customType for event in custom] == [
            CustomType.APPROVAL_REQUESTED,
            CustomType.APPROVAL_DECIDED,
        ]
        assert "TOP-SECRET" not in store.path.read_text(encoding="utf-8")
        for event in custom:
            assert gate.validate_artifact("session_event", event.model_dump(mode="json")) == []


def test_broker_rejects_and_conflicting_redecision_fails(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    broker = ApprovalBroker(default_timeout_s=5)
    thread, result = _request_in_thread(broker, parent, child)
    request = _wait_pending(broker)
    first = broker.decide(
        request.approval_id,
        decision="rejected",
        decided_by="parent-user",
    )
    assert broker.decide(
        request.approval_id,
        decision="rejected",
        decided_by="ignored",
    ) == first
    with pytest.raises(ApprovalBrokerError, match="不能改为 approved"):
        broker.decide(
            request.approval_id,
            decision="approved",
            decided_by="parent-user",
        )
    thread.join(timeout=5)
    assert result == [False]


def test_broker_timeout_and_cancel_fail_closed(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    timeout_broker = ApprovalBroker(default_timeout_s=0.01)
    assert timeout_broker.request(
        request_id="request-timeout",
        agent_id="agent-timeout",
        parent_session=parent,
        child_session=child,
        tool_name="write",
        permission_key="write",
        args={"path": "x"},
    ) is False
    timeout_receipt = next(
        event.payload.model_dump()
        for event in child.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
        and event.payload.model_dump().get("request_id") == "request-timeout"
    )
    assert timeout_receipt["decision"] == "timed_out"

    parent2, child2 = _sessions(tmp_path / "cancel")
    cancel = threading.Event()
    broker = ApprovalBroker(default_timeout_s=5)
    thread, result = _request_in_thread(broker, parent2, child2, cancel_event=cancel)
    request = _wait_pending(broker)
    cancel.set()
    thread.join(timeout=5)
    assert result == [False]
    assert broker.receipt(request.approval_id).decision is ApprovalDecision.CANCELLED


def test_broker_callback_keeps_legacy_bool_contract(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    calls = []
    broker = ApprovalBroker(
        approval_callback=lambda tool, args: (calls.append((tool, args["path"])), True)[1],
        default_timeout_s=5,
    )
    assert broker.request(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
        tool_name="write",
        permission_key="write",
        args={"path": "approved.txt"},
    ) is True
    assert calls == [("write", "approved.txt")]


def test_broker_callback_exception_rejects_without_leaking_message(tmp_path) -> None:
    parent, child = _sessions(tmp_path)

    def broken_callback(*_):
        raise RuntimeError("TOP-SECRET callback detail")

    broker = ApprovalBroker(approval_callback=broken_callback, default_timeout_s=5)
    assert broker.request(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
        tool_name="write",
        permission_key="write",
        args={"content": "TOP-SECRET argument"},
    ) is False
    receipt = next(
        event.payload.model_dump()
        for event in parent.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
    )
    assert receipt["decision"] == "rejected"
    assert receipt["reason"] == "approval callback failed: RuntimeError"
    assert "TOP-SECRET" not in parent.path.read_text(encoding="utf-8")
    assert "TOP-SECRET" not in child.path.read_text(encoding="utf-8")


def test_broker_filters_pending_and_rejects_unknown_approval(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    broker = ApprovalBroker(default_timeout_s=5)
    thread, result = _request_in_thread(broker, parent, child)
    request = _wait_pending(broker)
    assert broker.pending(request_id="request-1") == (request,)
    assert broker.pending(request_id="other-request") == ()
    with pytest.raises(ApprovalBrokerError, match="未知或已关闭"):
        broker.decide(
            "missing-approval",
            decision="approved",
            decided_by="parent-user",
        )
    broker.decide(request.approval_id, decision="rejected", decided_by="parent-user")
    thread.join(timeout=5)
    assert result == [False]


def test_broker_concurrent_same_decision_returns_one_receipt(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    broker = ApprovalBroker(default_timeout_s=5)
    thread, result = _request_in_thread(broker, parent, child)
    request = _wait_pending(broker)
    receipts = []
    errors = []

    def decide() -> None:
        try:
            receipts.append(
                broker.decide(
                    request.approval_id,
                    decision="approved",
                    decided_by="parent-user",
                )
            )
        except Exception as exc:  # pragma: no cover - asserted empty below
            errors.append(exc)

    deciders = [threading.Thread(target=decide) for _ in range(4)]
    for decider in deciders:
        decider.start()
    for decider in deciders:
        decider.join(timeout=5)
    thread.join(timeout=5)
    assert errors == []
    assert len(receipts) == 4
    assert len({receipt.receipt_id for receipt in receipts}) == 1
    assert result == [True]
    for store in (parent, child):
        assert sum(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.APPROVAL_DECIDED
            for event in store.load()
        ) == 1


def test_close_orphaned_is_idempotent(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    approval_id = "approval-orphan"
    payload = {
        "customType": CustomType.APPROVAL_REQUESTED,
        "protocol_version": "1.0",
        "approval_id": approval_id,
        "request_id": "request-1",
        "agent_id": "agent-1",
        "parent_session_id": parent.session_id,
        "child_session_id": child.session_id,
        "tool_name": "write",
        "permission_key": "write",
        "args_summary": '{"path":"str"}',
        "args_sha256": "a" * 64,
        "requested_at": "2026-07-30T00:00:00Z",
    }
    parent.append_new(EventType.CUSTOM, payload)
    child.append_new(EventType.CUSTOM, payload)
    broker = ApprovalBroker()
    first = broker.close_orphaned(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
    )
    second = broker.close_orphaned(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
    )
    assert len(first) == 1 and first[0].decision is ApprovalDecision.RUNTIME_RESTARTED
    assert second == ()
    for store in (parent, child):
        decisions = [
            event
            for event in store.load()
            if event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.APPROVAL_DECIDED
        ]
        assert len(decisions) == 1


def test_close_orphaned_reuses_parent_receipt_and_repairs_child(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    payload = {
        "customType": CustomType.APPROVAL_REQUESTED,
        "protocol_version": "1.0",
        "approval_id": "approval-partial",
        "request_id": "request-1",
        "agent_id": "agent-1",
        "parent_session_id": parent.session_id,
        "child_session_id": child.session_id,
        "tool_name": "write",
        "permission_key": "write",
        "args_summary": '{"path":"str"}',
        "args_sha256": "b" * 64,
        "requested_at": "2026-07-30T00:00:00Z",
    }
    parent.append_new(EventType.CUSTOM, payload)
    child.append_new(EventType.CUSTOM, payload)
    parent_decision = {
        "customType": CustomType.APPROVAL_DECIDED,
        "protocol_version": "1.0",
        "receipt_id": "receipt-parent-won",
        "approval_id": "approval-partial",
        "request_id": "request-1",
        "agent_id": "agent-1",
        "parent_session_id": parent.session_id,
        "child_session_id": child.session_id,
        "tool_name": "write",
        "permission_key": "write",
        "args_sha256": "b" * 64,
        "decision": "approved",
        "decided_by": "parent-user",
        "reason": "approved before crash",
        "decided_at": "2026-07-30T00:00:01Z",
    }
    parent.append_new(EventType.CUSTOM, parent_decision)

    broker = ApprovalBroker()
    repaired = broker.close_orphaned(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
    )
    assert len(repaired) == 1
    assert repaired[0].receipt_id == "receipt-parent-won"
    assert repaired[0].decision is ApprovalDecision.APPROVED
    assert broker.receipt("approval-partial") == repaired[0]
    child_decisions = [
        event.payload.model_dump()
        for event in child.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
    ]
    assert len(child_decisions) == 1
    assert child_decisions[0]["receipt_id"] == "receipt-parent-won"
    assert child_decisions[0]["decision"] == "approved"
    assert broker.close_orphaned(
        request_id="request-1",
        agent_id="agent-1",
        parent_session=parent,
        child_session=child,
    ) == ()


def test_close_orphaned_rejects_conflicting_parent_child_receipts(tmp_path) -> None:
    parent, child = _sessions(tmp_path)
    request_payload = {
        "customType": CustomType.APPROVAL_REQUESTED,
        "protocol_version": "1.0",
        "approval_id": "approval-conflict",
        "request_id": "request-1",
        "agent_id": "agent-1",
        "parent_session_id": parent.session_id,
        "child_session_id": child.session_id,
        "tool_name": "write",
        "permission_key": "write",
        "args_summary": "{}",
        "args_sha256": "c" * 64,
        "requested_at": "2026-07-30T00:00:00Z",
    }
    parent.append_new(EventType.CUSTOM, request_payload)
    child.append_new(EventType.CUSTOM, request_payload)
    for store, decision, receipt_id in (
        (parent, "approved", "parent-receipt"),
        (child, "rejected", "child-receipt"),
    ):
        store.append_new(
            EventType.CUSTOM,
            {
                "customType": CustomType.APPROVAL_DECIDED,
                "protocol_version": "1.0",
                "receipt_id": receipt_id,
                "approval_id": "approval-conflict",
                "request_id": "request-1",
                "agent_id": "agent-1",
                "parent_session_id": parent.session_id,
                "child_session_id": child.session_id,
                "tool_name": "write",
                "permission_key": "write",
                "args_sha256": "c" * 64,
                "decision": decision,
                "decided_by": "test",
                "reason": "",
                "decided_at": "2026-07-30T00:00:01Z",
            },
        )
    with pytest.raises(ApprovalBrokerError, match="decision receipt 冲突"):
        ApprovalBroker().close_orphaned(
            request_id="request-1",
            agent_id="agent-1",
            parent_session=parent,
            child_session=child,
        )
