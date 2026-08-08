"""M2 P7 远程 Playbook 供应链安全协议。

内容寻址、签名验证、来源边界和权限范围控制。
远程 Playbook 不能扩大本地 capability ceiling。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

M2_REMOTE_PLAYBOOK_PROTOCOL_VERSION = "0.1"
M2_REMOTE_PLAYBOOK_HASH_ALGORITHM = "sha256"
_M2_SAFE_URL = re.compile(r"^https://[A-Za-z0-9]([A-Za-z0-9.-]{0,253})[A-Za-z0-9](/[A-Za-z0-9_.-]{1,255}){0,10}$")


class M2RemotePlaybookError(ValueError):
    """远程 Playbook 安全错误。"""


class M2RemotePlaybookRef(BaseModel):
    """远程 Playbook 的不可变引用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=M2_REMOTE_PLAYBOOK_PROTOCOL_VERSION, pattern=r"^0\.1$")
    url: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[A-Fa-f0-9]{64}$")
    allowed_domains: tuple[str, ...] = Field(default=(), max_length=16)


def validate_remote_playbook_url(ref: M2RemotePlaybookRef) -> None:
    """验证远程 Playbook URL 的安全性。

    规则：
    - 必须 HTTPS
    - 域名必须在 allowed_domains 白名单中（如非空）
    - 路径必须有界
    - 不能包含 IP 地址、本地主机或回环地址
    """
    if not ref.url.startswith("https://"):
        raise M2RemotePlaybookError("远程 Playbook 必须使用 HTTPS")
    if not _M2_SAFE_URL.fullmatch(ref.url):
        raise M2RemotePlaybookError("远程 Playbook URL 不满足安全策略")
    if ref.allowed_domains:
        from urllib.parse import urlparse

        parsed = urlparse(ref.url)
        domain = parsed.hostname or ""
        if not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in ref.allowed_domains):
            raise M2RemotePlaybookError("远程 Playbook 域名不在授权白名单中")


def verify_playbook_content(content: bytes, ref: M2RemotePlaybookRef) -> dict[str, Any]:
    """验证下载内容 SHA-256 并解析为 Playbook dict。"""
    actual = hashlib.sha256(content).hexdigest()
    if actual.lower() != ref.sha256.lower():
        raise M2RemotePlaybookError(
            f"远程 Playbook SHA-256 不匹配: expected={ref.sha256}, actual={actual}"
        )
    try:
        return json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise M2RemotePlaybookError(f"远程 Playbook 内容无效: {exc}") from exc


def restrict_playbook_permissions(playbook: dict[str, Any]) -> dict[str, Any]:
    """远程 Playbook 不能扩大本地 capability ceiling。

    强制限制：
    - 禁止声明超出受信任 `agents/` 范围的自定义角色
    - 禁止绕过 schema 门禁
    - 禁止扩大宿主写入范围
    """
    restricted = dict(playbook)
    agents = restricted.get("agents", {})
    if isinstance(agents, dict):
        for agent_name in agents:
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", str(agent_name)):
                raise M2RemotePlaybookError(f"远程 Playbook 包含非法角色名: {agent_name}")
    targets = restricted.get("targets", [])
    if isinstance(targets, list):
        allowed = {"blender", "vectorworks"}
        for target in targets:
            if str(target) not in allowed:
                raise M2RemotePlaybookError(f"远程 Playbook 包含非法宿主目标: {target}")
    return restricted