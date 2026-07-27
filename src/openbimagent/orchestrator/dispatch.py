"""Orchestrator:子代理调度,PASS/FIX/ESCALATE,并发 ≤4,禁嵌套。

对应文档:
- docs/architecture/COMPONENTS.md §2.4 orchestrator、§3 角色表、§7 doom_loop
- docs/architecture/ARCHITECTURE.md §6 子代理协议、§8(同一资产反复 FIX 死循环)

子代理 = Markdown + YAML frontmatter(全局 agents/ + 包内 agents/ 覆盖);
子代理返回 = 结构化摘要 + 工件路径 + <200 字核心提示/警告;
原始过程留 child session,父代理按需深翻(artifact-mediated,不直接传上下文)。

M0 为顺序执行版(run_plan):按 plan 批次顺序逐批驱动,agent_fn 可注入;
并发(≤4 信号量)、角色 Markdown 加载、child session 挂载留 M1(见 dispatch/judge 的 TODO)。
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from openbimagent.session.schema import EventType, uuid7
from openbimagent.session.store import SessionStore

MAX_CONCURRENCY = 4
"""子代理并发上限(COMPONENTS §2.4)。M1 实现;M0 run_plan 为顺序执行。"""

MAX_HINT_CHARS = 200
"""子代理返回的核心提示/警告字数上限(COMPONENTS §2.4/§6),超长截断并告警。"""

DOOM_LOOP_MAX_FIX = 3
"""同一资产连续 FIX 无进展的 ESCALATE 阈值初值(COMPONENTS §7;可按包覆盖)。"""

DEFAULT_MAX_RETRIES = 3
"""同一批次 FIX 重试上限(重试次数,不含首次调用);超限 ESCALATE 不死循环。"""

TOOL_NAME = "subagent"
"""批次派发在 session 树里的工具名(对应 loop 工具集的 subagent;COMPONENTS §2.1)。"""


class Verdict(StrEnum):
    """orchestrator 对子代理结果的三种裁决(COMPONENTS §2.4)。"""

    PASS = "PASS"
    FIX = "FIX"  # 必须带可执行返工指令
    ESCALATE = "ESCALATE"  # 升模型或问人


class NestedDispatchError(RuntimeError):
    """depth > 0 的嵌套派发被禁止(ARCH §6:子代理禁嵌套)。"""


@dataclass(frozen=True)
class SubagentResult:
    """子代理返回(ARCH §6):摘要 + 工件路径 + <200 字提示;过程留 child session。"""

    summary: str
    artifact_paths: list[Path] = field(default_factory=list)
    hint: str = ""  # <200 字核心提示/警告,超长截断并告警
    child_session: Path | None = None


@dataclass(frozen=True)
class DispatchDecision:
    """一次裁决结果;FIX 必须带可执行返工指令(禁止空泛)。"""

    verdict: Verdict
    rework_instruction: str | None = None


@dataclass(frozen=True)
class BatchReport:
    """agent_fn 返回(ARCH §6 子代理返回契约的 M0 简化):裁决 + <200 字提示 + FIX 返工指令。"""

    verdict: Verdict
    hint: str = ""  # <200 字核心提示/警告,超长截断并告警
    rework_instruction: str | None = None  # verdict=FIX 时必须携带(缺则回退 hint,再缺判无法返工)


AgentFn = Callable[[str, str | None], BatchReport]
"""批次执行函数形态:(批次名, 上轮 FIX 返工指令|None) → BatchReport;M0 由外部注入。"""


@dataclass(frozen=True)
class BatchOutcome:
    """一个批次的最终处理结果;verdict 只会是 PASS 或 ESCALATE(FIX 是中间态)。"""

    batch: str
    verdict: Verdict  # PASS | ESCALATE
    attempts: int  # agent_fn 调用总次数(首次 + 重试)
    history: tuple[Verdict, ...] = ()  # 逐次裁决序列(doom_loop 判定的原始记录)
    hint: str = ""  # 最后一次返回的提示(已截断至 ≤200 字)
    reason: str = ""  # pass | agent_escalate | doom_loop | max_retries | no_rework_instruction


@dataclass(frozen=True)
class PlanRunResult:
    """run_plan 总结果:ok = 全部批次 PASS;逐项 BatchOutcome 供人审/回放。"""

    ok: bool
    outcomes: tuple[BatchOutcome, ...] = ()

    @property
    def escalated(self) -> tuple[str, ...]:
        """被 ESCALATE 的批次清单(升模型或问人,COMPONENTS §2.4)。"""
        return tuple(o.batch for o in self.outcomes if o.verdict is Verdict.ESCALATE)


# ---------- doom_loop 与 hint 截断(纯函数,可单测) ----------


def check_doom_loop(asset_id: str, history: list[Verdict] | tuple[Verdict, ...], max_fix: int = DOOM_LOOP_MAX_FIX) -> bool:
    """同一资产连续 N 次 FIX 无进展 → True(ESCALATE 问人,COMPONENTS §7)。

    M0 无进展判定 = 尾部连续 max_fix 次 FIX(每次 FIX 都未 PASS 即未完成);
    结合 score 事件分数 delta 的精细判定留 M1(骨架 TODO)。
    """
    if max_fix < 1 or len(history) < max_fix:
        return False
    return all(v is Verdict.FIX for v in history[-max_fix:])


def truncate_hint(hint: str, max_chars: int = MAX_HINT_CHARS) -> str:
    """核心提示/警告超过 max_chars 截断并告警(COMPONENTS §2.4/§6)。"""
    if len(hint) > max_chars:
        warnings.warn(f"子代理 hint 超长({len(hint)} 字),已截断至 {max_chars} 字", stacklevel=2)
        return hint[:max_chars]
    return hint


# ---------- M0 顺序执行驱动 ----------


def run_plan(
    batches: Iterable[str],
    agent_fn: AgentFn,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    doom_max_fix: int = DOOM_LOOP_MAX_FIX,
    depth: int = 0,
    session: SessionStore | None = None,
) -> PlanRunResult:
    """按 plan 批次顺序逐批驱动(M0 顺序执行;并发 ≤4 留 M1)。

    每批:agent_fn(batch, rework) → BatchReport;PASS 进下一批;FIX 带返工指令
    重试同批(最多 max_retries 次,指令缺省回退 hint,两者皆空判 no_rework_instruction
    直接 ESCALATE——返工指令必须可执行,禁止空泛重试);连续 doom_max_fix 次 FIX
    无进展触发 doom_loop → ESCALATE;agent_fn 自报 ESCALATE 立即升级。
    depth > 0 直接拒绝(禁嵌套,ARCH §6);session 非空时每次调用落
    tool_call 事件对(phase=call/result,同一 toolCallId)。
    """
    if depth > 0:
        raise NestedDispatchError(f"子代理禁嵌套(ARCH §6):depth={depth} 的派发被拒绝")
    batch_list = [str(b) for b in batches]
    if not batch_list:
        raise ValueError("批次序列不能为空(plan 至少一个渲染检查单位)")
    if max_retries < 0:
        raise ValueError(f"max_retries 须 ≥ 0,实收 {max_retries}")

    outcomes: list[BatchOutcome] = []
    for batch in batch_list:
        outcomes.append(_run_batch(batch, agent_fn, max_retries=max_retries, doom_max_fix=doom_max_fix, session=session))
    return PlanRunResult(ok=all(o.verdict is Verdict.PASS for o in outcomes), outcomes=tuple(outcomes))


def _run_batch(
    batch: str,
    agent_fn: AgentFn,
    *,
    max_retries: int,
    doom_max_fix: int,
    session: SessionStore | None,
) -> BatchOutcome:
    """单批 FIX 重试环:PASS/ESCALATE 即出;FIX 检查 doom_loop → 重试上限 → 返工指令齐备性。"""
    history: list[Verdict] = []
    rework: str | None = None
    attempt = 0
    while True:
        attempt += 1
        report = _call_agent(batch, attempt, rework, agent_fn, session)
        hint = truncate_hint(report.hint or "")
        history.append(report.verdict)
        if report.verdict is Verdict.PASS:
            return BatchOutcome(batch, Verdict.PASS, attempt, tuple(history), hint, reason="pass")
        if report.verdict is Verdict.ESCALATE:
            return BatchOutcome(batch, Verdict.ESCALATE, attempt, tuple(history), hint, reason="agent_escalate")
        # FIX:先查死循环,再查重试预算,最后落实返工指令
        if check_doom_loop(batch, history, doom_max_fix):
            return BatchOutcome(batch, Verdict.ESCALATE, attempt, tuple(history), hint, reason="doom_loop")
        if attempt > max_retries:
            return BatchOutcome(batch, Verdict.ESCALATE, attempt, tuple(history), hint, reason="max_retries")
        rework = report.rework_instruction or hint or None
        if rework is None:
            return BatchOutcome(batch, Verdict.ESCALATE, attempt, tuple(history), hint, reason="no_rework_instruction")


def _call_agent(batch: str, attempt: int, rework: str | None, agent_fn: AgentFn, session: SessionStore | None) -> BatchReport:
    """调一次 agent_fn;session 非空时落 tool_call 事件对(phase=call/result,同一 toolCallId)。"""
    call_id = str(uuid7())
    if session is not None:
        summary = f"batch={batch} attempt={attempt}"
        if rework:
            summary += f" rework={rework[:80]}"
        session.append_new(
            EventType.TOOL_CALL,
            {"toolCallId": call_id, "toolName": TOOL_NAME, "args_summary": summary, "phase": "call"},
        )
    report = agent_fn(batch, rework)
    if session is not None:
        session.append_new(
            EventType.TOOL_CALL,
            {
                "toolCallId": call_id,
                "toolName": TOOL_NAME,
                "args_summary": f"batch={batch} attempt={attempt}",
                "phase": "result",
                "result_llm_view": f"{report.verdict.value} | {truncate_hint(report.hint or '')}",
                "status": "ok",
            },
        )
    return report


# ---------- M1 待接:真实子代理派发与裁决 ----------


def judge(result: SubagentResult, *, gate_ok: bool, score: float | None) -> DispatchDecision:
    """对子代理结果裁决 PASS / FIX(带可执行返工指令)/ ESCALATE。

    TODO(M1): 结合 schema_gate 结果与双环评分;FIX 指令必须可执行。
    """
    raise NotImplementedError("TODO(M1): judge 结合 schema_gate 与双环评分裁决")


__all__ = [
    "DOOM_LOOP_MAX_FIX",
    "MAX_CONCURRENCY",
    "MAX_HINT_CHARS",
    "BatchOutcome",
    "BatchReport",
    "DispatchDecision",
    "NestedDispatchError",
    "PlanRunResult",
    "SubagentResult",
    "Verdict",
    "check_doom_loop",
    "judge",
    "run_plan",
    "truncate_hint",
]
