"""会话全文检索（P0-2；对齐 Hermes FTS5 session search，stdlib sqlite3 零新依赖）。

设计：
- FTS5 表存 (event_id, session_id, type, content, ts)；unicode61 分词（CJK 按字命中）。
- 索引文件随会话 JSONL 增量更新（按行数水位线）；``search`` 返回可溯源 (session_id, event_id, snippet)。
- 与归档范例检索互补：本模块查"历史上说过什么"，归档检索查"交付过什么"。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    ts TEXT,
    content TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    content, session_id UNINDEXED, event_id UNINDEXED,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS watermarks(session_id TEXT PRIMARY KEY, lines INTEGER NOT NULL);
"""


def _event_text(payload: dict[str, Any]) -> str:
    """从事件 payload 提取可检索文本（message 内容/工具名与摘要/custom 类型）。"""
    parts: list[str] = []
    for key in ("content", "toolName", "args_summary", "result_ui_view", "customType", "operation", "decision", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return " ".join(parts)


def _char_bigrams(text: str) -> list[str]:
    """CJK 连续串的字符 bigram 集（FTS5 unicode61 对 CJK 整串成单 token，子串命中靠 bigram 展开）。"""
    out: list[str] = []
    run = ""
    for ch in text.lower():
        if ch.isascii() or not ch.isalnum():
            if run:
                out.extend([run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)])
                run = ""
            continue
        run += ch
    if run:
        out.extend([run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)])
    return out


def _index_text(text: str) -> str:
    """索引入库文本 = 原文 + CJK bigram 展开（拉丁词仍按原 token 命中）。"""
    return text + " " + " ".join(_char_bigrams(text))


def _fts_query_of(query: str) -> str:
    """查询构造：CJK 词转 bigram AND、拉丁词前缀 AND；特殊字符已在外层清洗。"""
    terms = [t for t in "".join(c if (c.isalnum() or c.isspace()) else " " for c in query).split() if t]
    parts: list[str] = []
    for term in terms[:8]:
        if any(not c.isascii() for c in term):
            parts.extend(f'"{b}"' for b in _char_bigrams(term))
        else:
            parts.append(f'"{term.lower()}"*')
    return " AND ".join(parts)


class SessionSearchIndex:
    """sessions 目录的 FTS5 增量索引（线程安全；测试可指向任意 db 路径）。"""

    def __init__(self, sessions_dir: Path, db_path: Path) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------- 索引维护 ----------

    def sync(self) -> int:
        """按水位线增量索引所有会话文件的新增行；返回新索引事件数。"""
        added = 0
        if not self.sessions_dir.is_dir():
            return 0
        for path in self.sessions_dir.glob("*.jsonl"):
            session_id = path.stem
            with self._lock:
                row = self._conn.execute("SELECT lines FROM watermarks WHERE session_id=?", (session_id,)).fetchone()
                seen = row[0] if row else 0
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            new_lines = lines[seen:]
            if not new_lines:
                continue
            records: list[tuple[str, str, str, str, str]] = []
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") or {}
                text = _event_text(payload)
                if not text:
                    continue
                records.append(
                    (
                        str(event.get("id", "")),
                        session_id,
                        str(event.get("type", "")),
                        str(event.get("timestamp", "")),
                        text,
                    )
                )
            with self._lock:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO events(event_id, session_id, type, ts, content) VALUES(?,?,?,?,?)",
                    records,
                )
                self._conn.executemany(
                    "INSERT OR REPLACE INTO events_fts(event_id, session_id, content) VALUES(?,?,?)",
                    [(r[0], r[1], _index_text(r[4])) for r in records],
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO watermarks(session_id, lines) VALUES(?,?)", (session_id, len(lines))
                )
                self._conn.commit()
            added += len(records)
        return added

    def rebuild(self) -> int:
        """全量重建（索引损坏/首次）。返回索引事件数。"""
        with self._lock:
            self._conn.executescript("DELETE FROM events_fts; DELETE FROM events; DELETE FROM watermarks;")
            self._conn.commit()
        return self.sync()

    def delete_session(self, session_id: str) -> int:
        """删除会话的全部索引行（会话删除端点的一致性清理）；返回清理行数。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM events_fts WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM watermarks WHERE session_id=?", (session_id,))
            self._conn.commit()
            return cur.rowcount

    # ---------- 检索 ----------

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """FTS5 全文检索；返回可溯源命中（session_id/event_id/type/ts/snippet）。"""
        query = query.strip()
        if not query:
            return []
        fts_query = _fts_query_of(query)
        if not fts_query:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.event_id, e.session_id, e.type, e.ts, snippet(events_fts, 0, '[', ']', '…', 24) "
                "FROM events_fts f JOIN events e ON e.event_id = f.event_id "
                "WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, max(1, min(limit, 50))),
            ).fetchall()
        return [
            {"event_id": r[0], "session_id": r[1], "type": r[2], "ts": r[3], "snippet": r[4]} for r in rows
        ]


_DEFAULT: SessionSearchIndex | None = None
_DEFAULT_LOCK = threading.Lock()


def default_search_index(sessions_dir: Path) -> SessionSearchIndex:
    """进程级默认索引（db 放 sessions_dir/index_fts.db；测试请自建实例隔离）。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None or _DEFAULT.sessions_dir != Path(sessions_dir):
            _DEFAULT = SessionSearchIndex(Path(sessions_dir), Path(sessions_dir) / "index_fts.db")
        return _DEFAULT
