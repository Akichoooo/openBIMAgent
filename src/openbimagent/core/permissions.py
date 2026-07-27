"""权限三态(ask/allow/deny)+ 工具 glob 匹配。

对应文档:
- docs/architecture/COMPONENTS.md §7 安全与权限(定稿)
- docs/architecture/COMPONENTS.md §2.4 agents frontmatter 的 permissions 字段

默认策略:读 allow、MCP 写 ask、`execute_*_code` ask;
execute_blender_code / run_script 另走 AST allowlist + 操作前快照 + 危险 API 审批(§7)。
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum
from typing import Mapping


class Permission(StrEnum):
    """权限三态:ask=弹审批,allow=直接放行,deny=硬拒。"""

    ASK = "ask"
    ALLOW = "allow"
    DENY = "deny"


DEFAULT_RULES: dict[str, Permission] = {
    "read": Permission.ALLOW,
    "mcp_*": Permission.ASK,
    "execute_*_code": Permission.ASK,
}
"""全局默认规则(COMPONENTS §7);角色 frontmatter 的 permissions 在此基础上覆盖。"""


def _is_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[")


def check_permission(tool_name: str, rules: Mapping[str, Permission] | None = None) -> Permission:
    """按工具名查权限:精确名 > glob(fnmatch 风格,最长 pattern 优先)> 默认 ask。

    tool_name 可带参数摘要(如 ``bash:rm -rf /``),此时同时试全名与基名(``:`` 前段),
    规则如 ``bash:rm *`` 可精确拦截危险命令。合并顺序:DEFAULT_RULES ← 角色 frontmatter 覆盖。
    """
    merged: dict[str, Permission] = {**DEFAULT_RULES, **(rules or {})}
    base = tool_name.split(":", 1)[0]
    # 优先级:精确全名 > glob 全名(最长 pattern)> 精确基名 > glob 基名(最长 pattern)> 默认 ask
    if tool_name in merged and not _is_glob(tool_name):
        return merged[tool_name]
    for key in (tool_name, base):
        if key != tool_name and key in merged and not _is_glob(key):
            return merged[key]
        best: Permission | None = None
        best_len = -1
        for pattern, perm in merged.items():
            if _is_glob(pattern) and fnmatch.fnmatchcase(key, pattern) and len(pattern) > best_len:
                best, best_len = perm, len(pattern)
        if best is not None:
            return best
    return Permission.ASK
