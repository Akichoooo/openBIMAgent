"""Session v2 操作测试:投影式 rewind、fork 双语义、无 LLM 摘要、prompt 快照。

- B9 rewind:head 指针移动(文件字节不变,append-only),旧分支可回访;
- B7 fork mode=interrupted:打断式分叉带 [fork-interrupted] 标记(Codex ForkSnapshot);
- E4 summarize_no_llm:零模型调用的结构化会话元数据,trivial 标记(grok);
- B3:child 会话首条 prompt 快照(prompt_sha256 + 角色配置血缘)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbimagent.orchestrator.contracts import SubagentRequest, SubagentStatus
from openbimagent.orchestrator.runtime import ChildRunOutput, LocalSubagentRuntime
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


def _store_with_history(tmp_path: Path) -> tuple[SessionStore, list[str]]:
    store = SessionStore(tmp_path / "s.jsonl", title="回放")
    ids = [
        store.append_new(EventType.MESSAGE, {"role": "user", "content": f"msg-{i}"}).id
        for i in range(5)
    ]
    return store, ids


def test_rewind_moves_head_without_rewriting_file(tmp_path: Path) -> None:
    store, ids = _store_with_history(tmp_path)
    before_bytes = (tmp_path / "s.jsonl").read_bytes()
    removed = store.rewind(ids[1])
    assert removed == 3  # msg-2/3/4 移出当前主干
    assert (tmp_path / "s.jsonl").read_bytes() == before_bytes, "投影式 rewind 不改文件字节"
    assert store.head == ids[1]
    # 后续 append 以 rewind 点为父,形成新分支而非续写旧分支
    branch_event = store.append_new(EventType.MESSAGE, {"role": "user", "content": "new-branch"})
    assert branch_event.parentId == ids[1]
    # 旧分支仍可回访(树形语义)
    assert store.find_event(ids[4]) is not None
    with pytest.raises(KeyError):
        store.rewind("nonexistent")


def test_fork_interrupted_mode_appends_marker(tmp_path: Path) -> None:
    store, ids = _store_with_history(tmp_path)
    forked = store.fork(ids[2], mode="interrupted")
    events = forked.load()
    assert [e.id for e in events[:3]] == ids[:3]
    marker = events[3]
    assert marker.payload.model_dump(mode="json").get("fork_interrupted") is True
    with pytest.raises(ValueError, match="mode"):
        store.fork(ids[0], mode="bogus")


def test_summarize_no_llm_structured_metadata(tmp_path: Path) -> None:
    store, _ = _store_with_history(tmp_path)
    for i in range(3):
        store.append_new(
            EventType.TOOL_CALL,
            {"toolCallId": f"tc{i}", "toolName": "read", "args_summary": "{}", "phase": "call"},
        )
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "再检查一次", "steer": True})
    summary = store.summarize_no_llm()
    assert summary["trivial"] is False
    assert summary["user_message_count"] == 5  # steer 消息不计实质用户输入
    assert summary["event_counts"].get("tool_call") == 3
    assert summary["tool_usage"] == {"read": 3}
    assert summary["first_user_excerpt"] == "msg-0"
    assert summary["started_at"] and summary["last_active_at"]


def test_summarize_no_llm_marks_trivial_sessions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "t.jsonl", title="轻")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "就一句"})
    assert store.summarize_no_llm()["trivial"] is True


def test_child_session_records_prompt_snapshot(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: test-model\ntools: [read]\n"
        "permissions: { read: allow }\ncontext_mode: isolated\nmax_turns: 3\n"
        "artifact_contract: summary-v1\nnesting: false\n"
        'compaction_retain: ["验收结论"]\n---\n你是 worker。\n',
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        chat_fn=lambda role, messages, **kw: {"content": "done"},
        rehydrate=False,
    )
    parent = SessionStore.create(sessions, title="p")
    request = SubagentRequest.create(parent_session_id=parent.session_id, role="worker", task="t")
    envelope = runtime.run(request, parent_session=parent)
    assert envelope.status is SubagentStatus.COMPLETED
    events = [
        json.loads(line)
        for line in Path(envelope.child_session_path).read_text(encoding="utf-8").splitlines()
    ]
    snapshots = [e["payload"] for e in events if e["payload"].get("content") == "[prompt-snapshot]"]
    assert snapshots, "child 会话应有 prompt 快照"
    snap = snapshots[0]
    assert len(snap["prompt_sha256"]) == 64
    assert snap["role_name"] == "worker"
    assert snap["model"] == "test-model"
    assert snap["tools"] == ["read"]
    assert snap["compaction_retain"] == ["验收结论"]
    runtime.shutdown()
