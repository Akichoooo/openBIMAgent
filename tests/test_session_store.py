"""SessionStore 单测:append/load/branch/index 多会话/快照事件(07 报告 §2;COMPONENTS §2.6)。"""

import hashlib
import json
import warnings

import pytest

from openbimagent.session.schema import CustomType, EventType
from openbimagent.session.store import SessionStore


@pytest.fixture()
def sessions_dir(tmp_path):
    return tmp_path / "sessions"


@pytest.fixture()
def store(sessions_dir):
    return SessionStore.create(sessions_dir, title="测试会话", playbook="single_asset_hero")


def test_append_and_load_roundtrip(store) -> None:
    """append 自动 uuid + 单调 timestamp;parentId 链式挂载;load 完整往返。"""
    e1 = store.append_new(EventType.MESSAGE, {"role": "user", "content": "盖一条江户街区"})
    e2 = store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "收到,先澄清"})
    assert e1.id and e2.id and e1.id != e2.id
    assert e1.parentId is None and e2.parentId == e1.id
    assert e2.timestamp > e1.timestamp  # 单调递增

    events = store.load()
    assert [e.id for e in events] == [e1.id, e2.id]
    assert events[0].payload.role == "user"
    # JSONL 只追加:文件行数 = 事件数
    lines = [ln for ln in store.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:  # 每行都是合法 JSON 且含五元组
        rec = json.loads(line)
        assert set(rec) == {"id", "parentId", "timestamp", "type", "payload"}


def test_children(store) -> None:
    """children(id) 返回直接子事件(/tree 分支浏览)。"""
    root = store.append_new(EventType.MESSAGE, {"role": "user", "content": "root"})
    child = store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "child"}, parent_id=root.id)
    assert [e.id for e in store.children(root.id)] == [child.id]
    assert store.children(child.id) == []


def test_branch_creates_new_session_file(store, sessions_dir) -> None:
    """branch(from_id):复制根→from_id 主干链到新 session 文件,索引多一条。"""
    e1 = store.append_new(EventType.MESSAGE, {"role": "user", "content": "第一步"})
    e2 = store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "第二步"})
    store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "第三步(不带进分支)"})

    fork = store.branch(e2.id)
    assert fork.path != store.path and fork.path.is_file()
    fork_events = fork.load()
    assert [e.id for e in fork_events] == [e1.id, e2.id]
    assert fork.head == e2.id  # 新分支头指针 = 分支点,后续追加挂其下
    e3 = fork.append_new(EventType.MESSAGE, {"role": "user", "content": "分支重跑"})
    assert e3.parentId == e2.id
    assert len(SessionStore.list_sessions(sessions_dir)) == 2

    with pytest.raises(KeyError):
        store.branch("不存在的id")


def test_index_multi_sessions(sessions_dir) -> None:
    """sessions/index.json 多会话索引:id/title/playbook/created_at/last_active/event_count。"""
    s1 = SessionStore.create(sessions_dir, title="会话A", playbook="edo")
    s1.append_new(EventType.MESSAGE, {"role": "user", "content": "hi"})
    s2 = SessionStore.create(sessions_dir, title="会话B")
    entries = SessionStore.list_sessions(sessions_dir)
    assert len(entries) == 2
    by_id = {e["id"]: e for e in entries}
    a = by_id[s1.session_id]
    assert a["title"] == "会话A" and a["playbook"] == "edo"
    assert a["event_count"] == 1 and a["created_at"] and a["last_active"]
    assert by_id[s2.session_id]["event_count"] == 0
    # 重开已有会话:沿用索引元数据,不重置
    s1b = SessionStore(s1.path)
    assert s1b.head is not None
    assert len(SessionStore.list_sessions(sessions_dir)) == 2


def test_record_snapshot(store, tmp_path) -> None:
    """record_snapshot:路径 + sha256 落 custom 事件(customType=snapshot)。"""
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"fake-blend-bytes")
    event = store.record_snapshot(blend)
    assert event.type is EventType.CUSTOM
    assert event.payload.customType == CustomType.SNAPSHOT
    assert event.payload.hash == hashlib.sha256(b"fake-blend-bytes").hexdigest()
    # load 往返后仍是 snapshot 子型
    loaded = store.load()[-1]
    assert loaded.payload.customType == CustomType.SNAPSHOT
    assert loaded.payload.blend_file_path == str(blend)

    with pytest.raises(FileNotFoundError):
        store.record_snapshot(tmp_path / "不存在.blend")


def test_export_jsonl(store, tmp_path) -> None:
    """export_jsonl 原样导出全部事件行。"""
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "一"})
    store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "二"})
    out = tmp_path / "export" / "trace.jsonl"
    store.export_jsonl(out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"]["content"] == "一"


def test_corrupted_line_skipped(store) -> None:
    """损坏行容错:跳过并告警,其余事件正常加载。"""
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "好行"})
    with store.path.open("a", encoding="utf-8") as f:
        f.write("{这不是合法JSON\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        events = store.load()
    assert len(events) == 1 and caught
