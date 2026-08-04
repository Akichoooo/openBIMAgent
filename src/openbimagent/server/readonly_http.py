"""M2 pre-G7 只读 HTTP 输入解析、路由和错误状态映射纯函数。

该模块不创建 Web 应用，不绑定端口，不读取认证或 Runtime IPC token，不获取 Runtime lease。
调用方可在后续 Gate 将已解析请求接入具体 HTTP 框架；本模块只分派既有 M2ReadOnlyService。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openbimagent.server.contracts import M2ApiEnvelope, M2ErrorCode, make_m2_api_error
from openbimagent.server.resource_identity import is_m2_resource_id
from openbimagent.server.service import M2ReadOnlyService

M2_READONLY_HTTP_ADAPTER_VERSION = "0.1"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")
_ASCII_TARGET = re.compile(r"^[\x21-\x7e]{1,2048}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}$")
_STATUS = {"pending", "running", "completed", "failed", "cancelled"}
_ERROR_STATUS = {
    M2ErrorCode.INVALID_REQUEST: 400,
    M2ErrorCode.UNSUPPORTED_VERSION: 400,
    M2ErrorCode.UNAUTHORIZED: 401,
    M2ErrorCode.FORBIDDEN: 403,
    M2ErrorCode.NOT_FOUND: 404,
    M2ErrorCode.CONFLICT: 409,
    M2ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    M2ErrorCode.APPROVAL_REQUIRED: 409,
    M2ErrorCode.TERMINAL_STATE_CONFLICT: 409,
    M2ErrorCode.REPLAY_CURSOR_EXPIRED: 409,
    M2ErrorCode.PAYLOAD_TOO_LARGE: 413,
    M2ErrorCode.RATE_LIMITED: 429,
    M2ErrorCode.RUNTIME_UNAVAILABLE: 503,
    M2ErrorCode.INTERNAL_ERROR: 500,
}
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Type": "application/json",
    "X-Content-Type-Options": "nosniff",
}


class M2HttpHeader(BaseModel):
    """框架无关的单个 HTTP header；重复语义由 adapter 显式处理。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=2_000)

    @field_validator("name")
    @classmethod
    def _name_is_token(cls, value: str) -> str:
        if not _HEADER_NAME.fullmatch(value):
            raise ValueError("HTTP header name 非法")
        return value

    @field_validator("value")
    @classmethod
    def _value_has_no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("HTTP header value 禁止控制字符")
        return value


class M2ReadonlyHttpRequest(BaseModel):
    """已由外层框架提供的最小只读请求视图。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_version: str = Field(default=M2_READONLY_HTTP_ADAPTER_VERSION, pattern=r"^0\.1$")
    method: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z]+$")
    target: str = Field(min_length=1, max_length=2_048)
    headers: tuple[M2HttpHeader, ...] = ()
    body_size: int = Field(default=0, ge=0, le=1_048_576)

    @field_validator("target")
    @classmethod
    def _target_is_bounded_ascii_origin_form(cls, value: str) -> str:
        if not _ASCII_TARGET.fullmatch(value):
            raise ValueError("HTTP target 必须是 1..2048 字节可见 ASCII")
        return value


class M2ReadonlyHttpResponse(BaseModel):
    """可由未来框架 adapter 序列化的安全响应。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_version: str = Field(default=M2_READONLY_HTTP_ADAPTER_VERSION, pattern=r"^0\.1$")
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str]
    envelope: M2ApiEnvelope


class _RequestError(ValueError):
    def __init__(self, message: str, *, request_id: str = "invalid-request", status_code: int = 400) -> None:
        self.request_id = request_id
        self.status_code = status_code
        super().__init__(message)


class M2ReadonlyHttpAdapter:
    """仅持有注入的只读 service；不持有 socket、文件、Runtime 或 IPC client。"""

    def __init__(self, service: M2ReadOnlyService) -> None:
        self._service = service

    def dispatch(self, request: M2ReadonlyHttpRequest) -> M2ReadonlyHttpResponse:
        try:
            request_id = _request_id(request.headers)
            if request.method != "GET":
                raise _RequestError("只读端点仅允许 GET", request_id=request_id, status_code=405)
            if request.body_size != 0:
                raise _RequestError("GET 请求不能包含 body", request_id=request_id)
            path, query = _parse_target(request.target, request_id=request_id)
            envelope = self._dispatch_route(request_id=request_id, path=path, query=query)
            status_code = 200 if envelope.ok else m2_error_http_status(envelope.error.code)
            return _response(status_code, envelope)
        except _RequestError as exc:
            envelope = _error_envelope(
                request_id=exc.request_id,
                code=M2ErrorCode.INVALID_REQUEST if exc.status_code != 404 else M2ErrorCode.NOT_FOUND,
                message=str(exc),
            )
            return _response(exc.status_code, envelope, allow_get=exc.status_code == 405)

    def _dispatch_route(
        self,
        *,
        request_id: str,
        path: str,
        query: Mapping[str, str],
    ) -> M2ApiEnvelope:
        if path == "/api/v1/health":
            _require_query(query, (), request_id=request_id)
            return self._service.health(request_id=request_id)
        if path == "/api/v1/sessions":
            _require_query(query, (), request_id=request_id)
            return self._service.list_sessions(request_id=request_id)
        if path == "/api/v1/attempts":
            _require_query(query, ("lineage_id", "status", "parent_session_id"), request_id=request_id)
            lineage_id = _optional_resource(query, "lineage_id", request_id=request_id)
            parent_session_id = _optional_resource(query, "parent_session_id", request_id=request_id)
            status = query.get("status")
            if status is not None and status not in _STATUS:
                raise _RequestError("非法 status 查询参数", request_id=request_id)
            return self._service.list_attempts(
                request_id=request_id,
                lineage_id=lineage_id,
                status=status,
                parent_session_id=parent_session_id,
            )
        if path == "/api/v1/approvals":
            _require_query(query, ("request_id", "pending_only"), request_id=request_id)
            attempt_request_id = _optional_resource(query, "request_id", request_id=request_id)
            pending_only = _optional_bool(query, "pending_only", request_id=request_id)
            return self._service.list_approvals(
                request_id=request_id,
                attempt_request_id=attempt_request_id,
                pending_only=pending_only,
            )

        for prefix, handler in (
            ("/api/v1/sessions/", self._service.get_session),
            ("/api/v1/attempts/", self._service.get_attempt),
            ("/api/v1/lineages/", self._service.get_lineage),
            ("/api/v1/artifacts/", self._service.get_artifact_metadata),
        ):
            if path.startswith(prefix):
                _require_query(query, (), request_id=request_id)
                resource_id = path.removeprefix(prefix)
                if not is_m2_resource_id(resource_id):
                    raise _RequestError("非法资源标识", request_id=request_id)
                keyword = {
                    "/api/v1/sessions/": "session_id",
                    "/api/v1/attempts/": "attempt_request_id",
                    "/api/v1/lineages/": "lineage_id",
                    "/api/v1/artifacts/": "artifact_id",
                }[prefix]
                return handler(request_id=request_id, **{keyword: resource_id})
        raise _RequestError("资源不存在", request_id=request_id, status_code=404)


def m2_error_http_status(code: M2ErrorCode) -> int:
    """M2 错误协议到 HTTP 状态的完整稳定映射。"""

    return _ERROR_STATUS[code]


def _request_id(headers: tuple[M2HttpHeader, ...]) -> str:
    values = [header.value for header in headers if header.name.lower() == "x-request-id"]
    if len(values) != 1 or not _REQUEST_ID.fullmatch(values[0]):
        raise _RequestError("缺失、重复或非法 X-Request-ID")
    return values[0]


def _parse_target(target: str, *, request_id: str) -> tuple[str, dict[str, str]]:
    try:
        split = urlsplit(target)
    except ValueError as exc:
        raise _RequestError("HTTP target 结构非法", request_id=request_id) from exc
    if split.scheme or split.netloc or split.fragment or not split.path.startswith("/"):
        raise _RequestError("HTTP target 必须是无 fragment 的 origin-form", request_id=request_id)
    if split.path != "/" and split.path.endswith("/"):
        raise _RequestError("HTTP path 禁止尾随斜杠", request_id=request_id)
    try:
        raw_path = unquote_to_bytes(split.path)
        path = raw_path.decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise _RequestError("HTTP path 编码非法", request_id=request_id) from exc
    if path != split.path or "//" in path or any(segment in {".", ".."} for segment in path.split("/")):
        raise _RequestError("HTTP path 禁止编码、空段或目录语义", request_id=request_id)
    try:
        pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True, max_num_fields=20)
    except ValueError as exc:
        raise _RequestError("HTTP query 编码非法", request_id=request_id) from exc
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            raise _RequestError("HTTP query 参数重复", request_id=request_id)
        if not key or not value:
            raise _RequestError("HTTP query 参数不能为空", request_id=request_id)
        if not key.isascii() or not value.isascii():
            raise _RequestError("HTTP query 只允许 ASCII 协议值", request_id=request_id)
        query[key] = value
    return path, query


def _require_query(query: Mapping[str, str], allowed: tuple[str, ...], *, request_id: str) -> None:
    unknown = set(query) - set(allowed)
    if unknown:
        raise _RequestError("存在未知 query 参数", request_id=request_id)


def _optional_resource(query: Mapping[str, str], name: str, *, request_id: str) -> str | None:
    value = query.get(name)
    if value is None:
        return None
    if not is_m2_resource_id(value):
        raise _RequestError(f"非法 {name} 查询参数", request_id=request_id)
    return value


def _optional_bool(query: Mapping[str, str], name: str, *, request_id: str) -> bool:
    value = query.get(name)
    if value is None or value == "false":
        return False
    if value == "true":
        return True
    raise _RequestError(f"非法 {name} 查询参数", request_id=request_id)


def _error_envelope(*, request_id: str, code: M2ErrorCode, message: str) -> M2ApiEnvelope:
    error = make_m2_api_error(code=code, message=message, request_id=request_id)
    return M2ApiEnvelope(request_id=request_id, ok=False, error=error)


def _response(status_code: int, envelope: M2ApiEnvelope, *, allow_get: bool = False) -> M2ReadonlyHttpResponse:
    headers = dict(_SAFE_HEADERS)
    if allow_get:
        headers["Allow"] = "GET"
    return M2ReadonlyHttpResponse(status_code=status_code, headers=headers, envelope=envelope)


__all__ = [
    "M2_READONLY_HTTP_ADAPTER_VERSION",
    "M2HttpHeader",
    "M2ReadonlyHttpAdapter",
    "M2ReadonlyHttpRequest",
    "M2ReadonlyHttpResponse",
    "m2_error_http_status",
]
