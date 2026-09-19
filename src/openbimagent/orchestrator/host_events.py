"""A6 宿主事件通道(claude channel 语义:宿主主动推事件进会话,替代轮询)。

BIM 宿主(Blender/Vectorworks)的渲染/批量操作完成时,由宿主侧进程把事件推到
会话 JSONL 树,LLM/工作台下一轮即可看到,不必反复 ping 轮询。

本模块是通道的最小落地:进程内队列 + 可选直接落 session 事件;宿主侧 runner
通过 push() 发布,消费方 drain() 取走。跨进程投递沿用文件 IPC(与 vectorworks
runner 同通道),不在本模块范围。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


@dataclass(frozen=True)
class HostEvent:
    """一条宿主侧事件(kind: render_complete / export_done / host_error / batch_done)。"""

    kind: str
    host: str
    payload: dict[str, Any]


class HostEventChannel:
    """进程内宿主事件通道;subscribe 的会话在 push 时同步落 event,保证可审计。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: list[HostEvent] = []
        self._subscriptions: list[SessionStore] = []

    def subscribe(self, session: SessionStore) -> None:
        """把一个会话登记为事件落盘目标(重复登记幂等)。"""
        with self._lock:
            if session not in self._subscriptions:
                self._subscriptions.append(session)

    def unsubscribe(self, session: SessionStore) -> None:
        with self._lock:
            if session in self._subscriptions:
                self._subscriptions.remove(session)

    def push(self, event: HostEvent) -> None:
        """宿主侧发布:入队 + 对所有订阅会话落 custom 事件(宿主主动推,非轮询)。"""
        with self._lock:
            self._queue.append(event)
            targets = list(self._subscriptions)
        for session in targets:
            session.append_new(
                EventType.CUSTOM,
                {
                    "customType": "host_event",
                    "kind": event.kind,
                    "host": event.host,
                    **dict(event.payload),
                },
            )

    def drain(self) -> list[HostEvent]:
        """取走并清空未消费事件(消费方轮询入口,与落盘解耦)。"""
        with self._lock:
            drained, self._queue = self._queue, []
        return drained


_DEFAULT: HostEventChannel | None = None
_DEFAULT_LOCK = threading.Lock()


def default_host_channel() -> HostEventChannel:
    """进程级默认通道(宿主 runner 与 AgentLoop 同进程时共享)。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = HostEventChannel()
        return _DEFAULT


__all__ = ["HostEvent", "HostEventChannel", "default_host_channel"]
