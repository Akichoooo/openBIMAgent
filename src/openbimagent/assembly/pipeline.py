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

import hashlib
import json
import mimetypes
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
from openbimagent.deliver.manifest import commit_delivery_manifest
from openbimagent.domain_gate import DomainGateReport, evaluate_domain_gate
from openbimagent.orchestrator.dispatch import PlanRunResult, run_plan
from openbimagent.planner.instantiate import PlanArtifacts, instantiate, load_playbook
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.utility import (
    MUNICIPAL_RULE_SET_VERSION,
    UTILITY_SOLVER_INPUT_VERSION,
    UTILITY_SOLVER_NAME,
    UTILITY_SOLVER_VERSION,
    MunicipalRuleError,
    UtilitySolverError,
    UtilitySolverResult,
    compile_municipal_rule_set,
    solve_straight_gravity_utility,
)

OnPhase = Callable[[str, dict[str, Any]], None]
"""阶段进度回调:(phase_name, payload) → None;CLI 用来打印阶段标题。"""

COMPILED_UTILITY_IR_FILENAME = "compiled_utility_ir.json"
MUNICIPAL_RULE_SET_FILENAME = "municipal_rule_set.json"
DOMAIN_GATE_REPORT_FILENAME = "domain_gate_report.json"


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
    utility_solver: UtilitySolverResult | None = None
    compiled_utility_ir: Path | None = None
    municipal_rule_set: Path | None = None
    domain_gate_report: Path | None = None
    artifact_manifest: Path | None = None
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
    utility_solver_input: dict[str, Any] | Path | None = None,
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
    - 声明 ``municipal-straight-gravity-solver`` 的 playbook 可通过 utility_solver_input
      执行确定性求解并落 compiled IR；输入缺失或证据不足均为 UNKNOWN，构建前阻断。
    - v0.4 从 playbook 声明的受信任 rule_source 编译 MunicipalRuleSet；障碍物只提交
      工程事实。仅高置信且规范核验证据完整的规则产生 PASS/FAIL，其他情况产生 UNKNOWN。
    - domain_evidence 可补充 Solver 尚未判定的证据，但不能覆盖 Solver 已明确
      判定的 PASS/FAIL，避免外部布尔值绕过确定性工程门禁。
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

    # ---------- 4. domain_solver + domain_gate(确定性 evidence；UNKNOWN 不得放行) ----------
    utility_solver: UtilitySolverResult | None = None
    compiled_utility_ir_path: Path | None = None
    municipal_rule_set_path: Path | None = None
    domain_gate_report_path: Path | None = None
    domain_requirements = playbook["acceptance"].get("domain_gate")
    try:
        solver_phase = _find_solver_phase(playbook)
    except ValueError as exc:
        _phase("domain_solver", f"失败: {exc}")
        return PipelineResult(
            ok=False,
            artifacts_dir=out,
            session=store,
            error=str(exc),
            plan_artifacts=plan_artifacts,
            phases_log=tuple(phases_log),
        )
    effective_evidence = dict(domain_evidence or {}) if solver_phase is None else {}
    if solver_phase is not None:
        solver_name = str(solver_phase.get("solver") or "")
        try:
            _validate_solver_declaration(solver_phase)
        except ValueError as exc:
            _phase("domain_solver", f"失败: {exc}")
            return PipelineResult(
                ok=False,
                artifacts_dir=out,
                session=store,
                error=str(exc),
                plan_artifacts=plan_artifacts,
                phases_log=tuple(phases_log),
            )
        if utility_solver_input is None:
            _phase("domain_solver", f"solver={solver_name} 输入缺失；不猜测坐标、标高或规范参数")
        else:
            try:
                solver_payload = _load_solver_input(utility_solver_input)
                rule_source = _resolve_domain_pack_resource(
                    Path(playbook["path"]).parent,
                    str(solver_phase.get("rule_source") or ""),
                )
                municipal_rule_set = compile_municipal_rule_set(
                    rule_source,
                    logical_source_path=str(solver_phase["rule_source"]),
                )
                utility_solver = solve_straight_gravity_utility(
                    solver_payload,
                    domain_requirements=domain_requirements,
                    municipal_rule_set=municipal_rule_set,
                )
                municipal_rule_set_path = out / MUNICIPAL_RULE_SET_FILENAME
                _write_json_artifact(
                    municipal_rule_set_path,
                    municipal_rule_set.model_dump(mode="json"),
                )
            except (
                OSError,
                json.JSONDecodeError,
                TypeError,
                MunicipalRuleError,
                UtilitySolverError,
                ValueError,
            ) as exc:
                error = f"领域 Solver 执行失败: {exc}"
                _phase("domain_solver", f"失败: {exc}")
                return PipelineResult(
                    ok=False,
                    artifacts_dir=out,
                    session=store,
                    error=error,
                    plan_artifacts=plan_artifacts,
                    phases_log=tuple(phases_log),
                )
            try:
                compiled_utility_ir_path = _resolve_output_artifact(
                    out,
                    str(solver_phase.get("output") or COMPILED_UTILITY_IR_FILENAME),
                )
            except ValueError as exc:
                _phase("domain_solver", f"失败: {exc}")
                return PipelineResult(
                    ok=False,
                    artifacts_dir=out,
                    session=store,
                    error=str(exc),
                    plan_artifacts=plan_artifacts,
                    utility_solver=utility_solver,
                    phases_log=tuple(phases_log),
                )
            _write_json_artifact(
                compiled_utility_ir_path,
                utility_solver.compiled_ir.model_dump(mode="json"),
            )
            solver_evidence = utility_solver.compiled_ir.domain_evidence()
            effective_evidence = _merge_domain_evidence(solver_evidence, effective_evidence)
            _phase(
                "domain_solver",
                f"solver={solver_name} ir={compiled_utility_ir_path.name} "
                f"sha256={utility_solver.compiled_ir.canonical_sha256()}",
            )

    domain_report = evaluate_domain_gate(domain_requirements, effective_evidence)
    domain_gate_report_path = out / DOMAIN_GATE_REPORT_FILENAME
    _write_json_artifact(domain_gate_report_path, _domain_gate_payload(domain_report))
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
            utility_solver=utility_solver,
            compiled_utility_ir=compiled_utility_ir_path,
            municipal_rule_set=municipal_rule_set_path,
            domain_gate_report=domain_gate_report_path,
            phases_log=tuple(phases_log),
        )

    # ---------- 5. orchestrator.run_plan(agent_fn=targets 批次执行器) ----------
    batch_names = list(playbook["batches"]) or ["默认批次"]
    builder_fn = make_builder_fn(registry=registry)
    effective_approval = approval_fn if not yes else None

    targets = list(playbook.get("targets") or ["blender"])
    typed_vectorworks_requires_compiled_ir = (
        "vectorworks" in targets
        and vectorworks_builder is not None
        and hasattr(vectorworks_builder, "build")
        and utility_solver is None
    )
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
            is_typed_vectorworks_builder = hasattr(vectorworks_builder, "build")
            if is_typed_vectorworks_builder and utility_solver is None:
                executors["vectorworks"] = missing_target_executor(
                    "vectorworks",
                    "typed VectorworksBuilder 要求受 Solver 验证的 CompiledUtilityIR；禁止回退 Scene Graph IR",
                )
            else:
                vectorworks_ir = (
                    utility_solver.compiled_ir.model_dump(mode="json")
                    if is_typed_vectorworks_builder and utility_solver is not None
                    else ir
                )
                executors["vectorworks"] = make_vectorworks_batch_executor(
                    ir=vectorworks_ir,
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
        or (
            target == "vectorworks"
            and (
                vectorworks_client is None
                or vectorworks_builder is None
                or typed_vectorworks_requires_compiled_ir
            )
        )
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
            utility_solver=utility_solver,
            compiled_utility_ir=compiled_utility_ir_path,
            municipal_rule_set=municipal_rule_set_path,
            domain_gate_report=domain_gate_report_path,
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
    artifact_manifest_path: Path | None = None
    if delivery.ok and plan_run is not None and plan_run.ok:
        resolved_artifacts = [
            {
                "path": str(Path(path).resolve().relative_to(out.resolve())),
                "kind": "deliverable",
                "media_type": _media_type(Path(path)),
                "sha256": _file_sha256(Path(path)),
                "dependencies": [],
                "status": "completed",
            }
            for path in delivery.resolved.values()
        ]
        delivery_key = f"pipeline-delivery:{store.session_id}"
        delivery_result = commit_delivery_manifest(
            workdir=out,
            artifacts=resolved_artifacts,
            idempotency_key=delivery_key,
            domain_gate_status=domain_report.status.value,
            request_id=store.session_id,
            source_attempt_id=store.session_id,
            domain_gate_required=bool(domain_report.required),
        )
        artifact_manifest_path = delivery_result.manifest_path
    _phase(
        "deliver",
        f"ok={delivery.ok} accepted={delivery.accepted} missing={delivery.missing} "
        f"manifest={artifact_manifest_path.name if artifact_manifest_path else None}",
    )

    return PipelineResult(
        ok=(plan_run.ok if plan_run is not None else False) and delivery.ok,
        plan_run=plan_run,
        delivery=delivery,
        artifacts_dir=out,
        session=store,
        plan_artifacts=plan_artifacts,
        domain_gate=domain_report,
        utility_solver=utility_solver,
        compiled_utility_ir=compiled_utility_ir_path,
        municipal_rule_set=municipal_rule_set_path,
        domain_gate_report=domain_gate_report_path,
        artifact_manifest=artifact_manifest_path,
        phases_log=tuple(phases_log),
    )


def _find_solver_phase(playbook: dict[str, Any]) -> dict[str, Any] | None:
    """返回声明 Solver 的阶段；未声明时保持旧 Playbook 的无 Solver 语义。"""
    phases = playbook.get("phases") or []
    matches = [p for p in phases if isinstance(p, dict) and p.get("solver")]
    if len(matches) > 1:
        raise ValueError(f"一个 Playbook 只能声明一个领域 Solver，实际 {len(matches)} 个")
    return matches[0] if matches else None


def _validate_solver_declaration(phase: dict[str, Any]) -> None:
    """声明必须与内置 Solver 契约逐项匹配，禁止名称相同但版本/Schema 漂移。"""
    solver_name = str(phase.get("solver") or "")
    if solver_name != UTILITY_SOLVER_NAME:
        raise ValueError(f"不支持的领域 Solver: {solver_name!r}")
    declared_version = str(phase.get("solver_version") or "")
    if declared_version != UTILITY_SOLVER_VERSION:
        raise ValueError(
            f"Solver 版本不匹配: playbook={declared_version!r}, runtime={UTILITY_SOLVER_VERSION!r}"
        )
    expected_schema = "utility_solver_input.schema.json"
    declared_schema = str(phase.get("input_schema") or "")
    if declared_schema != expected_schema:
        raise ValueError(
            f"Solver 输入 Schema 不匹配: playbook={declared_schema!r}, expected={expected_schema!r}"
        )
    declared_rule_source = str(phase.get("rule_source") or "")
    if not declared_rule_source:
        raise ValueError("Solver 必须声明受信任 rule_source")
    declared_rule_set_schema = str(phase.get("rule_set_schema") or "")
    expected_rule_set_schema = "municipal_rule_set.schema.json"
    if declared_rule_set_schema != expected_rule_set_schema:
        raise ValueError(
            "Solver Rule Set Schema 不匹配: "
            f"playbook={declared_rule_set_schema!r}, expected={expected_rule_set_schema!r}"
        )
    if UTILITY_SOLVER_INPUT_VERSION != "0.4" or MUNICIPAL_RULE_SET_VERSION != "1.1":
        raise ValueError(
            "Runtime Solver/Rule Set 协议版本未受支持: "
            f"input={UTILITY_SOLVER_INPUT_VERSION!r}, rules={MUNICIPAL_RULE_SET_VERSION!r}"
        )


def _resolve_domain_pack_resource(pack_dir: Path, declared_path: str) -> Path:
    """把规则源限制在当前 Domain Pack 内，拒绝空值、绝对路径和 ``..`` 越界。"""
    if not declared_path:
        raise ValueError("Solver rule_source 不能为空")
    relative = Path(declared_path)
    if relative.is_absolute():
        raise ValueError(f"Solver rule_source 必须是 Domain Pack 内相对路径: {declared_path!r}")
    root = Path(pack_dir).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Solver rule_source 路径越界 Domain Pack: {declared_path!r}") from exc
    if not target.is_file():
        raise ValueError(f"Solver rule_source 不存在或不是文件: {declared_path!r}")
    return target



def _resolve_output_artifact(out_dir: Path, declared_path: str) -> Path:
    """将 Playbook 输出限制在本次 out_dir 内，拒绝绝对路径与 ``..`` 越界。"""
    relative = Path(declared_path)
    if relative.is_absolute():
        raise ValueError(f"Solver 输出必须是 out_dir 内相对路径: {declared_path!r}")
    root = out_dir.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Solver 输出路径越界 out_dir: {declared_path!r}") from exc
    return target


def _load_solver_input(value: dict[str, Any] | Path) -> dict[str, Any]:
    """读取版本化 Solver 输入；只接受 mapping 或 JSON 文件，不从 clarify 猜测工程参数。"""
    if isinstance(value, Path):
        payload = json.loads(value.read_text(encoding="utf-8"))
    elif isinstance(value, dict):
        payload = value
    else:
        raise TypeError(f"utility_solver_input 必须是 dict 或 JSON Path，实际 {type(value).__name__}")
    if not isinstance(payload, dict):
        raise TypeError("utility_solver_input JSON 根必须是 object")
    return payload


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _media_type(path: Path) -> str:
    return mimetypes.guess_type(Path(path).name)[0] or "application/octet-stream"


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    """以稳定、可审计的 UTF-8 JSON 落盘领域工件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _merge_domain_evidence(
    solver_evidence: dict[str, Any],
    supplemental_evidence: dict[str, Any],
) -> dict[str, Any]:
    """合并领域证据：Solver 的明确 PASS/FAIL 不可覆盖，UNKNOWN 可由后续检查器补齐。"""
    merged = dict(solver_evidence)
    for rule, extra in supplemental_evidence.items():
        current = merged.get(rule)
        current_state = _evidence_ok(current)
        if current_state is None:
            merged[rule] = extra
    return merged


def _evidence_ok(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        state = value.get("ok")
        return state if isinstance(state, bool) else None
    return None


def _domain_gate_payload(report: DomainGateReport) -> dict[str, Any]:
    return {
        "status": report.status.value,
        "ok": report.ok,
        "required": list(report.required),
        "passed": list(report.passed),
        "failed": list(report.failed),
        "unknown": list(report.unknown),
        "details": list(report.details),
        "rework_instruction": report.rework_instruction,
    }


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
