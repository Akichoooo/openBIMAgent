"""orchestrator.dispatch 测试(M0 阶段4a;COMPONENTS §2.4/§7;ARCH §6 子代理协议)。

覆盖:PASS 直通、FIX 带返工指令重试后 PASS、FIX 重试到上限 ESCALATE、
doom_loop 检测(连续 N 次 FIX 无进展)、禁嵌套(depth>0 拒绝)、hint <200 字截断告警、
FIX 无返工指令拒空泛重试、agent 自报 ESCALATE、session 事件链(tool_call phase=call/result)。
全程无网络:agent_fn 均为本地注入桩。
"""

import threading
import time

import pytest

from openbimagent.orchestrator.dispatch import (
    MAX_HINT_CHARS,
    BatchReport,
    NestedDispatchError,
    SubagentResult,
    Verdict,
    check_doom_loop,
    judge,
    run_plan,
)
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


def _fn_always(report: BatchReport):
    """永远返回同一 report 的 agent_fn 桩。"""

    def fn(batch: str, rework: str | None) -> BatchReport:
        return report

    return fn


# ---------- 统一 judge ----------


def test_judge_gate_failure_requires_actionable_fix() -> None:
    decision = judge(
        SubagentResult(summary="schema drift", hint="补齐 $.assets[0].description"),
        gate_ok=False,
        score=None,
    )
    assert decision.verdict is Verdict.FIX
    assert "$.assets[0].description" in (decision.rework_instruction or "")


def test_judge_gate_failure_without_feedback_escalates() -> None:
    decision = judge(SubagentResult(summary=""), gate_ok=False, score=None)
    assert decision.verdict is Verdict.ESCALATE
    assert decision.rework_instruction is None


def test_judge_score_pass_fix_and_missing_score() -> None:
    result = SubagentResult(summary="材质层次不足")
    assert judge(result, gate_ok=True, score=8.5).verdict is Verdict.PASS
    fix = judge(result, gate_ok=True, score=7.2)
    assert fix.verdict is Verdict.FIX
    assert "7.20" in (fix.rework_instruction or "")
    assert judge(result, gate_ok=True, score=None).verdict is Verdict.ESCALATE


# ---------- PASS 直通 ----------


def test_pass_through_all_batches() -> None:
    """全部 PASS:逐批顺序执行,每批 1 次调用,rework=None,result.ok。"""
    calls: list[tuple[str, str | None]] = []

    def fn(batch: str, rework: str | None) -> BatchReport:
        calls.append((batch, rework))
        return BatchReport(Verdict.PASS, hint=f"{batch} 完成")

    result = run_plan(["路面", "建筑xN"], fn)
    assert result.ok is True
    assert result.escalated == ()
    assert calls == [("路面", None), ("建筑xN", None)]  # 顺序驱动
    assert [o.attempts for o in result.outcomes] == [1, 1]
    assert all(o.verdict is Verdict.PASS for o in result.outcomes)
    assert result.outcomes[0].history == (Verdict.PASS,)
    assert result.outcomes[0].reason == "pass"
    assert result.outcomes[0].hint == "路面 完成"


def test_empty_batches_raises() -> None:
    with pytest.raises(ValueError, match="批次序列不能为空"):
        run_plan([], _fn_always(BatchReport(Verdict.PASS)))


# ---------- FIX 重试 ----------


def test_fix_then_pass_carries_rework_instruction() -> None:
    """FIX 带可执行返工指令重试同批:第二次调用收到上轮 rework_instruction。"""
    seen: list[str | None] = []
    reports = [
        BatchReport(Verdict.FIX, hint="售货机漂浮", rework_instruction="Object vending 沿 Z 降 0.2"),
        BatchReport(Verdict.PASS, hint="已贴地"),
    ]

    def fn(batch: str, rework: str | None) -> BatchReport:
        seen.append(rework)
        return reports.pop(0)

    result = run_plan(["自动售货机"], fn)
    assert result.ok is True
    outcome = result.outcomes[0]
    assert outcome.attempts == 2
    assert outcome.history == (Verdict.FIX, Verdict.PASS)
    assert seen == [None, "Object vending 沿 Z 降 0.2"]  # 返工指令传入下一轮


def test_fix_to_max_retries_escalates() -> None:
    """FIX 重试到上限(1 次首调 + max_retries 次重试)→ ESCALATE(reason=max_retries)。"""
    fn = _fn_always(BatchReport(Verdict.FIX, hint="仍漂浮", rework_instruction="再降 0.1"))
    result = run_plan(["主体"], fn, max_retries=2, doom_max_fix=10)
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "max_retries"
    assert outcome.attempts == 3  # 1 首调 + 2 重试
    assert outcome.history == (Verdict.FIX,) * 3
    assert result.ok is False
    assert result.escalated == ("主体",)


def test_doom_loop_escalates_before_retry_budget() -> None:
    """连续 doom_max_fix 次 FIX 无进展 → doom_loop ESCALATE(默认 3,先于重试预算耗尽)。"""
    fn = _fn_always(BatchReport(Verdict.FIX, hint="无进展", rework_instruction="再试一次"))
    result = run_plan(["招牌"], fn, max_retries=10)  # 重试预算充裕,doom_loop 先触发
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "doom_loop"
    assert outcome.attempts == 3  # 连续 3 次 FIX 即判死循环
    assert result.escalated == ("招牌",)


def test_check_doom_loop_unit() -> None:
    """doom_loop 纯函数:尾部连续 max_fix 次 FIX 才判死;历史不足/PASS 打断均不算。"""
    assert check_doom_loop("a", [Verdict.FIX] * 3) is True
    assert check_doom_loop("a", [Verdict.PASS, Verdict.FIX, Verdict.FIX, Verdict.FIX]) is True  # 看尾部
    assert check_doom_loop("a", [Verdict.FIX, Verdict.PASS, Verdict.FIX, Verdict.FIX]) is False  # PASS 打断
    assert check_doom_loop("a", [Verdict.FIX] * 2) is False  # 历史不足
    assert check_doom_loop("a", []) is False
    assert check_doom_loop("a", [Verdict.FIX] * 3, max_fix=0) is False  # 非法阈值不判死


def test_fix_without_rework_instruction_escalates() -> None:
    """FIX 但 rework_instruction 与 hint 皆空:禁止空泛重试,直接 ESCALATE。"""
    fn = _fn_always(BatchReport(Verdict.FIX, hint=""))
    result = run_plan(["电线"], fn)
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "no_rework_instruction"
    assert outcome.attempts == 1  # 不做无指令的空转重试


def test_agent_self_escalate_stops_immediately() -> None:
    """agent_fn 自报 ESCALATE:立即升级不重试(升模型或问人)。"""
    fn = _fn_always(BatchReport(Verdict.ESCALATE, hint="需要人审"))
    result = run_plan(["建筑xN"], fn)
    outcome = result.outcomes[0]
    assert outcome.verdict is Verdict.ESCALATE
    assert outcome.reason == "agent_escalate"
    assert outcome.attempts == 1
    assert result.ok is False


# ---------- 禁嵌套 / hint 截断 ----------


def test_nested_dispatch_forbidden() -> None:
    """depth>0 直接拒绝(ARCH §6 禁嵌套);agent_fn 不被调用。"""
    called = False

    def fn(batch: str, rework: str | None) -> BatchReport:
        nonlocal called
        called = True
        return BatchReport(Verdict.PASS)

    with pytest.raises(NestedDispatchError, match="禁嵌套"):
        run_plan(["a"], fn, depth=1)
    assert called is False


def test_hint_truncated_at_200_chars() -> None:
    """hint 超 200 字截断并告警(COMPONENTS §2.4/§6)。"""
    fn = _fn_always(BatchReport(Verdict.PASS, hint="长" * 500))
    with pytest.warns(UserWarning, match="截断"):
        result = run_plan(["a"], fn)
    assert len(result.outcomes[0].hint) == MAX_HINT_CHARS


# ---------- session 事件链 ----------


def test_session_event_chain_call_result_pairs(tmp_path) -> None:
    """每次 agent_fn 调用落 tool_call 事件对:phase=call/result 同一 toolCallId,parentId 成链。"""
    store = SessionStore.create(tmp_path / "sessions", title="dispatch-test")
    reports = [
        BatchReport(Verdict.FIX, hint="漂浮", rework_instruction="降 0.2"),
        BatchReport(Verdict.PASS, hint="ok"),
    ]

    def fn(batch: str, rework: str | None) -> BatchReport:
        return reports.pop(0)

    result = run_plan(["主体"], fn, session=store)
    assert result.ok is True

    events = store.load()
    assert len(events) == 4  # 2 次调用 × call/result
    assert all(e.type is EventType.TOOL_CALL for e in events)
    assert [e.payload.phase for e in events] == ["call", "result", "call", "result"]
    # 同一调用对共享 toolCallId;跨对不同
    assert events[0].payload.toolCallId == events[1].payload.toolCallId
    assert events[2].payload.toolCallId == events[3].payload.toolCallId
    assert events[0].payload.toolCallId != events[2].payload.toolCallId
    # parentId 依次成链(头事件挂空)
    assert events[0].parentId is None
    for prev, cur in zip(events, events[1:]):
        assert cur.parentId == prev.id
    # 事件内容:工具名 / 批次与返工摘要 / 裁决视图
    assert all(e.payload.toolName == "subagent" for e in events)
    assert "batch=主体" in events[0].payload.args_summary
    assert "rework=降 0.2" in events[2].payload.args_summary  # FIX 返工指令随下轮 call 落盘
    assert events[1].payload.result_llm_view.startswith("FIX")
    assert events[3].payload.result_llm_view.startswith("PASS")
    assert all(e.payload.status == "ok" for e in events if e.payload.phase == "result")


# ---------- 并发调度(concurrent=True) ----------


def test_concurrent_outcomes_follow_input_order() -> None:
    """并发完成顺序乱序时,outcomes 与 subagent_results 仍按输入批次顺序(gather 语义)。"""

    def fn(batch: str, rework: str | None) -> BatchReport:
        if batch != "快":
            time.sleep(0.05)  # 慢批次后完成,验证结果不按完成顺序排
        return BatchReport(Verdict.PASS, hint=f"{batch} ok")

    result = run_plan(["慢1", "快", "慢2"], fn, concurrent=True)
    assert result.ok is True
    assert [o.batch for o in result.outcomes] == ["慢1", "快", "慢2"]
    assert [r.hint for r in result.subagent_results] == ["慢1 ok", "快 ok", "慢2 ok"]


def test_concurrent_mixed_verdicts_and_escalated() -> None:
    """并发下 PASS/FIX→PASS/ESCALATE 混合:逐批裁决互不影响,escalated 与顺序语义不变。"""
    lock = threading.Lock()
    reports = {
        "a": [
            BatchReport(Verdict.FIX, hint="漂浮", rework_instruction="降 0.2"),
            BatchReport(Verdict.PASS, hint="ok"),
        ],
        "b": [BatchReport(Verdict.PASS, hint="ok")],
        "c": [BatchReport(Verdict.ESCALATE, hint="需人审")],
    }

    def fn(batch: str, rework: str | None) -> BatchReport:
        with lock:
            return reports[batch].pop(0)

    result = run_plan(["a", "b", "c"], fn, concurrent=True)
    assert result.ok is False
    assert [o.verdict for o in result.outcomes] == [Verdict.PASS, Verdict.PASS, Verdict.ESCALATE]
    assert result.escalated == ("c",)
    outcome_a = result.outcomes[0]
    assert outcome_a.attempts == 2
    assert outcome_a.history == (Verdict.FIX, Verdict.PASS)
    assert result.outcomes[2].reason == "agent_escalate"


def test_concurrent_fix_rework_stays_per_batch_sequential() -> None:
    """并发下批内 FIX 重试环仍严格顺序:每批第二次调用收到自己的返工指令,不串批。"""
    lock = threading.Lock()
    seen: dict[str, list[str | None]] = {"x": [], "y": []}

    def fn(batch: str, rework: str | None) -> BatchReport:
        with lock:
            seen[batch].append(rework)
            attempt = len(seen[batch])
        time.sleep(0.02)  # 制造批次间交错窗口
        if attempt == 1:
            return BatchReport(Verdict.FIX, hint=f"{batch} 漂浮", rework_instruction=f"降 {batch}")
        return BatchReport(Verdict.PASS, hint=f"{batch} ok")

    result = run_plan(["x", "y"], fn, concurrent=True)
    assert result.ok is True
    assert seen["x"] == [None, "降 x"]
    assert seen["y"] == [None, "降 y"]
    assert all(o.attempts == 2 for o in result.outcomes)


def test_concurrent_doom_loop_still_trips() -> None:
    """并发下 doom_loop 判定不变:连续 doom_max_fix 次 FIX 无进展 → ESCALATE,error=doom_loop。"""
    fn = _fn_always(BatchReport(Verdict.FIX, hint="无进展", rework_instruction="再试一次"))
    result = run_plan(["a", "b"], fn, concurrent=True, max_retries=10)
    assert all(o.verdict is Verdict.ESCALATE for o in result.outcomes)
    assert all(o.reason == "doom_loop" for o in result.outcomes)
    assert all(o.attempts == 3 for o in result.outcomes)
    assert result.error == "doom_loop"
    assert result.ok is False


def test_concurrent_respects_max_concurrency() -> None:
    """并发上限:峰值并行度 ≤ max_concurrency,且确实发生了并行(峰值 == 上限)。"""
    lock = threading.Lock()
    active = 0
    peak = 0

    def fn(batch: str, rework: str | None) -> BatchReport:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return BatchReport(Verdict.PASS, hint=f"{batch} ok")

    result = run_plan([f"b{i}" for i in range(8)], fn, concurrent=True, max_concurrency=2)
    assert result.ok is True
    assert peak <= 2
    assert peak == 2  # 8 批 × 20ms 在 2 槽位下必然重叠,证明并行真实发生


def test_concurrent_nested_dispatch_forbidden() -> None:
    """concurrent=True 不改变禁嵌套语义:depth>0 仍直接拒绝。"""
    with pytest.raises(NestedDispatchError, match="禁嵌套"):
        run_plan(["a"], _fn_always(BatchReport(Verdict.PASS)), concurrent=True, depth=1)


def test_concurrent_session_event_chain_single_line(tmp_path) -> None:
    """并发下 session 事件:call/result 事件对共享 toolCallId 且一一配对;
    parentId 链保持单链不分叉(SessionStore 锁保证,多批次交错落盘但不断链)。"""
    store = SessionStore.create(tmp_path / "sessions", title="dispatch-concurrent")

    def fn(batch: str, rework: str | None) -> BatchReport:
        time.sleep(0.01)  # 制造写交错窗口
        return BatchReport(Verdict.PASS, hint=f"{batch} ok")

    result = run_plan(["a", "b", "c", "d"], fn, session=store, concurrent=True)
    assert result.ok is True

    events = store.load()
    assert len(events) == 8  # 4 批 × call/result
    calls = {e.payload.toolCallId for e in events if e.payload.phase == "call"}
    results = {e.payload.toolCallId for e in events if e.payload.phase == "result"}
    assert calls == results
    assert len(calls) == 4
    # 单链:除头事件外,每事件 parentId 恰为前一事件 id(并发写不产生兄弟分叉)
    assert events[0].parentId is None
    for prev, cur in zip(events, events[1:]):
        assert cur.parentId == prev.id
