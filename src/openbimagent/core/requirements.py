"""F8 组织级 requirements 锁定(grok requirements.toml 语义)。

组织/部署可声明一组"无论如何都必须 DENY"的权限键,覆盖任何角色 frontmatter
与项目级配置——防 agent 经角色配置把自己的危险操作降到 allow。

来源优先级:OPENBIMAGENT_REQUIREMENTS_FILE 环境变量 → <repo>/.openbimagent/requirements.toml。
缺失视为无组织锁(不阻断)。TOML 形如:
    [deny]
    tools = ["bash:rm -rf *", "mcp_call:*.execute_code"]
    bypass_permissions = true   # 锁死"跳过审批"能力(本项目无该模式,占位向前兼容)
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from functools import lru_cache
from typing import Any


def _requirements_path() -> Path | None:
    override = os.environ.get("OPENBIMAGENT_REQUIREMENTS_FILE")
    if override:
        return Path(override)
    repo = Path(__file__).resolve().parents[3]
    candidate = repo / ".openbimagent" / "requirements.toml"
    return candidate if candidate.is_file() else None


@lru_cache(maxsize=1)
def _load_locks() -> frozenset[str]:
    path = _requirements_path()
    if path is None:
        return frozenset()
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return frozenset()
    deny = data.get("deny") or {}
    tools = deny.get("tools") if isinstance(deny, dict) else None
    if not isinstance(tools, list):
        return frozenset()
    return frozenset(str(item) for item in tools)


def is_org_denied(permission_key: str) -> bool:
    """权限键是否被组织 requirements 强制 DENY(覆盖角色配置)。"""
    locks = _load_locks()
    if not locks:
        return False
    base = permission_key.split(":", 1)[0]
    return permission_key in locks or base in locks or any(
        _glob_match(pattern, permission_key) for pattern in locks
    )


def _glob_match(pattern: str, value: str) -> bool:
    if "*" not in pattern and "?" not in pattern:
        return False
    import fnmatch

    return fnmatch.fnmatchcase(value, pattern)


def refresh_requirements() -> None:
    """测试/配置热重载:清缓存重读。"""
    _load_locks.cache_clear()


__all__ = ["is_org_denied", "refresh_requirements"]
