"""M2 pre-G7 远程 API/SSE 载荷的纯函数隐私与资源边界门禁。

该模块不脱敏、不修改输入，也不读取文件、环境、凭据或 Runtime 状态。任何无法证明安全、
有限且可编码为 JSON 的载荷均失败关闭；调用方只能改为显式白名单投影。
"""

from __future__ import annotations

import math
import re
from typing import Any

M2_REMOTE_PAYLOAD_POLICY_VERSION = "0.1"

_MAX_DEPTH = 16
_MAX_NODES = 1_000
_MAX_CONTAINER_ITEMS = 1_000
_MAX_KEY_LENGTH = 200
_MAX_STRING_LENGTH = 20_000

_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "bearer_token",
    "cookie",
    "instruction",
    "internal_path",
    "ipc_token",
    "file_path",
    "password",
    "path",
    "relative_path",
    "secret",
    "stack",
    "task",
    "traceback",
}
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?:authorization|bearer[_-]?token|ipc[_-]?token|api[_-]?key|token|password|secret|cookie)\s*[:=]"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{4,}")
_TRACEBACK_VALUE = re.compile(r"(?i)(?:traceback\s*\(|most recent call last|input_value\s*=)")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?:^|[\s('\"])[A-Z]:[\\/]")
_UNC_PATH = re.compile(r"(?:^|[\s('\"])[\\/]{2}[^\\/\s]+[\\/]")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s('\"])/(?:home|Users|root|private|var|etc|tmp|opt|mnt|srv|proc|sys|dev)/")


class RemotePayloadPrivacyError(ValueError):
    """远程载荷包含敏感、无界或非 JSON 值。"""


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.active_containers: set[int] = set()

    def consume(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise RemotePayloadPrivacyError("远程载荷节点数超过安全上限")


def validate_remote_payload(value: Any) -> Any:
    """验证远程可见载荷并原样返回；不执行隐式脱敏或类型转换。"""

    _walk(value, depth=0, budget=_Budget())
    return value


def _walk(value: Any, *, depth: int, budget: _Budget) -> None:
    budget.consume()
    if depth > _MAX_DEPTH:
        raise RemotePayloadPrivacyError("远程载荷嵌套深度超过安全上限")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RemotePayloadPrivacyError("远程载荷禁止 NaN 或 Infinity")
        return
    if isinstance(value, str):
        _validate_string(value)
        return
    if isinstance(value, dict):
        _enter_container(value, budget)
        try:
            if len(value) > _MAX_CONTAINER_ITEMS:
                raise RemotePayloadPrivacyError("远程载荷对象字段数超过安全上限")
            for key, child in value.items():
                if not isinstance(key, str):
                    raise RemotePayloadPrivacyError("远程载荷对象键必须是字符串")
                _validate_key(key)
                _walk(child, depth=depth + 1, budget=budget)
        finally:
            budget.active_containers.remove(id(value))
        return
    if isinstance(value, list):
        _enter_container(value, budget)
        try:
            if len(value) > _MAX_CONTAINER_ITEMS:
                raise RemotePayloadPrivacyError("远程载荷数组长度超过安全上限")
            for child in value:
                _walk(child, depth=depth + 1, budget=budget)
        finally:
            budget.active_containers.remove(id(value))
        return
    raise RemotePayloadPrivacyError("远程载荷包含非 JSON 类型")


def _enter_container(value: object, budget: _Budget) -> None:
    identity = id(value)
    if identity in budget.active_containers:
        raise RemotePayloadPrivacyError("远程载荷禁止循环引用")
    budget.active_containers.add(identity)


def _validate_key(key: str) -> None:
    if not key or len(key) > _MAX_KEY_LENGTH or any(ord(char) < 0x20 for char in key):
        raise RemotePayloadPrivacyError("远程载荷对象键非法")
    normalized = key.casefold().replace("-", "_")
    if (
        normalized in _FORBIDDEN_KEYS
        or normalized.startswith("authorization_")
        or normalized.endswith(("_token", "_secret", "_password", "_cookie", "_path", "_api_key"))
    ):
        raise RemotePayloadPrivacyError("远程载荷禁止敏感字段")


def _validate_string(value: str) -> None:
    if len(value) > _MAX_STRING_LENGTH:
        raise RemotePayloadPrivacyError("远程载荷字符串超过安全上限")
    if any(char in value for char in ("\x00", "\r")):
        raise RemotePayloadPrivacyError("远程载荷字符串包含控制字符")
    if (
        _SENSITIVE_ASSIGNMENT.search(value)
        or _BEARER_VALUE.search(value)
        or _TRACEBACK_VALUE.search(value)
        or _WINDOWS_ABSOLUTE_PATH.search(value)
        or _UNC_PATH.search(value)
        or _POSIX_ABSOLUTE_PATH.search(value)
    ):
        raise RemotePayloadPrivacyError("远程载荷疑似包含凭据、异常内部信息或绝对路径")


__all__ = [
    "M2_REMOTE_PAYLOAD_POLICY_VERSION",
    "RemotePayloadPrivacyError",
    "validate_remote_payload",
]
