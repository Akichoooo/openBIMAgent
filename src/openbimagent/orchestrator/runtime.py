"""受控的 Subagent Runtime v1。

P0 实现同步 LocalSubagentRuntime：创建独立 child Session、解析受信任 Markdown 角色、执行注入的
child runner、提交不可变工件、记录生命周期与投递回执。后台队列、跨进程恢复、steer/cancel
留给 P1，但 v1 handle/result 已预留稳定身份和状态。
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openbimagent.core.permissions import Permission
from openbimagent.orchestrator.actor import ActorLike, actor_ref
from openbimagent.orchestrator.approval import ApprovalBroker, ApprovalDecision, DecisionReceipt
from openbimagent.orchestrator.artifacts import ArtifactStore
from openbimagent.orchestrator.control import (
    ResumeReceipt,
    ResumeRequest,
    SteerDirective,
    SteerQueue,
    SteerReceipt,
    SteerStatus,
    append_resume_events,
    make_resume_receipt,
    make_resume_request,
)
from openbimagent.orchestrator.contracts import (
    ArtifactRecord,
    ArtifactStatus,
    ContextMode,
    ExecutionMode,
    SubagentError,
    SubagentHandle,
    SubagentRequest,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.state import (
    RuntimeLease,
    RuntimePhase,
    RuntimeStateRecord,
    RuntimeStateStore,
)
from openbimagent.schema_gate.gate import gate_or_fix
from openbimagent.session.schema import CustomType, EventType, uuid7
from openbimagent.session.store import SessionStore

if TYPE_CHECKING:
    from openbimagent.core.loop import ApprovalCallback, ChatFn
    from openbimagent.orchestrator.ipc import IpcDiscovery, RuntimeIpcServer

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
MAX_FORK_CONTEXT_CHARS = 50_000
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SubagentRuntimeError(RuntimeError):
    """请求、角色或运行时配置无效。"""


@dataclass(frozen=True)
class AgentProfile:
    """从受信任 agents/<role>.md 解析出的能力上限。"""

    name: str
    system_prompt: str
    model: str | None = None
    tools: tuple[str, ...] = ()
    permissions: dict[str, Permission] = field(default_factory=dict)
    context_mode: ContextMode = ContextMode.ISOLATED
    max_turns: int = 10
    artifact_contract: str = "summary-v1"
    nesting: bool = False


@dataclass(frozen=True)
class ChildRunOutput:
    """child runner 返回的最小内部结果；Runtime 负责转成版本化信封。"""

    summary: str
    hint: str = ""
    artifact_paths: tuple[Path, ...] = ()
    usage: dict[str, int | float] = field(default_factory=dict)


ChildRunner = Callable[[SubagentRequest, AgentProfile, SessionStore], ChildRunOutput]

MAX_BACKGROUND_SUBAGENTS = 4


@dataclass
class _BackgroundRun:
    """P1a 进程内后台任务状态；重启恢复和跨进程锁留给 P1b。"""

    request: SubagentRequest
    parent_session: SessionStore
    handle: SubagentHandle
    child_session: SessionStore
    profile: AgentProfile
    cancel_event: threading.Event
    future: Future[SubagentResultEnvelope] | None = None
    status: SubagentStatus = SubagentStatus.QUEUED
    result: SubagentResultEnvelope | None = None
    checkpoints: tuple[ArtifactRecord, ...] = ()
    resume_request: ResumeRequest | None = None
    resume_receipt: ResumeReceipt | None = None


class LocalSubagentRuntime:
    """P0 同步运行时；状态和结果落盘，不依赖 Pi 或终端 multiplexer。"""

    def __init__(
        self,
        *,
        sessions_dir: Path,
        artifacts_dir: Path,
        agents_dir: Path = AGENTS_DIR,
        child_runner: ChildRunner | None = None,
        chat_fn: ChatFn | None = None,
        approval_callback: ApprovalCallback | None = None,
        approval_timeout_s: float | None = 300.0,
        workdir: Path | None = None,
        max_background_workers: int = MAX_BACKGROUND_SUBAGENTS,
        rehydrate: bool = True,
        enable_ipc: bool = False,
        ipc_timeout_s: float = 5.0,
    ) -> None:
        if not 1 <= max_background_workers <= MAX_BACKGROUND_SUBAGENTS:
            raise ValueError(f"max_background_workers 必须在 1..{MAX_BACKGROUND_SUBAGENTS}")
        self.sessions_dir = Path(sessions_dir).resolve()
        self.artifact_store = ArtifactStore(artifacts_dir)
        self.agents_dir = Path(agents_dir).resolve()
        self.chat_fn = chat_fn
        self.workdir = Path(workdir).resolve() if workdir is not None else Path.cwd()
        self.child_runner = child_runner or self._default_child_runner
        self.approval_broker = ApprovalBroker(
            approval_callback=approval_callback,
            default_timeout_s=approval_timeout_s,
        )
        self.steer_queue = SteerQueue()
        self.max_background_workers = max_background_workers
        self.state_store = RuntimeStateStore(self.sessions_dir / "_runtime")
        self._lease = RuntimeLease(self.state_store.root / ".runtime.lock")
        self._shutdown_lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._background: dict[str, _BackgroundRun] = {}
        self._background_lock = threading.RLock()
        self._cancel_local = threading.local()
        self._ipc_server: RuntimeIpcServer | None = None
        try:
            self._executor = ThreadPoolExecutor(
                max_workers=max_background_workers,
                thread_name_prefix="openbim-subagent",
            )
            if rehydrate:
                self._rehydrate()
            if enable_ipc:
                self.start_ipc(timeout_s=ipc_timeout_s)
        except Exception:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._lease.release()
            raise

    def run(self, request: SubagentRequest, *, parent_session: SessionStore) -> SubagentResultEnvelope:
        """同步执行 foreground 请求；background 必须使用 submit()，避免误阻塞父代理。"""
        if request.execution_mode is not ExecutionMode.FOREGROUND:
            raise SubagentRuntimeError("background 请求必须使用 submit()，再通过 status/cancel/join 管理")
        with self._shutdown_lock:
            if self._executor is None:
                raise SubagentRuntimeError("Runtime 已关闭，不能再执行 foreground 请求")
            prepared = self._prepare(request, parent_session, status=SubagentStatus.CREATED)
            return self._execute_prepared(prepared)

    def submit(self, request: SubagentRequest, *, parent_session: SessionStore) -> SubagentHandle:
        """提交 P1a 进程内 background 请求，立即返回稳定 handle。"""
        if request.execution_mode is not ExecutionMode.BACKGROUND:
            raise SubagentRuntimeError("submit() 仅接受 execution_mode=background")
        with self._shutdown_lock:
            executor = self._executor
            if executor is None:
                raise SubagentRuntimeError("Runtime 已关闭，不能再提交 background 请求")
            with self._background_lock:
                if request.request_id in self._background:
                    raise SubagentRuntimeError(f"request_id 已存在: {request.request_id}")
            prepared = self._prepare(request, parent_session, status=SubagentStatus.QUEUED)
            with self._background_lock:
                self._background[request.request_id] = prepared
                self._persist(prepared, phase="prepared")
                prepared.future = executor.submit(self._execute_background, request.request_id)
        return prepared.handle

    def status(self, request_id: str) -> SubagentHandle:
        """返回后台任务当前状态；终态仍沿用同一 handle 身份。"""
        run = self._background_run(request_id)
        with self._background_lock:
            return run.handle.model_copy(update={"status": run.status})

    def cancel(self, request_id: str) -> bool:
        """请求协作式取消；queued/running 可取消，终态返回 False。"""
        run = self._background_run(request_id)
        with self._background_lock:
            if run.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED}:
                return False
            run.cancel_event.set()
            return True

    def resume(
        self,
        source_request_id: str,
        *,
        instruction: str,
        idempotency_key: str,
        requested_by: ActorLike = "parent",
    ) -> tuple[SubagentHandle, ResumeReceipt]:
        """从已终态 background attempt 创建新 attempt；旧副作用绝不自动重放。"""
        source = self._background_run(source_request_id)
        with self._shutdown_lock:
            executor = self._executor
            if executor is None:
                raise SubagentRuntimeError("Runtime 已关闭，不能 resume")
            with self._background_lock:
                if source.status not in {
                    SubagentStatus.COMPLETED,
                    SubagentStatus.FAILED,
                    SubagentStatus.CANCELLED,
                } or source.result is None:
                    raise SubagentRuntimeError("resume 仅允许从已终态且有 receipt 的 background attempt 创建")
                if not instruction.strip():
                    raise SubagentRuntimeError("resume instruction 不能为空")
                if not idempotency_key.strip():
                    raise SubagentRuntimeError("resume idempotency_key 不能为空")
                caller = actor_ref(requested_by)
                instruction_sha256 = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
                matching = [
                    candidate
                    for candidate in self._background.values()
                    if candidate.resume_request is not None
                    and candidate.resume_request.idempotency_key == idempotency_key
                    and candidate.resume_request.requested_by.actor_id == caller.actor_id
                ]
                if matching:
                    existing = matching[0]
                    existing_request = existing.resume_request
                    existing_receipt = existing.resume_receipt
                    if existing_request is None or existing_receipt is None:
                        raise SubagentRuntimeError("幂等 Resume 缺少持久化 request/receipt")
                    if (
                        existing_request.source_request_id != source_request_id
                        or existing_request.instruction_sha256 != instruction_sha256
                    ):
                        raise SubagentRuntimeError("resume idempotency_key 已用于不同语义的请求")
                    return existing.handle.model_copy(update={"status": existing.status}), existing_receipt
                latest_attempt = max(
                    candidate.request.attempt_number
                    for candidate in self._background.values()
                    if candidate.request.lineage_id == source.request.lineage_id
                )
                if source.request.attempt_number != latest_attempt:
                    raise SubagentRuntimeError(
                        "resume source 不是该 lineage 的最新 attempt；必须从最新终态继续，避免重复 attempt_number"
                    )
                new_request_id = str(uuid7())
                resume_request = make_resume_request(
                    source_request_id=source.request.request_id,
                    source_agent_id=source.handle.agent_id,
                    source_child_session_id=source.handle.child_session_id,
                    new_request_id=new_request_id,
                    lineage_id=source.request.lineage_id,
                    attempt_number=source.request.attempt_number + 1,
                    instruction=instruction,
                    idempotency_key=idempotency_key,
                    requested_by=caller,
                )
                task = _resume_task(source, instruction)
                request = SubagentRequest(
                    request_id=new_request_id,
                    parent_session_id=source.request.parent_session_id,
                    role=source.request.role,
                    task=task,
                    context_mode=source.request.context_mode,
                    execution_mode=ExecutionMode.BACKGROUND,
                    artifact_contract=source.request.artifact_contract,
                    lineage_id=source.request.lineage_id,
                    attempt_number=source.request.attempt_number + 1,
                    resumed_from_request_id=source.request.request_id,
                    resume_id=resume_request.resume_id,
                )
                prepared = self._prepare(request, source.parent_session, status=SubagentStatus.QUEUED)
                receipt = make_resume_receipt(
                    resume_request,
                    new_agent_id=prepared.handle.agent_id,
                    new_child_session_id=prepared.handle.child_session_id,
                )
                prepared.resume_request = resume_request
                prepared.resume_receipt = receipt
                self._background[request.request_id] = prepared
                self._persist(prepared, phase="prepared")
                append_resume_events(
                    parent_session=source.parent_session,
                    source_child_session=source.child_session,
                    new_child_session=prepared.child_session,
                    request=resume_request,
                    receipt=receipt,
                )
                prepared.future = executor.submit(self._execute_background, request.request_id)
        return prepared.handle, receipt

    def steer(
        self,
        request_id: str,
        *,
        instruction: str,
        requested_by: ActorLike = "parent",
    ) -> SteerReceipt:
        """向活跃 attempt 排队 steer；只在下一轮模型调用前应用。"""
        run = self._background_run(request_id)
        with self._background_lock:
            if run.status not in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}:
                raise SubagentRuntimeError("steer 仅允许绑定 queued/running attempt")
            if not instruction.strip():
                raise SubagentRuntimeError("steer instruction 不能为空")
            directive = SteerDirective.create(
                request_id=run.request.request_id,
                agent_id=run.handle.agent_id,
                child_session_id=run.handle.child_session_id,
                lineage_id=run.request.lineage_id,
                attempt_number=run.request.attempt_number,
                instruction=instruction,
                requested_by=requested_by,
            )
            return self.steer_queue.accept(
                directive,
                parent_session=run.parent_session,
                child_session=run.child_session,
            )

    def checkpoint_artifact(
        self,
        request_id: str,
        *,
        source: Path,
        idempotency_key: str,
        kind: str = "side-effect-checkpoint",
    ) -> ArtifactRecord:
        """Persist an immutable partial side-effect fact before an attempt can be lost."""
        run = self._background_run(request_id)
        if not idempotency_key.strip():
            raise SubagentRuntimeError("checkpoint idempotency_key 不能为空")
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"checkpoint 工件不存在: {source}")
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        destination = self.artifact_store.run_dir(run.handle.agent_id) / f"checkpoint-{digest}.bin"
        with self._background_lock:
            if run.status not in {SubagentStatus.QUEUED, SubagentStatus.RUNNING}:
                raise SubagentRuntimeError("checkpoint 只允许绑定 queued/running attempt")
            existing = next(
                (
                    record
                    for record in run.checkpoints
                    if Path(record.path).name == destination.name
                ),
                None,
            )
            if existing is not None:
                current_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
                if existing.sha256 != current_sha256:
                    raise SubagentRuntimeError(
                        "同一 checkpoint idempotency_key 对应不同副作用事实"
                    )
                return existing
            if destination.is_file():
                record = self.artifact_store.record_existing(
                    destination,
                    kind=kind,
                    expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    source_attempt_id=request_id,
                    status=ArtifactStatus.PARTIAL,
                )
            else:
                record = self.artifact_store.commit_file(
                    run.handle.agent_id,
                    source,
                    name=destination.name,
                    kind=kind,
                    source_attempt_id=request_id,
                    status=ArtifactStatus.PARTIAL,
                )
            run.checkpoints = (*run.checkpoints, record)
            self._persist(run, phase="running", status=run.status)
        run.child_session.append_new(
            EventType.CUSTOM,
            {
                "customType": CustomType.ARTIFACT_COMMITTED,
                "request_id": request_id,
                "agent_id": run.handle.agent_id,
                "artifact": record.model_dump(mode="json"),
            },
        )
        return record

    def pending_approvals(self, request_id: str | None = None):
        """列出等待父侧决策的 child 审批请求。"""
        return self.approval_broker.pending(request_id=request_id)

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: ActorLike = "parent",
        reason: str = "",
    ) -> DecisionReceipt:
        """父侧提交审批决策并签发稳定 decision receipt。"""
        return self.approval_broker.decide(
            approval_id,
            decision=ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED,
            decided_by=decided_by,
            reason=reason,
        )

    def join(self, request_id: str, timeout_s: float | None = None) -> SubagentResultEnvelope:
        """等待后台任务终态；超时抛 TimeoutError，不吞掉仍在运行的任务。"""
        run = self._background_run(request_id)
        if run.result is not None:
            return run.result
        if run.future is None:
            raise SubagentRuntimeError(f"后台任务未处于可 join 状态: {request_id}")
        try:
            result = run.future.result(timeout=timeout_s)
        except FutureTimeoutError as exc:
            raise TimeoutError(f"等待子代理超时: request_id={request_id}") from exc
        return result

    def start_ipc(self, *, timeout_s: float = 5.0) -> IpcDiscovery:
        """启动绑定当前 lease owner 的 loopback-only IPC 写控制服务。"""
        from openbimagent.orchestrator.ipc import RuntimeIpcServer

        with self._shutdown_lock:
            if self._executor is None:
                raise SubagentRuntimeError("Runtime 已关闭，不能启动 IPC")
            if self._ipc_server is None:
                self._ipc_server = RuntimeIpcServer(self, timeout_s=timeout_s)
            return self._ipc_server.start()

    def stop_ipc(self) -> None:
        """停止 IPC 并移除 discovery/token 文件；可重复调用。"""
        with self._shutdown_lock:
            service = self._ipc_server
            self._ipc_server = None
        if service is not None:
            service.stop()

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """释放 IPC、后台线程池和 Runtime lease；可重复调用。"""
        self.stop_ipc()
        with self._shutdown_lock:
            executor = self._executor
            if executor is None:
                return
            with self._background_lock:
                unfinished = [
                    run.request.request_id
                    for run in self._background.values()
                    if run.future is not None and not run.future.done()
                ]
            if not wait and unfinished:
                raise SubagentRuntimeError(
                    "存在运行中的 background 任务时 shutdown(wait=False) 无法安全释放 Runtime lease: "
                    + ", ".join(unfinished)
                )
            self._executor = None
            if cancel_pending:
                with self._background_lock:
                    for run in self._background.values():
                        if run.status not in {
                            SubagentStatus.COMPLETED,
                            SubagentStatus.FAILED,
                            SubagentStatus.CANCELLED,
                        }:
                            run.cancel_event.set()
            try:
                executor.shutdown(wait=wait, cancel_futures=False)
            finally:
                self._lease.release()

    def _background_run(self, request_id: str) -> _BackgroundRun:
        with self._background_lock:
            run = self._background.get(request_id)
            if run is None:
                raise SubagentRuntimeError(f"未知 background request_id: {request_id}")
            return run

    def _current_run_handle(self, request_id: str) -> SubagentHandle:
        run = getattr(self._cancel_local, "run", None)
        if run is not None and run.request.request_id == request_id:
            return run.handle
        with self._background_lock:
            background = self._background.get(request_id)
            if background is not None:
                return background.handle
        raise SubagentRuntimeError(f"找不到当前 child run: {request_id}")

    def _prepare(
        self,
        request: SubagentRequest,
        parent_session: SessionStore,
        *,
        status: SubagentStatus,
    ) -> _BackgroundRun:
        gate_or_fix("subagent_request", request.model_dump(mode="json"))
        if request.parent_session_id != parent_session.session_id:
            raise SubagentRuntimeError(
                f"parent_session_id 不匹配: request={request.parent_session_id}, actual={parent_session.session_id}"
            )
        profile = load_agent_profile(request.role, self.agents_dir)
        _enforce_profile_ceiling(request, profile)
        agent_id = str(uuid7())
        child = SessionStore.create(
            self.sessions_dir,
            title=f"subagent:{request.role}:{request.request_id[:8]}",
            playbook=None,
        )
        child.mark_child_of(
            parent_session_id=parent_session.session_id,
            parent_event_id=parent_session.head,
            request_id=request.request_id,
            agent_id=agent_id,
            role=request.role,
            lineage_id=request.lineage_id,
            attempt_number=request.attempt_number,
            resumed_from_request_id=request.resumed_from_request_id,
        )
        handle = SubagentHandle(
            request_id=request.request_id,
            agent_id=agent_id,
            parent_session_id=parent_session.session_id,
            child_session_id=child.session_id,
            child_session_path=str(child.path),
            status=status,
            lineage_id=request.lineage_id,
            attempt_number=request.attempt_number,
            resumed_from_request_id=request.resumed_from_request_id,
        )
        _append_lifecycle(parent_session, CustomType.SUBAGENT_CREATED, handle, role=request.role)
        _append_lifecycle(child, CustomType.SUBAGENT_CREATED, handle, role=request.role)
        return _BackgroundRun(
            request=request,
            parent_session=parent_session,
            handle=handle,
            child_session=child,
            profile=profile,
            cancel_event=threading.Event(),
            status=status,
        )

    def _execute_background(self, request_id: str) -> SubagentResultEnvelope:
        run = self._background_run(request_id)
        return self._execute_prepared(run)

    def _execute_prepared(self, run: _BackgroundRun) -> SubagentResultEnvelope:
        request = run.request
        parent_session = run.parent_session
        child = run.child_session
        handle = run.handle
        profile = run.profile
        started_at = datetime.now(timezone.utc)

        if run.cancel_event.is_set():
            status = SubagentStatus.CANCELLED
            output: ChildRunOutput | None = None
            error: SubagentError | None = SubagentError(
                code="Cancelled",
                message="子代理在开始执行前被取消",
                retryable=False,
            )
        else:
            with self._background_lock:
                run.status = SubagentStatus.RUNNING
                self._persist(run, phase="running")
            _append_lifecycle(
                parent_session,
                CustomType.SUBAGENT_STARTED,
                handle,
                role=request.role,
                status=SubagentStatus.RUNNING,
            )
            _append_lifecycle(
                child,
                CustomType.SUBAGENT_STARTED,
                handle,
                role=request.role,
                status=SubagentStatus.RUNNING,
            )
            output = None
            error = None
            status = SubagentStatus.COMPLETED
            try:
                self._cancel_local.event = run.cancel_event
                self._cancel_local.run = run
                output = self.child_runner(request, profile, child)
                if not isinstance(output, ChildRunOutput):
                    raise TypeError(f"child_runner 必须返回 ChildRunOutput，实收 {type(output).__name__}")
                if run.cancel_event.is_set():
                    status = SubagentStatus.CANCELLED
                    error = SubagentError(code="Cancelled", message="子代理执行被取消", retryable=False)
                    output = None
            except Exception as exc:
                status = SubagentStatus.FAILED
                error = SubagentError(code=type(exc).__name__, message=str(exc) or repr(exc), retryable=False)
                child.append_new(
                    EventType.MESSAGE,
                    {"role": "assistant", "content": f"[subagent failed] {error.code}: {error.message}"},
                )
            finally:
                self._cancel_local.event = None
                self._cancel_local.run = None

        try:
            records = self._commit_outputs(handle.agent_id, output, error)
        except Exception as exc:
            status = SubagentStatus.FAILED
            error = SubagentError(code=type(exc).__name__, message=str(exc) or repr(exc), retryable=False)
            output = None
            records = (
                self.artifact_store.commit_text(
                    handle.agent_id,
                    name="artifact-error.txt",
                    kind="error",
                    content=f"{error.code}: {error.message}\n",
                ),
            )
        _, manifest_path = self.artifact_store.write_manifest(
            request_id=request.request_id,
            agent_id=handle.agent_id,
            records=records,
        )
        for record in records:
            child.append_new(
                EventType.CUSTOM,
                {
                    "customType": CustomType.ARTIFACT_COMMITTED,
                    "request_id": request.request_id,
                    "agent_id": handle.agent_id,
                    "artifact": record.model_dump(mode="json"),
                },
            )

        ended_at = datetime.now(timezone.utc)
        summary = output.summary if output is not None else ""
        hint = _truncate_hint(output.hint or output.summary if output is not None else error.message if error else "")
        receipt_id = _receipt_id(request.request_id, handle.agent_id, status)
        envelope = SubagentResultEnvelope(
            request_id=request.request_id,
            agent_id=handle.agent_id,
            parent_session_id=parent_session.session_id,
            child_session_id=child.session_id,
            child_session_path=str(child.path),
            status=status,
            summary=summary,
            hint=hint,
            artifacts=records,
            manifest_path=str(manifest_path),
            started_at=started_at,
            ended_at=ended_at,
            usage=output.usage if output is not None else {},
            error=error,
            receipt_id=receipt_id,
            lineage_id=request.lineage_id,
            attempt_number=request.attempt_number,
            resumed_from_request_id=request.resumed_from_request_id,
        )
        gate_or_fix("subagent_result", envelope.model_dump(mode="json"))
        self._persist(run, phase="finalizing", status=status, result=envelope)
        terminal_types = {
            SubagentStatus.COMPLETED: CustomType.SUBAGENT_COMPLETED,
            SubagentStatus.FAILED: CustomType.SUBAGENT_FAILED,
            SubagentStatus.CANCELLED: CustomType.SUBAGENT_CANCELLED,
        }
        terminal_type = terminal_types[status]
        terminal_steer_status = (
            SteerStatus.REJECTED if status is SubagentStatus.CANCELLED else SteerStatus.SUPERSEDED
        )
        with self._background_lock:
            # 终态发布与 steer 接受使用同一把锁：先关闭未消费指令和写完回执，
            # 再让 status() 观察到终态，避免终态窗口漏掉新 accepted steer。
            self.steer_queue.reject_pending(
                request.request_id,
                status=terminal_steer_status,
                parent_session=parent_session,
                child_session=child,
                reason=f"attempt reached terminal status {status.value} before next model turn",
            )
            _append_lifecycle(child, terminal_type, handle, status=status, receipt_id=receipt_id, error=error)
            _append_lifecycle(parent_session, terminal_type, handle, status=status, receipt_id=receipt_id, error=error)
            parent_session.append_new(
                EventType.CUSTOM,
                {
                    "customType": CustomType.DELIVERY_RECEIPT,
                    "request_id": request.request_id,
                    "agent_id": handle.agent_id,
                    "child_session_id": child.session_id,
                    "receipt_id": receipt_id,
                    "status": status.value,
                    "manifest_path": str(manifest_path),
                },
            )
            run.status = status
            run.result = envelope
            self._persist(run, phase="terminal", status=status, result=envelope)
        return envelope

    def _persist(
        self,
        run: _BackgroundRun,
        *,
        phase: RuntimePhase,
        status: SubagentStatus | None = None,
        result: SubagentResultEnvelope | None = None,
    ) -> None:
        if run.request.execution_mode is not ExecutionMode.BACKGROUND:
            return
        current = status or run.status
        self.state_store.write(
            request=run.request,
            handle=run.handle,
            status=current,
            phase=phase,
            result=result,
            checkpoints=run.checkpoints,
            resume_request=run.resume_request,
            resume_receipt=run.resume_receipt,
        )

    def _rehydrate(self) -> None:
        """恢复终态记录；遗留非终态统一 RuntimeRestarted 失败关闭。"""
        records = self.state_store.load_all()
        for record in records:
            self._background[record.request.request_id] = self._run_from_state(record)
        for record in records:
            try:
                run = self._background[record.request.request_id]
                self._reconcile_resume_events(run)
                # 无论记录停在 running、finalizing 还是 terminal，都先对账历史 steer。
                # accepted 但未终结的指令只能签发 runtime_restarted，绝不重新入队。
                self.steer_queue.close_orphaned(
                    request_id=run.request.request_id,
                    agent_id=run.handle.agent_id,
                    parent_session=run.parent_session,
                    child_session=run.child_session,
                )
                if record.phase == "terminal" and record.result is not None:
                    continue
                if record.phase == "finalizing" and record.result is not None:
                    self._finish_recovered_finalizing(run, record.result)
                    continue
                self._fail_restarted(run)
            except Exception as exc:
                raise SubagentRuntimeError(
                    f"恢复 background request_id={record.request.request_id} 失败: {exc}"
                ) from exc

    def _run_from_state(self, record: RuntimeStateRecord) -> _BackgroundRun:
        parent = SessionStore(self.sessions_dir / f"{record.request.parent_session_id}.jsonl")
        child = SessionStore(Path(record.handle.child_session_path))
        profile = load_agent_profile(record.request.role, self.agents_dir)
        return _BackgroundRun(
            request=record.request,
            parent_session=parent,
            handle=record.handle,
            child_session=child,
            profile=profile,
            cancel_event=threading.Event(),
            future=None,
            status=record.status,
            result=record.result,
            checkpoints=record.checkpoints,
            resume_request=record.resume_request,
            resume_receipt=record.resume_receipt,
        )

    def _reconcile_resume_events(self, run: _BackgroundRun) -> None:
        """从 RuntimeState 幂等补齐三方 resume 事实，不重新提交旧 attempt。"""
        if run.resume_request is None or run.resume_receipt is None:
            return
        source = self._background.get(run.resume_request.source_request_id)
        if source is None:
            raise SubagentRuntimeError(
                f"resume source 状态缺失: {run.resume_request.source_request_id}"
            )
        append_resume_events(
            parent_session=run.parent_session,
            source_child_session=source.child_session,
            new_child_session=run.child_session,
            request=run.resume_request,
            receipt=run.resume_receipt,
        )

    def _finish_recovered_finalizing(
        self,
        run: _BackgroundRun,
        envelope: SubagentResultEnvelope,
    ) -> None:
        self._append_terminal_once(run, envelope)
        run.status = envelope.status
        run.result = envelope
        self._persist(run, phase="terminal", status=envelope.status, result=envelope)

    def _fail_restarted(self, run: _BackgroundRun) -> None:
        self.approval_broker.close_orphaned(
            request_id=run.request.request_id,
            agent_id=run.handle.agent_id,
            parent_session=run.parent_session,
            child_session=run.child_session,
        )
        error = SubagentError(
            code="RuntimeRestarted",
            message="后台子代理在 Runtime 重启前未完成；为避免重复副作用已失败关闭",
            retryable=True,
        )
        run_dir = self.artifact_store.run_dir(run.handle.agent_id)
        recovery_error_path = run_dir / "recovery-error.txt"
        if recovery_error_path.is_file():
            record = self.artifact_store.record_existing(recovery_error_path, kind="error")
        else:
            record = self.artifact_store.commit_text(
                run.handle.agent_id,
                name="recovery-error.txt",
                kind="error",
                content=f"{error.code}: {error.message}\n",
            )
        manifest_path = run_dir / "recovery-manifest.json"
        if manifest_path.is_file():
            manifest = self.artifact_store.load_manifest(manifest_path)
            records = manifest.records
        else:
            manifest, manifest_path = self.artifact_store.write_manifest(
                request_id=run.request.request_id,
                agent_id=run.handle.agent_id,
                records=(record, *run.checkpoints),
                name="recovery-manifest.json",
                lineage_id=run.request.lineage_id,
                attempt_number=run.request.attempt_number,
                resumed_from_request_id=run.request.resumed_from_request_id,
                status=ArtifactStatus.FAILED,
            )
            records = manifest.records
        child_events = run.child_session.load()
        for recovery_record in records:
            child_has_artifact = any(
                event.type is EventType.CUSTOM
                and event.payload.customType is CustomType.ARTIFACT_COMMITTED
                and event.payload.model_dump().get("artifact", {}).get("path") == recovery_record.path
                for event in child_events
            )
            if not child_has_artifact:
                run.child_session.append_new(
                    EventType.CUSTOM,
                    {
                        "customType": CustomType.ARTIFACT_COMMITTED,
                        "request_id": run.request.request_id,
                        "agent_id": run.handle.agent_id,
                        "artifact": recovery_record.model_dump(mode="json"),
                    },
                )
        now = datetime.now(timezone.utc)
        receipt_id = _receipt_id(run.request.request_id, run.handle.agent_id, SubagentStatus.FAILED)
        envelope = SubagentResultEnvelope(
            request_id=run.request.request_id,
            agent_id=run.handle.agent_id,
            parent_session_id=run.parent_session.session_id,
            child_session_id=run.child_session.session_id,
            child_session_path=str(run.child_session.path),
            status=SubagentStatus.FAILED,
            summary="",
            hint=_truncate_hint(error.message),
            artifacts=records,
            manifest_path=str(manifest_path),
            started_at=now,
            ended_at=now,
            error=error,
            receipt_id=receipt_id,
            lineage_id=run.request.lineage_id,
            attempt_number=run.request.attempt_number,
            resumed_from_request_id=run.request.resumed_from_request_id,
        )
        gate_or_fix("subagent_result", envelope.model_dump(mode="json"))
        self._persist(run, phase="finalizing", status=SubagentStatus.FAILED, result=envelope)
        self._append_terminal_once(run, envelope)
        run.status = SubagentStatus.FAILED
        run.result = envelope
        self._persist(run, phase="terminal", status=SubagentStatus.FAILED, result=envelope)

    def _append_terminal_once(self, run: _BackgroundRun, envelope: SubagentResultEnvelope) -> None:
        parent_events = run.parent_session.load()
        child_events = run.child_session.load()
        terminal_types = {
            SubagentStatus.COMPLETED: CustomType.SUBAGENT_COMPLETED,
            SubagentStatus.FAILED: CustomType.SUBAGENT_FAILED,
            SubagentStatus.CANCELLED: CustomType.SUBAGENT_CANCELLED,
        }
        terminal_type = terminal_types[envelope.status]
        child_has_terminal = any(
            event.type is EventType.CUSTOM
            and event.payload.customType is terminal_type
            and event.payload.model_dump().get("receipt_id") == envelope.receipt_id
            for event in child_events
        )
        parent_has_terminal = any(
            event.type is EventType.CUSTOM
            and event.payload.customType is terminal_type
            and event.payload.model_dump().get("receipt_id") == envelope.receipt_id
            for event in parent_events
        )
        parent_has_receipt = any(
            event.type is EventType.CUSTOM
            and event.payload.customType is CustomType.DELIVERY_RECEIPT
            and event.payload.model_dump().get("receipt_id") == envelope.receipt_id
            for event in parent_events
        )
        if not child_has_terminal:
            _append_lifecycle(
                run.child_session,
                terminal_type,
                run.handle,
                status=envelope.status,
                receipt_id=envelope.receipt_id,
                error=envelope.error,
            )
        if not parent_has_terminal:
            _append_lifecycle(
                run.parent_session,
                terminal_type,
                run.handle,
                status=envelope.status,
                receipt_id=envelope.receipt_id,
                error=envelope.error,
            )
        if parent_has_receipt:
            return
        run.parent_session.append_new(
            EventType.CUSTOM,
            {
                "customType": CustomType.DELIVERY_RECEIPT,
                "request_id": envelope.request_id,
                "agent_id": envelope.agent_id,
                "child_session_id": envelope.child_session_id,
                "receipt_id": envelope.receipt_id,
                "status": envelope.status.value,
                "manifest_path": envelope.manifest_path,
            },
        )

    def _commit_outputs(
        self,
        agent_id: str,
        output: ChildRunOutput | None,
        error: SubagentError | None,
    ) -> tuple[ArtifactRecord, ...]:
        records: list[ArtifactRecord] = []
        if output is not None:
            records.append(
                self.artifact_store.commit_text(
                    agent_id,
                    name="summary.md",
                    kind="summary",
                    content=output.summary,
                )
            )
            used_names = {"summary.md"}
            for index, path in enumerate(output.artifact_paths, start=1):
                name = Path(path).name
                if name in used_names:
                    name = f"{index}-{name}"
                used_names.add(name)
                source = Path(path)
                records.append(
                    self.artifact_store.commit_bytes(
                        agent_id,
                        name=name,
                        kind="output",
                        content=source.read_bytes(),
                    )
                )
        elif error is not None:
            records.append(
                self.artifact_store.commit_text(
                    agent_id,
                    name="error.txt",
                    kind="error",
                    content=f"{error.code}: {error.message}\n",
                )
            )
        return tuple(records)

    def _default_child_runner(
        self,
        request: SubagentRequest,
        profile: AgentProfile,
        child_session: SessionStore,
    ) -> ChildRunOutput:
        """使用现有 AgentLoop 跑 child；禁用 subagent 工具，形成 P0 真 child Session。"""
        from openbimagent.core.loop import AgentLoop, TOOL_NAMES

        tools = [tool for tool in profile.tools if tool in TOOL_NAMES and tool != "subagent"]
        unsupported = sorted(set(profile.tools) - set(TOOL_NAMES))
        if unsupported:
            raise SubagentRuntimeError(f"角色 {profile.name} 含 P0 不支持的工具: {unsupported}")
        task = request.task
        if request.context_mode is ContextMode.FORK:
            parent = SessionStore(self.sessions_dir / f"{request.parent_session_id}.jsonl")
            task = f"父会话上下文（只读）：\n{_parent_context(parent)}\n\n当前任务：\n{request.task}"
        handle = self._current_run_handle(request.request_id)

        def consume_steer() -> tuple[str, ...]:
            directives = self.steer_queue.consume(request.request_id)
            instructions: list[str] = []
            for directive in directives:
                if (
                    directive.agent_id != handle.agent_id
                    or directive.child_session_id != child_session.session_id
                    or directive.lineage_id != request.lineage_id
                    or directive.attempt_number != request.attempt_number
                ):
                    self.steer_queue.settle(
                        directive,
                        status=SteerStatus.REJECTED,
                        parent_session=parent,
                        child_session=child_session,
                        reason="steer attempt identity mismatch",
                    )
                    continue
                instructions.append(directive.instruction)
                self.steer_queue.settle(
                    directive,
                    status=SteerStatus.APPLIED,
                    parent_session=parent,
                    child_session=child_session,
                    reason="applied before next model turn",
                )
            return tuple(instructions)

        parent = SessionStore(self.sessions_dir / f"{request.parent_session_id}.jsonl")

        def request_approval(
            tool_name: str,
            permission_key: str,
            args: dict[str, Any],
            cancel_event: threading.Event | None,
        ) -> bool:
            return self.approval_broker.request(
                request_id=request.request_id,
                agent_id=handle.agent_id,
                parent_session=parent,
                child_session=child_session,
                tool_name=tool_name,
                permission_key=permission_key,
                args=args,
                cancel_event=cancel_event,
            )

        loop = AgentLoop(
            tools,
            child_session,
            chat_fn=self.chat_fn,
            approval_request_callback=request_approval,
            steer_callback=consume_steer,
            permission_rules=profile.permissions,
            max_steps=profile.max_turns,
            workdir=self.workdir,
            system_prompt=profile.system_prompt,
            role=profile.name,
            subagent_runtime=None,
            depth=1,
        )
        cancel_event = getattr(self._cancel_local, "event", None)
        summary = loop.run(task, cancel_event=cancel_event)
        return ChildRunOutput(summary=summary, hint=summary[:200])


def load_agent_profile(role: str, agents_dir: Path = AGENTS_DIR) -> AgentProfile:
    """解析 agents/<role>.md；项目角色是受信任配置和能力 ceiling。"""
    path = Path(agents_dir) / f"{role}.md"
    if not path.is_file():
        raise SubagentRuntimeError(f"未知子代理角色 {role!r}: {path} 不存在")
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SubagentRuntimeError(f"角色文件缺少 YAML frontmatter: {path}")
    raw, body = match.groups()
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise SubagentRuntimeError(f"角色 frontmatter 必须是 mapping: {path}")
    name = str(data.get("name") or role)
    if name != role:
        raise SubagentRuntimeError(f"角色文件名与 name 不一致: file={role}, name={name}")
    tools_raw = data.get("tools") or []
    if not isinstance(tools_raw, list) or not all(isinstance(item, str) for item in tools_raw):
        raise SubagentRuntimeError(f"角色 tools 必须是字符串列表: {path}")
    permissions_raw = data.get("permissions") or {}
    if not isinstance(permissions_raw, dict):
        raise SubagentRuntimeError(f"角色 permissions 必须是 mapping: {path}")
    try:
        permissions = {str(key): Permission(value) for key, value in permissions_raw.items()}
        context_mode = ContextMode(data.get("context_mode", ContextMode.ISOLATED))
    except ValueError as exc:
        raise SubagentRuntimeError(f"角色配置枚举值无效: {path}: {exc}") from exc
    max_turns = int(data.get("max_turns", 10))
    if max_turns < 1 or max_turns > 100:
        raise SubagentRuntimeError(f"角色 max_turns 必须在 1..100: {path}")
    return AgentProfile(
        name=name,
        model=str(data["model"]) if data.get("model") else None,
        tools=tuple(tools_raw),
        permissions=permissions,
        context_mode=context_mode,
        max_turns=max_turns,
        artifact_contract=str(data.get("artifact_contract") or "summary-v1"),
        nesting=bool(data.get("nesting", False)),
        system_prompt=body.strip(),
    )


def _enforce_profile_ceiling(request: SubagentRequest, profile: AgentProfile) -> None:
    if request.depth != 0:
        raise SubagentRuntimeError("Subagent Runtime v1 禁止嵌套派发")
    if request.artifact_contract != profile.artifact_contract:
        raise SubagentRuntimeError(
            f"artifact_contract 超出角色契约: request={request.artifact_contract}, profile={profile.artifact_contract}"
        )
    if request.context_mode is ContextMode.FORK and profile.context_mode is not ContextMode.FORK:
        raise SubagentRuntimeError(f"角色 {profile.name} 不允许 fork 父上下文")
    if profile.nesting:
        raise SubagentRuntimeError("P0 不支持 nesting=true，所有 child 均无 subagent 能力")


def _parent_context(parent: SessionStore) -> str:
    parts: list[str] = []
    for event in parent.get_event_chain():
        if event.type is EventType.MESSAGE:
            payload = event.payload.model_dump()
            content = str(payload.get("content") or "")
            if content:
                parts.append(f"[{payload.get('role', 'unknown')}] {content}")
    text = "\n".join(parts)
    return text[-MAX_FORK_CONTEXT_CHARS:]


def _append_lifecycle(
    store: SessionStore,
    custom_type: CustomType,
    handle: SubagentHandle,
    *,
    role: str | None = None,
    status: SubagentStatus | None = None,
    receipt_id: str | None = None,
    error: SubagentError | None = None,
) -> None:
    payload: dict[str, Any] = {
        "customType": custom_type,
        "request_id": handle.request_id,
        "agent_id": handle.agent_id,
        "lineage_id": handle.lineage_id,
        "attempt_number": handle.attempt_number,
        "resumed_from_request_id": handle.resumed_from_request_id,
        "parent_session_id": handle.parent_session_id,
        "child_session_id": handle.child_session_id,
        "child_session_path": handle.child_session_path,
        "status": (status or handle.status).value,
    }
    if role is not None:
        payload["role"] = role
    if receipt_id is not None:
        payload["receipt_id"] = receipt_id
    if error is not None:
        payload["error"] = error.model_dump(mode="json")
    store.append_new(EventType.CUSTOM, payload)


def _resume_task(source: _BackgroundRun, instruction: str) -> str:
    """新 attempt 只引用旧终态与不可变工件；不复制旧 tool calls，也不声称副作用已回滚。"""
    result = source.result
    if result is None:
        raise SubagentRuntimeError("resume source 缺少终态结果")
    artifacts = "\n".join(
        f"- {record.kind}: {record.path} sha256={record.sha256}"
        for record in result.artifacts
    ) or "- 无"
    return (
        "这是显式创建的新 attempt。不得假设或重放上一 attempt 的任何工具副作用；"
        "先检查当前外部状态，再决定是否执行新的写操作。\n\n"
        f"上一 attempt request_id: {source.request.request_id}\n"
        f"上一 attempt status: {result.status.value}\n"
        f"上一 attempt summary: {result.summary or result.hint}\n"
        f"上一 attempt immutable artifacts:\n{artifacts}\n\n"
        f"本次恢复指令:\n{instruction}"
    )


def _truncate_hint(value: str) -> str:
    return value[:200]


def _receipt_id(request_id: str, agent_id: str, status: SubagentStatus) -> str:
    raw = f"subagent-v1:{request_id}:{agent_id}:{status.value}".encode()
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "AGENTS_DIR",
    "AgentProfile",
    "ChildRunOutput",
    "ChildRunner",
    "LocalSubagentRuntime",
    "SubagentRuntimeError",
    "load_agent_profile",
]
