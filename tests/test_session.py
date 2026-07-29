"""SessionStore 分支能力单测:fork / find_event / get_event_chain(Relay 014 任务 D1)。

覆盖 /tree 回退所需的三个新方法:
- fork:复制根 → from_event_id 主干链到新 session,并在 index.json 标记 forked_from
- find_event:按 event_id 查找事件
- get_event_chain:反向遍历 parentId 构建从根到目标的事件链

与 test_session_store.py 互补:后者覆盖 append/load/branch/index/snapshot 基础能力。
"""

from __future__ import annotations

import json

import pytest

from openbimagent.session.schema import EventType
from openbimagent.session.store import INDEX_FILENAME, SessionStore


@pytest.fixture()
def sessions_dir(tmp_path):
    return tmp_path / "sessions"


@pytest.fixture()
def store(sessions_dir):
    return SessionStore.create(sessions_dir, title="分支测试会话", playbook="single_asset_hero")


def _append_chain(store: SessionStore, n: int) -> list:
    """向 store 追加 n 条 message 事件(自动链式挂载),返回事件列表。"""
    return [
        store.append_new(EventType.MESSAGE, {"role": "user", "content": f"事件{i}"})
        for i in range(n)
    ]


# ---------- fork ----------


def test_store_fork_creates_new_session(store, sessions_dir) -> None:
    """fork(from_event_id):复制根→from_event_id 主干链到新 session,事件 id 一致。"""
    events = _append_chain(store, 5)
    forked = store.fork(events[2].id)
    assert forked.path != store.path and forked.path.is_file()
    forked_events = forked.load()
    assert [e.id for e in forked_events] == [events[0].id, events[1].id, events[2].id]
    assert forked.head == events[2].id  # 新分支头指针 = 分支点


def test_store_fork_updates_index_with_forked_from(store, sessions_dir) -> None:
    """fork 后 index.json 新会话条目含 forked_from(parent_session_id / parent_event_id)。"""
    events = _append_chain(store, 3)
    forked = store.fork(events[1].id)
    index = json.loads((sessions_dir / INDEX_FILENAME).read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in index.get("sessions", [])}
    assert forked.session_id in by_id
    fork_info = by_id[forked.session_id]["forked_from"]
    assert fork_info["parent_session_id"] == store.session_id
    assert fork_info["parent_event_id"] == events[1].id


def test_store_fork_invalid_event_id_raises(store) -> None:
    """fork 不存在的 event_id → ValueError(与 branch 的 KeyError 区分)。"""
    with pytest.raises(ValueError):
        store.fork("不存在的id")


# ---------- find_event ----------


def test_store_find_event_returns_event(store) -> None:
    """find_event(event.id) 返回该事件对象。"""
    event = store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "你好"})
    found = store.find_event(event.id)
    assert found is not None
    assert found.id == event.id
    assert found.payload.content == "你好"


def test_store_find_event_not_found_returns_none(store) -> None:
    """find_event 不存在的 id 返回 None。"""
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "x"})
    assert store.find_event("不存在的id") is None


# ---------- get_event_chain ----------


def test_store_get_event_chain(store) -> None:
    """get_event_chain(until_event_id):返回从根到目标的正向链,长度 = 索引+1。"""
    events = _append_chain(store, 5)
    chain = store.get_event_chain(events[3].id)
    assert [e.id for e in chain] == [events[0].id, events[1].id, events[2].id, events[3].id]
    assert len(chain) == 4


def test_store_get_event_chain_defaults_to_head(store) -> None:
    """get_event_chain() 不传参 → 默认到 head,返回完整链。"""
    events = _append_chain(store, 5)
    chain = store.get_event_chain()
    assert [e.id for e in chain] == [e.id for e in events]
    assert chain[-1].id == store.head
