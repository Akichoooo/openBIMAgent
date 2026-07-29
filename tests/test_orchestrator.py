"""orchestrator M1 增强测试(Relay 013 任务 A4)。

覆盖:
- run_plan(concurrent=True) 并发调度:asyncio.gather + Semaphore(4) 真正并发,
  总耗时远 < 顺序执行;结果顺序与输入一致(gather 语义)。
- doom_loop 评分无进展检测:连续 doom_max_fix 次 FIX 且 hint 评分变化 < 0.3 → ESCALATE,
  PlanRunResult.error 含 "doom_loop"。
- SubagentResult 契约:每次 agent_fn 调用包装累积到 PlanRunResult.subagent_results。

全程无网络:agent_fn 均为本地注入桩。
"""

from __future__ import annotations

import time

from openbimagent.orchestrator.dispatch import (
    BatchReport,
    Verdict,
    run_plan,
)


# ---------- 并发调度 ----------


def test_run_plan_concurrent_batches() -> None:
    """concurrent=True:3 批次并发调度,总耗时远 < 顺序;outcomes 顺序与输入一致。

    证明并发:第二个 agent_fn 调用在第一个 sleep(0.1) 窗口内启动(开始时间差 < 0.1s)。
    顺序执行则第二个开始时间 ≥ 第一个结束时间(差 ≥ 0.1s)。
    """
    batches = ["batch_1", "batch_2", "batch_3"]
    call_starts: list[tuple[str, float]] = []

    def fn(batch: str, rework: str | None) -> BatchReport:
        start = time.monotonic()
        call_starts.append((batch, start))
        time.sleep(0.1)  # 模拟耗时
        return BatchReport(Verdict.PASS, hint=f"{batch} 完成")

    start = time.monotonic()
    result = run_plan(batches, fn, concurrent=True)
    elapsed = time.monotonic() - start

    assert result.ok is True
    assert all(o.verdict is Verdict.PASS for o in result.outcomes)
    # 并发:3 批 × 0.1s,顺序则 ≥0.3s,并发 ~0.1s;< 0.5s 留 overhead 余量
    assert elapsed < 0.5, f"并发未生效:耗时 {elapsed:.3f}s 接近/超过顺序 0.3s"
    # 证明并发放大镜:前两个调用开始时间差 < sleep 时长(说明第二个在第一个结束前启动)
    starts = sorted(t for _, t in call_starts)
    assert len(starts) == 3
    assert starts[1] - starts[0] < 0.1, "调用串行启动,并发未生效"
    # outcomes 顺序与输入一致(asyncio.gather 语义)
    assert [o.batch for o in result.outcomes] == batches


# ---------- doom_loop 评分无进展检测 ----------


def test_doom_loop_detection() -> None:
    """连续 doom_max_fix 次 FIX 且 hint 评分无进展(overall=5.0 不变)→ ESCALATE(doom_loop)。

    doom_max_fix=4 + max_retries=4(给足重试预算,确保 doom_loop 先于 max_retries 触发);
    第 4 次 FIX 后 score_history=[5.0]*4,相邻 delta=0 < 0.3 → 判死循环。
    """
    calls = 0

    def fn(batch: str, rework: str | None) -> BatchReport:
        nonlocal calls
        calls += 1
        # 评分恒定 5.0(无进展),带返工指令避免 no_rework_instruction 提前 ESCALATE
        return BatchReport(Verdict.FIX, hint="overall=5.0 几何未贴地", rework_instruction="Object A 沿 Z 降 0.2")

    result = run_plan(["资产A"], fn, max_retries=4, doom_max_fix=4)
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "doom_loop"
    assert outcome.attempts == 4  # 连续 4 次 FIX 触发
    assert "doom_loop" in result.error
    assert calls == 4


def test_doom_loop_score_progress_does_not_escalate() -> None:
    """评分有进展(overall 递增)时不判 doom_loop,继续重试直到 max_retries 或 PASS。

    回归保护:避免 check_doom_loop 的 M1 评分判定误杀有进展的迭代。
    """
    scores = iter([5.0, 6.0, 7.0, 8.0])  # 每次评分变化 ≥1.0,有进展

    def fn(batch: str, rework: str | None) -> BatchReport:
        s = next(scores)
        return BatchReport(Verdict.FIX, hint=f"overall={s}", rework_instruction="继续调整")

    # doom_max_fix=4 但评分每次 +1.0(变化 ≥0.3),不判 doom_loop;max_retries=3 先触发
    result = run_plan(["资产B"], fn, max_retries=3, doom_max_fix=4)
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "max_retries"  # 不是 doom_loop
    assert outcome.attempts == 4  # 1 首调 + 3 重试
    assert result.error == ""  # 无 doom_loop


# ---------- SubagentResult 契约 ----------


def test_subagent_result_accumulation() -> None:
    """每次 agent_fn 调用包装为 SubagentResult 累积;2 批次 → 2 个 SubagentResult。

    summary 取 BatchReport.hint(已截断 ≤200 字);顺序与批次输入一致。
    """
    hints = ["批次一完成,几何对齐", "批次二完成,材质达标"]

    def fn(batch: str, rework: str | None) -> BatchReport:
        idx = ["批次一", "批次二"].index(batch)
        return BatchReport(Verdict.PASS, hint=hints[idx])

    result = run_plan(["批次一", "批次二"], fn)
    assert result.ok is True
    assert len(result.subagent_results) == 2
    # summary 包含对应批次 hint,顺序与批次一致
    assert result.subagent_results[0].summary == hints[0]
    assert result.subagent_results[1].summary == hints[1]
    # artifact_paths 暂为空(M1 阶段;真实工件路径留 M2 builder 接线)
    assert all(sr.artifact_paths == [] for sr in result.subagent_results)


def test_subagent_result_accumulated_across_fix_retries() -> None:
    """FIX 重试场景:每次 agent_fn 调用(含重试)都累积一个 SubagentResult。"""
    reports = [
        BatchReport(Verdict.FIX, hint="overall=6.0 漂浮", rework_instruction="降 0.2"),
        BatchReport(Verdict.FIX, hint="overall=7.0 仍偏移", rework_instruction="再降 0.1"),
        BatchReport(Verdict.PASS, hint="overall=9.0 贴地"),
    ]

    def fn(batch: str, rework: str | None) -> BatchReport:
        return reports.pop(0)

    result = run_plan(["单批"], fn, max_retries=3, doom_max_fix=5)
    assert result.ok is True
    # 3 次 agent_fn 调用(2 次 FIX + 1 次 PASS)→ 3 个 SubagentResult
    assert len(result.subagent_results) == 3
    assert "overall=6.0" in result.subagent_results[0].summary
    assert "overall=9.0" in result.subagent_results[2].summary
