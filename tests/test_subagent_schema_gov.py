"""D4/D5/C5 批次测试:子代理 output_schema 契约、并发治理参数、角色压缩必保信息。

- D4(Codex final_output_json_schema):请求携带 output_schema 时,child 最终输出
  必须是满足该 JSON Schema 的合法 JSON;违反即 FAILED(fail-loud 结构化错误);
  角色未在 frontmatter 声明该能力 → 请求被拒绝(绝不 accepted-then-ignored)。
- D5:run_plan max_concurrency 治理参数(1..8,越界 fail-loud)。
- C5:角色 frontmatter compaction_retain 解析进 AgentProfile。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbimagent.orchestrator.contracts import SubagentRequest, SubagentStatus
from openbimagent.orchestrator.dispatch import BatchReport, Verdict, run_plan
from openbimagent.orchestrator.runtime import (
    ChildRunOutput,
    LocalSubagentRuntime,
    SubagentRuntimeError,
    load_agent_profile,
)
from openbimagent.session.store import SessionStore

_OK_SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def _agents_dir(tmp_path: Path, *, with_schema: bool) -> Path:
    agents = tmp_path / "agents"
    agents.mkdir(exist_ok=True)
    frontmatter = (
        "---\n"
        "name: worker\n"
        "model: test-model\n"
        "tools: [read, write]\n"
        "permissions: { read: allow, write: deny }\n"
        "context_mode: isolated\n"
        "max_turns: 7\n"
        "artifact_contract: summary-v1\n"
        "nesting: false\n"
        'compaction_retain: ["验收结论", "工件路径"]\n'
    )
    if with_schema:
        frontmatter += "output_schema:\n  type: object\n  required: [ok]\n  properties:\n    ok: {type: boolean}\n"
    frontmatter += "---\n你是测试 worker。\n"
    (agents / "worker.md").write_text(frontmatter, encoding="utf-8")
    return agents


def _runtime(tmp_path: Path, *, with_schema: bool, runner) -> LocalSubagentRuntime:
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    return LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path, with_schema=with_schema),
        child_runner=runner,
        rehydrate=False,
    )


def _request(tmp_path: Path, *, output_schema: dict | None) -> SubagentRequest:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    return SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="worker",
        task="执行检查",
        output_schema=output_schema,
    )


def test_profile_parses_compaction_retain_and_output_schema(tmp_path: Path) -> None:
    agents = _agents_dir(tmp_path, with_schema=True)
    profile = load_agent_profile("worker", agents)
    assert profile.compaction_retain == ("验收结论", "工件路径")
    assert profile.output_schema == _OK_SCHEMA


def test_project_roles_parse_new_frontmatter() -> None:
    for role in ("planner", "modeler", "critic_render", "critic_scad", "researcher", "deliver"):
        profile = load_agent_profile(role)
        assert profile.compaction_retain, f"{role} 应声明 compaction_retain"
    for role in ("critic_render", "critic_scad", "deliver"):
        assert load_agent_profile(role).output_schema, f"{role} 应声明 output_schema 能力"


def test_output_schema_violation_fails_loud(tmp_path: Path) -> None:
    outputs = ["not json at all", '{"ok": "yes"}']  # 非法 JSON / 类型不符
    for bad in outputs:
        runner = lambda request, profile, child: ChildRunOutput(summary=bad)
        runtime = _runtime(tmp_path / f"case-{hash(bad) % 9999}", with_schema=True, runner=runner)
        parent_dir = runtime.sessions_dir
        parent = SessionStore.create(parent_dir, title="p")
        request = SubagentRequest.create(
            parent_session_id=parent.session_id, role="worker", task="t", output_schema=_OK_SCHEMA,
        )
        envelope = runtime.run(request, parent_session=parent)
        assert envelope.status is SubagentStatus.FAILED
        assert envelope.error is not None
        assert envelope.error.code == "OutputSchemaViolation"
        runtime.shutdown()


def test_output_schema_valid_json_completes(tmp_path: Path) -> None:
    runner = lambda request, profile, child: ChildRunOutput(summary='{"ok": true}', hint="done")
    runtime = _runtime(tmp_path, with_schema=True, runner=runner)
    parent = SessionStore.create(runtime.sessions_dir, title="p")
    request = SubagentRequest.create(
        parent_session_id=parent.session_id, role="worker", task="t", output_schema=_OK_SCHEMA,
    )
    envelope = runtime.run(request, parent_session=parent)
    assert envelope.status is SubagentStatus.COMPLETED
    assert json.loads(envelope.summary) == {"ok": True}
    runtime.shutdown()


def test_output_schema_capability_not_declared_rejects_request(tmp_path: Path) -> None:
    runner = lambda request, profile, child: ChildRunOutput(summary="x")
    runtime = _runtime(tmp_path, with_schema=False, runner=runner)
    parent = SessionStore.create(runtime.sessions_dir, title="p")
    request = SubagentRequest.create(
        parent_session_id=parent.session_id, role="worker", task="t", output_schema=_OK_SCHEMA,
    )
    with pytest.raises(SubagentRuntimeError, match="output_schema 能力"):
        runtime.run(request, parent_session=parent)
    runtime.shutdown()


def test_default_runner_injects_schema_into_task(tmp_path: Path) -> None:
    captured: list[dict] = []

    def chat_fn(role, messages, tools=None, cancel_event=None, **kw):
        captured.append({"role": role, "messages": [dict(m) for m in messages]})
        return {"content": '{"ok": true}'}

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path, with_schema=True),
        chat_fn=chat_fn,
        rehydrate=False,
    )
    parent = SessionStore.create(sessions, title="p")
    request = SubagentRequest.create(
        parent_session_id=parent.session_id, role="worker", task="检查交付", output_schema=_OK_SCHEMA,
    )
    envelope = runtime.run(request, parent_session=parent)
    assert envelope.status is SubagentStatus.COMPLETED
    user_messages = [m for call in captured for m in call["messages"] if m.get("role") == "user"]
    assert any("JSON Schema" in str(m.get("content", "")) for m in user_messages)
    runtime.shutdown()


def test_run_plan_max_concurrency_governance() -> None:
    agent_fn = lambda batch, rework: BatchReport(verdict=Verdict.PASS, hint="ok")
    result = run_plan(["a", "b", "c"], agent_fn, concurrent=True, max_concurrency=2)
    assert result.ok and len(result.outcomes) == 3
    with pytest.raises(ValueError, match="max_concurrency"):
        run_plan(["a"], agent_fn, concurrent=True, max_concurrency=0)
    with pytest.raises(ValueError, match="max_concurrency"):
        run_plan(["a"], agent_fn, concurrent=True, max_concurrency=9)
