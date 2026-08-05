"""M2 pre-G7 只读列表分页纯函数策略。

Cursor 是版本化、查询作用域与事实快照绑定的透明 continuation 数据。其无密钥
SHA-256 只用于检测损坏和非规范编码，不提供来源真实性或对抗性防篡改保证；正式
认证 cursor 必须等待 G6/G7 后的身份与 secret 决策，不得在本模块伪造安全声明。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openbimagent.server.contracts import M2ErrorCode

M2_PAGINATION_POLICY_VERSION = "0.1"
M2_PAGE_LIMIT_DEFAULT = 50
M2_PAGE_LIMIT_MAX = 100
M2_PAGE_CURSOR_CHARS_MAX = 1_024
M2_PAGINATION_CURSOR_AUTHENTICATED = False

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
M2PageResource = Literal["sessions", "attempts", "lineages", "approvals"]


class M2PageCursor(BaseModel):
    """可序列化的透明分页 continuation；不是 SSE cursor 或资源身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default="1.0", pattern=r"^1\.0$")
    policy_version: str = Field(default=M2_PAGINATION_POLICY_VERSION, pattern=r"^0\.1$")
    resource: M2PageResource
    scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    offset: int = Field(ge=1, le=2_147_483_647)
    limit: int = Field(ge=1, le=M2_PAGE_LIMIT_MAX)
    integrity_sha256: str = Field(pattern=_SHA256_PATTERN)


class M2Page(BaseModel):
    """有界只读页；count 仅表示本页条目数，不声称全局总数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[dict[str, Any], ...]
    count: int = Field(ge=0, le=M2_PAGE_LIMIT_MAX)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=M2_PAGE_CURSOR_CHARS_MAX)


class M2PaginationError(ValueError):
    def __init__(self, code: M2ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def paginate_m2_items(
    items: Sequence[Mapping[str, Any]],
    *,
    resource: M2PageResource,
    scope: Mapping[str, Any],
    limit: int = M2_PAGE_LIMIT_DEFAULT,
    cursor: str | None = None,
) -> M2Page:
    """对已稳定排序的远程白名单投影分页，cursor 不匹配时失败关闭。"""

    _validate_limit(limit)
    normalized_items = tuple(dict(item) for item in items)
    scope_sha256 = _canonical_sha256(dict(scope), label="分页查询作用域")
    snapshot_sha256 = _canonical_sha256(normalized_items, label="分页事实快照")
    offset = 0
    if cursor is not None:
        decoded = decode_m2_page_cursor(cursor)
        if decoded.resource != resource or decoded.scope_sha256 != scope_sha256 or decoded.limit != limit:
            raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 不属于当前资源、查询作用域或 limit")
        if decoded.snapshot_sha256 != snapshot_sha256 or decoded.offset > len(normalized_items):
            raise M2PaginationError(M2ErrorCode.REPLAY_CURSOR_EXPIRED, "分页 cursor 已过期或与当前事实快照不一致")
        offset = decoded.offset

    page_items = normalized_items[offset : offset + limit]
    next_offset = offset + len(page_items)
    has_more = next_offset < len(normalized_items)
    next_cursor = None
    if has_more:
        next_cursor = _encode_m2_page_cursor(
            resource=resource,
            scope_sha256=scope_sha256,
            snapshot_sha256=snapshot_sha256,
            offset=next_offset,
            limit=limit,
        )
    return M2Page(items=page_items, count=len(page_items), has_more=has_more, next_cursor=next_cursor)


def decode_m2_page_cursor(value: str) -> M2PageCursor:
    """严格解码并校验 canonical cursor 与无密钥完整性摘要。"""

    if not isinstance(value, str) or not _CURSOR_PATTERN.fullmatch(value):
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 编码非法")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 编码非法") from exc
    if not isinstance(payload, dict):
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 结构非法")
    try:
        cursor = M2PageCursor.model_validate(payload)
    except ValidationError as exc:
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 结构非法") from exc
    unsigned = cursor.model_dump(mode="json", exclude={"integrity_sha256"})
    if cursor.integrity_sha256 != _canonical_sha256(unsigned, label="分页 cursor"):
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 完整性校验失败")
    canonical_raw = _canonical_bytes(cursor.model_dump(mode="json"), label="分页 cursor")
    canonical_value = base64.urlsafe_b64encode(canonical_raw).decode("ascii").rstrip("=")
    if raw != canonical_raw or value != canonical_value:
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 cursor 不是规范编码")
    return cursor


def _encode_m2_page_cursor(
    *,
    resource: M2PageResource,
    scope_sha256: str,
    snapshot_sha256: str,
    offset: int,
    limit: int,
) -> str:
    unsigned = {
        "protocol_version": "1.0",
        "policy_version": M2_PAGINATION_POLICY_VERSION,
        "resource": resource,
        "scope_sha256": scope_sha256,
        "snapshot_sha256": snapshot_sha256,
        "offset": offset,
        "limit": limit,
    }
    payload = {**unsigned, "integrity_sha256": _canonical_sha256(unsigned, label="分页 cursor")}
    encoded = base64.urlsafe_b64encode(_canonical_bytes(payload, label="分页 cursor")).decode("ascii").rstrip("=")
    if len(encoded) > M2_PAGE_CURSOR_CHARS_MAX:
        raise M2PaginationError(M2ErrorCode.INTERNAL_ERROR, "分页 cursor 超过协议上限")
    return encoded


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= M2_PAGE_LIMIT_MAX:
        raise M2PaginationError(M2ErrorCode.INVALID_REQUEST, "分页 limit 必须在 1..100")


def _canonical_sha256(value: Any, *, label: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, label=label)).hexdigest()


def _canonical_bytes(value: Any, *, label: str) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise M2PaginationError(M2ErrorCode.CONFLICT, f"{label} 不可规范序列化") from exc


__all__ = [
    "M2_PAGE_CURSOR_CHARS_MAX",
    "M2_PAGE_LIMIT_DEFAULT",
    "M2_PAGE_LIMIT_MAX",
    "M2_PAGINATION_CURSOR_AUTHENTICATED",
    "M2_PAGINATION_POLICY_VERSION",
    "M2Page",
    "M2PageCursor",
    "M2PageResource",
    "M2PaginationError",
    "decode_m2_page_cursor",
    "paginate_m2_items",
]
