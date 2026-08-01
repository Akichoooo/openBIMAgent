"""单机 Runtime IPC v1：loopback discovery、认证、写控制和幂等。"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.orchestrator.contracts import ExecutionMode, SubagentRequest
from openbimagent.orchestrator.ipc import IpcError, RuntimeIpcClient
from openbimagent.orchestrator.runtime import ChildRunOutput, LocalSubagentRuntime
from openbimagent.session.store import SessionStore


def _agents(tmp_path: Path) -> Path:
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
        "---\n测试 worker\n",
        encoding="utf-8",
    )
    return agents


def _runtime(tmp_path: Path, runner=None) -> LocalSubagentRuntime:
    return LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents(tmp_path),
        child_runner=runner,
        rehydrate=False,
    )


def _request(runtime: LocalSubagentRuntime, parent: SessionStore) -> SubagentRequest:
    return SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="task",
        execution_mode=ExecutionMode.BACKGROUND,
    )


def test_ipc_ping_and_discovery_are_loopback_only(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        discovery = runtime.start_ipc()
        assert discovery.host == "127.0.0.1"
        assert discovery.port > 0
        assert runtime.state_store.root.joinpath("control-ipc.token").is_file()
        result = RuntimeIpcClient(tmp_path / "sessions").call(
            "ping",
            actor=ActorRef(actor_id="human:test", actor_type=ActorType.HUMAN),
            idempotency_key="ping-1",
        )
        assert result["runtime_instance_id"] == discovery.runtime_instance_id
    finally:
        runtime.shutdown()
    with pytest.raises(IpcError, match="连接失败"):
        RuntimeIpcClient(tmp_path / "sessions", timeout_s=0.2).call(
            "ping",
            actor=ActorRef(actor_id="human:test", actor_type=ActorType.HUMAN),
            idempotency_key="ping-after-stop",
        )


def test_ipc_cancel_requires_active_runtime_and_is_idempotent(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(request, profile, child):
        started.set()
        release.wait(timeout=5)
        return ChildRunOutput(summary="done")

    runtime = _runtime(tmp_path, runner)
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    request = _request(runtime, parent)
    try:
        runtime.start_ipc()
        handle = runtime.submit(request, parent_session=parent)
        assert started.wait(timeout=5)
        client = RuntimeIpcClient(tmp_path / "sessions")
        actor = ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN)
        first = client.call(
            "attempt.cancel",
            actor=actor,
            idempotency_key="cancel-1",
            payload={"request_id": request.request_id},
        )
        second = client.call(
            "attempt.cancel",
            actor=actor,
            idempotency_key="cancel-1",
            payload={"request_id": request.request_id},
        )
        assert first == second
        assert first["cancel_accepted"] is True
        assert first["handle"]["request_id"] == handle.request_id
    finally:
        release.set()
        runtime.shutdown(cancel_pending=True)


def test_ipc_unauthorized_and_idempotency_conflict(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        discovery = runtime.start_ipc()
        request = {
            "protocol_version": "1.0",
            "message_id": "bad-auth",
            "operation": "ping",
            "actor": {"actor_id": "human:test", "actor_type": "human", "display_name": None, "protocol_version": "1.0"},
            "idempotency_key": "same-key",
            "payload": {},
            "bearer_token": "wrong-token-that-is-long-enough-0000000000000000",
        }
        with socket.create_connection((discovery.host, discovery.port), timeout=2) as conn:
            conn.sendall(json.dumps(request).encode() + b"\n")
            response = json.loads(conn.makefile("rb").readline())
        assert response["error_code"] == "Unauthorized"

        client = RuntimeIpcClient(tmp_path / "sessions")
        actor = ActorRef(actor_id="human:test", actor_type=ActorType.HUMAN)
        client.call("ping", actor=actor, idempotency_key="same-key")
        with pytest.raises(IpcError, match="IdempotencyConflict"):
            client.call("approval.decide", actor=actor, idempotency_key="same-key", payload={"approval_id": "a", "approved": True})
    finally:
        runtime.shutdown()


def test_ipc_approval_decision_unblocks_waiter(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    child = SessionStore.create(tmp_path / "sessions", title="child")
    outcome: dict[str, bool] = {}

    def wait_for_approval() -> None:
        outcome["approved"] = runtime.approval_broker.request(
            request_id="request-approval",
            agent_id="agent-approval",
            parent_session=parent,
            child_session=child,
            tool_name="write",
            permission_key="write",
            args={"content": "secret"},
            timeout_s=5,
        )

    waiter = threading.Thread(target=wait_for_approval)
    try:
        runtime.start_ipc()
        waiter.start()
        deadline = time.monotonic() + 5
        while not runtime.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.01)
        approval = runtime.pending_approvals()[0]
        result = RuntimeIpcClient(tmp_path / "sessions").call(
            "approval.decide",
            actor=ActorRef(actor_id="human:approver", actor_type=ActorType.HUMAN),
            idempotency_key="approval-1",
            payload={"approval_id": approval.approval_id, "approved": True, "reason": "reviewed"},
        )
        waiter.join(timeout=5)
        assert outcome == {"approved": True}
        assert result["decision_receipt"]["decision"] == "approved"
        assert result["decision_receipt"]["decided_by"]["actor_id"] == "human:approver"
    finally:
        runtime.shutdown()
        waiter.join(timeout=1)


def test_ipc_steer_routes_to_active_attempt(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(request, profile, child):
        started.set()
        release.wait(timeout=5)
        return ChildRunOutput(summary="done")

    runtime = _runtime(tmp_path, runner)
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    request = _request(runtime, parent)
    try:
        runtime.start_ipc()
        runtime.submit(request, parent_session=parent)
        assert started.wait(timeout=5)
        result = RuntimeIpcClient(tmp_path / "sessions").call(
            "attempt.steer",
            actor=ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN),
            idempotency_key="steer-1",
            payload={"request_id": request.request_id, "instruction": "check current state first"},
        )
        assert result["steer_receipt"]["status"] == "accepted"
        assert result["steer_receipt"]["request_id"] == request.request_id
    finally:
        release.set()
        runtime.shutdown()


def test_ipc_resume_routes_to_runtime(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, lambda request, profile, child: ChildRunOutput(summary="done"))
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    source = _request(runtime, parent)
    try:
        runtime.start_ipc()
        runtime.submit(source, parent_session=parent)
        runtime.join(source.request_id, timeout_s=5)
        result = RuntimeIpcClient(tmp_path / "sessions").call(
            "attempt.resume",
            actor=ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN),
            idempotency_key="resume-1",
            payload={"source_request_id": source.request_id, "instruction": "retry safely"},
        )
        assert result["handle"]["attempt_number"] == 2
        duplicate = RuntimeIpcClient(tmp_path / "sessions").call(
            "attempt.resume",
            actor=ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN),
            idempotency_key="resume-1",
            payload={"source_request_id": source.request_id, "instruction": "retry safely"},
        )
        assert duplicate == result
    finally:
        runtime.shutdown()
