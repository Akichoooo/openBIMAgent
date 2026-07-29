"""环 2 · Blender 美学精检环(M0 阶段3b,依赖阶段 2 fork 的 blender-mcp)。

对应文档:
- docs/architecture/ARCHITECTURE.md §3 环 2(双环视觉自检,防放水五件套)、§6.5 每批 HTML 验收页
- docs/architecture/COMPONENTS.md §2.5 vision
- mcp_servers/blender_mcp/FORK_NOTES.md(范围锁 g、快照 c、截图非黑 f、AST allowlist c)

每批流程(ARCH §3 环 2):
1. **范围锁**:set_editable_scope(batch 对象白名单,fork 默认解锁,必须显式上锁防越界)。
2. **建模**:execute_code(builder_fn 产出的代码;addon 自动快照 + AST allowlist + 范围锁校验)。
3. **自检图**:screenshot_or_render(视口截图,GUI 走 GPUOffScreen,headless 走 render_fallback)。
4. **验收图**:batch_render(指定相机列表)或 turntable(绕目标环绕)多视角正式渲染。
5. **critic_render 评分**:VLMCritic 六维(rubric.BLENDER_DIMENSIONS),强制 CoT + 防放水五件套。
6. **返工循环**:未达阈值 → actionable_rework_command 交 builder_fn 重改(最多 max_iters)。
7. **收敛判定**:复用 scad_loop 的四选一 + best-so-far(ADR-0004):
   perfect_score(≥min_score)/ convergence_delta(停滞)/ hard_limit(耗尽)/
   divergence_fallback(连续 2 轮降分 → restore_snapshot 回滚 best .blend)。
8. **HTML 验收页**:每批结束调 write_html_report(三视角截图 + rubric 表 + 返工指令 + 留痕)。
9. **事件落盘**:screenshot/score/patch/snapshot 四类 custom 事件进 SessionStore。

best_snapshot 为 best-so-far .blend 文件路径(fork 快照机制承载,divergence_fallback 时
restore_snapshot 回滚);阈值在 playbook `acceptance.blender_loop`;超限 ESCALATE 不死循环。
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbimagent.mcp_clients.blender import BlenderMCPClient
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.vision.html_report import write_html_report
from openbimagent.vision.rubric import (
    BLENDER_DIMENSIONS,
    Critic,
    CritiqueResult,
    check_score_payload,
)

CONVERGENCE_DELTA: float = 0.5
"""收敛 delta 阈值(0-10 分制,与 scad_loop.CONVERGENCE_DELTA 一致;ADR-0004)。"""

FALLBACK_CONSECUTIVE_DROPS: int = 2
"""连续降分轮数达到该值即 divergence_fallback 回退 best-so-far(先于 delta 判定,防缓慢下降误判)。"""

TERMINATE_REASONS: tuple[str, ...] = ("perfect_score", "convergence_delta", "hard_limit", "divergence_fallback")
"""收敛判定四选一(ADR-0004 语义;与 scad_loop.TERMINATE_REASONS 一致)。"""

BuilderFn = Callable[[CritiqueResult | None, dict[str, Any]], str]
"""builder_fn 形态:(上一轮 critique, None 表示首轮;batch 上下文)→ 建模 Python 代码字符串。

builder_fn 拿到 prev_critique.actionable_feedback(可执行返工指令)后产出修正版代码;
首轮 prev_critique=None,builder_fn 出初版建模代码。代码交 execute_code(addon AST allowlist + 快照)。
"""


@dataclass(frozen=True)
class RenderLoopResult:
    """Blender 环收敛结果;best_snapshot 为 best-so-far 回退点(ADR-0004,.blend 文件路径)。"""

    converged: bool
    best_score: float
    best_snapshot: Path | None
    iters: int
    terminate_reason: str = ""  # TERMINATE_REASONS 四选一
    scores: tuple[float, ...] = ()  # 每轮 overall_score(六维均值)
    html_report: Path | None = None  # 每批 HTML 验收页路径(§6.5)


async def run_render_loop(
    batch: list[str],
    blend_path: Path,
    *,
    min_score: float,
    max_iters: int,
    client: BlenderMCPClient,
    critic: Critic,
    builder_fn: BuilderFn,
    work_dir: Path,
    scope_objects: list[str] | None = None,
    ir: dict[str, Any] | None = None,
    session: SessionStore | None = None,
    cameras: list[str] | None = None,
    turntable_target: str | None = None,
    turntable_frames: int = 4,
    image_size: int = 512,
    batch_label: str = "",
) -> RenderLoopResult:
    """对一批资产执行 Blender 精检环直到收敛(≥min_score)或耗尽 max_iters。

    流程见模块 docstring(范围锁 → 建模 → 自检 → 验收图 → critic_render 六维 → 返工循环 →
    收敛四选一 + best-so-far → HTML 验收页)。VLMCritic 一律由调用方注入(测试用 MockCritic,
    禁真实 LLM 请求)。session 非空时每轮落 screenshot/score/patch/snapshot 四类 custom 事件。
    cameras 与 turntable_target 二选一:cameras 非空走 batch_render,否则 turntable_target 非空
    走 camera_turntable;两者都空则只用视口截图评分(验收图缺省,不推荐)。
    """
    # resolve() 成绝对路径:addon 收到的 filepath/output_dir 会按 Blender 进程 cwd 解析,
    # 相对路径会被解析到错误驱动器(如 C:\)→ 目录不存在 → 黑图。snapshot 路径由 addon 自拼(绝对)不受影响。
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    scope_objs = list(scope_objects) if scope_objects is not None else list(batch)
    batch_ctx: dict[str, Any] = {
        "batch": list(batch),
        "blend_path": str(blend_path),
        "ir": deepcopy(ir) if ir is not None else None,
        "label": batch_label,
    }

    # 1. 范围锁(每批显式上锁;fork 默认解锁)
    # collections=scope_objs:modeler 会把子对象命名成 {asset_id}_base/_cabinet 等前缀名,
    # addon 范围锁只认精确对象名(见 addon._is_editable),前缀子对象会被判越界回滚;
    # 改为按集合白名单——modeler 把所有子对象 link 进以 asset_id 命名的集合,集合内一律放行。
    if scope_objs:
        await client.set_editable_scope(objects=scope_objs, collections=scope_objs, enabled=True)

    best_score = -1.0
    best_snapshot: Path | None = None
    prev_score: float | None = None
    consecutive_drops = 0
    scores: list[float] = []
    delta_history: list[float] = []  # M1:连续 2 轮 delta < CONVERGENCE_DELTA 才判 convergence_delta(ADR-0004,与 scad_loop 一致)
    prev_critique: CritiqueResult | None = None
    prev_images: list[Path] = []
    prev_scores_dict: dict[str, float] | None = None
    prev_screenshots: dict[str, Path] | None = None
    terminate_reason = ""
    converged = False
    iteration = 0

    for iteration in range(1, max(1, max_iters) + 1):
        # 2. 建模:builder_fn 产出代码 → execute_code(addon 自动快照 + AST + 范围锁校验)
        code = builder_fn(prev_critique, dict(batch_ctx))
        exec_result = await client.execute_code(code)
        snapshot_path_str = exec_result.get("snapshot")
        snapshot_path = Path(snapshot_path_str) if snapshot_path_str else None

        # 2b. snapshot 事件落盘(fork 快照机制承载;ADR-0004 回滚点)
        if session is not None and snapshot_path is not None:
            session.record_snapshot(snapshot_path)

        # 3. 自检图(视口截图,headless 走 render_fallback)
        shot_path = work_dir / f"iter{iteration}_viewport.png"
        await client.screenshot_or_render(filepath=str(shot_path), max_size=image_size)
        screenshot_views: dict[str, Path] = {"viewport": shot_path}

        # 4. 验收图(batch_render 或 turntable)
        verify_paths: list[Path] = []
        if cameras:
            batch_dir = work_dir / f"iter{iteration}_batch"
            br = await client.batch_render(
                output_dir=str(batch_dir), cameras=cameras, width=image_size, height=image_size
            )
            for r in br.get("results", []):
                fp = r.get("filepath")
                if fp:
                    verify_paths.append(Path(fp))
                    screenshot_views[Path(fp).stem] = Path(fp)
        elif turntable_target:
            tt_dir = work_dir / f"iter{iteration}_turntable"
            tt = await client.turntable(
                output_dir=str(tt_dir), target=turntable_target, frames=turntable_frames, width=image_size
            )
            for r in tt.get("results", []):
                fp = r.get("filepath")
                if fp:
                    verify_paths.append(Path(fp))
                    screenshot_views[Path(fp).stem] = Path(fp)

        # 4b. screenshot 事件落盘(每视角一条)
        if session is not None:
            for view, png in screenshot_views.items():
                session.append_new(
                    EventType.CUSTOM,
                    {
                        "customType": "screenshot",
                        "camera_view": view,
                        "image_path": str(png),
                        "phase": "blender",
                        "iteration": iteration,
                    },
                )

        # 5. critic_render 六维评分(A/B swap 上下文 = 上轮截图 + best-so-far 快照引用)
        image_paths = [shot_path, *verify_paths]
        context = {
            "iteration": iteration,
            "ir": deepcopy(ir) if ir is not None else None,
            "batch": list(batch),
            "previous_image_paths": prev_images,
            "ab_swap_ref": str(best_snapshot) if best_snapshot is not None else None,
        }
        critique = critic.critique(image_paths, BLENDER_DIMENSIONS, context)
        payload = critique.to_score_payload(phase="blender")
        payload["iteration"] = iteration
        payload["overall_score"] = critique.overall_score
        if "ab_swap_ref" not in payload and best_snapshot is not None:
            payload["ab_swap_ref"] = str(best_snapshot)
        check_score_payload(payload, phase="blender")  # 防放水留痕校验:不过即失败(拒放水评分进环)
        if session is not None:
            session.append_new(EventType.CUSTOM, payload)

        score = critique.overall_score
        scores.append(score)

        # 6. best-so-far 快照(ADR-0004;fork .blend 文件路径)
        if score > best_score and snapshot_path is not None:
            best_score = score
            best_snapshot = snapshot_path

        # 7. 收敛判定(四选一;顺序同 ADR-0004:fallback 先于 delta,防缓慢下降误判)
        if score >= min_score:
            terminate_reason, converged = "perfect_score", True
            break
        if prev_score is not None:
            consecutive_drops = consecutive_drops + 1 if score < prev_score else 0
            if consecutive_drops >= FALLBACK_CONSECUTIVE_DROPS:
                terminate_reason = "divergence_fallback"
                # 回滚到 best-so-far .blend(fork restore_snapshot)
                if best_snapshot is not None:
                    try:
                        await client.restore_snapshot(snapshot_path=str(best_snapshot))
                    except Exception:
                        pass  # 回滚失败不致命:best_snapshot 仍作为引用记录,人工接管
                break
            delta = abs(score - prev_score)
            delta_history.append(delta)
            # M1:连续 2 轮 delta < CONVERGENCE_DELTA 且非下降才判 convergence_delta
            # (ADR-0004:单轮 delta 小可能是 patch 微动,连续 2 轮停滞才视为真正收敛;与 scad_loop 一致)
            if (
                len(delta_history) >= 2
                and delta_history[-1] < CONVERGENCE_DELTA
                and delta_history[-2] < CONVERGENCE_DELTA
                and score > 0
                and prev_score > 0
                and score >= prev_score
            ):
                terminate_reason = "convergence_delta"  # 未达标但已停滞;converged 保持 False
                break
        prev_score = score
        if iteration >= max_iters:
            terminate_reason = "hard_limit"
            break

        # 8. patch 事件落盘(actionable_rework_command 交 builder_fn;非数值变更不进 IR)
        if session is not None:
            session.append_new(
                EventType.CUSTOM,
                {
                    "customType": "patch",
                    "target_file": str(blend_path),
                    "diff": critique.actionable_feedback,
                    "iteration": iteration,
                    "status": "rework_command",
                },
            )

        prev_critique = critique
        prev_images = image_paths
        prev_scores_dict = dict(critique.rubric_scores)
        prev_screenshots = dict(screenshot_views)

    # 9. 每批 HTML 验收页(§6.5);critique 是最后一轮的评分(for 循环至少跑一次)
    html_path = write_html_report(
        work_dir,
        screenshots=screenshot_views,
        scores=dict(critique.rubric_scores),
        reasoning=critique.reasoning,
        anchor_ref=critique.anchor_ref,
        actionable_feedback=critique.actionable_feedback,
        previous_screenshots=prev_screenshots,
        previous_scores=prev_scores_dict,
        title=f"Blender 环验收页 · {batch_label or 'batch'}",
        name=f"blender_acceptance_{batch_label or 'batch'}.html",
    )

    return RenderLoopResult(
        converged=converged,
        best_score=best_score if best_score >= 0 else 0.0,
        best_snapshot=best_snapshot,
        iters=iteration,
        terminate_reason=terminate_reason,
        scores=tuple(scores),
        html_report=html_path,
    )


__all__ = [
    "CONVERGENCE_DELTA",
    "FALLBACK_CONSECUTIVE_DROPS",
    "TERMINATE_REASONS",
    "BuilderFn",
    "RenderLoopResult",
    "run_render_loop",
]
