"""M2 P1 pre-G7 版本化 API、SSE、控制和工件元数据契约。

该模块只冻结协议模型，不实现 FastAPI server，也不获取或构造 Runtime。远程认证主体必须由
未来 server 注入 ActorRef；控制请求模型故意不接受 actor、token、路径或 capability 字段。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openbimagent.core.events import SSEEventType
from openbimagent.server.payload_privacy import M2_REMOTE_PAYLOAD_POLICY_VERSION, validate_remote_payload
from openbimagent.server.resource_identity import (
    M2_RESOURCE_ID_PATTERN,
    M2_RESOURCE_ID_POLICY_VERSION,
    validate_m2_resource_id,
)

M2_API_PROTOCOL_VERSION = "1.0"
M2_SSE_PROTOCOL_VERSION = "1.0"
M2_ERROR_RETRY_POLICY_VERSION = "0.1"
_ID_PATTERN = r"^[A-Za-z0-9_.:@/-]+$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MEDIA_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class M2ErrorCode(StrEnum):
    INVALID_REQUEST = "InvalidRequest"
    UNSUPPORTED_VERSION = "UnsupportedVersion"
    UNAUTHORIZED = "Unauthorized"
    FORBIDDEN = "Forbidden"
    NOT_FOUND = "NotFound"
    CONFLICT = "Conflict"
    IDEMPOTENCY_CONFLICT = "IdempotencyConflict"
    RATE_LIMITED = "RateLimited"
    PAYLOAD_TOO_LARGE = "PayloadTooLarge"
    RUNTIME_UNAVAILABLE = "RuntimeUnavailable"
    APPROVAL_REQUIRED = "ApprovalRequired"
    TERMINAL_STATE_CONFLICT = "TerminalStateConflict"
    REPLAY_CURSOR_EXPIRED = "ReplayCursorExpired"
    INTERNAL_ERROR = "InternalError"


_RETRYABLE_ERROR_CODES = frozenset(
    {
        M2ErrorCode.RATE_LIMITED,
        M2ErrorCode.RUNTIME_UNAVAILABLE,
    }
)


def m2_error_is_retryable(code: M2ErrorCode) -> bool:
    """返回稳定错误码是否允许客户端对同一语义请求安全重试。"""

    return code in _RETRYABLE_ERROR_CODES


class M2ApiError(BaseModel):
    """远程安全错误；不得包含 token、请求正文、异常堆栈或内部路径。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "x-openbimagent-retry-policy": M2_ERROR_RETRY_POLICY_VERSION,
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "code": {
                                "enum": [
                                    M2ErrorCode.RATE_LIMITED.value,
                                    M2ErrorCode.RUNTIME_UNAVAILABLE.value,
                                ]
                            }
                        },
                        "required": ["code"],
                    },
                    "then": {"properties": {"retryable": {"const": True}}},
                    "else": {"properties": {"retryable": {"const": False}}},
                }
            ],
        },
    )

    code: M2ErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool
    request_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _message_is_safe(cls, value: str) -> str:
        validate_remote_payload(value)
        return value

    @field_validator("details")
    @classmethod
    def _details_are_small_and_safe(cls, value: dict[str, str | int | float | bool | None]) -> dict[str, Any]:
        if len(value) > 20:
            raise ValueError("error.details 最多允许 20 个字段")
        for key, item in value.items():
            if isinstance(item, str) and len(item) > 500:
                raise ValueError(f"error.details.{key} 超过 500 字符")
        validate_remote_payload(value)
        return value

    @model_validator(mode="after")
    def _retryable_matches_stable_code(self) -> "M2ApiError":
        if self.retryable is not m2_error_is_retryable(self.code):
            raise ValueError("retryable 必须由稳定错误码决定")
        return self


def make_m2_api_error(
    *,
    code: M2ErrorCode,
    message: str,
    request_id: str,
    details: dict[str, str | int | float | bool | None] | None = None,
) -> M2ApiError:
    """按稳定错误码派生 retryable，避免 adapter 自行声明重试语义。"""

    return M2ApiError(
        code=code,
        message=message,
        retryable=m2_error_is_retryable(code),
        request_id=request_id,
        details={} if details is None else details,
    )


class M2ApiEnvelope(BaseModel):
    """非流式 API 的统一响应信封。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_API_PROTOCOL_VERSION, pattern=r"^1\.0$")
    request_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    ok: bool
    data: dict[str, Any] | None = Field(
        default=None,
        json_schema_extra={"x-openbimagent-remote-payload-policy": M2_REMOTE_PAYLOAD_POLICY_VERSION},
    )
    error: M2ApiError | None = None

    @field_validator("data")
    @classmethod
    def _data_is_safe(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_remote_payload(value)
        return value

    @model_validator(mode="after")
    def _success_and_error_are_exclusive(self) -> "M2ApiEnvelope":
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("成功响应必须携带 data 且不能携带 error")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("失败响应必须携带 error 且不能携带 data")
        if self.error is not None and self.error.request_id != self.request_id:
            raise ValueError("响应与 error.request_id 必须一致")
        return self


class M2SseEventType(StrEnum):
    PROGRESS = SSEEventType.PROGRESS.value
    VISION_SCORECARD = SSEEventType.VISION_SCORECARD.value
    CLARIFY_FORM = SSEEventType.CLARIFY_FORM.value
    ATTEMPT = "data-attempt"
    APPROVAL = "data-approval"
    ARTIFACT = "data-artifact"
    ERROR = "data-error"
    TERMINAL = "data-terminal"


class M2SseCursor(BaseModel):
    """断线回放游标；由 session scope 与最后确认的 event/sequence 绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_SSE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    last_event_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    last_sequence: int = Field(ge=1)


class M2SseEvent(BaseModel):
    """从持久 Session/Runtime 事实投影的 SSE data part。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_SSE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    event_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    event_type: M2SseEventType
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_ID_PATTERN)
    lineage_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_ID_PATTERN)
    attempt_number: int | None = Field(default=None, ge=1)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    terminal: bool = False
    data: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"x-openbimagent-remote-payload-policy": M2_REMOTE_PAYLOAD_POLICY_VERSION},
    )

    @model_validator(mode="after")
    def _identity_and_terminal_semantics(self) -> "M2SseEvent":
        attempt_fields = (self.request_id, self.lineage_id, self.attempt_number)
        if any(value is not None for value in attempt_fields) and not all(value is not None for value in attempt_fields):
            raise ValueError("attempt 事件身份必须同时包含 request_id、lineage_id 和 attempt_number")
        if self.terminal != (self.event_type is M2SseEventType.TERMINAL):
            raise ValueError("terminal 只允许且必须用于 data-terminal 事件")
        return self

    @field_validator("data")
    @classmethod
    def _data_does_not_expose_forbidden_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_remote_payload(value)
        return value


class M2ArtifactMetadata(BaseModel):
    """远程可见的不可变工件元数据；不暴露服务端绝对 path。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_API_PROTOCOL_VERSION, pattern=r"^1\.0$")
    artifact_id: str = Field(
        max_length=128,
        pattern=M2_RESOURCE_ID_PATTERN,
        json_schema_extra={"x-openbimagent-resource-id-policy": M2_RESOURCE_ID_POLICY_VERSION},
    )
    kind: str = Field(min_length=1, max_length=128)
    media_type: str = Field(min_length=3, max_length=255)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    immutable: bool = True
    status: str = Field(pattern=r"^(completed|partial|failed)$")
    source_attempt_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_ID_PATTERN)
    download_available: bool = False

    @field_validator("artifact_id", mode="before")
    @classmethod
    def _artifact_id_is_external_identity(cls, value: object) -> object:
        if isinstance(value, str):
            return validate_m2_resource_id(value)
        return value

    @field_validator("media_type")
    @classmethod
    def _media_type_is_safe(cls, value: str) -> str:
        if not _MEDIA_TYPE_PATTERN.fullmatch(value):
            raise ValueError("media_type 必须是不含参数的 type/subtype")
        return value

    @model_validator(mode="after")
    def _download_only_completed(self) -> "M2ArtifactMetadata":
        if self.download_available and self.status != "completed":
            raise ValueError("只有 completed 工件可作为正式下载")
        return self


class M2ControlOperation(StrEnum):
    APPROVAL_DECIDE = "approval.decide"
    ATTEMPT_RESUME = "attempt.resume"
    ATTEMPT_STEER = "attempt.steer"
    ATTEMPT_CANCEL = "attempt.cancel"


class M2ControlRequest(BaseModel):
    """远程写控制请求；actor 由认证后的 server 注入，不能由客户端提交。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_API_PROTOCOL_VERSION, pattern=r"^1\.0$")
    operation: M2ControlOperation
    resource_id: str = Field(
        pattern=M2_RESOURCE_ID_PATTERN,
        json_schema_extra={"x-openbimagent-resource-id-policy": M2_RESOURCE_ID_POLICY_VERSION},
    )
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=_ID_PATTERN)
    approved: bool | None = None
    instruction: str | None = Field(default=None, min_length=1, max_length=20_000)
    reason: str = Field(default="", max_length=1_000)

    @field_validator("resource_id", mode="before")
    @classmethod
    def _resource_id_is_external_identity(cls, value: object) -> object:
        if isinstance(value, str):
            return validate_m2_resource_id(value)
        return value

    @model_validator(mode="after")
    def _operation_payload_is_exact(self) -> "M2ControlRequest":
        if self.operation is M2ControlOperation.APPROVAL_DECIDE:
            if self.approved is None:
                raise ValueError("approval.decide 需要 approved")
            if self.instruction is not None:
                raise ValueError("approval.decide 不接受 instruction")
        elif self.operation in {M2ControlOperation.ATTEMPT_RESUME, M2ControlOperation.ATTEMPT_STEER}:
            if self.instruction is None:
                raise ValueError(f"{self.operation.value} 需要 instruction")
            if self.approved is not None or self.reason:
                raise ValueError(f"{self.operation.value} 不接受 approved/reason")
        else:
            if self.approved is not None or self.instruction is not None or self.reason:
                raise ValueError("attempt.cancel 不接受 approved/instruction/reason")
        return self


__all__ = [
    "M2_API_PROTOCOL_VERSION",
    "M2_ERROR_RETRY_POLICY_VERSION",
    "M2_SSE_PROTOCOL_VERSION",
    "M2ApiEnvelope",
    "M2ApiError",
    "M2ArtifactMetadata",
    "M2ControlOperation",
    "M2ControlRequest",
    "M2ErrorCode",
    "M2SseCursor",
    "M2SseEvent",
    "M2SseEventType",
    "m2_error_is_retryable",
    "make_m2_api_error",
]
