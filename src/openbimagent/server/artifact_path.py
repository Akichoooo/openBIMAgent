"""M2 pre-G7 工件相对路径的纯函数失败关闭策略。

该模块只验证 Artifact Manifest 中用于未来下载定位的相对路径字符串；不访问文件系统，
不解析真实路径或符号链接，也不提供下载、Range 或 Content-Disposition 能力。
"""

from __future__ import annotations

import re

M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION = "0.1"
M2_ARTIFACT_RELATIVE_PATH_CHARS_MAX = 512

_WINDOWS_RESERVED_BASENAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|CONIN\$|CONOUT\$)$",
    re.IGNORECASE,
)


def is_m2_artifact_relative_path(value: str) -> bool:
    """判断值是否为规范、可移植且无 Windows 特殊路径语义的 POSIX 相对路径。"""

    if not isinstance(value, str) or not value or len(value) > M2_ARTIFACT_RELATIVE_PATH_CHARS_MAX:
        return False
    if value.startswith("/") or "\\" in value or any(char in value for char in '<>:"|?*\x00'):
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False

    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    for part in parts:
        if part.endswith((" ", ".")):
            return False
        basename = part.split(".", 1)[0]
        if _WINDOWS_RESERVED_BASENAME.fullmatch(basename):
            return False
    return True


def validate_m2_artifact_relative_path(value: str) -> str:
    """验证并原样返回工件相对路径；不做静默分隔符归一化。"""

    if not is_m2_artifact_relative_path(value):
        raise ValueError("工件相对路径不满足 M2 安全策略")
    return value


__all__ = [
    "M2_ARTIFACT_RELATIVE_PATH_CHARS_MAX",
    "M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION",
    "is_m2_artifact_relative_path",
    "validate_m2_artifact_relative_path",
]
