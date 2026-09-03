"""Hooks 生命周期事件总线（P1-2，对标 Claude Code hooks / OpenClaw 插件事件）。

四类事件：
- ``pre_tool``（能力调用前）：**可否决**——任一 handler 返回 False 或抛异常即 veto（fail-closed）；
- ``post_tool``（能力调用后，含异常路径）：观测型，handler 异常仅记录不扩散；
- ``turn_end``（一轮用户指令处理完）：观测型；
- ``run_end``（一次运行结束，finally 必触发）：观测型。

设计纪律：handler 注册即代码级（插件/测试/扩展点），不提供 HTTP 注册面（防注入）；
总线保留最近 200 次触发记录（ring buffer），供测试断言与审查回放。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

EVENTS = ("pre_tool", "post_tool", "turn_end", "run_end")
_VETO_EVENTS = {"pre_tool"}  # 仅 pre_tool 可否决
_RING_CAP = 200


class HookVeto(PermissionError):
    """pre_tool handler 否决：能力调用被钩子阻断。"""


class HookBus:
    """线程安全的生命周期事件总线。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[str, list[Callable[..., Any]]] = {e: [] for e in EVENTS}
        self.fired: list[dict[str, Any]] = []  # ring buffer（测试/审查回放）

    def register(self, event: str, handler: Callable[..., Any]) -> None:
        if event not in self._handlers:
            raise ValueError(f"未知 hook 事件: {event}（可选：{EVENTS}）")
        with self._lock:
            self._handlers[event].append(handler)

    def unregister(self, event: str, handler: Callable[..., Any]) -> None:
        with self._lock:
            if handler in self._handlers.get(event, []):
                self._handlers[event].remove(handler)

    def clear(self) -> None:
        """测试隔离：清空全部 handler 与触发记录。"""
        with self._lock:
            for event in self._handlers:
                self._handlers[event] = []
            self.fired = []

    def _record(self, event: str, payload: dict[str, Any], outcome: str) -> None:
        with self._lock:
            self.fired.append({"event": event, "at": time.time(), "outcome": outcome, **{k: str(v)[:200] for k, v in payload.items()}})
            if len(self.fired) > _RING_CAP:
                del self.fired[: len(self.fired) - _RING_CAP]

    def emit(self, event: str, **payload: Any) -> None:
        """观测型事件（post_tool/turn_end/run_end）：handler 异常仅记录，不扩散。"""
        if event in _VETO_EVENTS:
            raise ValueError(f"{event} 走 check()（可否决语义），不允许 emit")
        with self._lock:
            handlers = list(self._handlers[event])
        errors: list[str] = []
        for handler in handlers:
            try:
                handler(**payload)
            except Exception as exc:  # noqa: BLE001 — 观测型 handler 必须隔离
                errors.append(f"{handler}: {exc}")
        self._record(event, payload, "ok" if not errors else f"handler_errors:{len(errors)}")

    def check(self, event: str, **payload: Any) -> None:
        """可否决事件（pre_tool）：任一 handler 返回 False 或抛异常 → HookVeto。"""
        if event not in _VETO_EVENTS:
            raise ValueError(f"{event} 不可否决，请用 emit()")
        with self._lock:
            handlers = list(self._handlers[event])
        for handler in handlers:
            try:
                verdict = handler(**payload)
            except Exception as exc:  # noqa: BLE001 — fail-closed：handler 崩溃视为否决
                self._record(event, payload, f"veto:handler_crash:{exc}")
                raise HookVeto(f"hook {handler} 异常，fail-closed 视为否决: {exc}") from exc
            if verdict is False:
                self._record(event, payload, f"veto:{handler}")
                raise HookVeto(f"hook {handler} 否决了 {payload.get('capability', event)}")
        self._record(event, payload, "ok")


_DEFAULT: HookBus | None = None
_DEFAULT_LOCK = threading.Lock()


def default_hook_bus() -> HookBus:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = HookBus()
        return _DEFAULT


def reset_hook_bus() -> None:
    """测试隔离：清空默认总线的 handler 与记录。"""
    default_hook_bus().clear()
