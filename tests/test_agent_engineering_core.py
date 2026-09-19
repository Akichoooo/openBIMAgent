"""Agent 工程吸收批次一测试(session 版本链/压缩 v2/OBSK/doom-loop/stop-gate/项目指令/懒发现)。

对齐外部工具最佳实践的断言点:
- B1 session 事件 schema_version + 邻接迁移链(dsh: 每步只管 vN→vN+1,未来版本 fail-loud);
- C1 近期保留 token 预算制(opencode: min(20k, max(2k, usable/4)))+ 8 组硬帽;
- C2 split-turn:超大工具结果 elision 收缩,绝不因近期内容超预算 hard fail;
- A3 OBSK:超长结果溢写 content-addressed artifact,回灌头部 + 可 read 取回的引用;
- A8 doom-loop:同工具同参连续第 3 次拦截,轮询/探测豁免;
- C5 compaction_retain 角色必保信息注入摘要器;
- C6 WORKSPACE.md 项目指令注入(根→workdir 收集,超预算截断不失败);
- D10 stop_gate 收口门禁(拦截→反馈回灌→继续;连续 8 次上限放行);
- A4 lookup_api 懒发现(vs_index 离线查询,无需活 client,不走 ASK)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbimagent.core import loop as loop_mod
from openbimagent.core.loop import AgentLoop, _compact_result, _load_workspace_instructions
from openbimagent.core.permissions import Permission
from openbimagent.mcp_clients.vectorworks import lookup_vs_signatures
from openbimagent.session import schema as session_schema
from openbimagent.session.schema import (
    SESSION_SCHEMA_VERSION,
    SessionSchemaError,
    EventType,
    MessagePayload,
    migrate_event_dict,
    new_event,
)
from openbimagent.session.store import SessionStore


# ---------- B1: session 事件版本与迁移链 ----------

def test_new_events_are_stamped_with_current_schema_version(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.jsonl", title="v")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "hi"})
    line = json.loads((tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["schema_version"] == SESSION_SCHEMA_VERSION


def test_legacy_lines_load_projected_and_disk_unchanged(tmp_path: Path) -> None:
    legacy = {
        "id": "legacy-1",
        "parentId": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "type": "message",
        "payload": {"role": "user", "content": "旧会话"},
    }
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    store = SessionStore(path, title="legacy")
    events = store.load()
    assert events[0].schema_version == SESSION_SCHEMA_VERSION
    # 磁盘行保持 v1 原样(投影迁移,不偷偷改写)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "schema_version" not in raw
    versions = store.schema_versions()
    assert versions["legacy_lines"] == 1
    assert versions["max_version"] == 1


def test_future_schema_version_fails_loud(tmp_path: Path) -> None:
    future = {
        "id": "future-1",
        "parentId": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "type": "message",
        "schema_version": SESSION_SCHEMA_VERSION + 1,
        "payload": {"role": "user", "content": "from the future"},
    }
    path = tmp_path / "future.jsonl"
    path.write_text(json.dumps(future, ensure_ascii=False) + "\n", encoding="utf-8")
    # fail-loud:构造(load)即抛,绝不把未来版本行当损坏行跳过后静默继续
    with pytest.raises(SessionSchemaError, match="fail-loud"):
        SessionStore(path, title="future")


def test_upgrade_file_stamps_lines_and_keeps_backup(tmp_path: Path) -> None:
    legacy = {
        "id": "legacy-1",
        "parentId": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "type": "message",
        "payload": {"role": "user", "content": "旧会话"},
    }
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    store = SessionStore(path, title="upgrade")
    upgraded = store.upgrade_file()
    assert upgraded == 1
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert raw["schema_version"] == SESSION_SCHEMA_VERSION
    backup = tmp_path / "s.pre-v1.bak.jsonl"
    assert backup.is_file()
    assert "schema_version" not in json.loads(backup.read_text(encoding="utf-8"))
    # 幂等:已是当前版本再升级返回 0
    assert store.upgrade_file() == 0


def test_migration_chain_runs_stepwise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 v2→v3 迁移步:验证 v1 数据沿链走完每一步(邻接迁移纪律)。"""
    monkeypatch.setattr(session_schema, "SESSION_SCHEMA_VERSION", 3)
    monkeypatch.setitem(
        session_schema._EVENT_MIGRATIONS,
        2,
        lambda data: {**data, "payload": {**data["payload"], "content": data["payload"]["content"] + "+v3"}},
    )
    migrated = migrate_event_dict({"id": "x", "payload": {"role": "user", "content": "旧"}})
    assert migrated["schema_version"] == 3
    assert migrated["payload"]["content"] == "旧+v3"


def test_missing_migration_step_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_schema, "SESSION_SCHEMA_VERSION", 3)
    monkeypatch.delitem(session_schema._EVENT_MIGRATIONS, 2, raising=False)
    with pytest.raises(SessionSchemaError, match="迁移步"):
        migrate_event_dict({"id": "x", "payload": {"role": "user", "content": "旧"}})


# ---------- 测试用 provider ----------

class _Provider:
    """记录调用的假模型;按队列返回固定响应;clarify 摘要单独记录。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.clarify_calls: list[dict] = []

    def __call__(self, role, messages, tools=None, cancel_event=None, **kw):
        if role == "clarify":
            self.clarify_calls.append({"messages": [dict(m) for m in messages]})
            return {"content": "摘要:保留需求与关键决定。"}
        self.calls.append({"messages": [dict(m) for m in messages]})
        return self.responses.pop(0)


def _resp(content: str, tool_calls: list[dict] | None = None) -> dict:
    resp: dict = {"content": content}
    if tool_calls:
        resp["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
            }
            for i, tc in enumerate(tool_calls)
        ]
    return resp


def _make_loop(tmp_path: Path, provider, *, tools=("bash",), window=None, **kwargs) -> AgentLoop:
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    loop = AgentLoop(
        list(tools),
        session,
        chat_fn=provider,
        workdir=tmp_path,
        approval_callback=lambda name, args: True,
        **kwargs,
    )
    if window is not None:
        loop._context_window = lambda: window
    return loop


# ---------- C1/C2: 压缩 v2 ----------

def test_compaction_token_budget_limits_retention(tmp_path: Path) -> None:
    """C1:大窗口下 8 组条数帽不触发,但 keep_budget 应把近期保留压到 < 8 组。"""
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider, window=100_000)
    loop.messages.append({"role": "user", "content": "任务:生成管网"})
    # 20 组 × ~6KB ≈ 120KB > budget(80k) 触发压缩;keep_budget=20k → 只保留 ~3 组
    for i in range(20):
        loop.messages.append({
            "role": "assistant", "content": "x" * 3072,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
        })
        loop.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 3072})
    loop._maybe_compact()
    non_anchor = [m for m in loop.messages if m.get("role") != "system" and not str(m.get("content", "")).startswith("[context-compaction]")]
    assert len(non_anchor) < 16, "token 预算应在 8 组硬帽之前限制保留量"


def test_split_turn_elides_oversized_newest_tool_result(tmp_path: Path) -> None:
    """C2:最新组的超大工具结果被 elision 收缩(保头部+sha256),不丢弃、不 hard fail。"""
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider, window=4096)
    loop.messages.append({"role": "user", "content": "任务:渲染"})
    loop.messages.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "big", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
    })
    loop.messages.append({"role": "tool", "tool_call_id": "big", "content": "z" * 30000})
    loop._maybe_compact()  # 旧实现:最新组超预算被整组丢弃 → 这里必须存活
    tool_messages = [m for m in loop.messages if m.get("role") == "tool"]
    assert tool_messages, "最新工具组不得被整组丢弃"
    assert "elided" in tool_messages[0]["content"]
    assert len(tool_messages[0]["content"]) < 1000
    assistant_with_calls = [m for m in loop.messages if m.get("tool_calls")]
    assert assistant_with_calls, "工具调用结构必须保留(调用与结果配对)"
    assert loop._estimate_tokens(loop.messages) <= int(4096 * 0.8) + 200


def test_split_turn_records_elided_count_and_file_tracking(tmp_path: Path) -> None:
    """B4:压缩事件附 elided_messages 与 read_files/modified_files。"""
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider, window=4096, tools=("read", "write", "bash"))
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    loop._files_read.add(str(tmp_path / "a.txt"))
    loop._files_modified.add(str(tmp_path / "b.py"))
    loop.messages.append({"role": "user", "content": "任务"})
    loop.messages.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "big", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
    })
    loop.messages.append({"role": "tool", "tool_call_id": "big", "content": "z" * 30000})
    loop._maybe_compact()
    events = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    compaction = [e["payload"] for e in events if e["payload"].get("context_compaction")]
    assert compaction, "应有压缩审计事件"
    assert compaction[-1].get("elided_messages", 0) >= 1
    assert compaction[-1].get("read_files") == [str(tmp_path / "a.txt")]
    assert compaction[-1].get("modified_files") == [str(tmp_path / "b.py")]


def test_compaction_retain_hint_reaches_summarizer(tmp_path: Path) -> None:
    """C5:compaction_retain 必保信息注入摘要器 system 消息。"""
    provider = _Provider([_resp("done")])
    loop = _make_loop(
        tmp_path, provider, window=4096,
        compaction_retain=["管网坡度与埋深", "GB 50289 净距结论"],
    )
    loop.messages.append({"role": "user", "content": "任务"})
    for i in range(12):
        loop.messages.append({"role": "assistant", "content": "x" * 2048})
    loop._maybe_compact()
    assert provider.clarify_calls, "摘要器应被调用"
    system_message = provider.clarify_calls[0]["messages"][0]["content"]
    assert "管网坡度与埋深" in system_message
    assert "GB 50289 净距结论" in system_message


# ---------- A3: OBSK 溢写 ----------

def test_compact_result_spills_oversized_payload_to_artifact(tmp_path: Path) -> None:
    payload = {"rows": [{"id": i, "blob": "x" * 100} for i in range(400)]}
    view = _compact_result(payload, workdir=tmp_path)
    assert "OBSK" in view
    assert "out/results/" in view
    artifact = tmp_path / "out" / "results"
    spilled = list(artifact.glob("*.txt"))
    assert spilled, "完整结果应落盘为 content-addressed artifact"
    full = json.loads(spilled[0].read_text(encoding="utf-8"))
    assert len(full["rows"]) == 400
    # 小结果行为不变:原样 JSON,不落盘
    small = _compact_result({"ok": True}, workdir=tmp_path)
    assert json.loads(small) == {"ok": True}
    assert len(list(artifact.glob("*.txt"))) == 1


def test_bash_output_spills_to_artifact(tmp_path: Path) -> None:
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider)
    big = "line\n" * 6000  # ~30KB > 20k
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    result = loop._dispatch("bash", {"command": "type big.txt"})
    assert result["status"] == "ok"
    assert "OBSK" in result["llm_view"]
    assert "result_artifact" in result["ui_view"]
    assert (tmp_path / result["ui_view"]["result_artifact"]).read_text(encoding="utf-8") == big


# ---------- A8: doom-loop ----------

def test_doom_loop_blocks_third_identical_call(tmp_path: Path) -> None:
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider)
    loop._run_tool = lambda name, args: {"status": "ok", "llm_view": "r", "ui_view": {}}
    args = {"command": "echo same"}
    assert loop._dispatch("bash", args)["status"] == "ok"
    assert loop._dispatch("bash", args)["status"] == "ok"
    third = loop._dispatch("bash", args)
    assert third["status"] == "error"
    assert third["ui_view"]["doom_loop"] is True
    # 换参数立即重置
    assert loop._dispatch("bash", {"command": "echo other"})["status"] == "ok"
    assert loop._dispatch("bash", {"command": "echo other"})["status"] == "ok"
    assert loop._dispatch("bash", {"command": "echo other"})["ui_view"].get("doom_loop") is True


def test_doom_loop_exempts_polling(tmp_path: Path) -> None:
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider, tools=("mcp_call", "subagent"))
    loop._run_tool = lambda name, args: {"status": "ok", "llm_view": "r", "ui_view": {}}
    ping = {"server": "blender", "tool": "ping", "arguments": {}}
    for _ in range(5):
        assert loop._dispatch("mcp_call", ping)["status"] == "ok"
    status = {"action": "status", "request_id": "r1"}
    for _ in range(5):
        assert loop._dispatch("subagent", status)["status"] == "ok"


# ---------- C6: WORKSPACE.md 项目指令 ----------

def test_workspace_instructions_injected_into_system_prompt(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "WORKSPACE.md").write_text("单位:毫米;图层命名:ST-结构。", encoding="utf-8")
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider)
    assert "[项目约定]" in loop.messages[0]["content"]
    assert "单位:毫米" in loop.messages[0]["content"]


def test_workspace_instructions_absent_is_noop(tmp_path: Path) -> None:
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider)
    assert "[项目约定]" not in loop.messages[0]["content"]


def test_load_workspace_instructions_stops_at_project_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "WORKSPACE.md").write_text("root doc", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "WORKSPACE.md").write_text("sub doc", encoding="utf-8")
    text = _load_workspace_instructions(sub)
    assert "root doc" in text and "sub doc" in text
    assert text.index("root doc") < text.index("sub doc")  # 根在前,越具体越靠后


# ---------- D10: stop-gate ----------

def test_stop_gate_blocks_then_releases(tmp_path: Path) -> None:
    provider = _Provider([_resp("草稿:未验证"), _resp("终稿:已验证")])
    gate_calls: list[str] = []

    def gate(content: str) -> str | None:
        gate_calls.append(content)
        if "未验证" in content:
            return "结论缺验证证据,请补充测试结果"
        return None

    loop = _make_loop(tmp_path, provider, stop_gate=gate)
    result = loop.run("做任务")
    assert result == "终稿:已验证"
    assert len(gate_calls) == 2
    assert any("[stop-gate]" in str(m.get("content", "")) for m in loop.messages)
    # session 留痕
    events = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["payload"].get("stop_gate") for e in events)


def test_stop_gate_caps_at_eight_blocks(tmp_path: Path) -> None:
    provider = _Provider([_resp(f"answer {i}") for i in range(12)])
    loop = _make_loop(tmp_path, provider, stop_gate=lambda content: "永远不放行")
    result = loop.run("任务", )
    # 连续拦截 8 次后放行(防死锁),第 9 次 assistant 消息成为最终答案
    assert result.startswith("answer ")
    gate_messages = [m for m in loop.messages if str(m.get("content", "")).startswith("[stop-gate]")]
    assert len(gate_messages) == 8


# ---------- A4: lookup_api 懒发现 ----------

def test_lookup_vs_signatures_real_index() -> None:
    matches = lookup_vs_signatures("Wall", limit=3)
    assert matches, "真实 vs_index 应有 Wall 相关函数"
    assert all(m["name"].startswith("vs.") for m in matches)
    assert all(set(m) >= {"name", "args", "arity", "return_type", "doc"} for m in matches)


def test_lookup_api_dispatch_offline_without_client(tmp_path: Path) -> None:
    """lookup_api 不需要活的 MCP client(离线索引),且不走 ASK 审批。"""
    provider = _Provider([_resp("done")])
    approvals: list[str] = []

    def strict_approval(name: str, args) -> bool:
        approvals.append(name)
        return False  # 任何审批都拒绝:lookup_api 不该走到审批

    session = SessionStore(tmp_path / "s.jsonl", title="lookup")
    loop = AgentLoop(
        ["mcp_call"], session, chat_fn=provider, workdir=tmp_path, approval_callback=strict_approval,
    )
    result = loop._dispatch("mcp_call", {"server": "vectorworks", "tool": "lookup_api", "arguments": {"query": "Wall", "limit": 2}})
    assert result["status"] == "ok"
    assert approvals == []
    assert result["ui_view"]["match_count"] >= 1


def test_lookup_api_rejects_unknown_server(tmp_path: Path) -> None:
    provider = _Provider([_resp("done")])
    loop = _make_loop(tmp_path, provider, tools=("mcp_call",))
    result = loop._dispatch("mcp_call", {"server": "blender", "tool": "lookup_api", "arguments": {"query": "x"}})
    assert result["status"] == "error"


# ---------- D13: planner JSON 修复 + D2: sibling roster ----------

def test_extract_json_repairs_trailing_commas_and_smart_quotes() -> None:
    from openbimagent.planner.instantiate import _extract_json

    repaired = _extract_json('{"a": 1, "b": "x",}')
    assert repaired == {"a": 1, "b": "x"}
    smart = _extract_json('{\u201ca\u201d: \u201cvalue\u201d,}')  # 智能引号 + 尾逗号
    assert smart.get("a") == "value"
    fenced = _extract_json('```json\n{"k": [1,2,3,]}\n```')
    assert fenced == {"k": [1, 2, 3]}


def test_sibling_roster_injected_when_subagent_mounted(tmp_path: Path) -> None:
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    loop = AgentLoop(
        ["subagent", "read"],
        session,
        chat_fn=lambda *a, **k: {"content": "x"},
        workdir=tmp_path,
    )
    assert "[可派发子代理角色]" in loop.system_prompt
    assert "planner" in loop.system_prompt and "modeler" in loop.system_prompt


def test_sibling_roster_absent_without_subagent_tool(tmp_path: Path) -> None:
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    loop = AgentLoop(["read"], session, chat_fn=lambda *a, **k: {"content": "x"}, workdir=tmp_path)
    assert "[可派发子代理角色]" not in loop.system_prompt
