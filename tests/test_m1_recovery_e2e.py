"""M1 G5 cross-component recovery and side-effect safety acceptance tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openbimagent.assembly.target_executor import make_vectorworks_batch_executor
from openbimagent.assembly.vectorworks_plan import (
    FakeVectorworksExecutor,
    ReceiptStatus,
    VectorworksBuilder,
)
from openbimagent.deliver.manifest import commit_delivery_manifest
from openbimagent.orchestrator.approval import ApprovalBroker
from openbimagent.orchestrator.artifacts import ArtifactStore, ImmutableArtifactError
from openbimagent.orchestrator.contracts import (
    ExecutionMode,
    SubagentRequest,
    SubagentStatus,
)
from openbimagent.orchestrator.dispatch import Verdict
from openbimagent.orchestrator.runtime import (
    ChildRunOutput,
    LocalSubagentRuntime,
    SubagentRuntimeError,
)
from openbimagent.session.store import SessionStore
from test_compiled_utility_ir import solved_payload


def _agents_dir(root: Path) -> Path:
    agents = root / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\n"
        "name: worker\n"
        "model: test-model\n"
        "tools: [read]\n"
        "permissions: { read: allow }\n"
        "context_mode: isolated\n"
        "max_turns: 4\n"
        "artifact_contract: summary-v1\n"
        "nesting: false\n"
        "---\n"
        "你是 G5 恢复测试 worker。\n",
        encoding="utf-8",
    )
    return agents


def _delivery_artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": "vectorworks-execution-receipt",
        "media_type": "application/json",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dependencies": [],
        "status": "completed",
    }


def test_rejected_and_timed_out_approval_never_call_typed_host(tmp_path) -> None:
    plan_builder = VectorworksBuilder()
    rejected_host = FakeVectorworksExecutor()
    rejected = make_vectorworks_batch_executor(
        ir=solved_payload(),
        batch_names=["全部资产"],
        work_dir=tmp_path / "rejected",
        client=rejected_host,
        builder_fn=plan_builder,
        approval_fn=lambda *_: False,
    )("全部资产", None)
    assert rejected.verdict is Verdict.ESCALATE
    assert rejected_host.execute_calls == 0
    assert rejected_host.apply_calls == 0

    sessions = tmp_path / "approval-sessions"
    parent = SessionStore.create(sessions, title="parent")
    child = SessionStore.create(sessions, title="child")
    broker = ApprovalBroker(default_timeout_s=0)
    timed_out_host = FakeVectorworksExecutor()

    def approval_timeout(tool_name: str, args: dict[str, object]) -> bool:
        return broker.request(
            request_id="attempt-timeout",
            agent_id="agent-timeout",
            parent_session=parent,
            child_session=child,
            tool_name=tool_name,
            permission_key="execute_vectorworks_plan",
            args=args,
        )

    timed_out = make_vectorworks_batch_executor(
        ir=solved_payload(),
        batch_names=["全部资产"],
        work_dir=tmp_path / "timed-out",
        client=timed_out_host,
        builder_fn=plan_builder,
        approval_fn=approval_timeout,
    )("全部资产", None)
    assert timed_out.verdict is Verdict.ESCALATE
    assert timed_out_host.execute_calls == 0
    assert timed_out_host.apply_calls == 0
    decision = next(
        event.payload.model_dump(mode="json")
        for event in parent.load()
        if event.payload.model_dump(mode="json").get("decision") == "timed_out"
    )
    assert decision["decided_by"]["actor_id"] == "service:approval-broker"


def test_partial_host_restart_resume_and_delivery_recovery_are_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    sessions = tmp_path / "sessions"
    artifacts = tmp_path / "runtime-artifacts"
    workdir = tmp_path / "work"
    workdir.mkdir()
    agents = _agents_dir(tmp_path)
    parent = SessionStore.create(sessions, title="parent")
    plan = VectorworksBuilder().build(solved_payload())
    host_state = workdir / "fake-vectorworks-host.json"

    first_host = FakeVectorworksExecutor(
        fail_after_operations=4,
        state_path=host_state,
    )
    partial = first_host.execute_plan(plan)
    assert partial.status is ReceiptStatus.PARTIAL
    applied_before_restart = json.loads(host_state.read_text(encoding="utf-8"))["applied"][plan.idempotency_key]
    assert len(applied_before_restart) == 4

    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        rehydrate=False,
    )
    first_request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="execute typed Vectorworks plan",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    first_run = seed._prepare(first_request, parent, status=SubagentStatus.RUNNING)
    seed._background[first_request.request_id] = first_run
    seed._persist(first_run, phase="running")
    checkpoint = seed.checkpoint_artifact(
        first_request.request_id,
        source=host_state,
        idempotency_key=plan.idempotency_key,
    )
    repeated_checkpoint = seed.checkpoint_artifact(
        first_request.request_id,
        source=host_state,
        idempotency_key=plan.idempotency_key,
    )
    assert repeated_checkpoint == checkpoint
    conflicting_checkpoint = workdir / "conflicting-host-fact.json"
    conflicting_checkpoint.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SubagentRuntimeError, match="不同副作用事实"):
        seed.checkpoint_artifact(
            first_request.request_id,
            source=conflicting_checkpoint,
            idempotency_key=plan.idempotency_key,
        )
    seed.shutdown()

    completed_receipt_path = workdir / "vectorworks-completed-receipt.json"
    resumed_host_apply_calls: list[int] = []
    observed_resume_tasks: list[str] = []

    def resumed_runner(request, *_):
        observed_resume_tasks.append(request.task)
        recovered_host = FakeVectorworksExecutor(state_path=host_state)
        completed = recovered_host.execute_plan(plan)
        assert completed.status is ReceiptStatus.COMPLETED
        resumed_host_apply_calls.append(recovered_host.apply_calls)
        completed_receipt_path.write_text(
            json.dumps(completed.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return ChildRunOutput(
            summary="typed host recovered without replay",
            artifact_paths=(completed_receipt_path,),
        )

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=resumed_runner,
    )
    failed = restored.join(first_request.request_id)
    assert failed.status is SubagentStatus.FAILED
    assert failed.error is not None and failed.error.code == "RuntimeRestarted"
    recovered_checkpoint = next(
        record for record in failed.artifacts if record.kind == "side-effect-checkpoint"
    )
    assert recovered_checkpoint.path == checkpoint.path
    assert recovered_checkpoint.sha256 == checkpoint.sha256

    resumed_handle, resume_receipt = restored.resume(
        first_request.request_id,
        instruction="对账 checkpoint 和当前宿主，只执行未确认 operation",
        idempotency_key="g5:resume:vectorworks:case-001",
        requested_by="human:jy",
    )
    resumed = restored.join(resumed_handle.request_id, timeout_s=5)
    assert resumed.status is SubagentStatus.COMPLETED
    assert resumed.lineage_id == first_request.lineage_id
    assert resumed.attempt_number == 2
    assert resumed.resumed_from_request_id == first_request.request_id
    assert resume_receipt.new_request_id == resumed.request_id
    resumed_state = restored.state_store.load(resumed.request_id)
    assert resumed_state.resume_request is not None
    assert resumed_state.resume_request.requested_by.actor_id == "human:jy"
    assert resumed_state.resume_request.idempotency_key == "g5:resume:vectorworks:case-001"
    assert checkpoint.path in observed_resume_tasks[0]
    assert resumed_host_apply_calls == [len(plan.operations) - len(applied_before_restart)]

    replay_handle, replay_receipt = restored.resume(
        first_request.request_id,
        instruction="对账 checkpoint 和当前宿主，只执行未确认 operation",
        idempotency_key="g5:resume:vectorworks:case-001",
        requested_by="human:jy",
    )
    assert replay_handle.request_id == resumed_handle.request_id
    assert replay_receipt == resume_receipt
    assert resumed_host_apply_calls == [len(plan.operations) - len(applied_before_restart)]
    restored.shutdown()

    delivery_args = {
        "workdir": workdir,
        "artifacts": [_delivery_artifact(workdir, completed_receipt_path)],
        "idempotency_key": "g5:delivery:case-001",
        "domain_gate_status": "PASS",
        "request_id": resumed.request_id,
        "source_attempt_id": resumed.request_id,
        "lineage_id": resumed.lineage_id,
        "attempt_number": resumed.attempt_number,
        "resumed_from_request_id": resumed.resumed_from_request_id,
    }
    original_write_manifest = ArtifactStore.write_manifest
    manifest_calls = 0

    def crash_before_manifest(self, **kwargs):
        nonlocal manifest_calls
        manifest_calls += 1
        if manifest_calls == 1:
            raise RuntimeError("injected delivery crash")
        return original_write_manifest(self, **kwargs)

    monkeypatch.setattr(ArtifactStore, "write_manifest", crash_before_manifest)
    with pytest.raises(RuntimeError, match="injected delivery crash"):
        commit_delivery_manifest(**delivery_args)
    recovered_delivery = commit_delivery_manifest(**delivery_args)
    replayed_delivery = commit_delivery_manifest(**delivery_args)
    assert recovered_delivery.reused is False
    assert replayed_delivery.reused is True
    assert replayed_delivery.manifest == recovered_delivery.manifest
    assert recovered_delivery.manifest.lineage_id == resumed.lineage_id
    assert recovered_delivery.manifest.attempt_number == 2
    assert recovered_delivery.manifest.resumed_from_request_id == first_request.request_id
    assert recovered_delivery.manifest.records[0].source_attempt_id == resumed.request_id

    completed_receipt_path.write_text("{\"different\": true}\n", encoding="utf-8")
    with pytest.raises(ImmutableArtifactError, match="不同工件语义"):
        commit_delivery_manifest(
            **{
                **delivery_args,
                "artifacts": [_delivery_artifact(workdir, completed_receipt_path)],
            }
        )
    historical = commit_delivery_manifest(
        **{
            **delivery_args,
            "artifacts": [_delivery_artifact(workdir, completed_receipt_path)],
            "idempotency_key": "g5:delivery:case-001:new-history",
        }
    )
    assert historical.manifest_path != recovered_delivery.manifest_path
    assert recovered_delivery.manifest_path.is_file()
    assert historical.manifest_path.is_file()
