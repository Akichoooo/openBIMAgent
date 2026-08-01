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

import asyncio
import re
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

DOOM_LOOP_MIN_DELTA = 0.3
"""doom_loop 评分无进展判定阈值(M1):相邻评分变化 < 此值视为无进展(COMPONENTS §7)。"""

DEFAULT_MAX_RETRIES = 3
"""同一批次 FIX 重试上限(重试次数,不含首次调用);超限 ESCALATE 不死循环。"""

DEFAULT_PASS_SCORE = 8.5
"""统一 judge 的默认通过分；与 Blender 主环默认 min_score 保持一致。"""

_SCORE_PATTERN = re.compile(r"(?:overall|score)=([0-9]+(?:\.[0-9]+)?)")
"""从 BatchReport.hint 提取评分(overall=X.X 或 score=X.X)的正则(M1 doom_loop 增强)。"""

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
    """Orchestrator 裁决视图：兼容旧 agent_fn，并可由 Runtime v1 结果信封构造。"""

    summary: str
    artifact_paths: list[Path] = field(default_factory=list)
    hint: str = ""  # <200 字核心提示/警告,超长截断并告警
    child_session: Path | None = None
    request_id: str | None = None
    agent_id: str | None = None
    receipt_id: str | None = None

    @classmethod
    def from_envelope(cls, envelope: object) -> "SubagentResult":
        """把 SubagentResultEnvelope 转成现有 judge() 可消费的紧凑视图，避免过程污染。"""
        return cls(
            summary=str(getattr(envelope, "summary")),
            artifact_paths=[Path(record.path) for record in getattr(envelope, "artifacts")],
            hint=truncate_hint(str(getattr(envelope, "hint"))),
            child_session=Path(str(getattr(envelope, "child_session_path"))),
            request_id=str(getattr(envelope, "request_id")),
            agent_id=str(getattr(envelope, "agent_id")),
            receipt_id=str(getattr(envelope, "receipt_id")),
        )


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
    """run_plan 总结果:ok = 全部批次 PASS;逐项 BatchOutcome 供人审/回放。

    M1 新增:
    - error:首个 doom_loop/失败原因的简短标识(空字符串表示无异常),供上层诊断。
    - subagent_results:每次 agent_fn 调用包装的 SubagentResult 累积(ARCH §6 契约完整化)。
    """

    ok: bool
    outcomes: tuple[BatchOutcome, ...] = ()
    error: str = ""
    subagent_results: list[SubagentResult] = field(default_factory=list)

    @property
    def escalated(self) -> tuple[str, ...]:
        """被 ESCALATE 的批次清单(升模型或问人,COMPONENTS §2.4)。"""
        return tuple(o.batch for o in self.outcomes if o.verdict is Verdict.ESCALATE)


# ---------- doom_loop 与 hint 截断(纯函数,可单测) ----------


def check_doom_loop(
    asset_id: str,
    history: list[Verdict] | tuple[Verdict, ...],
    max_fix: int = DOOM_LOOP_MAX_FIX,
    *,
    scores: list[float] | tuple[float, ...] | None = None,
    min_delta: float = DOOM_LOOP_MIN_DELTA,
) -> bool:
    """同一资产连续 N 次 FIX 无进展 → True(ESCALATE 问人,COMPONENTS §7)。

    M0 无进展判定 = 尾部连续 max_fix 次 FIX(每次 FIX 都未 PASS 即未完成)。
    M1 增强:若提供 scores(从 hint 提取的评分序列),额外要求最近 max_fix 次评分
    相邻变化全部 < min_delta 才判死循环(评分有进展即不判 doom_loop,允许继续迭代)。
    scores 为 None(向后兼容 M0)或含 -1.0(无评分记录)时退化为纯 FIX 计数判定。
    """
    if max_fix < 1 or len(history) < max_fix:
        return False
    if not all(v is Verdict.FIX for v in history[-max_fix:]):
        return False
    # M1 评分无进展判定:提供 scores 且有 ≥2 个有效评分时,要求相邻变化全部 < min_delta
    if scores is not None and len(scores) >= max_fix:
        recent = [s for s in scores[-max_fix:] if s >= 0]  # 过滤 -1.0(无评分记录)
        if len(recent) >= 2:
            deltas = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
            if not all(d < min_delta for d in deltas):
                return False  # 评分有进展,不判 doom_loop
    return True


def extract_score(hint: str | None) -> float:
    """从 hint 提取评分(overall=X.X 或 score=X.X);无则返回 -1.0(表示无评分记录)。

    M1 doom_loop 增强用:把 BatchReport.hint 的评分落进 fix_history 供无进展判定。
    """
    if not hint:
        return -1.0
    match = _SCORE_PATTERN.search(hint)
    if match is None:
        return -1.0
    try:
        return float(match.group(1))
    except ValueError:
        return -1.0


def truncate_hint(hint: str, max_chars: int = MAX_HINT_CHARS) -> str:
    """核心提示/警告超过 max_chars 截断并告警(COMPONENTS §2.4/§6)。"""
    if len(hint) > max_chars:
        warnings.warn(f"子代理 hint 超长({len(hint)} 字),已截断至 {max_chars} 字", stacklevel=2)
        return hint[:max_chars]
    return hint


# ---------- M0 顺序执行驱动 / M1 并发调度 ----------


def run_plan(
    batches: Iterable[str],
    agent_fn: AgentFn,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    doom_max_fix: int = DOOM_LOOP_MAX_FIX,
    depth: int = 0,
    session: SessionStore | None = None,
    concurrent: bool = False,
) -> PlanRunResult:
    """按 plan 批次驱动 agent_fn;concurrent=False 顺序执行(M0 默认),True 并发 ≤4(M1)。

    每批:agent_fn(batch, rework) → BatchReport;PASS 进下一批;FIX 带返工指令
    重试同批(最多 max_retries 次,指令缺省回退 hint,两者皆空判 no_rework_instruction
    直接 ESCALATE——返工指令必须可执行,禁止空泛重试);连续 doom_max_fix 次 FIX
    无进展触发 doom_loop → ESCALATE;agent_fn 自报 ESCALATE 立即升级。
    depth > 0 直接拒绝(禁嵌套,ARCH §6);session 非空时每次调用落
    tool_call 事件对(phase=call/result,同一 toolCallId)。

    M1 增强:
    - concurrent=True:用 asyncio.gather + Semaphore(MAX_CONCURRENCY) 并发调度所有批次,
      同步 agent_fn 用 asyncio.to_thread 包装;结果顺序与输入一致(gather 语义)。
    - doom_loop 检测结合 hint 提取的评分(overall=/score=)做无进展判定。
    - 每次 agent_fn 调用包装为 SubagentResult 累积到 PlanRunResult.subagent_results。
    """
    if depth > 0:
        raise NestedDispatchError(f"子代理禁嵌套(ARCH §6):depth={depth} 的派发被拒绝")
    batch_list = [str(b) for b in batches]
    if not batch_list:
        raise ValueError("批次序列不能为空(plan 至少一个渲染检查单位)")
    if max_retries < 0:
        raise ValueError(f"max_retries 须 ≥ 0,实收 {max_retries}")

    if concurrent:
        return asyncio.run(_run_plan_concurrent(batch_list, agent_fn, max_retries, doom_max_fix, session))

    outcomes: list[BatchOutcome] = []
    subagent_results: list[SubagentResult] = []
    for batch in batch_list:
        outcomes.append(
            _run_batch(
                batch,
                agent_fn,
                max_retries=max_retries,
                doom_max_fix=doom_max_fix,
                session=session,
                subagent_results=subagent_results,
            )
        )
    return _assemble_result(outcomes, subagent_results)


async def _run_plan_concurrent(
    batches: list[str],
    agent_fn: AgentFn,
    max_retries: int,
    doom_max_fix: int,
    session: SessionStore | None,
) -> PlanRunResult:
    """并发调度所有批次(M1):asyncio.gather + Semaphore(MAX_CONCURRENCY)。

    每批用 asyncio.to_thread 包装同步 _run_batch;结果顺序与输入一致(gather 语义)。
    每批独立 subagent_results 列表(线程隔离),最后按批次顺序合并。
    """
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run_one(batch: str) -> tuple[BatchOutcome, list[SubagentResult]]:
        batch_results: list[SubagentResult] = []
        async with sem:
            outcome = await asyncio.to_thread(
                _run_batch,
                batch,
                agent_fn,
                max_retries=max_retries,
                doom_max_fix=doom_max_fix,
                session=session,
                subagent_results=batch_results,
            )
        return outcome, batch_results

    results = await asyncio.gather(*(run_one(b) for b in batches))
    outcomes = tuple(r[0] for r in results)
    subagent_results: list[SubagentResult] = []
    for _, batch_results in results:
        subagent_results.extend(batch_results)
    return _assemble_result(outcomes, subagent_results)


def _assemble_result(outcomes: tuple[BatchOutcome, ...] | list[BatchOutcome], subagent_results: list[SubagentResult]) -> PlanRunResult:
    """汇总 outcomes 与 subagent_results 成 PlanRunResult;首个 doom_loop 落进 error。"""
    error = ""
    for o in outcomes:
        if o.reason == "doom_loop":
            error = "doom_loop"
            break
    return PlanRunResult(
        ok=all(o.verdict is Verdict.PASS for o in outcomes),
        outcomes=tuple(outcomes),
        error=error,
        subagent_results=subagent_results,
    )


def _run_batch(
    batch: str,
    agent_fn: AgentFn,
    *,
    max_retries: int,
    doom_max_fix: int,
    session: SessionStore | None,
    subagent_results: list[SubagentResult] | None = None,
) -> BatchOutcome:
    """单批 FIX 重试环:PASS/ESCALATE 即出;FIX 检查 doom_loop → 重试上限 → 返工指令齐备性。

    M1 增强:
    - 维护 score_history(从 hint 提取评分)传入 check_doom_loop 做无进展判定。
    - subagent_results 非空时,每次 agent_fn 调用包装为 SubagentResult 累积(ARCH §6 契约)。
    """
    history: list[Verdict] = []
    score_history: list[float] = []
    rework: str | None = None
    attempt = 0
    while True:
        attempt += 1
        report = _call_agent(batch, attempt, rework, agent_fn, session)
        hint = truncate_hint(report.hint or "")
        history.append(report.verdict)
        score_history.append(extract_score(report.hint))
        if subagent_results is not None:
            subagent_results.append(
                SubagentResult(summary=hint, artifact_paths=[], hint=hint)
            )
        if report.verdict is Verdict.PASS:
            return BatchOutcome(batch, Verdict.PASS, attempt, tuple(history), hint, reason="pass")
        if report.verdict is Verdict.ESCALATE:
            return BatchOutcome(batch, Verdict.ESCALATE, attempt, tuple(history), hint, reason="agent_escalate")
        # FIX:先查死循环,再查重试预算,最后落实返工指令
        if check_doom_loop(batch, history, doom_max_fix, scores=score_history):
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


def judge(
    result: SubagentResult,
    *,
    gate_ok: bool,
    score: float | None,
    min_score: float = DEFAULT_PASS_SCORE,
) -> DispatchDecision:
    """统一裁决 Schema/Domain Gate 与双环评分。

    规则：
    - gate 失败：使用子代理 hint/summary 作为可执行返工依据；两者都为空则无法安全
      自动修复，ESCALATE。
    - gate 通过但 score 缺失：缺少客观验收证据，ESCALATE，不伪造 PASS。
    - score 达标：PASS。
    - score 未达标：FIX，并给出目标分、当前分和子代理反馈。
    """
    if min_score < 0:
        raise ValueError(f"min_score 须 ≥ 0,实收 {min_score}")
    feedback = (result.hint or result.summary or "").strip()
    if not gate_ok:
        if not feedback:
            return DispatchDecision(Verdict.ESCALATE)
        return DispatchDecision(
            Verdict.FIX,
            rework_instruction=f"修复 Schema/Domain Gate 失败项后重新生成并复验：{feedback}",
        )
    if score is None:
        return DispatchDecision(Verdict.ESCALATE)
    if score >= min_score:
        return DispatchDecision(Verdict.PASS)
    detail = feedback or "重新执行 critic，定位低分维度并逐项修复"
    return DispatchDecision(
        Verdict.FIX,
        rework_instruction=(
            f"当前评分 {score:.2f} 低于通过线 {min_score:.2f}；{detail}；"
            "修复后重新运行确定性门禁与 critic 评分"
        ),
    )


__all__ = [
    "DOOM_LOOP_MAX_FIX",
    "DOOM_LOOP_MIN_DELTA",
    "DEFAULT_PASS_SCORE",
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
    "extract_score",
    "judge",
    "run_plan",
    "truncate_hint",
]
