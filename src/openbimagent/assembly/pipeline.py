"""Pipeline:把任务生命周期串成可用 CLI 产品(ARCH §2 完整生命周期)。

链路:
  load playbook → clarify(CLI 一问一答) → planner.instantiate(registry 真实 / 模板回退)
  → schema_gate(已在 instantiate 内) → orchestrator.run_plan(agent_fn=真实批次执行器)
  → 批次执行器 = builder + scad_loop/render_loop 双环
  → deliver 门禁 → 输出交付清单。

Ctrl+C 中断 → 落 checkpoint 事件(MESSAGE 形态)→ 返回 interrupted=True,可续跑。
审批门:MCP 写操作(execute_code)前 + deliver 前调 approval_fn(y/N,--yes 跳过)。
所有 LLM 调用走 providers registry(role=modeler/planner/critic_*),可注入替换(测试全 mock)。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbimagent.assembly.batch_executor import (
    ApprovalFn,
    OnHtmlReport,
    make_batch_executor,
)
from openbimagent.assembly.builder import make_builder_fn
from openbimagent.assembly.target_executor import (
    VectorworksBuilder,
    combine_target_executors,
    make_vectorworks_batch_executor,
    missing_target_executor,
)
from openbimagent.clarify import slots as clarify
from openbimagent.deliver.gate import DeliveryReport, check_deliverables, make_acceptance_fn
from openbimagent.domain_gate import DomainGateReport, evaluate_domain_gate
from openbimagent.orchestrator.dispatch import PlanRunResult, run_plan
from openbimagent.planner.instantiate import PlanArtifacts, instantiate, load_playbook
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore

OnPhase = Callable[[str, dict[str, Any]], None]
"""阶段进度回调:(phase_name, payload) → None;CLI 用来打印阶段标题。"""


@dataclass(frozen=True)
class PipelineResult:
    """run_pipeline 总结果:ok = 全流程成功(orchestrator ok + deliver ok)。"""

    ok: bool
    plan_run: PlanRunResult | None = None
    delivery: DeliveryReport | None = None
    artifacts_dir: Path | None = None
    session: SessionStore | None = None
    interrupted: bool = False
    error: str | None = None
    plan_artifacts: PlanArtifacts | None = None
    domain_gate: DomainGateReport | None = None
    phases_log: tuple[tuple[str, str], ...] = ()  # (phase_name, outcome_note) 序列


def run_pipeline(
    playbook_path: Path,
    *,
    out_dir: Path,
    registry: Any = None,
    blender_client: Any = None,
    vectorworks_client: Any = None,
    vectorworks_builder: VectorworksBuilder | None = None,
    domain_evidence: dict[str, Any] | None = None,
    scad_critic: Any = None,
    render_critic: Any = None,
    input_func: Callable[[str], str] = input,
    approval_fn: ApprovalFn | None = None,
    on_html_report: OnHtmlReport | None = None,
    on_phase: OnPhase | None = None,
    sessions_dir: Path | None = None,
    yes: bool = False,
    cameras: list[str] | None = None,
    turntable_target: str | None = None,
    turntable_frames: int = 4,
    image_size: int = 512,
    max_retries: int = 3,
    doom_max_fix: int = 3,
    session_id: str | None = None,
) -> PipelineResult:
    """跑全流程:load playbook → clarify → plan → orchestrate → deliver。

    - registry 为空:planner + builder 都走确定性模板(可离线跑,测试默认路径)。
    - ``targets`` 控制后端；未声明时向后兼容为 ``[blender]``。声明目标缺少 client/builder
      时对应批次明确 ESCALATE，不静默跳过。
    - acceptance.domain_gate 启用项必须在 domain_evidence 中有显式 bool 证据；缺失为
      UNKNOWN 并在构建前阻断，避免语义 IR 被误判为工程合规。
    - approval_fn 为空 且 yes=False:不审批(测试默认);yes=True 跳过所有审批门。
    - Ctrl+C 中断:落 checkpoint 事件到 session,返回 interrupted=True。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sessions_root = Path(sessions_dir) if sessions_dir is not None else out / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    phases_log: list[tuple[str, str]] = []

    def _phase(name: str, note: str = "", **kwargs: Any) -> None:
        phases_log.append((name, note))
        if on_phase is not None:
            on_phase(name, {"note": note, **kwargs})

    store = _open_or_create_session(sessions_root, session_id, playbook_path)

    # ---------- 1. load playbook ----------
    try:
        playbook = load_playbook(Path(playbook_path))
    except Exception as exc:
        _phase("load_playbook", f"失败: {exc}")
        return PipelineResult(ok=False, session=store, error=str(exc), phases_log=tuple(phases_log))
    _phase("load_playbook", f"name={playbook['name']} batches={playbook['batches']}")

    # ---------- 2. clarify(CLI 一问一答) ----------
    # 问答对全程捕获并落 message 事件(验收 e):assistant 问 / user 答成对写入,
    # 支持 /tree 回退到任一追问前改答(M0 冒烟缺口:clarify 曾只走 stdout 不落 session)。
    qa_pairs: list[tuple[str, str]] = []

    def _clarify_input(prompt: str) -> str:
        answer = input_func(prompt)
        qa_pairs.append((prompt, answer))
        return answer

    try:
        entry = store._index_entry()
        is_resume = entry is not None and entry.get("forked_from") is not None
        if is_resume:
            # 分支会话(/tree fork):从 forked_from.parent_event_id 恢复已问槽位,只问剩余
            fork_info = entry["forked_from"]
            slot_state = clarify.resume_from_session(
                clarify.load_playbook_slots(Path(playbook_path)),
                store,
                from_event_id=fork_info["parent_event_id"],
            )
        else:
            slot_state = clarify.SlotState(slots=clarify.load_playbook_slots(Path(playbook_path)))
        try:
            clarify.run_clarify(slot_state, input_func=_clarify_input, resume=is_resume)
        finally:
            _record_clarify_messages(store, qa_pairs)
        slots_filled = {s.id: s.value for s in slot_state.slots if s.value is not None}
        if not clarify.may_proceed(slot_state):
            note = f"clarify 未达放行阈值(completion_score={slot_state.completion_score:.1f} < {clarify.PASS_THRESHOLD})"
            _phase("clarify", note)
            return PipelineResult(ok=False, session=store, error=note, phases_log=tuple(phases_log))
        _phase("clarify", f"completion_score={slot_state.completion_score:.1f} slots={list(slots_filled)}")
    except Exception as exc:
        _phase("clarify", f"失败: {exc}")
        return PipelineResult(ok=False, session=store, error=str(exc), phases_log=tuple(phases_log))

    # ---------- 3. planner.instantiate(registry 真实 / 模板回退) ----------
    try:
        plan_artifacts = instantiate(playbook, slots_filled, out, registry=registry)
    except Exception as exc:
        _phase("planner_instantiate", f"失败: {exc}")
        return PipelineResult(ok=False, session=store, error=str(exc), phases_log=tuple(phases_log))
    ir = json.loads(plan_artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    _phase("planner_instantiate", f"ir_assets={len(ir.get('assets', []))} batches={len(ir.get('batches', []))}")

    # ---------- 4. domain_gate(确定性 evidence；UNKNOWN 不得放行) ----------
    domain_report = evaluate_domain_gate(
        playbook["acceptance"].get("domain_gate"),
        domain_evidence,
    )
    _phase(
        "domain_gate",
        f"status={domain_report.status.value} failed={list(domain_report.failed)} unknown={list(domain_report.unknown)}",
    )
    if not domain_report.ok:
        return PipelineResult(
            ok=False,
            artifacts_dir=out,
            session=store,
            error=domain_report.rework_instruction or "domain_gate 未通过",
            plan_artifacts=plan_artifacts,
            domain_gate=domain_report,
            phases_log=tuple(phases_log),
        )

    # ---------- 5. orchestrator.run_plan(agent_fn=targets 批次执行器) ----------
    batch_names = list(playbook["batches"]) or ["默认批次"]
    builder_fn = make_builder_fn(registry=registry)
    effective_approval = approval_fn if not yes else None

    targets = list(playbook.get("targets") or ["blender"])
    executors: dict[str, Any] = {}
    if "blender" in targets:
        if blender_client is None:
            executors["blender"] = missing_target_executor("blender", "缺少 blender_client")
        else:
            executors["blender"] = make_batch_executor(
                ir=ir,
                batch_names=batch_names,
                work_dir=out / "batches" / "blender",
                acceptance=playbook["acceptance"],
                client=blender_client,
                builder_fn=builder_fn,
                scad_critic=scad_critic,
                render_critic=render_critic,
                session=store,
                blend_path=out / "scene.blend",
                cameras=cameras,
                turntable_target=turntable_target,
                turntable_frames=turntable_frames,
                image_size=image_size,
                approval_fn=effective_approval,
                on_html_report=on_html_report,
            )
    if "vectorworks" in targets:
        if vectorworks_client is None:
            executors["vectorworks"] = missing_target_executor(
                "vectorworks", "缺少 vectorworks_client"
            )
        elif vectorworks_builder is None:
            executors["vectorworks"] = missing_target_executor(
                "vectorworks", "缺少 vectorworks_builder，不能从语义 IR 伪造 BIM 代码"
            )
        else:
            executors["vectorworks"] = make_vectorworks_batch_executor(
                ir=ir,
                batch_names=batch_names,
                work_dir=out / "batches" / "vectorworks",
                client=vectorworks_client,
                builder_fn=vectorworks_builder,
                approval_fn=effective_approval,
                auto_approve=yes,
            )

    missing_targets = [
        target
        for target in targets
        if (target == "blender" and blender_client is None)
        or (target == "vectorworks" and (vectorworks_client is None or vectorworks_builder is None))
    ]
    if missing_targets:
        _phase("target_dispatch", f"配置不完整: missing={missing_targets}")
        agent_fn = missing_target_executor(
            "configuration",
            f"声明目标缺少运行依赖: {missing_targets}；为避免部分写入，所有 target 均未执行",
        )
    else:
        agent_fn = combine_target_executors(executors)
        _phase("target_dispatch", f"targets={targets} available={list(executors)}")
    plan_run: PlanRunResult | None = None
    try:
        plan_run = run_plan(
            batch_names,
            agent_fn,
            session=store,
            max_retries=max_retries,
            doom_max_fix=doom_max_fix,
        )
    except KeyboardInterrupt:
        _record_checkpoint(store, "orchestrate", "Ctrl+C 中断 @ orchestrator.run_plan")
        _phase("orchestrate", "中断(Ctrl+C)")
        return PipelineResult(
            ok=False,
            session=store,
            interrupted=True,
            error="Ctrl+C",
            domain_gate=domain_report,
            phases_log=tuple(phases_log),
        )
    escalated = list(plan_run.escalated)
    _phase("orchestrate", f"ok={plan_run.ok} escalated={escalated}")

    # ---------- 6. deliver 门禁 ----------
    deliver_approval = approval_fn if not yes else None
    if deliver_approval is not None:
        approved = deliver_approval("deliver", {
            "artifacts_dir": str(out),
            "deliverables": playbook["deliverables"],
        })
        if not approved:
            _phase("deliver", "用户拒绝 deliver 审批门")
            return PipelineResult(ok=False, plan_run=plan_run, session=store,
                                  artifacts_dir=out, error="用户拒绝 deliver 审批门",
                                  plan_artifacts=plan_artifacts, domain_gate=domain_report,
                                  phases_log=tuple(phases_log))

    accepted_fn = make_acceptance_fn(store, playbook["acceptance"])
    delivery = check_deliverables(playbook["deliverables"], out, accepted_fn=accepted_fn)
    _phase("deliver", f"ok={delivery.ok} accepted={delivery.accepted} missing={delivery.missing}")

    return PipelineResult(
        ok=(plan_run.ok if plan_run is not None else False) and delivery.ok,
        plan_run=plan_run,
        delivery=delivery,
        artifacts_dir=out,
        session=store,
        plan_artifacts=plan_artifacts,
        domain_gate=domain_report,
        phases_log=tuple(phases_log),
    )


def _open_or_create_session(sessions_root: Path, session_id: str | None, playbook_path: Path) -> SessionStore:
    """按 session_id 打开已有会话(分支续跑)或新建会话。

    session_id 支持完整 id 或唯一前缀匹配(与 cli._open_session 同规则);为 None 时新建。
    续跑时 SessionStore 重开会沿用 index 里登记的 title/playbook/forked_from 元数据。
    """
    if session_id is None:
        return SessionStore.create(
            sessions_root,
            title=f"openBIMAgent · {Path(playbook_path).parent.name}",
            playbook=str(playbook_path),
        )
    target = sessions_root / f"{session_id}.jsonl"
    if not target.is_file():
        matches = list(sessions_root.glob(f"{session_id}*.jsonl"))
        if len(matches) == 1:
            target = matches[0]
        elif not matches:
            raise FileNotFoundError(
                f"会话 {session_id!r} 不存在(sessions_dir={sessions_root})"
            )
        else:
            raise FileNotFoundError(
                f"会话 id 前缀 {session_id!r} 匹配多个:{[m.stem for m in matches]}"
            )
    return SessionStore(target)


def _record_checkpoint(store: SessionStore, phase: str, note: str) -> None:
    """Ctrl+C 中断时落 checkpoint 事件(MESSAGE 形态,可后续 /tree 回退续跑)。

    session.schema.CustomType 枚举不含 checkpoint,借用 MESSAGE 落地最简;后续可扩 schema。
    """
    store.append_new(
        EventType.MESSAGE,
        {"role": "assistant", "content": f"[checkpoint] phase={phase} note={note}"},
    )


def _record_clarify_messages(store: SessionStore, qa_pairs: list[tuple[str, str]]) -> None:
    """把 clarify 一问一答落成 message 事件:assistant 问 / user 答成对追加(验收 e 五类事件之 message)。

    每对两条事件、按问答顺序挂事件树,使 /tree 可回退到任一追问之前改答。
    """
    for question, answer in qa_pairs:
        store.append_new(EventType.MESSAGE, {"role": "assistant", "content": question})
        store.append_new(EventType.MESSAGE, {"role": "user", "content": answer})


__all__ = ["OnPhase", "PipelineResult", "run_pipeline"]
