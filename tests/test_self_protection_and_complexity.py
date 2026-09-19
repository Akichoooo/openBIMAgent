"""F3 自保护 + H3 操作复杂度指标测试。"""

from __future__ import annotations

from pathlib import Path

from openbimagent.core.loop import AgentLoop
from openbimagent.orchestrator.dispatch import (
    BatchReport,
    PlanRunResult,
    Verdict,
    complexity_metrics,
    run_plan,
)
from openbimagent.session.store import SessionStore


def _loop(tmp_path: Path) -> AgentLoop:
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    # workdir = 仓库根,使 agents/schemas/config 落入自保护范围
    return AgentLoop(
        ["read", "write", "edit"],
        session,
        chat_fn=lambda *a, **k: {"content": "x"},
        workdir=Path(__file__).resolve().parents[1],
        approval_callback=lambda name, args: True,
    )


def test_self_protection_blocks_governance_writes(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    # 改写角色 ceiling 与门禁 schema 均被拒
    for bad in ("agents/clarify.md", "schemas/session_event.schema.json", "config/models.toml", "pyproject.toml"):
        result = loop._dispatch("write", {"path": bad, "content": "evil"})
        assert result["status"] == "denied", f"{bad} 应被自保护拦截"
        assert result["ui_view"]["self_protection"] is True


def test_self_protection_allows_normal_writes(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    target = tmp_path / "note.md"
    result = loop._dispatch("write", {"path": str(target), "content": "ok"})
    assert result["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "ok"


def test_complexity_metrics_from_plan_result() -> None:
    agent_fn = lambda batch, rework: BatchReport(verdict=Verdict.FIX, hint="overall=5.0", rework_instruction="fix A") if batch == "b1" else BatchReport(verdict=Verdict.PASS, hint="ok")
    result = run_plan(["b1", "b2"], agent_fn, max_retries=3)
    metrics = complexity_metrics(result)
    assert metrics["batches"] == 2
    assert metrics["total_attempts"] >= 3  # b1 至少 FIX 一次再... 实际取决于重试
    assert metrics["total_fix_steps"] >= 1
    assert metrics["ok"] is False  # b1 doom 或重试耗尽 → ESCALATE
    assert "b1" in metrics["escalated_batches"]
