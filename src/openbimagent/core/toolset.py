"""工具集预设（P0-3）：运行时能力面收敛，对标 Claude Code 的 toolset 切换语义。

- ``minimal``：仅 ``solver:*``（纯计算，不触宿主写盘）；
- ``modeling``：``solver:*`` + ``cad_host:*``（求解 + 双宿主建模）；
- ``full``：不过滤（缺省）。

过滤作用于两个层面（同一份真值）：
1. ``/api/v1/plugins`` 清单输出（前端可见面）；
2. ``/api/v1/plugins/invoke`` 调用门（被滤能力返回 403，fail-closed）。
"""

from __future__ import annotations

import os
import threading
from typing import Any

TOOLSET_PRESETS: dict[str, tuple[str, ...] | None] = {
    "minimal": ("solver:",),
    "modeling": ("solver:", "cad_host:"),
    "full": None,
}

_LOCK = threading.Lock()
_CURRENT: str | None = None


def current_toolset() -> str:
    """当前预设名（env OPENBIMAGENT_TOOLSET 为初始值，缺省 full）。"""
    global _CURRENT
    with _LOCK:
        if _CURRENT is None:
            initial = os.environ.get("OPENBIMAGENT_TOOLSET", "full")
            _CURRENT = initial if initial in TOOLSET_PRESETS else "full"
        return _CURRENT


def set_toolset(name: str) -> str:
    """切换预设；未知名抛 ValueError（fail-closed）。"""
    if name not in TOOLSET_PRESETS:
        raise ValueError(f"未知工具集预设: {name}（可选：{sorted(TOOLSET_PRESETS)}）")
    global _CURRENT
    with _LOCK:
        _CURRENT = name
    return name


def reset_toolset() -> None:
    """测试隔离：恢复 env 初始语义。"""
    global _CURRENT
    with _LOCK:
        _CURRENT = None


def is_allowed(capability: str) -> bool:
    """能力在当前预设下是否可调用。"""
    prefixes = TOOLSET_PRESETS[current_toolset()]
    if prefixes is None:
        return True
    return any(capability.startswith(p) for p in prefixes)


def filter_capabilities(capabilities_map: dict[str, Any]) -> dict[str, Any]:
    """过滤清单（键 = 能力名）。"""
    prefixes = TOOLSET_PRESETS[current_toolset()]
    if prefixes is None:
        return dict(capabilities_map)
    return {k: v for k, v in capabilities_map.items() if any(k.startswith(p) for p in prefixes)}
