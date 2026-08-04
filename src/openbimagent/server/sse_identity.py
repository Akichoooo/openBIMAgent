"""M2 pre-G7 SSE 流信封标识的纯函数失败关闭策略。

该模块只验证 SSE event_id、stream session_id 与 cursor 对应字段；不处理 attempt request_id、
lineage_id、correlation ID、外部资源 ID、ActorRef、幂等键或内部持久事实身份。
"""

from __future__ import annotations

import re

M2_SSE_STREAM_ID_POLICY_VERSION = "0.1"
M2_SSE_STREAM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$"
_SSE_STREAM_ID = re.compile(M2_SSE_STREAM_ID_PATTERN)


def is_m2_sse_stream_id(value: str) -> bool:
    """判断值是否为有界、无路径、无盘符和无空白语义的 SSE 流标识。"""

    return _SSE_STREAM_ID.fullmatch(value) is not None


def validate_m2_sse_stream_id(value: str) -> str:
    """验证并原样返回 SSE 流标识；非法值失败关闭。"""

    if not is_m2_sse_stream_id(value):
        raise ValueError("SSE 流标识不满足 M2 安全策略")
    return value


__all__ = [
    "M2_SSE_STREAM_ID_PATTERN",
    "M2_SSE_STREAM_ID_POLICY_VERSION",
    "is_m2_sse_stream_id",
    "validate_m2_sse_stream_id",
]
