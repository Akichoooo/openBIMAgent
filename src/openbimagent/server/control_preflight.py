"""M2 P3 pre-G7 写控制的纯函数身份、授权与幂等预检核。

本模块不读取或写入持久状态、不读取 Runtime IPC discovery/token、不连接 IPC、不构造 Runtime，
也不执行 approval/resume/steer/cancel。它只把 server 已认证的 ActorRef、授权角色和严格控制请求
收敛为可审计的内部代理计划，并对调用方提供的持久幂等事实做确定性对账。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openbimagent.orchestrator.actor import ActorRef
from openbimagent.server.authentication import M2AuthenticatedPrincipal, M2ControlRole
from openbimagent.server.contracts import (
    M2ApiError,
    M2ControlOperation,
    M2ControlRequest,
    M2ErrorCode,
    make_m2_api_error,
)
from openbimagent.server.resource_identity import M2_RESOURCE_ID_PATTERN, is_m2_resource_id

M2_CONTROL_PREFLIGHT_VERSION = "0.1"


class M2IdempotencyDisposition(StrEnum):
    """纯函数幂等对账结果；持久化由后续 ACTIVE Gate 的 adapter 负责。"""

    NEW = "new"
    REPLAY = "replay"


class M2ControlPreflightError(ValueError):
    """安全、稳定且不回显请求正文的控制预检错误。"""

    def __init__(self, code: M2ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)

    def to_api_error(self, request_id: str) -> M2ApiError:
        return make_m2_api_error(
            code=self.code,
            message=str(self),
            request_id=request_id,
        )


class M2ControlProxyPlan(BaseModel):
    """仅供可信 server 内部 adapter 使用的不可变 IPC 代理计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preflight_version: str = Field(default=M2_CONTROL_PREFLIGHT_VERSION, pattern=r"^0\.1$")
    actor: ActorRef
    role: M2ControlRole
    operation: M2ControlOperation
    resource_id: str = Field(pattern=M2_RESOURCE_ID_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")
    idempotency_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ipc_operation: str = Field(pattern=r"^(approval\.decide|attempt\.resume|attempt\.steer|attempt\.cancel)$")
    ipc_payload: dict[str, Any]


class M2IdempotencyFact(BaseModel):
    """后续持久 store 注入的最小幂等事实；不包含响应正文或 token。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")


class M2IdempotencyDecision(BaseModel):
    """幂等事实的无副作用判定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: M2IdempotencyDisposition
    receipt_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")


class M2ControlPreflight:
    """不持有缓存、文件、网络 client 或 Runtime 的纯函数策略核。"""

    def prepare(
        self,
        *,
        principal: M2AuthenticatedPrincipal,
        request: M2ControlRequest,
    ) -> M2ControlProxyPlan:
        if M2ControlRole.OPERATOR not in principal.roles:
            raise M2ControlPreflightError(
                M2ErrorCode.FORBIDDEN,
                "当前认证主体无权执行远程控制",
            )
        actor = principal.actor
        if not is_m2_resource_id(request.resource_id):
            raise M2ControlPreflightError(
                M2ErrorCode.INVALID_REQUEST,
                "控制资源标识不满足远程协议",
            )

        semantic = _semantic_payload(request)
        scope = {
            "actor_id": actor.actor_id,
            "endpoint_operation": request.operation.value,
            "idempotency_key": request.idempotency_key,
        }
        return M2ControlProxyPlan(
            actor=actor,
            role=M2ControlRole.OPERATOR,
            operation=request.operation,
            resource_id=request.resource_id,
            idempotency_key=request.idempotency_key,
            idempotency_scope_sha256=_sha256(scope),
            semantic_fingerprint=_sha256(semantic),
            ipc_operation=request.operation.value,
            ipc_payload=_ipc_payload(request),
        )

    def reconcile(
        self,
        *,
        plan: M2ControlProxyPlan,
        existing: M2IdempotencyFact | None,
    ) -> M2IdempotencyDecision:
        if existing is None:
            return M2IdempotencyDecision(disposition=M2IdempotencyDisposition.NEW)
        if existing.idempotency_scope_sha256 != plan.idempotency_scope_sha256:
            raise M2ControlPreflightError(
                M2ErrorCode.CONFLICT,
                "幂等事实与当前控制域不一致",
            )
        if existing.semantic_fingerprint != plan.semantic_fingerprint:
            raise M2ControlPreflightError(
                M2ErrorCode.IDEMPOTENCY_CONFLICT,
                "同一幂等键已用于不同控制语义",
            )
        return M2IdempotencyDecision(
            disposition=M2IdempotencyDisposition.REPLAY,
            receipt_id=existing.receipt_id,
        )


def _semantic_payload(request: M2ControlRequest) -> dict[str, Any]:
    return {
        "operation": request.operation.value,
        "resource_id": request.resource_id,
        "approved": request.approved,
        "instruction": request.instruction,
        "reason": request.reason,
    }


def _ipc_payload(request: M2ControlRequest) -> dict[str, Any]:
    if request.operation is M2ControlOperation.APPROVAL_DECIDE:
        return {
            "approval_id": request.resource_id,
            "approved": request.approved,
            "reason": request.reason,
        }
    if request.operation is M2ControlOperation.ATTEMPT_RESUME:
        return {
            "source_request_id": request.resource_id,
            "instruction": request.instruction,
        }
    if request.operation is M2ControlOperation.ATTEMPT_STEER:
        return {
            "request_id": request.resource_id,
            "instruction": request.instruction,
        }
    return {"request_id": request.resource_id}


def _sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "M2_CONTROL_PREFLIGHT_VERSION",
    "M2ControlPreflight",
    "M2ControlPreflightError",
    "M2ControlProxyPlan",
    "M2ControlRole",
    "M2IdempotencyDecision",
    "M2IdempotencyDisposition",
    "M2IdempotencyFact",
]
