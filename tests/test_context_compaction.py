"""上下文预算与压缩测试（COMPONENTS §5；core/loop.py _maybe_compact）。

对齐 Codex auto-compaction / pi 滑窗纪律的断言点：
- 超预算触发压缩，system + 首条任务锚点 + 最近 N 条保留；
- 摘要消息带审计标记并写 session 树（含 digest_sha256）；
- 压缩后每次模型调用的 messages 均在预算内；
- 摘要器失败时回退确定性骨架（离线安全）；
- 未超预算零干预（行为不变）。
"""

from __future__ import annotations

import json
from pathlib import Path

from openbimagent.core.loop import (
    COMPACT_KEEP_RECENT,
    CONTEXT_BUDGET_RATIO,
    AgentLoop,
)
from openbimagent.session.store import SessionStore


class _Provider:
    """记录每次调用的 messages;按队列返回固定响应;摘要调用单独计数。"""

    def __init__(self, responses: list[dict], *, fail_on_clarify: bool = False) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.clarify_calls = 0
        self.fail_on_clarify = fail_on_clarify

    def __call__(self, role, messages, tools=None, cancel_event=None, **kw):
        if role == "clarify":
            self.clarify_calls += 1
            if self.fail_on_clarify:
                raise RuntimeError("no registry offline")
            return {"content": "摘要:用户要求生成 DN400 管网,已完成路由求解,待交付。"}
        self.calls.append({"role": role, "messages": [dict(m) for m in messages]})
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


def _big_text(kb: int) -> str:
    return "x" * (kb * 1024)


def _run_compaction_loop(tmp_path: Path, provider: _Provider, *, window: int) -> AgentLoop:
    session = SessionStore(tmp_path / "s.jsonl", title="compact-test")
    loop = AgentLoop(
        ["read", "bash"],
        session,
        chat_fn=provider,
        workdir=tmp_path,
        max_steps=8,
        approval_callback=lambda name, args: True,  # 测试环境免人工审批
    )
    loop._context_window = lambda: window  # 测试注入小窗口,免灌 128k 文本
    return loop


def test_compaction_triggers_and_preserves_anchors(tmp_path: Path) -> None:
    provider = _Provider(
        [
            _resp("第一批", [{"name": "bash", "arguments": {"command": "echo 1"}}]),
            _resp("全部完成。"),
        ]
    )
    loop = _run_compaction_loop(tmp_path, provider, window=4096)
    # 灌入超预算历史:system+首条 user + 多条大消息
    loop.messages.append({"role": "user", "content": "任务:生成管网"})
    for i in range(20):
        loop.messages.append({"role": "assistant", "content": _big_text(2)})
        loop.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": _big_text(1)})
    budget = int(4096 * CONTEXT_BUDGET_RATIO)
    assert loop._estimate_tokens() > budget

    loop.run("继续任务")

    # system + 首条任务锚点保留
    assert loop.messages[0]["role"] == "system"
    assert any(m.get("content") == "任务:生成管网" for m in loop.messages[:3])
    # 压缩标记 + 摘要存在
    markers = [m for m in loop.messages if "[context-compaction]" in str(m.get("content", ""))]
    assert markers, "应有压缩标记消息"
    assert any("[早期上下文摘要]" in str(m.get("content", "")) for m in loop.messages)
    # 保留的近期消息数不超过 COMPACT_KEEP_RECENT + 锚点与摘要
    assert len(loop.messages) <= COMPACT_KEEP_RECENT + 6
    # 每次实际模型调用的 messages 都在预算内
    for call in provider.calls:
        assert loop._estimate_tokens(call["messages"]) <= int(4096 * 0.92) + 200
    # 摘要器被调用(clarify 角色;小窗口下多步调用可能触发多次压缩,均合法)
    assert provider.clarify_calls >= 1
    # session 树有压缩审计事件
    events = [e for e in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines() if e.strip()]
    payloads = [json.loads(line)["payload"] for line in events]
    compaction_events = [p for p in payloads if p.get("compacted_messages")]
    assert compaction_events and compaction_events[0]["digest_sha256"]


def test_compaction_fallback_to_deterministic_skeleton(tmp_path: Path) -> None:
    provider = _Provider([_resp("done。")], fail_on_clarify=True)
    loop = _run_compaction_loop(tmp_path, provider, window=4096)
    loop.messages.append({"role": "user", "content": "任务"})
    for _ in range(16):
        loop.messages.append({"role": "assistant", "content": _big_text(2)})
    loop.run("go")
    summaries = [m for m in loop.messages if "[早期上下文摘要]" in str(m.get("content", ""))]
    assert summaries, "离线失败也应产出确定性骨架摘要"
    assert "骨架" in summaries[0]["content"] or "共" in summaries[0]["content"]


def test_no_compaction_when_within_budget(tmp_path: Path) -> None:
    provider = _Provider([_resp("done。")])
    loop = _run_compaction_loop(tmp_path, provider, window=1_000_000)
    before = len(loop.messages)
    loop.run("小任务")
    assert provider.clarify_calls == 0
    assert not any("[context-compaction]" in str(m.get("content", "")) for m in loop.messages)
    assert len(loop.messages) >= before  # 零干预,只增不压
