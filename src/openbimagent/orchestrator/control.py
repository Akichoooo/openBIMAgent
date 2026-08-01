"""Subagent Runtime v1 P1c resume/steer 控制协议。

resume 永远创建新 attempt；steer 仅绑定单个活跃 attempt，并在 AgentLoop 的安全轮次边界消费。
Session 是持久审计事实源，进程内队列只承载当前 Runtime 可消费的指令。
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openbimagent.orchestrator.actor import ActorLike, ActorRef, actor_ref
from openbimagent.schema_gate.gate import gate_or_fix
from openbimagent.session.schema import CustomType, EventType, uuid7
from openbimagent.session.store import SessionStore

CONTROL_PROTOCOL_VERSION = "1.1"


class SteerStatus(StrEnum):
    ACCEPTED = "accepted"
    APPLIED = "applied"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RUNTIME_RESTARTED = "runtime_restarted"


class ResumeRequest(BaseModel):
    """从已终态 attempt 创建新 attempt 的版本化请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=CONTROL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    resume_id: str = Field(min_length=1)
    source_request_id: str = Field(min_length=1)
    source_agent_id: str = Field(min_length=1)
    source_child_session_id: str = Field(min_length=1)
    new_request_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=2)
    instruction: str = Field(min_length=1, max_length=20_000)
    instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")
    requested_by: ActorRef
    requested_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_request(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        instruction = upgraded.get("instruction")
        if isinstance(instruction, str):
            upgraded.setdefault("instruction_sha256", hashlib.sha256(instruction.encode("utf-8")).hexdigest())
        upgraded.setdefault(
            "idempotency_key",
            f"legacy:{upgraded.get('source_request_id', 'unknown')}:{upgraded.get('resume_id', 'unknown')}",
        )
        requested_by = upgraded.get("requested_by")
        if isinstance(requested_by, str):
            upgraded["requested_by"] = ActorRef.legacy(requested_by).model_dump(mode="json")
        return upgraded

    @model_validator(mode="after")
    def _instruction_hash_matches(self) -> "ResumeRequest":
        expected = hashlib.sha256(self.instruction.encode("utf-8")).hexdigest()
        if self.instruction_sha256 != expected:
            raise ValueError("instruction_sha256 与 instruction 不一致")
        return self


class ResumeReceipt(BaseModel):
    """新 attempt 已创建的稳定回执。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=CONTROL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    receipt_id: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    source_request_id: str = Field(min_length=1)
    new_request_id: str = Field(min_length=1)
    new_agent_id: str = Field(min_length=1)
    new_child_session_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=2)
    created_at: datetime


class SteerDirective(BaseModel):
    """仅绑定一个 request/agent/child attempt 的运行中指令。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=CONTROL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    steer_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    child_session_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=20_000)
    instruction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: ActorRef
    requested_at: datetime

    @field_validator("requested_by", mode="before")
    @classmethod
    def _read_legacy_actor(cls, value: object) -> object:
        return ActorRef.legacy(value) if isinstance(value, str) else value

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        agent_id: str,
        child_session_id: str,
        lineage_id: str,
        attempt_number: int,
        instruction: str,
        requested_by: ActorLike,
    ) -> "SteerDirective":
        return cls(
            steer_id=str(uuid7()),
            request_id=request_id,
            agent_id=agent_id,
            child_session_id=child_session_id,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            instruction=instruction,
            instruction_sha256=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            requested_by=actor_ref(requested_by),
            requested_at=datetime.now(timezone.utc),
        )


class SteerReceipt(BaseModel):
    """steer 的 accepted/applied/rejected 等稳定回执。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=CONTROL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    receipt_id: str = Field(min_length=1)
    steer_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    status: SteerStatus
    reason: str = Field(default="", max_length=1000)
    created_at: datetime


class SteerQueue:
    """当前 Runtime 的线程安全 steer 队列；不从历史 Session 自动重建。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queues: dict[str, list[SteerDirective]] = {}
        self._receipts: dict[tuple[str, SteerStatus], SteerReceipt] = {}

    def accept(
        self,
        directive: SteerDirective,
        *,
        parent_session: SessionStore,
        child_session: SessionStore,
    ) -> SteerReceipt:
        gate_or_fix("steer_directive", directive.model_dump(mode="json"))
        with self._lock:
            receipt = self._make_receipt(directive, SteerStatus.ACCEPTED)
            # 先把请求与 accepted receipt 持久化，再让消费端看到内存指令；
            # consume() 共用该锁，因此不会错过正在提交的安全轮次边界。
            _append_steer_requested(parent_session, child_session, directive)
            _append_steer_receipt(parent_session, child_session, receipt)
            self._queues.setdefault(directive.request_id, []).append(directive)
            return receipt

    def consume(self, request_id: str) -> tuple[SteerDirective, ...]:
        with self._lock:
            return tuple(self._queues.pop(request_id, ()))

    def settle(
        self,
        directive: SteerDirective,
        *,
        status: SteerStatus,
        parent_session: SessionStore,
        child_session: SessionStore,
        reason: str = "",
    ) -> SteerReceipt:
        if status is SteerStatus.ACCEPTED:
            raise ValueError("settle() 不能再次签发 accepted")
        with self._lock:
            receipt = self._make_receipt(directive, status, reason=reason)
        _append_steer_receipt(parent_session, child_session, receipt)
        return receipt

    def reject_pending(
        self,
        request_id: str,
        *,
        status: SteerStatus,
        parent_session: SessionStore,
        child_session: SessionStore,
        reason: str,
    ) -> tuple[SteerReceipt, ...]:
        directives = self.consume(request_id)
        return tuple(
            self.settle(
                directive,
                status=status,
                parent_session=parent_session,
                child_session=child_session,
                reason=reason,
            )
            for directive in directives
        )

    def close_orphaned(
        self,
        *,
        request_id: str,
        agent_id: str,
        parent_session: SessionStore,
        child_session: SessionStore,
    ) -> tuple[SteerReceipt, ...]:
        """重启时关闭已 accepted 但未终结的 steer；绝不重新入队。"""
        requested: dict[str, SteerDirective] = {}
        observed_receipts: dict[tuple[str, SteerStatus], SteerReceipt] = {}
        terminal_by_steer: dict[str, SteerReceipt] = {}
        for store in (parent_session, child_session):
            for event in store.load():
                if event.type is not EventType.CUSTOM:
                    continue
                data = event.payload.model_dump(mode="json")
                if event.payload.customType is CustomType.STEER_REQUESTED:
                    directive = _directive_from_payload(data)
                    existing = requested.get(directive.steer_id)
                    if existing is not None and existing != directive:
                        raise RuntimeError(f"父子 Session steer 冲突: {directive.steer_id}")
                    requested[directive.steer_id] = directive
                elif event.payload.customType is CustomType.STEER_RECEIPT:
                    receipt = _receipt_from_payload(data)
                    key = (receipt.steer_id, receipt.status)
                    existing = observed_receipts.get(key)
                    if existing is not None and existing != receipt:
                        raise RuntimeError(f"父子 Session steer receipt 冲突: {receipt.receipt_id}")
                    observed_receipts[key] = receipt
                    if receipt.status is not SteerStatus.ACCEPTED:
                        terminal = terminal_by_steer.get(receipt.steer_id)
                        if terminal is not None and terminal != receipt:
                            raise RuntimeError(f"同一 steer 存在冲突终态: {receipt.steer_id}")
                        terminal_by_steer[receipt.steer_id] = receipt

        for directive in requested.values():
            _append_steer_requested(parent_session, child_session, directive)
        for receipt in observed_receipts.values():
            _append_steer_receipt(parent_session, child_session, receipt)

        receipts: list[SteerReceipt] = []
        for directive in requested.values():
            if (
                directive.request_id != request_id
                or directive.agent_id != agent_id
                or directive.steer_id in terminal_by_steer
            ):
                continue
            receipts.append(
                self.settle(
                    directive,
                    status=SteerStatus.RUNTIME_RESTARTED,
                    parent_session=parent_session,
                    child_session=child_session,
                    reason="runtime restarted before steer reached a safe turn boundary",
                )
            )
        return tuple(receipts)

    def _make_receipt(
        self,
        directive: SteerDirective,
        status: SteerStatus,
        *,
        reason: str = "",
    ) -> SteerReceipt:
        key = (directive.steer_id, status)
        existing = self._receipts.get(key)
        if existing is not None:
            return existing
        raw = f"steer-v1:{directive.steer_id}:{status.value}".encode("utf-8")
        receipt = SteerReceipt(
            receipt_id=hashlib.sha256(raw).hexdigest(),
            steer_id=directive.steer_id,
            request_id=directive.request_id,
            agent_id=directive.agent_id,
            status=status,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
        gate_or_fix("steer_receipt", receipt.model_dump(mode="json"))
        self._receipts[key] = receipt
        return receipt


def make_resume_request(
    *,
    source_request_id: str,
    source_agent_id: str,
    source_child_session_id: str,
    new_request_id: str,
    lineage_id: str,
    attempt_number: int,
    instruction: str,
    idempotency_key: str,
    requested_by: ActorLike,
) -> ResumeRequest:
    request = ResumeRequest(
        resume_id=str(uuid7()),
        source_request_id=source_request_id,
        source_agent_id=source_agent_id,
        source_child_session_id=source_child_session_id,
        new_request_id=new_request_id,
        lineage_id=lineage_id,
        attempt_number=attempt_number,
        instruction=instruction,
        instruction_sha256=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        idempotency_key=idempotency_key,
        requested_by=actor_ref(requested_by),
        requested_at=datetime.now(timezone.utc),
    )
    gate_or_fix("resume_request", request.model_dump(mode="json"))
    return request


def make_resume_receipt(request: ResumeRequest, *, new_agent_id: str, new_child_session_id: str) -> ResumeReceipt:
    raw = f"resume-v1:{request.resume_id}:{request.new_request_id}:{new_agent_id}".encode("utf-8")
    receipt = ResumeReceipt(
        receipt_id=hashlib.sha256(raw).hexdigest(),
        resume_id=request.resume_id,
        source_request_id=request.source_request_id,
        new_request_id=request.new_request_id,
        new_agent_id=new_agent_id,
        new_child_session_id=new_child_session_id,
        lineage_id=request.lineage_id,
        attempt_number=request.attempt_number,
        created_at=datetime.now(timezone.utc),
    )
    gate_or_fix("resume_receipt", receipt.model_dump(mode="json"))
    return receipt


def append_resume_events(
    *,
    parent_session: SessionStore,
    source_child_session: SessionStore,
    new_child_session: SessionStore,
    request: ResumeRequest,
    receipt: ResumeReceipt,
) -> None:
    requested_payload = {"customType": CustomType.RESUME_REQUESTED, **request.model_dump(mode="json")}
    receipt_payload = {"customType": CustomType.RESUME_RECEIPT, **receipt.model_dump(mode="json")}
    for store in (parent_session, source_child_session, new_child_session):
        _append_control_event_once(
            store,
            custom_type=CustomType.RESUME_REQUESTED,
            identity_key="resume_id",
            identity_value=request.resume_id,
            payload=requested_payload,
        )
        _append_control_event_once(
            store,
            custom_type=CustomType.RESUME_RECEIPT,
            identity_key="receipt_id",
            identity_value=receipt.receipt_id,
            payload=receipt_payload,
        )


def _append_steer_requested(
    parent_session: SessionStore,
    child_session: SessionStore,
    directive: SteerDirective,
) -> None:
    payload = {"customType": CustomType.STEER_REQUESTED, **directive.model_dump(mode="json")}
    for store in (parent_session, child_session):
        _append_control_event_once(
            store,
            custom_type=CustomType.STEER_REQUESTED,
            identity_key="steer_id",
            identity_value=directive.steer_id,
            payload=payload,
        )


def _append_steer_receipt(
    parent_session: SessionStore,
    child_session: SessionStore,
    receipt: SteerReceipt,
) -> None:
    payload = {"customType": CustomType.STEER_RECEIPT, **receipt.model_dump(mode="json")}
    for store in (parent_session, child_session):
        _append_control_event_once(
            store,
            custom_type=CustomType.STEER_RECEIPT,
            identity_key="receipt_id",
            identity_value=receipt.receipt_id,
            payload=payload,
        )


def _append_control_event_once(
    store: SessionStore,
    *,
    custom_type: CustomType,
    identity_key: str,
    identity_value: str,
    payload: dict[str, object],
) -> None:
    """按协议身份幂等补写控制事件；同身份不同内容严格失败。"""
    matches = [
        event.payload.model_dump(mode="json")
        for event in store.load()
        if event.type is EventType.CUSTOM
        and event.payload.customType is custom_type
        and event.payload.model_dump().get(identity_key) == identity_value
    ]
    if not matches:
        store.append_new(EventType.CUSTOM, payload)
        return
    if any(existing != payload for existing in matches):
        raise RuntimeError(
            f"Session 控制事件冲突: customType={custom_type.value}, {identity_key}={identity_value}"
        )


def _directive_from_payload(payload: dict[str, object]) -> SteerDirective:
    return SteerDirective.model_validate(
        {key: payload[key] for key in SteerDirective.model_fields}
    )


def _receipt_from_payload(payload: dict[str, object]) -> SteerReceipt:
    return SteerReceipt.model_validate(
        {key: payload[key] for key in SteerReceipt.model_fields}
    )


__all__ = [
    "CONTROL_PROTOCOL_VERSION",
    "ResumeReceipt",
    "ResumeRequest",
    "SteerDirective",
    "SteerQueue",
    "SteerReceipt",
    "SteerStatus",
    "append_resume_events",
    "make_resume_receipt",
    "make_resume_request",
]
