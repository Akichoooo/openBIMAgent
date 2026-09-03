"""FTS5 会话全文检索测试（P0-2）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openbimagent.session.schema import EventType
from openbimagent.session.search import SessionSearchIndex
from openbimagent.session.store import SessionStore


@pytest.fixture()
def ws(tmp_path: Path) -> SessionSearchIndex:
    sessions = tmp_path / "sessions"
    store = SessionStore(sessions / "s1.jsonl", title="管网任务")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "生成 DN400 污水重力管，避让东侧建筑物"})
    store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "已调度自愈求解器，净距 3.1m 合规"})
    store2 = SessionStore(sessions / "s2.jsonl", title="街区任务")
    store2.append_new(EventType.MESSAGE, {"role": "user", "content": "江户赛博街区资产装配"})
    return SessionSearchIndex(sessions, tmp_path / "fts.db")


def test_search_finds_cjk_keyword(ws: SessionSearchIndex) -> None:
    ws.sync()
    hits = ws.search("污水")
    assert len(hits) == 1
    assert hits[0]["session_id"] == "s1" and hits[0]["event_id"]
    assert "污水" in hits[0]["snippet"]


def test_search_ranks_and_scopes(ws: SessionSearchIndex) -> None:
    ws.sync()
    assert {h["session_id"] for h in ws.search("建筑物")} == {"s1"}
    assert {h["session_id"] for h in ws.search("赛博")} == {"s2"}
    assert ws.search("不存在的词xyzzy") == []


def test_incremental_sync_after_new_append(ws: SessionSearchIndex, tmp_path: Path) -> None:
    ws.sync()
    store = SessionStore(tmp_path / "sessions" / "s2.jsonl")
    store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "新增：覆土深度 1.42m 验收通过"})
    added = ws.sync()
    assert added >= 1
    assert {h["session_id"] for h in ws.search("覆土")} == {"s2"}


def test_query_sanitization(ws: SessionSearchIndex) -> None:
    ws.sync()
    # 注入式查询不报错（特殊字符被清洗）
    assert isinstance(ws.search('污水 OR "建筑物" NEAR/0 (malformed)'), list)
    assert ws.search("") == [] and ws.search("***") == []


def test_rebuild(ws: SessionSearchIndex) -> None:
    ws.sync()
    assert ws.rebuild() >= 3
    assert len(ws.search("污水")) == 1
