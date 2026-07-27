"""Batch executor:把双环(scad_loop + render_loop)包成 orchestrator.AgentFn。

对应文档:
- docs/architecture/ARCHITECTURE.md §2 步骤 4-5、§3 双环视觉自检
- docs/architecture/COMPONENTS.md §2.4 orchestrator(agent_fn 注入)、§2.5 vision
- src/openbimagent/orchestrator/dispatch.py AgentFn 形态、run_plan

agent_fn 形态(orchestrator.AgentFn):
    (batch: str, rework: str | None) -> BatchReport

batch 名(playbook phases.batches 声明,如「主体」)经 pipeline 与 ir["batches"]
按声明序对应(同序映射),executor 内部按 batch_names.index(batch) 取该批资产 id 列表。

每批流程:
1. **SCAD 环(可选)**:scad_critic 注入且 IR 含 primitive 字段时跑;否则跳过(M0
   planner IR 是语义 IR,无 primitive/size/position,SCAD 环留待 Solver 接入后激活)。
   SCAD 未收敛 → FIX(critic actionable_feedback 作返工指令)。
2. **审批门**:execute_code 前调 approval_fn("execute_code", {...})(ARCH §6.5
   审批门:权限三态 ask 落在 MCP 写操作 / execute_*_code / deliver);拒绝 → ESCALATE。
3. **Blender 环**:run_render_loop(builder_fn 产 bpy 代码 → execute_code → critic 六维)。
   - perfect_score → PASS
   - hard_limit / convergence_delta → FIX(末轮 critique.actionable_feedback 作返工指令)
   - divergence_fallback → ESCALATE(已回滚 best-so-far,人审接管)
4. **HTML 验收页**:每批结束调 on_html_report(path)(CLI 打印路径)。

测试友好:scad_loop_fn / render_loop_fn 可注入 fake(禁真实 LLM/Blender)。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from openbimagent.orchestrator.dispatch import BatchReport, Verdict
from openbimagent.vision.render_loop import RenderLoopResult, run_render_loop
from openbimagent.vision.rubric import Critic
from openbimagent.vision.scad_loop import ScadLoopResult, run_scad_loop

ApprovalFn = Callable[[str, dict[str, Any]], bool]
"""审批门函数:(operation, params) → True 同意 / False 拒绝。CLI 默认 y/N 询问。"""

OnHtmlReport = Callable[[Path, str], None]
"""每批 HTML 验收页回调:(html_path, batch_label) → None。CLI 用来打印路径。"""


def make_batch_executor(
    *,
    ir: dict[str, Any],
    batch_names: list[str],
    work_dir: Path,
    acceptance: dict[str, Any],
    client: Any,
    builder_fn: Callable[..., str],
    render_critic: Critic,
    scad_critic: Critic | None = None,
    session: Any | None = None,
    blend_path: Path | None = None,
    cameras: list[str] | None = None,
    turntable_target: str | None = None,
    turntable_frames: int = 4,
    image_size: int = 512,
    approval_fn: ApprovalFn | None = None,
    on_html_report: OnHtmlReport | None = None,
    scad_loop_fn: Callable[..., ScadLoopResult] = run_scad_loop,
    render_loop_fn: Callable[..., RenderLoopResult] = run_render_loop,
) -> Callable[[str, str | None], BatchReport]:
    """构造批次执行器(符合 orchestrator.AgentFn 形态)。

    batch_names 与 ir["batches"] 按声明序一一对应(同序映射);executor 内部按
    batch_names.index(batch) 取该批资产 id 列表。approval_fn 非空时在 execute_code
    前调 approval_fn("execute_code", {"batch": ..., "code_preview": ...});拒绝 → ESCALATE。
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    blend = blend_path or (work_dir / "scene.blend")

    scad_acceptance = acceptance.get("scad_loop") or {}
    render_acceptance = acceptance.get("blender_loop") or {}
    has_geometric_ir = _has_geometric_info(ir)

    def agent_fn(batch: str, rework: str | None) -> BatchReport:
        idx = _batch_index(batch, batch_names)
        batch_assets = list(ir.get("batches", [[]])[idx]) if idx < len(ir.get("batches", [])) else [batch]
        batch_label = batch_names[idx] if idx < len(batch_names) else batch
        batch_work = work_dir / f"batch_{idx + 1:02d}_{_slug(batch_label)}"
        batch_work.mkdir(parents=True, exist_ok=True)

        # 1. SCAD 环(可选):仅在 scad_critic 注入且 IR 含 primitive 字段时跑(M0 语义 IR 跳过)
        if scad_critic is not None and has_geometric_ir:
            scad_result = _run_scad_phase(
                scad_loop_fn=scad_loop_fn,
                ir=ir,
                batch_assets=batch_assets,
                work_dir=batch_work,
                acceptance=scad_acceptance,
                critic=scad_critic,
                session=session,
            )
            if scad_result is not None and not scad_result.converged:
                return BatchReport(
                    Verdict.FIX,
                    hint=f"SCAD 环未收敛:{scad_result.terminate_reason}(best={scad_result.best_score:.1f})",
                    rework_instruction=f"SCAD 环返工:best_score={scad_result.best_score:.1f},"
                    f"reason={scad_result.terminate_reason};请按 critic 反馈调整 IR 几何参数后重跑",
                )

        # 2. 审批门:execute_code 前 CLI 确认(ARCH §6.5)
        if approval_fn is not None:
            approved = approval_fn(
                "execute_code",
                {"batch": batch, "batch_assets": batch_assets, "blend_path": str(blend)},
            )
            if not approved:
                return BatchReport(
                    Verdict.ESCALATE,
                    hint=f"用户拒绝 execute_code 审批门(batch={batch})",
                )

        # 3. Blender 环(builder_fn 产 bpy 代码 → execute_code → critic 六维)
        render_result = _run_render_phase(
            render_loop_fn=render_loop_fn,
            batch_assets=batch_assets,
            blend_path=blend,
            acceptance=render_acceptance,
            client=client,
            critic=render_critic,
            builder_fn=builder_fn,
            work_dir=batch_work,
            ir=ir,
            session=session,
            cameras=cameras,
            turntable_target=turntable_target,
            turntable_frames=turntable_frames,
            image_size=image_size,
            batch_label=batch_label,
        )

        # 4. HTML 验收页路径回调(CLI 打印)
        if render_result.html_report is not None and on_html_report is not None:
            on_html_report(render_result.html_report, batch_label)

        # 5. 收敛判定 → BatchReport
        return _map_render_result(render_result, batch_label)

    return agent_fn


# ---------- SCAD 环(可选) ----------


def _run_scad_phase(
    *,
    scad_loop_fn: Callable[..., ScadLoopResult],
    ir: dict[str, Any],
    batch_assets: list[str],
    work_dir: Path,
    acceptance: dict[str, Any],
    critic: Critic,
    session: Any | None,
) -> ScadLoopResult | None:
    """跑 SCAD 环:取该批资产的几何子集 IR,写临时文件后调 run_scad_loop。"""
    min_score = float(acceptance.get("min_score", 8.0))
    max_iters = int(acceptance.get("max_iters", 6))
    subset = _batch_ir_subset(ir, batch_assets)
    if not subset.get("assets"):
        return None
    ir_path = work_dir / "scad_ir.json"
    ir_path.write_text(json.dumps(subset, ensure_ascii=False, indent=2), encoding="utf-8")
    return scad_loop_fn(
        ir_path,
        work_dir / "scad_artifacts",
        min_score=min_score,
        max_iters=max_iters,
        critic=critic,
        session=session,
    )


# ---------- Blender 环 ----------


def _run_render_phase(
    *,
    render_loop_fn: Callable[..., RenderLoopResult],
    batch_assets: list[str],
    blend_path: Path,
    acceptance: dict[str, Any],
    client: Any,
    critic: Critic,
    builder_fn: Callable[..., str],
    work_dir: Path,
    ir: dict[str, Any],
    session: Any | None,
    cameras: list[str] | None,
    turntable_target: str | None,
    turntable_frames: int,
    image_size: int,
    batch_label: str,
) -> RenderLoopResult:
    """跑 Blender 环:run_render_loop 是 async,用 asyncio.run 串行驱动(M0 顺序执行)。"""
    min_score = float(acceptance.get("min_score", 8.5))
    max_iters = int(acceptance.get("max_iters", 4))

    async def _run() -> RenderLoopResult:
        # client 生命周期由装配层管理:render_loop 直接用 client.set_editable_scope 等需先 connect。
        # is_connected 守护避免重复连;hasattr 兼容测试注入的无 connect/close 的 fake client。
        if hasattr(client, "connect") and not getattr(client, "is_connected", False):
            await client.connect()
        try:
            render_result = await render_loop_fn(
                batch=batch_assets,
                blend_path=blend_path,
                min_score=min_score,
                max_iters=max_iters,
                client=client,
                critic=critic,
                builder_fn=builder_fn,
                work_dir=work_dir,
                ir=ir,
                session=session,
                cameras=cameras,
                turntable_target=turntable_target,
                turntable_frames=turntable_frames,
                image_size=image_size,
                batch_label=batch_label,
            )
            # 保存交付物(.blend 工程 / 英雄镜头渲染 x1)供 deliver 门禁 C5 核对:
            # best_snapshot → blend_path;末轮视口截图 → 英雄镜头渲染 x1.png。
            # 纯文件复制不调 Blender(避免 client 生命周期/测试 fake 兼容问题);失败不阻断。
            _save_deliverables(render_result, blend_path, work_dir)
            return render_result
        finally:
            if hasattr(client, "close"):
                await client.close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # 已在事件循环内(nested):用 ensure_future + run_until_complete 兜底(M0 不推荐)
        future = asyncio.ensure_future(_run(), loop=loop)
        loop.run_until_complete(future)
        return future.result()
    return asyncio.run(_run())


def _save_deliverables(
    result: RenderLoopResult,
    blend_path: Path,
    work_dir: Path,
) -> None:
    """把 render loop 产物落为交付物供 deliver 门禁 C5 核对(ARCH §2 步骤 7)。

    - .blend 工程:复制 best_snapshot(addon 已存的 .blend)→ blend_path;deliverable 按后缀 .blend 匹配。
    - 英雄镜头渲染 x1:复制末轮视口截图 → work_dir/英雄镜头渲染 x1.png;deliverable 按文件名子串匹配。
    纯文件复制(不调 Blender):避免 client 生命周期/测试 fake 兼容问题;失败不阻断(缺失由 C5 报 missing)。
    """
    import shutil

    blend_dst = Path(blend_path).resolve()
    blend_dst.parent.mkdir(parents=True, exist_ok=True)
    if result.best_snapshot is not None:
        src = Path(result.best_snapshot)
        if src.exists():
            try:
                shutil.copyfile(src, blend_dst)
            except OSError:
                pass
    hero_dst = Path(work_dir).resolve() / "英雄镜头渲染 x1.png"
    shots = sorted(Path(work_dir).resolve().glob("iter*_viewport.png"))
    if shots:
        try:
            shutil.copyfile(shots[-1], hero_dst)
        except OSError:
            pass


def _map_render_result(result: RenderLoopResult, batch_label: str) -> BatchReport:
    """RenderLoopResult → BatchReport:perfect_score→PASS;hard_limit/convergence_delta→FIX;divergence_fallback→ESCALATE。"""
    hint = f"batch={batch_label} reason={result.terminate_reason} best={result.best_score:.1f} iters={result.iters}"
    if result.converged:
        return BatchReport(Verdict.PASS, hint=hint)
    if result.terminate_reason == "divergence_fallback":
        return BatchReport(
            Verdict.ESCALATE,
            hint=hint + "(已回滚 best-so-far,人审接管)",
        )
    # hard_limit / convergence_delta:返工指令交 builder 重改
    rework = (
        f"Blender 环未收敛:reason={result.terminate_reason},best={result.best_score:.1f};"
        "请按末轮 critic 的 actionable_feedback 调整建模代码后重跑"
    )
    return BatchReport(Verdict.FIX, hint=hint, rework_instruction=rework)


# ---------- 工具函数 ----------


def _batch_index(batch: str, batch_names: list[str]) -> int:
    """batch 名 → 索引;未找到按 0 兜底(单批 playbook 常见)。"""
    try:
        return batch_names.index(batch)
    except ValueError:
        return 0


def _slug(text: str) -> str:
    """批次名 → 路径片段:空白/斜杠折叠为下划线(与 planner._slug 同规则)。"""
    import re

    return re.sub(r"[\s/\\]+", "_", text.strip()) or "batch"


def _has_geometric_info(ir: dict[str, Any]) -> bool:
    """IR 是否含 SCAD 环需要的几何字段(任一 asset 有 primitive 字段)。"""
    for asset in ir.get("assets") or []:
        if isinstance(asset, dict) and "primitive" in asset:
            return True
    return False


def _batch_ir_subset(ir: dict[str, Any], batch_assets: list[str]) -> dict[str, Any]:
    """取该批资产的子 IR(保留 version/assets/batches/spatial_constraints 结构)。"""
    by_id = {a.get("id"): a for a in (ir.get("assets") or []) if isinstance(a, dict)}
    assets = [by_id[aid] for aid in batch_assets if aid in by_id]
    return {
        "version": ir.get("version", "0.1"),
        "assets": deepcopy(assets),
        "spatial_constraints": [
            c for c in (ir.get("spatial_constraints") or [])
            if isinstance(c, dict) and (c.get("subject") in batch_assets or c.get("object") in batch_assets)
        ],
        "batches": [list(batch_assets)],
    }


__all__ = ["ApprovalFn", "OnHtmlReport", "make_batch_executor"]
