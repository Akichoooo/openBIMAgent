"""M2 pre-G7 客户端关联标识的纯函数失败关闭策略。

该模块只验证 X-Request-ID 及其 API response/error envelope 投影；不处理 attempt request_id、
SSE event identity、外部资源 ID、ActorRef、幂等键或内部持久事实身份。
"""

from __future__ import annotations

import re

M2_CORRELATION_ID_POLICY_VERSION = "0.1"
M2_CORRELATION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$"
_CORRELATION_ID = re.compile(M2_CORRELATION_ID_PATTERN)


def is_m2_correlation_id(value: str) -> bool:
    """判断值是否为有界、无路径和无空白语义的客户端关联标识。"""

    return _CORRELATION_ID.fullmatch(value) is not None


def validate_m2_correlation_id(value: str) -> str:
    """验证并原样返回客户端关联标识；非法值失败关闭。"""

    if not is_m2_correlation_id(value):
        raise ValueError("客户端关联标识不满足 M2 安全策略")
    return value


__all__ = [
    "M2_CORRELATION_ID_PATTERN",
    "M2_CORRELATION_ID_POLICY_VERSION",
    "is_m2_correlation_id",
    "validate_m2_correlation_id",
]
