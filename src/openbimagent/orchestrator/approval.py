"""Subagent Runtime v1 P1b-B Approval Broker。

child AgentLoop 的 Permission.ASK 不直接读取 stdin，而是向父 Session 发布审批请求；
父侧可同步回调或通过 decide() 异步决策。事件只保存参数摘要与 SHA-256，不保存原始参数。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openbimagent.orchestrator.actor import ActorLike, ActorRef, ActorType, actor_ref
from openbimagent.schema_gate.gate import gate_or_fix
from openbimagent.session.schema import CustomType, EventType, uuid7
from openbimagent.session.store import SessionStore

APPROVAL_PROTOCOL_VERSION = "1.1"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RUNTIME_RESTARTED = "runtime_restarted"


class ApprovalRequest(BaseModel):
    """写入父/子 Session 的最小审批请求，不含原始工具参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=APPROVAL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    approval_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    child_session_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    permission_key: str = Field(min_length=1)
    args_summary: str = Field(max_length=500)
    args_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_at: datetime


class DecisionReceipt(BaseModel):
    """稳定、可幂等重放的审批决策回执。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=APPROVAL_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    receipt_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    decision: ApprovalDecision
    decided_by: ActorRef
    reason: str = Field(default="", max_length=1000)
    decided_at: datetime

    @field_validator("decided_by", mode="before")
    @classmethod
    def _read_legacy_actor(cls, value: object) -> object:
        return ActorRef.legacy(value) if isinstance(value, str) else value

    @property
    def approved(self) -> bool:
        return self.decision is ApprovalDecision.APPROVED


class ApprovalBrokerError(RuntimeError):
    """审批不存在、重复冲突或上下文无效。"""


ApprovalCallback = Callable[[str, dict[str, Any]], bool]


class _PendingApproval:
    def __init__(
        self,
        request: ApprovalRequest,
        *,
        parent_session: SessionStore,
        child_session: SessionStore,
    ) -> None:
        self.request = request
        self.parent_session = parent_session
        self.child_session = child_session
        self.event = threading.Event()
        self.receipt: DecisionReceipt | None = None


class ApprovalBroker:
    """线程安全的父会话审批中介；Session 事件是持久审计事实源。"""

    def __init__(
        self,
        *,
        approval_callback: ApprovalCallback | None = None,
        default_timeout_s: float | None = 300.0,
    ) -> None:
        if default_timeout_s is not None and default_timeout_s < 0:
            raise ValueError("default_timeout_s 不能为负数")
        self.approval_callback = approval_callback
        self.default_timeout_s = default_timeout_s
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingApproval] = {}
        self._receipts: dict[str, DecisionReceipt] = {}

    def request(
        self,
        *,
        request_id: str,
        agent_id: str,
        parent_session: SessionStore,
        child_session: SessionStore,
        tool_name: str,
        permission_key: str,
        args: dict[str, Any],
        cancel_event: threading.Event | None = None,
        timeout_s: float | None = None,
    ) -> bool:
        """发布审批并等待决策；取消、超时和回调异常全部失败关闭。"""
        encoded = _canonical_args(args)
        approval = ApprovalRequest(
            approval_id=str(uuid7()),
            request_id=request_id,
            agent_id=agent_id,
            parent_session_id=parent_session.session_id,
            child_session_id=child_session.session_id,
            tool_name=tool_name,
            permission_key=permission_key,
            args_summary=_summarize_args(args),
            args_sha256=hashlib.sha256(encoded).hexdigest(),
            requested_at=datetime.now(timezone.utc),
        )
        gate_or_fix("approval_request", approval.model_dump(mode="json"))
        pending = _PendingApproval(
            approval,
            parent_session=parent_session,
            child_session=child_session,
        )
        with self._lock:
            self._pending[approval.approval_id] = pending
        self._append_requested_once(pending)

        if self.approval_callback is not None:
            try:
                approved = bool(self.approval_callback(tool_name, args))
            except Exception as exc:
                self._settle_system_decision(
                    approval.approval_id,
                    decision=ApprovalDecision.REJECTED,
                    decided_by=ActorRef(actor_id="service:approval-broker", actor_type=ActorType.SERVICE),
                    reason=f"approval callback failed: {type(exc).__name__}",
                )
            else:
                self._settle_system_decision(
                    approval.approval_id,
                    decision=ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED,
                    decided_by=ActorRef(actor_id="service:parent-callback", actor_type=ActorType.SERVICE),
                )

        effective_timeout = self.default_timeout_s if timeout_s is None else timeout_s
        deadline = None if effective_timeout is None else time.monotonic() + effective_timeout
        while not pending.event.wait(timeout=0.05):
            if cancel_event is not None and cancel_event.is_set():
                self._settle_system_decision(
                    approval.approval_id,
                    decision=ApprovalDecision.CANCELLED,
                    decided_by=ActorRef(actor_id="runtime:local", actor_type=ActorType.RUNTIME),
                    reason="subagent cancelled while waiting for approval",
                )
                break
            if deadline is not None and time.monotonic() >= deadline:
                self._settle_system_decision(
                    approval.approval_id,
                    decision=ApprovalDecision.TIMED_OUT,
                    decided_by=ActorRef(actor_id="service:approval-broker", actor_type=ActorType.SERVICE),
                    reason="approval timed out",
                )
                break
        with self._lock:
            receipt = pending.receipt
            self._pending.pop(approval.approval_id, None)
        if receipt is None:
            raise ApprovalBrokerError(f"审批结束但没有 decision receipt: {approval.approval_id}")
        return receipt.approved

    def pending(self, *, request_id: str | None = None) -> tuple[ApprovalRequest, ...]:
        with self._lock:
            requests = [item.request for item in self._pending.values() if item.receipt is None]
        if request_id is not None:
            requests = [item for item in requests if item.request_id == request_id]
        return tuple(sorted(requests, key=lambda item: (item.requested_at, item.approval_id)))

    def receipt(self, approval_id: str) -> DecisionReceipt | None:
        with self._lock:
            return self._receipts.get(approval_id)

    def decide(
        self,
        approval_id: str,
        *,
        decision: ApprovalDecision | str,
        decided_by: ActorLike,
        reason: str = "",
    ) -> DecisionReceipt:
        """决策一次；同一 decision 重放返回原 receipt，冲突决策拒绝。"""
        normalized = ApprovalDecision(decision)
        with self._lock:
            existing = self._receipts.get(approval_id)
            if existing is not None:
                if existing.decision is not normalized:
                    raise ApprovalBrokerError(
                        f"审批 {approval_id} 已是 {existing.decision.value}，不能改为 {normalized.value}"
                    )
                pending = self._pending.get(approval_id)
                if pending is not None:
                    self._append_decided_once(pending, existing)
                    pending.event.set()
                return existing
            pending = self._pending.get(approval_id)
            if pending is None:
                raise ApprovalBrokerError(f"未知或已关闭 approval_id: {approval_id}")
            receipt = _make_receipt(
                pending.request,
                decision=normalized,
                decided_by=decided_by,
                reason=reason,
            )
            gate_or_fix("decision_receipt", receipt.model_dump(mode="json"))
            pending.receipt = receipt
            self._receipts[approval_id] = receipt
            self._append_decided_once(pending, receipt)
            pending.event.set()
            return receipt

    def _settle_system_decision(
        self,
        approval_id: str,
        *,
        decision: ApprovalDecision,
        decided_by: ActorLike,
        reason: str = "",
    ) -> DecisionReceipt:
        """取消/超时等系统决策与父决策竞态时采用 first-decision-wins。"""
        try:
            return self.decide(
                approval_id,
                decision=decision,
                decided_by=decided_by,
                reason=reason,
            )
        except ApprovalBrokerError:
            existing = self.receipt(approval_id)
            if existing is None:
                raise
            return existing

    def close_orphaned(
        self,
        *,
        request_id: str,
        agent_id: str,
        parent_session: SessionStore,
        child_session: SessionStore,
    ) -> tuple[DecisionReceipt, ...]:
        """对账父子 Session；复用既有决策补齐单边写入，否则失败关闭。"""
        parent_requests, parent_receipts = _load_approval_facts(parent_session)
        child_requests, child_receipts = _load_approval_facts(child_session)
        requests = _merge_matching_facts(parent_requests, child_requests, fact_name="approval request")
        known_receipts = _merge_matching_facts(parent_receipts, child_receipts, fact_name="decision receipt")
        receipt_without_request = set(known_receipts) - set(requests)
        if receipt_without_request:
            raise ApprovalBrokerError(
                "decision receipt 缺少对应 approval request: "
                + ", ".join(sorted(receipt_without_request))
            )
        recovered: list[DecisionReceipt] = []
        for approval_id, approval in requests.items():
            if approval.request_id != request_id or approval.agent_id != agent_id:
                continue
            request_complete = approval_id in parent_requests and approval_id in child_requests
            receipt_complete = approval_id in parent_receipts and approval_id in child_receipts
            pending = _PendingApproval(
                approval,
                parent_session=parent_session,
                child_session=child_session,
            )
            self._append_requested_once(pending)
            receipt = known_receipts.get(approval_id)
            if receipt is None:
                receipt = _make_receipt(
                    approval,
                    decision=ApprovalDecision.RUNTIME_RESTARTED,
                    decided_by=ActorRef(actor_id="runtime:recovery", actor_type=ActorType.RUNTIME),
                    reason="runtime restarted before approval was decided",
                )
            elif receipt.request_id != approval.request_id or receipt.agent_id != approval.agent_id:
                raise ApprovalBrokerError(f"审批 {approval_id} 的 request 与 decision receipt 身份不一致")
            gate_or_fix("decision_receipt", receipt.model_dump(mode="json"))
            pending.receipt = receipt
            self._append_decided_once(pending, receipt)
            with self._lock:
                existing = self._receipts.get(approval_id)
                if existing is not None and existing != receipt:
                    raise ApprovalBrokerError(f"审批 {approval_id} 的内存与 Session decision receipt 冲突")
                self._receipts[approval_id] = receipt
            if not request_complete or not receipt_complete:
                recovered.append(receipt)
        return tuple(recovered)

    @staticmethod
    def _append_requested_once(pending: _PendingApproval) -> None:
        payload = {
            "customType": CustomType.APPROVAL_REQUESTED,
            **pending.request.model_dump(mode="json"),
        }
        for store in (pending.child_session, pending.parent_session):
            if not _has_approval_event(store, CustomType.APPROVAL_REQUESTED, pending.request.approval_id):
                store.append_new(EventType.CUSTOM, payload)

    @staticmethod
    def _append_decided_once(pending: _PendingApproval, receipt: DecisionReceipt) -> None:
        payload = {
            "customType": CustomType.APPROVAL_DECIDED,
            **receipt.model_dump(mode="json"),
            "parent_session_id": pending.request.parent_session_id,
            "child_session_id": pending.request.child_session_id,
            "tool_name": pending.request.tool_name,
            "permission_key": pending.request.permission_key,
            "args_sha256": pending.request.args_sha256,
        }
        for store in (pending.child_session, pending.parent_session):
            if not _has_receipt(store, receipt.receipt_id):
                store.append_new(EventType.CUSTOM, payload)


def _canonical_args(args: dict[str, Any]) -> bytes:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _summarize_args(args: dict[str, Any]) -> str:
    """仅记录字段结构与值类型，避免把 code/content/token 等原文写入审计事件。"""
    return json.dumps(
        {str(key): type(value).__name__ for key, value in sorted(args.items())},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:500]


def _make_receipt(
    request: ApprovalRequest,
    *,
    decision: ApprovalDecision,
    decided_by: ActorLike,
    reason: str,
) -> DecisionReceipt:
    raw = f"approval-v1:{request.approval_id}:{decision.value}".encode("utf-8")
    return DecisionReceipt(
        receipt_id=hashlib.sha256(raw).hexdigest(),
        approval_id=request.approval_id,
        request_id=request.request_id,
        agent_id=request.agent_id,
        decision=decision,
        decided_by=actor_ref(decided_by, default_type=ActorType.HUMAN),
        reason=reason,
        decided_at=datetime.now(timezone.utc),
    )


def _request_from_payload(payload: dict[str, Any]) -> ApprovalRequest:
    fields = ApprovalRequest.model_fields
    return ApprovalRequest.model_validate({key: payload[key] for key in fields})


def _receipt_from_payload(payload: dict[str, Any]) -> DecisionReceipt:
    fields = DecisionReceipt.model_fields
    return DecisionReceipt.model_validate({key: payload[key] for key in fields})


def _load_approval_facts(
    store: SessionStore,
) -> tuple[dict[str, ApprovalRequest], dict[str, DecisionReceipt]]:
    requests: dict[str, ApprovalRequest] = {}
    receipts: dict[str, DecisionReceipt] = {}
    for event in store.load():
        if event.type is not EventType.CUSTOM:
            continue
        data = event.payload.model_dump(mode="json")
        if event.payload.customType is CustomType.APPROVAL_REQUESTED:
            fact: ApprovalRequest | DecisionReceipt = _request_from_payload(data)
            target = requests
        elif event.payload.customType is CustomType.APPROVAL_DECIDED:
            fact = _receipt_from_payload(data)
            target = receipts
        else:
            continue
        existing = target.get(fact.approval_id)
        if existing is not None and existing != fact:
            raise ApprovalBrokerError(
                f"Session {store.session_id} 中 approval_id={fact.approval_id} 存在冲突事实"
            )
        target[fact.approval_id] = fact
    return requests, receipts


def _merge_matching_facts(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    fact_name: str,
) -> dict[str, Any]:
    merged = dict(primary)
    for approval_id, fact in secondary.items():
        existing = merged.get(approval_id)
        if existing is not None and existing != fact:
            raise ApprovalBrokerError(f"父子 Session 的 {fact_name} 冲突: approval_id={approval_id}")
        merged[approval_id] = fact
    return merged


def _has_approval_event(store: SessionStore, custom_type: CustomType, approval_id: str) -> bool:
    return any(
        event.type is EventType.CUSTOM
        and event.payload.customType is custom_type
        and event.payload.model_dump().get("approval_id") == approval_id
        for event in store.load()
    )


def _has_receipt(store: SessionStore, receipt_id: str) -> bool:
    return any(
        event.type is EventType.CUSTOM
        and event.payload.customType is CustomType.APPROVAL_DECIDED
        and event.payload.model_dump().get("receipt_id") == receipt_id
        for event in store.load()
    )


__all__ = [
    "APPROVAL_PROTOCOL_VERSION",
    "ApprovalBroker",
    "ApprovalBrokerError",
    "ApprovalDecision",
    "ApprovalRequest",
    "DecisionReceipt",
]
