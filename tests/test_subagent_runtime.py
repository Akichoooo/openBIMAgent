"""LocalSubagentRuntime：child Session、artifact、lifecycle 与失败关闭测试。"""

import json
import multiprocessing
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openbimagent.orchestrator.contracts import (
    ContextMode,
    ExecutionMode,
    SubagentRequest,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.control import SteerDirective, SteerStatus
from openbimagent.orchestrator.dispatch import SubagentResult
from openbimagent.orchestrator.runtime import (
    AGENTS_DIR,
    ChildRunOutput,
    LocalSubagentRuntime,
    SubagentRuntimeError,
    load_agent_profile,
)
from openbimagent.orchestrator.state import RuntimeLeaseError
from openbimagent.schema_gate import gate
from openbimagent.session.schema import CustomType, EventType
from openbimagent.session.store import INDEX_FILENAME, SessionStore


def _hold_runtime_lease_in_process(args: tuple[str, str, str, object, object]) -> None:
    sessions_raw, artifacts_raw, agents_raw, ready, release = args
    runtime = LocalSubagentRuntime(
        sessions_dir=Path(sessions_raw),
        artifacts_dir=Path(artifacts_raw),
        agents_dir=Path(agents_raw),
        rehydrate=False,
    )
    ready.set()
    assert release.wait(timeout=10)
    runtime.shutdown()


def _agents_dir(tmp_path: Path, *, context_mode: str = "isolated") -> Path:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\n"
        "name: worker\n"
        "model: test-model\n"
        "tools: [read, write]\n"
        "permissions: { read: allow, write: deny }\n"
        f"context_mode: {context_mode}\n"
        "max_turns: 7\n"
        "artifact_contract: summary-v1\n"
        "nesting: false\n"
        "---\n"
        "你是测试 worker。\n",
        encoding="utf-8",
    )
    return agents


def test_all_project_agent_profiles_load_for_runtime_v1() -> None:
    roles = {path.stem for path in AGENTS_DIR.glob("*.md")}
    assert roles == {
        "clarify",
        "critic_render",
        "critic_scad",
        "deliver",
        "lighter",
        "materialist",
        "modeler",
        "orchestrator",
        "planner",
        "researcher",
    }
    for role in roles:
        profile = load_agent_profile(role)
        assert profile.name == role
        assert profile.system_prompt
        assert profile.max_turns >= 1
        assert profile.artifact_contract == "summary-v1"
        assert profile.nesting is False


def test_runtime_creates_child_session_artifacts_and_receipt(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    parent.append_new(EventType.MESSAGE, {"role": "user", "content": "父任务"})
    produced = tmp_path / "result.json"
    produced.write_text('{"ok": true}', encoding="utf-8")

    def runner(request, profile, child):
        assert profile.tools == ("read", "write")
        child.append_new(EventType.MESSAGE, {"role": "assistant", "content": "child process trace"})
        return ChildRunOutput(
            summary="完成建模检查",
            hint="完成",
            artifact_paths=(produced,),
            usage={"turns": 2, "tokens": 123},
        )

    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=runner,
    )
    request = SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="执行检查")
    result = runtime.run(request, parent_session=parent)

    assert result.status is SubagentStatus.COMPLETED
    assert Path(result.child_session_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert {record.kind for record in result.artifacts} == {"summary", "output"}
    assert all(Path(record.path).is_file() for record in result.artifacts)
    assert result.receipt_id

    parent_events = parent.load()
    for event in parent_events:
        assert gate.validate_artifact("session_event", event.model_dump(mode="json")) == []
    child_events = SessionStore(Path(result.child_session_path)).load()
    for event in child_events:
        assert gate.validate_artifact("session_event", event.model_dump(mode="json")) == []
    custom_types = [event.payload.customType for event in parent_events if event.type is EventType.CUSTOM]
    assert CustomType.SUBAGENT_CREATED in custom_types
    assert CustomType.SUBAGENT_COMPLETED in custom_types
    assert CustomType.DELIVERY_RECEIPT in custom_types
    assert "child process trace" not in "\n".join(
        str(event.payload.model_dump()) for event in parent_events
    )

    index = json.loads((sessions / INDEX_FILENAME).read_text(encoding="utf-8"))
    child_entry = next(entry for entry in index["sessions"] if entry["id"] == result.child_session_id)
    assert child_entry["child_of"]["parent_session_id"] == parent.session_id
    assert child_entry["child_of"]["request_id"] == request.request_id

    compact = SubagentResult.from_envelope(result)
    assert compact.child_session == Path(result.child_session_path)
    assert compact.request_id == request.request_id
    assert compact.artifact_paths


def test_runtime_failure_is_structured_and_receipted(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")

    def broken(request, profile, child):
        raise RuntimeError("boom")

    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=broken,
    )
    result = runtime.run(
        SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="失败任务"),
        parent_session=parent,
    )
    assert result.status is SubagentStatus.FAILED
    assert result.error is not None and result.error.code == "RuntimeError"
    assert any(record.kind == "error" for record in result.artifacts)
    assert any(
        event.type is EventType.CUSTOM and event.payload.customType is CustomType.DELIVERY_RECEIPT
        for event in parent.load()
    )


def test_runtime_missing_output_artifact_becomes_failed_receipt(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    missing = tmp_path / "missing.json"
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda *_: ChildRunOutput(summary="claimed", artifact_paths=(missing,)),
    )
    result = runtime.run(
        SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="x"),
        parent_session=parent,
    )
    assert result.status is SubagentStatus.FAILED
    assert result.error is not None and result.error.code == "FileNotFoundError"
    assert any(Path(record.path).name == "artifact-error.txt" for record in result.artifacts)
    assert result.receipt_id


def test_runtime_background_submit_status_and_join(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    release = threading.Event()

    def runner(*_):
        assert release.wait(timeout=5)
        return ChildRunOutput(summary="background ok")

    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=runner,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    handle = runtime.submit(request, parent_session=parent)
    assert handle.status is SubagentStatus.QUEUED
    assert runtime.status(request.request_id).status in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}
    release.set()
    result = runtime.join(request.request_id, timeout_s=5)
    assert result.status is SubagentStatus.COMPLETED
    assert result.summary == "background ok"
    assert runtime.status(request.request_id).status is SubagentStatus.COMPLETED
    runtime.shutdown()


def test_runtime_background_cancel_is_terminal_and_receipted(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    started = threading.Event()
    release = threading.Event()

    def runner(*_):
        started.set()
        assert release.wait(timeout=5)
        return ChildRunOutput(summary="should be discarded")

    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=runner,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    assert started.wait(timeout=5)
    assert runtime.cancel(request.request_id) is True
    release.set()
    result = runtime.join(request.request_id, timeout_s=5)
    assert result.status is SubagentStatus.CANCELLED
    assert result.error is not None and result.error.code == "Cancelled"
    assert result.receipt_id
    assert any(
        event.type is EventType.CUSTOM and event.payload.customType is CustomType.SUBAGENT_CANCELLED
        for event in parent.load()
    )
    runtime.shutdown()


def test_runtime_rejects_duplicate_background_before_child_creation(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    release = threading.Event()
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda *_: (release.wait(timeout=5), ChildRunOutput(summary="ok"))[1],
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    session_count = len(SessionStore.list_sessions(sessions))
    with pytest.raises(SubagentRuntimeError, match="request_id 已存在"):
        runtime.submit(request, parent_session=parent)
    assert len(SessionStore.list_sessions(sessions)) == session_count
    release.set()
    runtime.join(request.request_id, timeout_s=5)
    runtime.shutdown()


def test_runtime_rejects_wrong_entrypoint_and_context_escalation(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda *_: ChildRunOutput(summary="ok"),
    )
    background = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    with pytest.raises(SubagentRuntimeError, match="必须使用 submit"):
        runtime.run(background, parent_session=parent)

    fork = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        context_mode=ContextMode.FORK,
    )
    with pytest.raises(SubagentRuntimeError, match="不允许 fork"):
        runtime.run(fork, parent_session=parent)


def test_runtime_lease_rejects_second_live_runtime_and_releases_on_shutdown(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    artifacts = tmp_path / "artifacts"
    agents = _agents_dir(tmp_path)
    first = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    with pytest.raises(RuntimeLeaseError, match="已有活跃 Runtime"):
        LocalSubagentRuntime(
            sessions_dir=sessions,
            artifacts_dir=artifacts,
            agents_dir=agents,
        )
    first.shutdown()
    first.shutdown()
    parent = SessionStore.create(tmp_path / "other-sessions", title="parent")
    request = SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="x")
    with pytest.raises(SubagentRuntimeError, match="Runtime 已关闭"):
        first.run(request, parent_session=parent)

    second = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    second.shutdown()


def test_runtime_lease_is_exclusive_across_spawn_processes(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    artifacts = tmp_path / "artifacts"
    agents = _agents_dir(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_runtime_lease_in_process,
        args=((str(sessions), str(artifacts), str(agents), ready, release),),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(RuntimeLeaseError, match="已有活跃 Runtime"):
            LocalSubagentRuntime(
                sessions_dir=sessions,
                artifacts_dir=artifacts,
                agents_dir=agents,
            )
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    restored.shutdown()


def test_runtime_initialization_failure_releases_lease(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    artifacts = tmp_path / "artifacts"
    agents = _agents_dir(tmp_path)
    runtime_root = sessions / "_runtime"
    runtime_root.mkdir(parents=True)
    (runtime_root / "broken.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(Exception, match="broken.json"):
        LocalSubagentRuntime(
            sessions_dir=sessions,
            artifacts_dir=artifacts,
            agents_dir=agents,
        )
    with pytest.raises(Exception, match="broken.json") as second_error:
        LocalSubagentRuntime(
            sessions_dir=sessions,
            artifacts_dir=artifacts,
            agents_dir=agents,
        )
    assert not isinstance(second_error.value, RuntimeLeaseError)


def test_runtime_rejects_nonwaiting_shutdown_with_active_background(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    agents = _agents_dir(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def runner(*_):
        started.set()
        assert release.wait(timeout=5)
        return ChildRunOutput(summary="done")

    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        child_runner=runner,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    assert started.wait(timeout=5)
    with pytest.raises(SubagentRuntimeError, match=r"shutdown\(wait=False\)"):
        runtime.shutdown(wait=False)
    with pytest.raises(RuntimeLeaseError):
        LocalSubagentRuntime(
            sessions_dir=tmp_path / "sessions",
            artifacts_dir=tmp_path / "other-artifacts",
            agents_dir=agents,
        )
    release.set()
    assert runtime.join(request.request_id, timeout_s=5).status is SubagentStatus.COMPLETED
    runtime.shutdown()


def test_runtime_default_child_ask_waits_for_parent_decision(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: fake\ntools: [write]\npermissions: { write: ask }\n"
        "context_mode: isolated\nmax_turns: 3\nartifact_contract: summary-v1\nnesting: false\n---\nworker",
        encoding="utf-8",
    )
    calls = 0

    def chat_fn(*, role, messages, tools, cancel_event=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "need write",
                            "tool_calls": [
                                {
                                    "id": "write-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": json.dumps({"path": "approved.txt", "content": "approved"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}

    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        chat_fn=chat_fn,
        workdir=tmp_path,
        approval_timeout_s=5,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="write",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    deadline = time.monotonic() + 5
    pending = ()
    while time.monotonic() < deadline and not pending:
        pending = runtime.pending_approvals(request.request_id)
        time.sleep(0.01)
    assert len(pending) == 1
    receipt = runtime.decide_approval(
        pending[0].approval_id,
        approved=True,
        decided_by="test-parent",
    )
    result = runtime.join(request.request_id, timeout_s=5)
    assert receipt.approved is True
    assert result.status is SubagentStatus.COMPLETED
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "approved"
    child_text = Path(result.child_session_path).read_text(encoding="utf-8")
    assert '"content":"approved"' not in child_text
    assert '"path":"approved.txt"' not in child_text
    assert "args_sha256" in child_text
    parent_types = [
        event.payload.customType
        for event in parent.load()
        if event.type is EventType.CUSTOM
    ]
    assert CustomType.APPROVAL_REQUESTED in parent_types
    assert CustomType.APPROVAL_DECIDED in parent_types
    assert CustomType.DELIVERY_RECEIPT in parent_types
    runtime.shutdown()


def test_runtime_default_child_ask_rejection_prevents_tool(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: fake\ntools: [write]\npermissions: { write: ask }\n"
        "context_mode: isolated\nmax_turns: 3\nartifact_contract: summary-v1\nnesting: false\n---\nworker",
        encoding="utf-8",
    )
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "need write",
                            "tool_calls": [
                                {
                                    "id": "write-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": json.dumps({"path": "denied.txt", "content": "no"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {"choices": [{"message": {"role": "assistant", "content": "rejected"}, "finish_reason": "stop"}]},
        ]
    )
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        chat_fn=lambda **_: next(responses),
        workdir=tmp_path,
        approval_callback=lambda tool, args: False,
    )
    result = runtime.run(
        SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="write"),
        parent_session=parent,
    )
    assert result.status is SubagentStatus.COMPLETED
    assert not (tmp_path / "denied.txt").exists()
    decision = next(
        event.payload.model_dump()
        for event in parent.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
    )
    assert decision["decision"] == "rejected"
    runtime.shutdown()


def test_runtime_cancel_while_child_waits_for_approval(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: fake\ntools: [write]\npermissions: { write: ask }\n"
        "context_mode: isolated\nmax_turns: 3\nartifact_contract: summary-v1\nnesting: false\n---\nworker",
        encoding="utf-8",
    )
    calls = 0

    def chat_fn(**_):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("取消后不得再次调用模型")
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "need write",
                        "tool_calls": [
                            {
                                "id": "write-cancel",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": json.dumps(
                                        {"path": "cancelled.txt", "content": "must-not-write"}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        chat_fn=chat_fn,
        workdir=tmp_path,
        approval_timeout_s=5,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="write",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    deadline = time.monotonic() + 5
    pending = ()
    while time.monotonic() < deadline and not pending:
        pending = runtime.pending_approvals(request.request_id)
        time.sleep(0.01)
    assert len(pending) == 1
    assert runtime.cancel(request.request_id) is True
    result = runtime.join(request.request_id, timeout_s=5)
    assert result.status is SubagentStatus.CANCELLED
    assert result.error is not None and result.error.code == "Cancelled"
    assert not (tmp_path / "cancelled.txt").exists()
    decision = next(
        event.payload.model_dump()
        for event in parent.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
    )
    assert decision["approval_id"] == pending[0].approval_id
    assert decision["decision"] == "cancelled"
    assert runtime.pending_approvals(request.request_id) == ()
    runtime.shutdown()


def test_runtime_rehydrates_terminal_background_without_rerun(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    calls = 0

    def runner(*_):
        nonlocal calls
        calls += 1
        return ChildRunOutput(summary="persisted ok")

    kwargs = {
        "sessions_dir": sessions,
        "artifacts_dir": tmp_path / "artifacts",
        "agents_dir": _agents_dir(tmp_path),
    }
    runtime = LocalSubagentRuntime(**kwargs, child_runner=runner)
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    runtime.submit(request, parent_session=parent)
    first = runtime.join(request.request_id, timeout_s=5)
    runtime.shutdown()
    assert calls == 1

    restored = LocalSubagentRuntime(
        **kwargs,
        child_runner=lambda *_: (_ for _ in ()).throw(AssertionError("不得重跑模型")),
    )
    assert restored.status(request.request_id).status is SubagentStatus.COMPLETED
    second = restored.join(request.request_id)
    assert second == first
    restored.shutdown()


def test_runtime_rehydrate_finalizing_completes_receipt_once(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        rehydrate=False,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    run = seed._prepare(request, parent, status=SubagentStatus.RUNNING)
    seed._background[request.request_id] = run
    record = seed.artifact_store.commit_text(
        run.handle.agent_id,
        name="summary.md",
        kind="summary",
        content="finished before crash",
    )
    _, manifest_path = seed.artifact_store.write_manifest(
        request_id=request.request_id,
        agent_id=run.handle.agent_id,
        records=(record,),
    )
    now = datetime.now(timezone.utc)
    envelope = SubagentResultEnvelope(
        request_id=request.request_id,
        agent_id=run.handle.agent_id,
        parent_session_id=parent.session_id,
        child_session_id=run.child_session.session_id,
        child_session_path=str(run.child_session.path),
        status=SubagentStatus.COMPLETED,
        summary="finished before crash",
        hint="finished",
        artifacts=(record,),
        manifest_path=str(manifest_path),
        started_at=now,
        ended_at=now,
        receipt_id="finalizing-receipt",
        lineage_id=request.lineage_id,
        attempt_number=request.attempt_number,
    )
    seed._persist(run, phase="finalizing", status=SubagentStatus.COMPLETED, result=envelope)
    seed.shutdown()

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda *_: (_ for _ in ()).throw(AssertionError("finalizing 不得重跑模型")),
    )
    assert restored.join(request.request_id) == envelope
    restored.shutdown()

    restored_again = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    assert restored_again.join(request.request_id) == envelope
    restored_again.shutdown()
    parent_events = parent.load()
    assert (
        sum(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.DELIVERY_RECEIPT
            and event.payload.model_dump().get("receipt_id") == envelope.receipt_id
            for event in parent_events
        )
        == 1
    )
    assert (
        sum(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.SUBAGENT_COMPLETED
            and event.payload.model_dump().get("receipt_id") == envelope.receipt_id
            for event in parent_events
        )
        == 1
    )


def test_runtime_rehydrate_orphan_running_fails_closed_once(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda *_: ChildRunOutput(summary="never"),
        rehydrate=False,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    run = seed._prepare(request, parent, status=SubagentStatus.RUNNING)
    seed._background[request.request_id] = run
    seed._persist(run, phase="running")
    seed.shutdown()

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda *_: (_ for _ in ()).throw(AssertionError("孤儿任务不得自动重跑")),
    )
    result = restored.join(request.request_id)
    assert result.status is SubagentStatus.FAILED
    assert result.error is not None and result.error.code == "RuntimeRestarted"
    assert Path(result.manifest_path).name == "recovery-manifest.json"
    assert result.receipt_id
    receipt_count = sum(
        event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.DELIVERY_RECEIPT
        and event.payload.model_dump().get("receipt_id") == result.receipt_id
        for event in parent.load()
    )
    assert receipt_count == 1
    restored.shutdown()

    restored_again = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    assert restored_again.join(request.request_id) == result
    receipt_count_again = sum(
        event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.DELIVERY_RECEIPT
        and event.payload.model_dump().get("receipt_id") == result.receipt_id
        for event in parent.load()
    )
    assert receipt_count_again == 1
    restored_again.shutdown()


def test_runtime_recovery_artifacts_are_reused_after_second_crash_window(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        rehydrate=False,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode="background",
    )
    run = seed._prepare(request, parent, status=SubagentStatus.RUNNING)
    seed._background[request.request_id] = run
    seed._persist(run, phase="running")
    recovery_record = seed.artifact_store.commit_text(
        run.handle.agent_id,
        name="recovery-error.txt",
        kind="error",
        content="RuntimeRestarted: preexisting\n",
    )
    seed.artifact_store.write_manifest(
        request_id=request.request_id,
        agent_id=run.handle.agent_id,
        records=(recovery_record,),
        name="recovery-manifest.json",
    )
    seed.shutdown()

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
    )
    result = restored.join(request.request_id)
    assert result.status is SubagentStatus.FAILED
    assert Path(result.manifest_path).name == "recovery-manifest.json"
    assert result.artifacts[0].path == recovery_record.path
    restored.shutdown()


def test_foreground_run_does_not_create_rehydrate_state(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda *_: ChildRunOutput(summary="foreground"),
    )
    result = runtime.run(
        SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="x"),
        parent_session=parent,
    )
    assert result.status is SubagentStatus.COMPLETED
    assert list((sessions / "_runtime").glob("*.json")) == []
    runtime.shutdown()


def test_runtime_rejects_parent_session_mismatch_before_child_creation(tmp_path) -> None:
    parent = SessionStore.create(tmp_path / "sessions", title="parent")
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=lambda *_: ChildRunOutput(summary="ok"),
    )
    request = SubagentRequest.create(parent_session_id="wrong", role="worker", task="x")
    with pytest.raises(SubagentRuntimeError, match="不匹配"):
        runtime.run(request, parent_session=parent)


def test_runtime_resume_creates_new_attempt_without_silent_replay(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    observed: list[SubagentRequest] = []

    def runner(request, *_):
        observed.append(request)
        if request.attempt_number == 1:
            return ChildRunOutput(summary="first side effect completed")
        assert "不得假设或重放上一 attempt 的任何工具副作用" in request.task
        assert "本次恢复指令" in request.task
        return ChildRunOutput(summary="resumed safely")

    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=runner,
    )
    first_request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="perform one side effect",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    first_handle = runtime.submit(first_request, parent_session=parent)
    first_result = runtime.join(first_request.request_id, timeout_s=5)
    resumed_handle, receipt = runtime.resume(
        first_request.request_id,
        instruction="检查当前状态，只执行仍然需要的工作",
        idempotency_key="test:resume:first:continue",
        requested_by="test-parent",
    )
    resumed_result = runtime.join(resumed_handle.request_id, timeout_s=5)

    assert len(observed) == 2
    assert resumed_handle.request_id != first_handle.request_id
    assert resumed_handle.agent_id != first_handle.agent_id
    assert resumed_handle.child_session_id != first_handle.child_session_id
    assert resumed_handle.lineage_id == first_handle.lineage_id
    assert resumed_handle.attempt_number == 2
    assert resumed_handle.resumed_from_request_id == first_request.request_id
    assert resumed_result.lineage_id == first_result.lineage_id
    assert resumed_result.attempt_number == 2
    assert receipt.new_request_id == resumed_handle.request_id
    assert receipt.new_agent_id == resumed_handle.agent_id

    replay_handle, replay_receipt = runtime.resume(
        first_request.request_id,
        instruction="检查当前状态，只执行仍然需要的工作",
        idempotency_key="test:resume:first:continue",
        requested_by="test-parent",
    )
    assert replay_handle.request_id == resumed_handle.request_id
    assert replay_receipt == receipt
    assert len(observed) == 2
    with pytest.raises(SubagentRuntimeError, match="已用于不同语义"):
        runtime.resume(
            first_request.request_id,
            instruction="冲突的新语义",
            idempotency_key="test:resume:first:continue",
            requested_by="test-parent",
        )

    old_child_types = [
        event.payload.customType
        for event in SessionStore(Path(first_result.child_session_path)).load()
        if event.type is EventType.CUSTOM
    ]
    new_child_types = [
        event.payload.customType
        for event in SessionStore(Path(resumed_result.child_session_path)).load()
        if event.type is EventType.CUSTOM
    ]
    assert CustomType.RESUME_REQUESTED in old_child_types
    assert CustomType.RESUME_REQUESTED in new_child_types
    assert CustomType.RESUME_RECEIPT in old_child_types
    assert CustomType.RESUME_RECEIPT in new_child_types
    with pytest.raises(SubagentRuntimeError, match="不是该 lineage 的最新 attempt"):
        runtime.resume(
            first_request.request_id,
            instruction="不得从旧 attempt 分叉出重复编号",
            idempotency_key="test:resume:conflict:old-source",
        )
    runtime.shutdown()


def test_runtime_rehydrate_resume_idempotency_reuses_original_attempt(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    observed: list[int] = []

    def runner(request, *_):
        observed.append(request.attempt_number)
        return ChildRunOutput(summary=f"attempt {request.attempt_number}")

    first_runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=runner,
    )
    first_request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="first",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    first_runtime.submit(first_request, parent_session=parent)
    first_runtime.join(first_request.request_id, timeout_s=5)
    resumed_handle, original_receipt = first_runtime.resume(
        first_request.request_id,
        instruction="continue after restart",
        idempotency_key="test:restart:resume",
        requested_by="human:jy",
    )
    first_runtime.join(resumed_handle.request_id, timeout_s=5)
    first_runtime.shutdown()

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda *_: (_ for _ in ()).throw(AssertionError("幂等重试不得重跑")),
    )
    replay_handle, replay_receipt = restored.resume(
        first_request.request_id,
        instruction="continue after restart",
        idempotency_key="test:restart:resume",
        requested_by="human:jy",
    )
    assert replay_handle.request_id == resumed_handle.request_id
    assert replay_handle.child_session_id == resumed_handle.child_session_id
    assert replay_receipt == original_receipt
    assert observed == [1, 2]
    restored.shutdown()


def test_runtime_steer_applies_only_at_next_safe_model_turn(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    first_provider_entered = threading.Event()
    release_provider = threading.Event()
    calls: list[list[dict]] = []

    def chat_fn(*, messages, **_):
        calls.append(list(messages))
        if len(calls) == 1:
            first_provider_entered.set()
            assert release_provider.wait(timeout=5)
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "read first",
                        "tool_calls": [{
                            "id": "read-before-steer",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps({"path": "input.txt"}),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}

    (tmp_path / "input.txt").write_text("state", encoding="utf-8")
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        chat_fn=chat_fn,
        workdir=tmp_path,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="read state",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    runtime.submit(request, parent_session=parent)
    assert first_provider_entered.wait(timeout=5)
    accepted = runtime.steer(request.request_id, instruction="下一轮先核对读取结果")
    assert accepted.status is SteerStatus.ACCEPTED
    release_provider.set()
    result = runtime.join(request.request_id, timeout_s=5)
    assert result.status is SubagentStatus.COMPLETED
    assert not any(message.get("content", "").startswith("[steer]") for message in calls[0])
    assert any(
        message.get("role") == "user" and message.get("content") == "[steer] 下一轮先核对读取结果"
        for message in calls[1]
    )
    child_events = SessionStore(Path(result.child_session_path)).load()
    steer_message_index = next(
        index
        for index, event in enumerate(child_events)
        if event.type is EventType.MESSAGE and event.payload.content.startswith("[steer]")
    )
    tool_result_index = max(
        index
        for index, event in enumerate(child_events)
        if event.type is EventType.TOOL_CALL and event.payload.phase == "result"
    )
    assert tool_result_index < steer_message_index
    steer_statuses = [
        event.payload.model_dump().get("status")
        for event in child_events
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.STEER_RECEIPT
    ]
    assert steer_statuses == [SteerStatus.ACCEPTED.value, SteerStatus.APPLIED.value]
    runtime.shutdown()


def test_runtime_rehydrate_finalizing_closes_unsettled_steer(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        rehydrate=False,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    run = seed._prepare(request, parent, status=SubagentStatus.RUNNING)
    seed._background[request.request_id] = run
    record = seed.artifact_store.commit_text(
        run.handle.agent_id,
        name="summary.md",
        kind="summary",
        content="finished",
    )
    _, manifest_path = seed.artifact_store.write_manifest(
        request_id=request.request_id,
        agent_id=run.handle.agent_id,
        records=(record,),
    )
    now = datetime.now(timezone.utc)
    envelope = SubagentResultEnvelope(
        request_id=request.request_id,
        agent_id=run.handle.agent_id,
        parent_session_id=parent.session_id,
        child_session_id=run.child_session.session_id,
        child_session_path=str(run.child_session.path),
        status=SubagentStatus.COMPLETED,
        summary="finished",
        hint="finished",
        artifacts=(record,),
        manifest_path=str(manifest_path),
        started_at=now,
        ended_at=now,
        receipt_id="finalizing-with-steer",
        lineage_id=request.lineage_id,
        attempt_number=1,
    )
    seed.steer_queue.accept(
        SteerDirective.create(
            request_id=request.request_id,
            agent_id=run.handle.agent_id,
            child_session_id=run.handle.child_session_id,
            lineage_id=request.lineage_id,
            attempt_number=1,
            instruction="accepted before crash",
            requested_by="test",
        ),
        parent_session=parent,
        child_session=run.child_session,
    )
    seed._persist(run, phase="finalizing", status=SubagentStatus.COMPLETED, result=envelope)
    seed.shutdown()

    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda *_: (_ for _ in ()).throw(AssertionError("不得重跑")),
    )
    assert restored.join(request.request_id) == envelope
    statuses = [
        event.payload.model_dump().get("status")
        for event in run.child_session.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.STEER_RECEIPT
    ]
    assert statuses == [SteerStatus.ACCEPTED.value, SteerStatus.RUNTIME_RESTARTED.value]
    restored.shutdown()


def test_runtime_pending_steer_is_superseded_at_terminal(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    started = threading.Event()
    release = threading.Event()

    def runner(*_):
        started.set()
        assert release.wait(timeout=5)
        return ChildRunOutput(summary="finished without another model turn")

    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        child_runner=runner,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    runtime.submit(request, parent_session=parent)
    assert started.wait(timeout=5)
    runtime.steer(request.request_id, instruction="too late")
    release.set()
    result = runtime.join(request.request_id, timeout_s=5)
    statuses = [
        event.payload.model_dump().get("status")
        for event in SessionStore(Path(result.child_session_path)).load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.STEER_RECEIPT
    ]
    assert statuses == [SteerStatus.ACCEPTED.value, SteerStatus.SUPERSEDED.value]
    with pytest.raises(SubagentRuntimeError, match="queued/running"):
        runtime.steer(request.request_id, instruction="terminal reject")
    runtime.shutdown()


def test_runtime_restart_closes_steer_and_never_injects_it_into_resume(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    agents = _agents_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    seed = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        rehydrate=False,
    )
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="x",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    run = seed._prepare(request, parent, status=SubagentStatus.RUNNING)
    seed._background[request.request_id] = run
    seed._persist(run, phase="running")
    seed.steer_queue.accept(
        directive=SteerDirective.create(
            request_id=request.request_id,
            agent_id=run.handle.agent_id,
            child_session_id=run.handle.child_session_id,
            lineage_id=request.lineage_id,
            attempt_number=1,
            instruction="old steer must not replay",
            requested_by="test",
        ),
        parent_session=parent,
        child_session=run.child_session,
    )
    seed.shutdown()

    observed_tasks: list[str] = []
    restored = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=artifacts,
        agents_dir=agents,
        child_runner=lambda new_request, *_: (
            observed_tasks.append(new_request.task),
            ChildRunOutput(summary="resumed"),
        )[1],
    )
    failed = restored.join(request.request_id)
    assert failed.error is not None and failed.error.code == "RuntimeRestarted"
    old_child = SessionStore(Path(failed.child_session_path))
    statuses = [
        event.payload.model_dump().get("status")
        for event in old_child.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.STEER_RECEIPT
    ]
    assert statuses == [SteerStatus.ACCEPTED.value, SteerStatus.RUNTIME_RESTARTED.value]

    resumed_handle, _ = restored.resume(
        request.request_id,
        instruction="resume explicitly",
        idempotency_key="test:resume:restart:explicit",
    )
    resumed = restored.join(resumed_handle.request_id, timeout_s=5)
    assert observed_tasks and "old steer must not replay" not in observed_tasks[0]
    assert not any(
        event.type is EventType.MESSAGE and event.payload.content.startswith("[steer]")
        for event in SessionStore(Path(resumed.child_session_path)).load()
    )
    restored.shutdown()
