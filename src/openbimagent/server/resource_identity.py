"""M2 pre-G7 外部可寻址资源标识的纯函数安全策略。

该模块只验证由 API path、query 或控制请求提供的资源 ID；不处理 correlation ID、ActorRef、
idempotency key、receipt/reservation ID 或内部持久事实身份，也不读取文件、网络或 Runtime 状态。
"""

from __future__ import annotations

import re

M2_RESOURCE_ID_POLICY_VERSION = "0.1"
M2_RESOURCE_ID_PATTERN = r"^[A-Za-z0-9_@-][A-Za-z0-9_.@-]{0,199}$"
_RESOURCE_ID = re.compile(M2_RESOURCE_ID_PATTERN)


def is_m2_resource_id(value: str) -> bool:
    """判断值是否为不含路径、盘符、空白或编码语义的外部资源 ID。"""

    return _RESOURCE_ID.fullmatch(value) is not None


def validate_m2_resource_id(value: str) -> str:
    """验证并原样返回外部资源 ID；非法值失败关闭。"""

    if not is_m2_resource_id(value):
        raise ValueError("外部资源标识不满足 M2 安全策略")
    return value


__all__ = [
    "M2_RESOURCE_ID_PATTERN",
    "M2_RESOURCE_ID_POLICY_VERSION",
    "is_m2_resource_id",
    "validate_m2_resource_id",
]
