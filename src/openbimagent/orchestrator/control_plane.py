"""P1d 跨进程只读 Control Plane。

该模块只读取 RuntimeState 与 Session 审计事实，不获取 Runtime lease，也不提供写操作。
控制写入仍必须由持有 lease 的 LocalSubagentRuntime 执行。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from openbimagent.orchestrator.actor import ActorRef
from openbimagent.orchestrator.approval import ApprovalRequest, DecisionReceipt
from openbimagent.orchestrator.control import ResumeReceipt, ResumeRequest, SteerDirective, SteerReceipt, SteerStatus
from openbimagent.orchestrator.contracts import SubagentStatus
from openbimagent.orchestrator.state import RuntimePhase, RuntimeStateRecord, RuntimeStateStore
from openbimagent.session.schema import CustomType, EventType
from openbimagent.session.store import SessionStore

CONTROL_PLANE_VERSION = "1.0"
_TERMINAL_STEER = {
    SteerStatus.APPLIED,
    SteerStatus.REJECTED,
    SteerStatus.SUPERSEDED,
    SteerStatus.RUNTIME_RESTARTED,
}


class ControlPlaneError(RuntimeError):
    """查询目标不存在或持久审计事实冲突。"""


class AttemptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_plane_version: str = CONTROL_PLANE_VERSION
    request_id: str
    agent_id: str
    parent_session_id: str
    child_session_id: str
    role: str
    lineage_id: str
    attempt_number: int
    resumed_from_request_id: str | None
    status: SubagentStatus
    phase: RuntimePhase
    updated_at: datetime
    result_hint: str
    error_code: str | None
    receipt_id: str | None
    artifact_count: int


class ApprovalView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_plane_version: str = CONTROL_PLANE_VERSION
    approval_id: str
    request_id: str
    agent_id: str
    parent_session_id: str
    child_session_id: str
    tool_name: str
    permission_key: str
    args_summary: str
    args_sha256: str
    requested_at: datetime
    pending: bool
    decision: str | None
    decided_by: ActorRef | None
    reason: str
    decided_at: datetime | None
    receipt_id: str | None


class ResumeView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_plane_version: str = CONTROL_PLANE_VERSION
    resume_id: str
    source_request_id: str
    new_request_id: str
    lineage_id: str
    attempt_number: int
    instruction_sha256: str
    idempotency_key: str
    requested_by: ActorRef
    requested_at: datetime
    receipt_id: str | None
    new_agent_id: str | None
    new_child_session_id: str | None
    created_at: datetime | None


class SteerView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_plane_version: str = CONTROL_PLANE_VERSION
    steer_id: str
    request_id: str
    agent_id: str
    child_session_id: str
    lineage_id: str
    attempt_number: int
    instruction_sha256: str
    requested_by: ActorRef
    requested_at: datetime
    statuses: tuple[SteerStatus, ...]
    latest_status: SteerStatus | None
    latest_reason: str
    latest_receipt_id: str | None
    latest_at: datetime | None


class ReadOnlyControlPlane:
    """Runtime 的持久化只读投影；可与活跃 Runtime 并行查询。"""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = Path(sessions_dir).resolve()
        self.state_store = RuntimeStateStore(self.sessions_dir / "_runtime")

    def list_attempts(
        self,
        *,
        lineage_id: str | None = None,
        status: SubagentStatus | str | None = None,
        parent_session_id: str | None = None,
    ) -> tuple[AttemptView, ...]:
        normalized_status = SubagentStatus(status) if status is not None else None
        views = [self._attempt_view(record) for record in self.state_store.load_all()]
        if lineage_id is not None:
            views = [view for view in views if view.lineage_id == lineage_id]
        if normalized_status is not None:
            views = [view for view in views if view.status is normalized_status]
        if parent_session_id is not None:
            views = [view for view in views if view.parent_session_id == parent_session_id]
        return tuple(sorted(views, key=lambda item: (item.lineage_id, item.attempt_number, item.request_id)))

    def get_attempt(self, request_id: str) -> AttemptView:
        path = self.state_store.path_for(request_id)
        if not path.is_file():
            raise ControlPlaneError(f"未知 request_id: {request_id}")
        return self._attempt_view(self.state_store.load(request_id))

    def get_lineage(self, lineage_id: str) -> tuple[AttemptView, ...]:
        attempts = self.list_attempts(lineage_id=lineage_id)
        if not attempts:
            raise ControlPlaneError(f"未知 lineage_id: {lineage_id}")
        return attempts

    def list_approvals(
        self,
        *,
        request_id: str | None = None,
        pending_only: bool = False,
    ) -> tuple[ApprovalView, ...]:
        facts = self._facts()
        requests: dict[str, ApprovalRequest] = facts[CustomType.APPROVAL_REQUESTED]
        receipts: dict[str, DecisionReceipt] = facts[CustomType.APPROVAL_DECIDED]
        orphaned = set(receipts) - set(requests)
        if orphaned:
            raise ControlPlaneError("decision receipt 缺少 approval request: " + ", ".join(sorted(orphaned)))
        views: list[ApprovalView] = []
        for approval_id, approval in requests.items():
            receipt = receipts.get(approval_id)
            if receipt is not None and (
                receipt.request_id != approval.request_id or receipt.agent_id != approval.agent_id
            ):
                raise ControlPlaneError(f"审批 {approval_id} 的 request/receipt 身份不一致")
            view = ApprovalView(
                approval_id=approval_id,
                request_id=approval.request_id,
                agent_id=approval.agent_id,
                parent_session_id=approval.parent_session_id,
                child_session_id=approval.child_session_id,
                tool_name=approval.tool_name,
                permission_key=approval.permission_key,
                args_summary=approval.args_summary,
                args_sha256=approval.args_sha256,
                requested_at=approval.requested_at,
                pending=receipt is None,
                decision=receipt.decision.value if receipt else None,
                decided_by=receipt.decided_by if receipt else None,
                reason=receipt.reason if receipt else "",
                decided_at=receipt.decided_at if receipt else None,
                receipt_id=receipt.receipt_id if receipt else None,
            )
            if request_id is not None and view.request_id != request_id:
                continue
            if pending_only and not view.pending:
                continue
            views.append(view)
        return tuple(sorted(views, key=lambda item: (item.requested_at, item.approval_id)))

    def list_resumes(self, *, lineage_id: str | None = None) -> tuple[ResumeView, ...]:
        facts = self._facts()
        requests: dict[str, ResumeRequest] = facts[CustomType.RESUME_REQUESTED]
        receipts: dict[str, ResumeReceipt] = facts[CustomType.RESUME_RECEIPT]
        views: list[ResumeView] = []
        for resume_id, request in requests.items():
            receipt = receipts.get(resume_id)
            if receipt is not None and (
                receipt.source_request_id != request.source_request_id
                or receipt.new_request_id != request.new_request_id
                or receipt.lineage_id != request.lineage_id
                or receipt.attempt_number != request.attempt_number
            ):
                raise ControlPlaneError(f"Resume {resume_id} 的 request/receipt 身份不一致")
            if lineage_id is not None and request.lineage_id != lineage_id:
                continue
            views.append(
                ResumeView(
                    resume_id=resume_id,
                    source_request_id=request.source_request_id,
                    new_request_id=request.new_request_id,
                    lineage_id=request.lineage_id,
                    attempt_number=request.attempt_number,
                    instruction_sha256=request.instruction_sha256,
                    idempotency_key=request.idempotency_key,
                    requested_by=request.requested_by,
                    requested_at=request.requested_at,
                    receipt_id=receipt.receipt_id if receipt else None,
                    new_agent_id=receipt.new_agent_id if receipt else None,
                    new_child_session_id=receipt.new_child_session_id if receipt else None,
                    created_at=receipt.created_at if receipt else None,
                )
            )
        orphaned = set(receipts) - set(requests)
        if orphaned:
            raise ControlPlaneError("resume receipt 缺少 request: " + ", ".join(sorted(orphaned)))
        return tuple(sorted(views, key=lambda item: (item.lineage_id, item.attempt_number, item.resume_id)))

    def list_steers(self, *, request_id: str | None = None) -> tuple[SteerView, ...]:
        facts = self._facts()
        directives: dict[str, SteerDirective] = facts[CustomType.STEER_REQUESTED]
        receipts_by_steer: dict[str, list[SteerReceipt]] = facts[CustomType.STEER_RECEIPT]
        orphaned = set(receipts_by_steer) - set(directives)
        if orphaned:
            raise ControlPlaneError("steer receipt 缺少 directive: " + ", ".join(sorted(orphaned)))
        views: list[SteerView] = []
        for steer_id, directive in directives.items():
            receipts = sorted(receipts_by_steer.get(steer_id, ()), key=lambda item: (item.created_at, item.receipt_id))
            terminal = [receipt for receipt in receipts if receipt.status in _TERMINAL_STEER]
            if len({receipt.status for receipt in terminal}) > 1:
                raise ControlPlaneError(f"Steer {steer_id} 存在冲突终态")
            for receipt in receipts:
                if receipt.request_id != directive.request_id or receipt.agent_id != directive.agent_id:
                    raise ControlPlaneError(f"Steer {steer_id} 的 directive/receipt 身份不一致")
            latest = receipts[-1] if receipts else None
            view = SteerView(
                steer_id=steer_id,
                request_id=directive.request_id,
                agent_id=directive.agent_id,
                child_session_id=directive.child_session_id,
                lineage_id=directive.lineage_id,
                attempt_number=directive.attempt_number,
                instruction_sha256=directive.instruction_sha256,
                requested_by=directive.requested_by,
                requested_at=directive.requested_at,
                statuses=tuple(receipt.status for receipt in receipts),
                latest_status=latest.status if latest else None,
                latest_reason=latest.reason if latest else "",
                latest_receipt_id=latest.receipt_id if latest else None,
                latest_at=latest.created_at if latest else None,
            )
            if request_id is None or view.request_id == request_id:
                views.append(view)
        return tuple(sorted(views, key=lambda item: (item.requested_at, item.steer_id)))

    @staticmethod
    def _attempt_view(record: RuntimeStateRecord) -> AttemptView:
        result = record.result
        return AttemptView(
            request_id=record.request.request_id,
            agent_id=record.handle.agent_id,
            parent_session_id=record.request.parent_session_id,
            child_session_id=record.handle.child_session_id,
            role=record.request.role,
            lineage_id=record.request.lineage_id,
            attempt_number=record.request.attempt_number,
            resumed_from_request_id=record.request.resumed_from_request_id,
            status=record.status,
            phase=record.phase,
            updated_at=record.updated_at,
            result_hint=result.hint if result else "",
            error_code=result.error.code if result and result.error else None,
            receipt_id=result.receipt_id if result else None,
            artifact_count=len(result.artifacts) if result else 0,
        )

    def _facts(self) -> dict[CustomType, Any]:
        stores = self._session_stores()
        facts: dict[CustomType, Any] = {
            CustomType.APPROVAL_REQUESTED: {},
            CustomType.APPROVAL_DECIDED: {},
            CustomType.RESUME_REQUESTED: {},
            CustomType.RESUME_RECEIPT: {},
            CustomType.STEER_REQUESTED: {},
            CustomType.STEER_RECEIPT: {},
        }
        for store in stores:
            for event in store.load():
                if event.type is not EventType.CUSTOM or event.payload.customType not in facts:
                    continue
                data = event.payload.model_dump(mode="json")
                custom_type = event.payload.customType
                if custom_type is CustomType.APPROVAL_REQUESTED:
                    model = _model_from_payload(ApprovalRequest, data)
                    _merge_fact(facts[custom_type], model.approval_id, model, store.session_id)
                elif custom_type is CustomType.APPROVAL_DECIDED:
                    model = _model_from_payload(DecisionReceipt, data)
                    _merge_fact(facts[custom_type], model.approval_id, model, store.session_id)
                elif custom_type is CustomType.RESUME_REQUESTED:
                    model = _model_from_payload(ResumeRequest, data)
                    _merge_fact(facts[custom_type], model.resume_id, model, store.session_id)
                elif custom_type is CustomType.RESUME_RECEIPT:
                    model = _model_from_payload(ResumeReceipt, data)
                    _merge_fact(facts[custom_type], model.resume_id, model, store.session_id)
                elif custom_type is CustomType.STEER_REQUESTED:
                    model = _model_from_payload(SteerDirective, data)
                    _merge_fact(facts[custom_type], model.steer_id, model, store.session_id)
                elif custom_type is CustomType.STEER_RECEIPT:
                    model = _model_from_payload(SteerReceipt, data)
                    bucket = facts[custom_type].setdefault(model.steer_id, {})
                    _merge_fact(bucket, model.receipt_id, model, store.session_id)
        facts[CustomType.STEER_RECEIPT] = {
            steer_id: list(receipts.values())
            for steer_id, receipts in facts[CustomType.STEER_RECEIPT].items()
        }
        return facts

    def _session_stores(self) -> tuple[SessionStore, ...]:
        paths: set[Path] = set()
        for record in self.state_store.load_all():
            paths.add(self.sessions_dir / f"{record.request.parent_session_id}.jsonl")
            paths.add(Path(record.handle.child_session_path))
        missing = sorted(str(path) for path in paths if not path.is_file())
        if missing:
            raise ControlPlaneError("Runtime 状态引用的 Session 不存在: " + ", ".join(missing))
        return tuple(SessionStore(path) for path in sorted(paths))


def _model_from_payload(model_type: type[BaseModel], payload: dict[str, Any]) -> Any:
    try:
        return model_type.model_validate({key: payload[key] for key in model_type.model_fields if key in payload})
    except Exception as exc:
        identity = payload.get("approval_id") or payload.get("resume_id") or payload.get("steer_id") or "unknown"
        raise ControlPlaneError(f"Session 审计事实无效或冲突: identity={identity}: {exc}") from exc


def _merge_fact(target: dict[str, Any], identity: str, fact: Any, session_id: str) -> None:
    existing = target.get(identity)
    if existing is not None and existing != fact:
        raise ControlPlaneError(f"Session 审计事实冲突: identity={identity}, session={session_id}")
    target[identity] = fact


__all__ = [
    "CONTROL_PLANE_VERSION",
    "ApprovalView",
    "AttemptView",
    "ControlPlaneError",
    "ReadOnlyControlPlane",
    "ResumeView",
    "SteerView",
]
