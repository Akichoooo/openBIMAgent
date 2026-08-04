"""M2 P4 pre-G7 持久 Session 事实到 SSE 的纯函数投影与离线回放。

模块不读取或写入文件、不绑定网络、不构造 Runtime，也不获取 Runtime lease。调用方必须先读取并
验证 SessionEvent；本模块仅接受白名单持久事实，确定性去重、排序、编号和脱敏投影。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openbimagent.orchestrator.approval import ApprovalRequest, DecisionReceipt
from openbimagent.orchestrator.contracts import ArtifactRecord
from openbimagent.server.contracts import (
    M2ApiError,
    M2ErrorCode,
    M2SseCursor,
    M2SseEvent,
    M2SseEventType,
    make_m2_api_error,
)
from openbimagent.session.schema import CustomType, EventType, SessionEvent

M2_SSE_PROJECTION_VERSION = "0.1"
_TERMINAL_LIFECYCLE = {
    CustomType.SUBAGENT_COMPLETED,
    CustomType.SUBAGENT_FAILED,
    CustomType.SUBAGENT_CANCELLED,
}
_LIFECYCLE = {
    CustomType.SUBAGENT_CREATED,
    CustomType.SUBAGENT_STARTED,
    *_TERMINAL_LIFECYCLE,
}
_SUPPORTED = {
    *_LIFECYCLE,
    CustomType.APPROVAL_REQUESTED,
    CustomType.APPROVAL_DECIDED,
    CustomType.ARTIFACT_COMMITTED,
}


class SseProjectionError(ValueError):
    """持久事实无效、同身份冲突或回放 cursor 不可用。"""

    def __init__(self, code: M2ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)

    def to_api_error(self, request_id: str) -> M2ApiError:
        return make_m2_api_error(
            code=self.code,
            message=str(self),
            request_id=request_id,
        )


@dataclass(frozen=True)
class _Fact:
    identity: str
    occurred_at: datetime
    event_type: M2SseEventType
    session_id: str
    request_id: str
    lineage_id: str
    attempt_number: int
    terminal: bool
    data: dict[str, Any]

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "lineage_id": self.lineage_id,
            "attempt_number": self.attempt_number,
            "terminal": self.terminal,
            "data": self.data,
        }


class M2SseProjector:
    """白名单持久事实的确定性 SSE 投影器。"""

    def project(
        self,
        *,
        session_id: str,
        events: Iterable[SessionEvent],
    ) -> tuple[M2SseEvent, ...]:
        source_events = tuple(events)
        attempt_index = self._attempt_index(source_events)
        facts: dict[str, _Fact] = {}
        for event in source_events:
            fact = self._project_fact(
                session_id=session_id,
                event=event,
                attempt_index=attempt_index,
            )
            if fact is None:
                continue
            existing = facts.get(fact.identity)
            if existing is not None:
                if existing.semantic_payload() != fact.semantic_payload():
                    raise SseProjectionError(
                        M2ErrorCode.CONFLICT,
                        f"SSE 持久事实冲突: identity={fact.identity}",
                    )
                if existing.occurred_at <= fact.occurred_at:
                    continue
            facts[fact.identity] = fact
        ordered = sorted(
            facts.values(),
            key=lambda item: (
                item.occurred_at,
                item.request_id,
                item.attempt_number,
                item.identity,
            ),
        )
        terminal_requests: set[str] = set()
        for fact in ordered:
            if fact.request_id in terminal_requests:
                raise SseProjectionError(
                    M2ErrorCode.CONFLICT,
                    f"终态后仍存在 SSE 事实: request_id={fact.request_id}",
                )
            if fact.terminal:
                terminal_requests.add(fact.request_id)
        return tuple(
            M2SseEvent(
                event_id=_fact_event_id(fact),
                event_type=fact.event_type,
                session_id=fact.session_id,
                request_id=fact.request_id,
                lineage_id=fact.lineage_id,
                attempt_number=fact.attempt_number,
                sequence=index,
                occurred_at=fact.occurred_at,
                terminal=fact.terminal,
                data=fact.data,
            )
            for index, fact in enumerate(ordered, start=1)
        )

    def replay(
        self,
        *,
        session_id: str,
        events: Sequence[M2SseEvent],
        cursor: M2SseCursor | None = None,
        limit: int = 100,
    ) -> tuple[M2SseEvent, ...]:
        if limit < 1 or limit > 1_000:
            raise SseProjectionError(M2ErrorCode.INVALID_REQUEST, "SSE replay limit 必须在 1..1000")
        self._validate_stream(session_id=session_id, events=events)
        start = 0
        if cursor is not None:
            if cursor.session_id != session_id:
                raise SseProjectionError(M2ErrorCode.REPLAY_CURSOR_EXPIRED, "SSE cursor 不属于当前 session")
            matches = [
                index
                for index, event in enumerate(events)
                if event.event_id == cursor.last_event_id and event.sequence == cursor.last_sequence
            ]
            if len(matches) != 1:
                raise SseProjectionError(M2ErrorCode.REPLAY_CURSOR_EXPIRED, "SSE cursor 已过期或与持久事实不匹配")
            start = matches[0] + 1
        return tuple(events[start : start + limit])

    def cursor_for(self, event: M2SseEvent) -> M2SseCursor:
        return M2SseCursor(
            session_id=event.session_id,
            last_event_id=event.event_id,
            last_sequence=event.sequence,
        )

    def _project_fact(
        self,
        *,
        session_id: str,
        event: SessionEvent,
        attempt_index: dict[str, tuple[str, int, str]],
    ) -> _Fact | None:
        if event.type is not EventType.CUSTOM:
            return None
        custom_type = event.payload.customType
        if custom_type not in _SUPPORTED:
            return None
        payload = event.payload.model_dump(mode="json")
        if custom_type in _LIFECYCLE:
            return self._lifecycle(session_id, event.timestamp, custom_type, payload)
        request_id = _required_string(payload, "request_id")
        attempt_identity = attempt_index.get(request_id)
        if attempt_identity is None:
            raise SseProjectionError(
                M2ErrorCode.CONFLICT,
                f"SSE 事实缺少 request_id={request_id} 的 lifecycle 身份",
            )
        if custom_type is CustomType.APPROVAL_REQUESTED:
            return self._approval_requested(
                session_id,
                event.timestamp,
                payload,
                attempt_identity,
            )
        if custom_type is CustomType.APPROVAL_DECIDED:
            return self._approval_decided(
                session_id,
                event.timestamp,
                payload,
                attempt_identity,
            )
        return self._artifact(
            session_id,
            event.timestamp,
            payload,
            attempt_identity,
        )

    @staticmethod
    def _attempt_index(events: Sequence[SessionEvent]) -> dict[str, tuple[str, int, str]]:
        index: dict[str, tuple[str, int, str]] = {}
        for event in events:
            if event.type is not EventType.CUSTOM or event.payload.customType not in _LIFECYCLE:
                continue
            payload = event.payload.model_dump(mode="json")
            request_id, lineage_id, attempt_number = _attempt_identity(payload)
            identity = (lineage_id, attempt_number, _required_string(payload, "agent_id"))
            existing = index.get(request_id)
            if existing is not None and existing != identity:
                raise SseProjectionError(
                    M2ErrorCode.CONFLICT,
                    f"request_id={request_id} 对应冲突 attempt 身份",
                )
            index[request_id] = identity
        return index

    @staticmethod
    def _lifecycle(
        session_id: str,
        occurred_at: datetime,
        custom_type: CustomType,
        payload: dict[str, Any],
    ) -> _Fact:
        request_id, lineage_id, attempt_number = _attempt_identity(payload)
        status = str(payload.get("status", ""))
        expected_status = {
            CustomType.SUBAGENT_CREATED: {"created", "queued"},
            CustomType.SUBAGENT_STARTED: {"running"},
            CustomType.SUBAGENT_COMPLETED: {"completed"},
            CustomType.SUBAGENT_FAILED: {"failed"},
            CustomType.SUBAGENT_CANCELLED: {"cancelled"},
        }[custom_type]
        if status not in expected_status:
            raise SseProjectionError(M2ErrorCode.CONFLICT, f"SSE lifecycle status 与 customType 冲突: {custom_type.value}")
        terminal = custom_type in _TERMINAL_LIFECYCLE
        event_type = M2SseEventType.TERMINAL if terminal else M2SseEventType.ATTEMPT
        data: dict[str, Any] = {
            "status": status,
            "phase": "terminal" if terminal else "running" if custom_type is CustomType.SUBAGENT_STARTED else "prepared",
            "agent_id": _required_string(payload, "agent_id"),
            "child_session_id": _required_string(payload, "child_session_id"),
        }
        if payload.get("role"):
            data["role"] = str(payload["role"])
        if payload.get("resumed_from_request_id"):
            data["resumed_from_request_id"] = str(payload["resumed_from_request_id"])
        if payload.get("receipt_id"):
            data["receipt_id"] = str(payload["receipt_id"])
        error = payload.get("error")
        if isinstance(error, dict) and error.get("code"):
            data["error_code"] = str(error["code"])
            data["retryable"] = bool(error.get("retryable", False))
        return _Fact(
            identity=f"lifecycle:{request_id}:{custom_type.value}",
            occurred_at=occurred_at,
            event_type=event_type,
            session_id=session_id,
            request_id=request_id,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            terminal=terminal,
            data=data,
        )

    @staticmethod
    def _approval_requested(
        session_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        attempt_identity: tuple[str, int, str],
    ) -> _Fact:
        approval = _model_from_payload(ApprovalRequest, payload, "approval request")
        lineage_id, attempt_number, agent_id = attempt_identity
        if approval.agent_id != agent_id:
            raise SseProjectionError(M2ErrorCode.CONFLICT, "approval agent_id 与 lifecycle 冲突")
        return _Fact(
            identity=f"approval-requested:{approval.approval_id}",
            occurred_at=occurred_at,
            event_type=M2SseEventType.APPROVAL,
            session_id=session_id,
            request_id=approval.request_id,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            terminal=False,
            data={
                "approval_id": approval.approval_id,
                "state": "pending",
                "tool_name": approval.tool_name,
                "permission_key": approval.permission_key,
                "args_sha256": approval.args_sha256,
            },
        )

    @staticmethod
    def _approval_decided(
        session_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        attempt_identity: tuple[str, int, str],
    ) -> _Fact:
        receipt = _model_from_payload(DecisionReceipt, payload, "approval decision")
        lineage_id, attempt_number, agent_id = attempt_identity
        if receipt.agent_id != agent_id:
            raise SseProjectionError(M2ErrorCode.CONFLICT, "approval receipt agent_id 与 lifecycle 冲突")
        return _Fact(
            identity=f"approval-decided:{receipt.approval_id}:{receipt.receipt_id}",
            occurred_at=occurred_at,
            event_type=M2SseEventType.APPROVAL,
            session_id=session_id,
            request_id=receipt.request_id,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            terminal=False,
            data={
                "approval_id": receipt.approval_id,
                "state": "decided",
                "decision": receipt.decision.value,
                "receipt_id": receipt.receipt_id,
            },
        )

    @staticmethod
    def _artifact(
        session_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        attempt_identity: tuple[str, int, str],
    ) -> _Fact:
        artifact_payload = payload.get("artifact")
        if not isinstance(artifact_payload, dict):
            raise SseProjectionError(M2ErrorCode.CONFLICT, "artifact_committed 缺少结构化 artifact")
        artifact = _model_from_payload(ArtifactRecord, artifact_payload, "artifact")
        request_id = _required_string(payload, "request_id")
        if artifact.source_attempt_id not in {None, request_id}:
            raise SseProjectionError(M2ErrorCode.CONFLICT, "artifact source_attempt_id 与 request_id 冲突")
        lineage_id, attempt_number, agent_id = attempt_identity
        if _required_string(payload, "agent_id") != agent_id:
            raise SseProjectionError(M2ErrorCode.CONFLICT, "artifact agent_id 与 lifecycle 冲突")
        return _Fact(
            identity=f"artifact:{artifact.artifact_id}",
            occurred_at=occurred_at,
            event_type=M2SseEventType.ARTIFACT,
            session_id=session_id,
            request_id=request_id,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            terminal=False,
            data={
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "media_type": artifact.media_type or "application/octet-stream",
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "status": artifact.status.value,
                "download_available": False,
            },
        )

    @staticmethod
    def _validate_stream(*, session_id: str, events: Sequence[M2SseEvent]) -> None:
        seen_ids: set[str] = set()
        terminal_requests: set[str] = set()
        previous_order_key: tuple[datetime, str, int, str] | None = None
        for expected, event in enumerate(events, start=1):
            if event.session_id != session_id:
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream 混入其他 session")
            if event.sequence != expected:
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream sequence 不连续")
            if event.event_id in seen_ids:
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream event_id 重复")
            if event.event_id != _sse_event_id(event):
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream event_id 与事件语义不一致")
            identity = _identity_from_event(event)
            order_key = (
                event.occurred_at,
                event.request_id or "",
                event.attempt_number or 0,
                identity,
            )
            if previous_order_key is not None and order_key < previous_order_key:
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream 事件顺序与持久事实排序不一致")
            if event.request_id in terminal_requests:
                raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE stream 终态后仍存在 attempt 事实")
            if event.terminal:
                terminal_requests.add(event.request_id)
            previous_order_key = order_key
            seen_ids.add(event.event_id)


def _fact_event_id(fact: _Fact) -> str:
    digest = hashlib.sha256(_canonical_bytes(fact.semantic_payload())).hexdigest()
    return f"evt-{digest}"


def _sse_event_id(event: M2SseEvent) -> str:
    payload = {
        "identity": _identity_from_event(event),
        "event_type": event.event_type.value,
        "session_id": event.session_id,
        "request_id": event.request_id,
        "lineage_id": event.lineage_id,
        "attempt_number": event.attempt_number,
        "terminal": event.terminal,
        "data": event.data,
    }
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return f"evt-{digest}"


def _identity_from_event(event: M2SseEvent) -> str:
    if event.event_type in {M2SseEventType.ATTEMPT, M2SseEventType.TERMINAL}:
        status = str(event.data.get("status", ""))
        lifecycle = {
            "created": CustomType.SUBAGENT_CREATED.value,
            "queued": CustomType.SUBAGENT_CREATED.value,
            "running": CustomType.SUBAGENT_STARTED.value,
            "completed": CustomType.SUBAGENT_COMPLETED.value,
            "failed": CustomType.SUBAGENT_FAILED.value,
            "cancelled": CustomType.SUBAGENT_CANCELLED.value,
        }.get(status)
        if lifecycle is None:
            raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE attempt status 无法重建事件身份")
        return f"lifecycle:{event.request_id}:{lifecycle}"
    if event.event_type is M2SseEventType.APPROVAL:
        state = event.data.get("state")
        approval_id = event.data.get("approval_id")
        if state == "pending":
            return f"approval-requested:{approval_id}"
        if state == "decided":
            return f"approval-decided:{approval_id}:{event.data.get('receipt_id')}"
        raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE approval state 无法重建事件身份")
    if event.event_type is M2SseEventType.ARTIFACT:
        return f"artifact:{event.data.get('artifact_id')}"
    raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE event type 不属于持久事实投影")


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _attempt_identity(payload: dict[str, Any]) -> tuple[str, str, int]:
    request_id = _required_string(payload, "request_id")
    lineage_id, attempt_number = _lineage_from_payload(payload)
    return request_id, lineage_id, attempt_number


def _lineage_from_payload(payload: dict[str, Any]) -> tuple[str, int]:
    lineage_id = _required_string(payload, "lineage_id")
    attempt_number = payload.get("attempt_number")
    if not isinstance(attempt_number, int) or isinstance(attempt_number, bool) or attempt_number < 1:
        raise SseProjectionError(M2ErrorCode.CONFLICT, "SSE 持久事实缺少合法 attempt_number")
    return lineage_id, attempt_number


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise SseProjectionError(M2ErrorCode.CONFLICT, f"SSE 持久事实缺少 {field}")
    return value


def _model_from_payload(model_type: type[Any], payload: dict[str, Any], label: str) -> Any:
    try:
        return model_type.model_validate({key: payload[key] for key in model_type.model_fields if key in payload})
    except Exception as exc:
        raise SseProjectionError(M2ErrorCode.CONFLICT, f"无效 {label} 持久事实") from exc


__all__ = [
    "M2_SSE_PROJECTION_VERSION",
    "M2SseProjector",
    "SseProjectionError",
]
