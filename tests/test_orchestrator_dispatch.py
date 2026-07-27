"""orchestrator.dispatch 测试(M0 阶段4a;COMPONENTS §2.4/§7;ARCH §6 子代理协议)。

覆盖:PASS 直通、FIX 带返工指令重试后 PASS、FIX 重试到上限 ESCALATE、
doom_loop 检测(连续 N 次 FIX 无进展)、禁嵌套(depth>0 拒绝)、hint <200 字截断告警、
FIX 无返工指令拒空泛重试、agent 自报 ESCALATE、session 事件链(tool_call phase=call/result)。
全程无网络:agent_fn 均为本地注入桩。
"""

import pytest

from openbimagent.orchestrator.dispatch import (
    MAX_HINT_CHARS,
    BatchReport,
    NestedDispatchError,
    Verdict,
    check_doom_loop,
    run_plan,
)
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


def _fn_always(report: BatchReport):
    """永远返回同一 report 的 agent_fn 桩。"""

    def fn(batch: str, rework: str | None) -> BatchReport:
        return report

    return fn


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
