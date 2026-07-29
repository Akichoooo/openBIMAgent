"""Session JSONL 树读写(store)。

对应文档:
- docs/architecture/COMPONENTS.md §2.6 session
- docs/research/07_gemini_trace_observability.md §1 观测后端决策(纯文件原地 JSONL,不接 Langfuse)

每会话一个 JSONL 文件,事件只追加不可改;分支 = 复制到 from_event_id 为止的主干链,落新 session 文件。
`sessions/index.json` 是多会话索引(TUI 侧边栏数据源);快照在每次 MCP 写操作前自动落盘。
M3 按需离线导出 BIMBench 评测格式(草案见 07 §4,P1)。
"""

from __future__ import annotations

import hashlib
import json
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openbimagent.session.schema import (
    CustomPayload,
    EventType,
    MessagePayload,
    SessionEvent,
    SnapshotPayload,
    ToolCallPayload,
    new_event,
    uuid7,
)

INDEX_FILENAME = "index.json"
"""多会话索引文件名(位于 sessions 目录根部)。"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """单个 session JSONL 文件的读写与树操作(append-only,分支落新文件)。

    线程安全靠进程内锁;跨进程文件锁 TODO(M1)。损坏行容错:跳过并告警。
    """

    def __init__(self, path: Path, *, title: str | None = None, playbook: str | None = None) -> None:
        """打开(必要时创建)一个 session 文件,并在 index.json 中登记/更新本会话条目。"""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.RLock()  # append → _sync_index 会重入,需可重入锁
        self._head: str | None = None  # 当前分支头指针(最后一条事件 id)
        self._created_at = _utc_now_iso()
        self._title = title or "未命名会话"
        self._playbook = playbook
        # 重开已有会话时沿用索引里的元数据与头指针
        entry = self._index_entry()
        if entry is not None:
            self._title = title or entry.get("title", self._title)
            self._playbook = playbook or entry.get("playbook")
            self._created_at = entry.get("created_at", self._created_at)
        for event in self.load():
            self._head = event.id  # 文件顺序即追加顺序,最后一条即当前头
        self._sync_index()

    # ---------- 基本读写 ----------

    @property
    def session_id(self) -> str:
        return self.path.stem

    @property
    def head(self) -> str | None:
        """当前分支头事件 id;append_new 默认把新事件挂到它下面。"""
        return self._head

    def append(self, event: SessionEvent) -> None:
        """追加一条事件记录(序列化 + flush;子代理写各自的 child session 文件)。"""
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
            self._head = event.id
            self._sync_index()

    def append_new(
        self,
        type: EventType,
        payload: MessagePayload | ToolCallPayload | CustomPayload | dict[str, Any],
        parent_id: str | None = None,
    ) -> SessionEvent:
        """构造(自动 uuid + 单调 timestamp)并追加;parent_id 缺省挂当前头指针。"""
        event = new_event(type, payload, parent_id=parent_id if parent_id is not None else self._head)
        self.append(event)
        return event

    def load(self) -> list[SessionEvent]:
        """全量读取(文件顺序),并重建 id → event 与 parentId → children 索引。"""
        events: list[SessionEvent] = []
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(SessionEvent.model_validate(json.loads(line)))
            except Exception as exc:  # 损坏行容错:跳过并告警
                warnings.warn(f"{self.path}:{lineno} 损坏行已跳过: {exc}", stacklevel=2)
        self._by_id = {e.id: e for e in events}
        self._children: dict[str | None, list[str]] = {}
        for e in events:
            self._children.setdefault(e.parentId, []).append(e.id)
        return events

    def children(self, event_id: str) -> list[SessionEvent]:
        """某事件的直接子事件(/tree 分支浏览用)。"""
        self.load()
        return [self._by_id[cid] for cid in self._children.get(event_id, [])]

    # ---------- 分支与快照 ----------

    def branch(self, from_id: str, *, title: str | None = None) -> SessionStore:
        """`/tree` 分支:复制根 → from_id 的主干链到新 session 文件,返回新 store。"""
        self.load()
        if from_id not in self._by_id:
            raise KeyError(f"事件 {from_id!r} 不在会话 {self.session_id!r} 中")
        chain: list[SessionEvent] = []
        cursor: str | None = from_id
        while cursor is not None:
            event = self._by_id[cursor]
            chain.append(event)
            cursor = event.parentId
        chain.reverse()
        new_store = SessionStore(
            self.path.parent / f"{uuid7()}.jsonl",
            title=title or f"{self._title} 的分支",
            playbook=self._playbook,
        )
        for event in chain:
            new_store.append(event)
        return new_store

    def fork(self, from_event_id: str, *, title: str | None = None) -> SessionStore:
        """`/tree` 分支(M1 强化):复制根 → from_event_id 的主干链到新 session,并在 index.json
        标记 forked_from 关系(parent_session_id / parent_event_id),供 pipeline 检测续跑。

        与 branch 的差异:fork 写 forked_from 元数据(管道据此触发 Clarify 续跑)、
        不存在时抛 ValueError(branch 抛 KeyError);两者复制主干链的逻辑一致。
        """
        events = self.load()
        fork_index = None
        for i, event in enumerate(events):
            if event.id == from_event_id:
                fork_index = i
                break
        if fork_index is None:
            raise ValueError(f"事件 {from_event_id!r} 不在会话 {self.session_id!r} 中")
        new_title = title or f"{self._title} · 分支自 {from_event_id[:8]}"
        new_session = SessionStore(
            self.path.parent / f"{uuid7()}.jsonl",
            title=new_title,
            playbook=self._playbook,
        )
        for event in events[: fork_index + 1]:
            new_session.append(event)
        new_session._mark_fork(self.session_id, from_event_id)
        return new_session

    def find_event(self, event_id: str) -> SessionEvent | None:
        """根据 event_id 查找事件;不存在返回 None(/tree 选择回退点用)。"""
        for event in self.load():
            if event.id == event_id:
                return event
        return None

    def get_event_chain(self, until_event_id: str | None = None) -> list[SessionEvent]:
        """获取从根到指定事件的事件链(按时间正向顺序);until_event_id 缺省取当前 head。

        通过 parentId 反向遍历构建;until_event_id 不存在抛 ValueError;空会话返回 []。
        """
        events = self.load()
        event_map = {e.id: e for e in events}
        target_id = until_event_id if until_event_id is not None else self._head
        if target_id is None:
            return []
        if target_id not in event_map:
            raise ValueError(f"事件 {target_id!r} 不在会话 {self.session_id!r} 中")
        chain: list[SessionEvent] = []
        cursor: str | None = target_id
        while cursor is not None:
            event = event_map[cursor]
            chain.append(event)
            cursor = event.parentId
        return list(reversed(chain))

    def _mark_fork(self, parent_session_id: str, parent_event_id: str) -> None:
        """在 index.json 本会话条目里写入 forked_from(parent_session_id / parent_event_id)。

        _sync_index 的 update 只覆盖 title/playbook/last_active/event_count,不会删 forked_from,
        故后续 append 触发的 _sync_index 保留本字段。
        """
        index_path = self._index_path()
        index: dict[str, Any] = {"sessions": []}
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index.get("sessions", []):
            if entry.get("id") == self.session_id:
                entry["forked_from"] = {
                    "parent_session_id": parent_session_id,
                    "parent_event_id": parent_event_id,
                }
                break
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_snapshot(self, blend_file_path: Path, file_hash: str | None = None) -> SessionEvent:
        """MCP 写操作前自动落盘 snapshot 事件(回滚点);hash 缺省时按文件内容 sha256。"""
        p = Path(blend_file_path)
        if file_hash is None:
            if not p.is_file():
                raise FileNotFoundError(f"快照目标不存在: {p}")
            file_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        return self.append_new(
            EventType.CUSTOM,
            {"customType": "snapshot", "blend_file_path": str(p), "hash": file_hash},
        )

    # ---------- 导出与多会话索引 ----------

    def export_jsonl(self, out_path: Path) -> None:
        """原样导出全部事件行(JSONL),供回放/外部分析。"""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    def export_bimbench(self, out_path: Path) -> None:
        """离线导出 BIMBench 评测格式(草案见 07 报告 §4)。

        TODO(M3): trajectory / final_artefacts / critic_scores 提炼。
        """
        raise NotImplementedError("TODO(M3): export_bimbench")

    @classmethod
    def create(
        cls,
        sessions_dir: Path,
        *,
        title: str = "未命名会话",
        playbook: str | None = None,
    ) -> "SessionStore":
        """在 sessions_dir 下新建一个会话(uuid7 文件名)并登记索引。"""
        return cls(Path(sessions_dir) / f"{uuid7()}.jsonl", title=title, playbook=playbook)

    @classmethod
    def list_sessions(cls, sessions_dir: Path) -> list[dict[str, Any]]:
        """读 sessions/index.json,按 last_active 倒序返回会话条目(侧边栏数据源)。"""
        index_path = Path(sessions_dir) / INDEX_FILENAME
        if not index_path.is_file():
            return []
        entries = json.loads(index_path.read_text(encoding="utf-8")).get("sessions", [])
        return sorted(entries, key=lambda e: e.get("last_active", ""), reverse=True)

    def _index_path(self) -> Path:
        return self.path.parent / INDEX_FILENAME

    def _index_entry(self) -> dict[str, Any] | None:
        index_path = self._index_path()
        if not index_path.is_file():
            return None
        for entry in json.loads(index_path.read_text(encoding="utf-8")).get("sessions", []):
            if entry.get("id") == self.session_id:
                return entry
        return None

    def _sync_index(self) -> None:
        """把本会话的 id/title/playbook/created_at/last_active/event_count 写进 index.json。"""
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
            event_count = sum(1 for line in text.splitlines() if line.strip())
        index_path = self._index_path()
        index: dict[str, Any] = {"sessions": []}
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = self._index_entry() or {"id": self.session_id, "created_at": self._created_at}
        entry.update(
            {
                "title": self._title,
                "playbook": self._playbook,
                "last_active": _utc_now_iso(),
                "event_count": event_count,
            }
        )
        sessions = [e for e in index.get("sessions", []) if e.get("id") != self.session_id]
        sessions.append(entry)
        index["sessions"] = sessions
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["SessionStore", "SessionEvent", "SnapshotPayload"]
