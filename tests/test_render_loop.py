"""Blender 精检环(run_render_loop)单测(ARCH §3 环 2;ADR-0004 收敛语义)。

覆盖:
- 范围锁:每批首先调 set_editable_scope(objects=batch, enabled=True)(fork 默认解锁,必须显式上锁)。
- 收敛四选一:perfect_score(首轮 ≥min_score)/ hard_limit(耗尽)/ divergence_fallback(连续 2 轮降分 →
  restore_snapshot 回滚 best-so-far)/ convergence_delta(停滞)。
- HTML 验收页:每批结束 write_html_report 出文件,路径回传 RenderLoopResult.html_report。
- 事件链:screenshot(每视角)/ score / patch(返工指令)/ snapshot(每次 execute_code 后)四类 custom 事件
  落 SessionStore,parentId 链式挂载,可回放成线性 trace。
- builder_fn:首轮 prev_critique=None;未达标轮拿到上轮 critique.actionable_feedback 产出修正版代码。
- cameras vs turntable:cameras 非空走 batch_render;turntable_target 非空走 camera_turntable。
- 黑图断言:screenshot_or_render 抛 BlenderClientError 即终止本轮(不让黑图进评分)。

全程 mock:BlenderMCPClient 用 AsyncMock 注入,VLMCritic 用 MockCritic,禁真实 LLM/Blender 请求。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openbimagent.mcp_clients.blender import BlenderClientError, BlenderMCPClient
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.vision.render_loop import (
    FALLBACK_CONSECUTIVE_DROPS,
    RenderLoopResult,
    run_render_loop,
)
from openbimagent.vision.rubric import BLENDER_DIMENSIONS, MockCritic

# 1x1 PNG(合法头 + 非空;render_loop 不要求尺寸,只要文件能落盘)
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_1PX)
    return path


def _make_mock_client(
    *,
    tmp_path: Path,
    snapshot_paths: list[str] | None = None,
    brightness: float = 0.282,
    batch_results: list[dict[str, Any]] | None = None,
    turntable_results: list[dict[str, Any]] | None = None,
) -> tuple[BlenderMCPClient, dict[str, AsyncMock]]:
    """构造一个全 mock 的 BlenderMCPClient:所有 async 方法返回预设值,可断言调用参数。

    返回 (client, mocks):mocks 字典含 set_editable_scope / execute_code / screenshot_or_render /
    batch_render / turntable / restore_snapshot / close,供断言 call_args_list。

    tmp_path 用于落 snapshot 空文件(让 SessionStore.record_snapshot 能算 hash;mock 路径在
    Windows 上 /tmp 不存在会 FileNotFoundError)。snapshot_paths 缺省指向 tmp_path/snapshots/。
    """
    client = BlenderMCPClient.transport_socket(port=9887)  # 不真连,所有方法被 AsyncMock 覆盖
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    if snapshot_paths is None:
        snapshot_paths = [str(snap_dir / f"snap_iter{i}.blend") for i in range(1, 4)]
    snapshots = list(snapshot_paths)

    async def _set_editable_scope(*, objects=None, collections=None, enabled=True):
        return {"enabled": enabled, "objects": list(objects or []), "collections": list(collections or [])}

    async def _execute_code(code: str) -> dict[str, Any]:
        snap = snapshots.pop(0) if snapshots else str(snap_dir / "snap_default.blend")
        Path(snap).write_bytes(b"BLENDER-mock-snapshot")  # 创建真实文件让 record_snapshot 能算 hash
        return {
            "executed": True,
            "result": "ok",
            "snapshot": snap,
            "scope_checked": True,
        }

    async def _screenshot(*, filepath: str, max_size: int = 512, format: str = "png") -> dict[str, Any]:
        _write_png(Path(filepath))
        # 模拟真实客户端的 brightness 复检(黑图抛 BlenderClientError,防黑图进评分)
        if brightness < 0.01:
            raise BlenderClientError(f"截图黑图:brightness={brightness} < 0.01(filepath={filepath})")
        return {"brightness": brightness, "method": "render_fallback", "filepath": filepath}

    async def _batch_render(*, output_dir, cameras, width=512, height=512):
        results = batch_results or [
            {"filepath": f"{output_dir}/batch_{i:03d}_{c}.png", "brightness": 0.3}
            for i, c in enumerate(cameras)
        ]
        for r in results:
            _write_png(Path(r["filepath"]))
        return {"count": len(cameras), "all_nonblack": True, "results": results}

    async def _turntable(*, output_dir, target, frames=4, width=256):
        results = turntable_results or [
            {"filepath": f"{output_dir}/tt_{i:03d}.png", "brightness": 0.3} for i in range(frames)
        ]
        for r in results:
            _write_png(Path(r["filepath"]))
        return {"frames": frames, "all_nonblack": True, "results": results}

    async def _restore_snapshot(*, snapshot_path):
        return {"restored": True, "snapshot_path": snapshot_path}

    async def _close():
        return None

    mocks = {
        "set_editable_scope": AsyncMock(side_effect=_set_editable_scope),
        "execute_code": AsyncMock(side_effect=_execute_code),
        "screenshot_or_render": AsyncMock(side_effect=_screenshot),
        "batch_render": AsyncMock(side_effect=_batch_render),
        "turntable": AsyncMock(side_effect=_turntable),
        "restore_snapshot": AsyncMock(side_effect=_restore_snapshot),
        "close": AsyncMock(side_effect=_close),
    }
    client.set_editable_scope = mocks["set_editable_scope"]  # type: ignore[method-assign]
    client.execute_code = mocks["execute_code"]  # type: ignore[method-assign]
    client.screenshot_or_render = mocks["screenshot_or_render"]  # type: ignore[method-assign]
    client.batch_render = mocks["batch_render"]  # type: ignore[method-assign]
    client.turntable = mocks["turntable"]  # type: ignore[method-assign]
    client.restore_snapshot = mocks["restore_snapshot"]  # type: ignore[method-assign]
    client.close = mocks["close"]  # type: ignore[method-assign]
    return client, mocks


def _builder_factory(code_log: list[str]) -> Any:
    """builder_fn 工厂:记录每次产出的代码;首轮产初版,后续轮把 prev_critique 反馈拼进代码。"""

    def builder(prev_critique, batch_ctx) -> str:
        if prev_critique is None:
            code = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
        else:
            feedback = prev_critique.actionable_feedback
            code = f"import bpy\n# rework: {feedback}\nbpy.ops.mesh.primitive_cube_add()"
        code_log.append(code)
        return code

    return builder


# ---------- 范围锁 ----------


def test_scope_lock_called_per_batch_with_batch_objects(tmp_path) -> None:
    """每批首先调 set_editable_scope(objects=batch, enabled=True);fork 默认解锁必须显式上锁。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is True
    mocks["set_editable_scope"].assert_awaited_once()
    call_kwargs = mocks["set_editable_scope"].call_args.kwargs
    assert call_kwargs["objects"] == ["M0Cube"]
    assert call_kwargs["enabled"] is True


def test_scope_lock_respects_explicit_scope_objects(tmp_path) -> None:
    """scope_objects 非空时用它(而非默认 batch);允许锁定比 batch 更大的范围。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["hero"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            scope_objects=["hero", "ground", "light_rig"],
            cameras=["Camera"],
        )

    asyncio.run(run())
    call_kwargs = mocks["set_editable_scope"].call_args.kwargs
    assert call_kwargs["objects"] == ["hero", "ground", "light_rig"]


# ---------- 收敛四选一 ----------


def test_perfect_score_first_iter_converges(tmp_path) -> None:
    """首轮 ≥min_score → perfect_score,只跑 1 轮,best_snapshot 为首轮快照。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=5,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is True
    assert result.terminate_reason == "perfect_score"
    assert result.iters == 1
    assert result.best_score == 9.0
    assert result.scores == (9.0,)
    # best_snapshot 是首轮快照路径(平台无关:用 as_posix 比较,避免 Windows \ 与 Unix / 分歧)
    assert result.best_snapshot is not None
    assert result.best_snapshot.as_posix().endswith("snapshots/snap_iter1.blend")
    assert mocks["execute_code"].await_count == 1
    assert mocks["restore_snapshot"].await_count == 0  # 未触发回滚


def test_hard_limit_when_never_reaches_threshold(tmp_path) -> None:
    """分数持续上涨但始终 <min_score,耗尽 max_iters → hard_limit。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    code_log: list[str] = []
    builder = _builder_factory(code_log)

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=3,
            client=client,
            critic=MockCritic([5.0, 6.0, 7.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is False
    assert result.terminate_reason == "hard_limit"
    assert result.iters == 3
    assert result.best_score == 7.0
    assert result.scores == (5.0, 6.0, 7.0)
    assert mocks["execute_code"].await_count == 3
    assert mocks["restore_snapshot"].await_count == 0
    # builder_fn 拿到上轮 critique 产出修正版代码(第 2/3 轮 code 含「rework:」)
    assert "rework:" not in code_log[0]
    assert "rework:" in code_log[1] and "rework:" in code_log[2]


def test_divergence_fallback_restores_best_snapshot(tmp_path) -> None:
    """连续 2 轮降分 → divergence_fallback,restore_snapshot 回滚到 best-so-far 快照。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=5,
            client=client,
            critic=MockCritic([7.0, 6.0, 5.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is False
    assert result.terminate_reason == "divergence_fallback"
    assert result.iters == FALLBACK_CONSECUTIVE_DROPS + 1  # 1 升到 best,再 2 连降
    assert result.best_score == 7.0
    # best_snapshot 是首轮的快照路径(平台无关:as_posix 比较,避免 Windows \ 与 Unix / 分歧)
    assert result.best_snapshot is not None
    assert result.best_snapshot.as_posix().endswith("snapshots/snap_iter1.blend")
    # 回滚调用:restore_snapshot(snapshot_path=best_snapshot 路径)
    mocks["restore_snapshot"].assert_awaited_once()
    call_kwargs = mocks["restore_snapshot"].call_args.kwargs
    assert Path(call_kwargs["snapshot_path"]).as_posix().endswith("snapshots/snap_iter1.blend")


def test_convergence_delta_when_score_stalls_below_threshold(tmp_path) -> None:
    """两轮分数 delta < 0.5 且未降分 → convergence_delta(未达标但已停滞;converged=False)。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=5,
            client=client,
            critic=MockCritic([6.0, 6.2]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is False
    assert result.terminate_reason == "convergence_delta"
    assert result.iters == 2
    assert result.scores == (6.0, 6.2)


def test_rework_loop_passes_prev_critique_to_builder(tmp_path) -> None:
    """builder_fn 拿到 prev_critique.actionable_feedback 产出修正版代码;MockCritic 反馈含量化参数。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    received_feedbacks: list[str | None] = []

    def builder(prev_critique, batch_ctx) -> str:
        received_feedbacks.append(prev_critique.actionable_feedback if prev_critique else None)
        return "import bpy\nbpy.ops.mesh.primitive_cube_add()"

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=9.5,
            max_iters=3,
            client=client,
            critic=MockCritic([6.0, 7.0, 8.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.terminate_reason == "hard_limit" and result.iters == 3
    # 首轮 prev_critique=None;第 2/3 轮 prev_critique 携带量化 actionable_feedback
    assert received_feedbacks[0] is None
    assert received_feedbacks[1] is not None and "0.8" in received_feedbacks[1]  # MockCritic 默认反馈含量化
    assert received_feedbacks[2] is not None and "0.8" in received_feedbacks[2]


# ---------- cameras vs turntable ----------


def test_cameras_branch_invokes_batch_render(tmp_path) -> None:
    """cameras 非空走 batch_render;results 文件路径进 screenshot_views 与评分 image_paths。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera", "TopCam"],
        )

    asyncio.run(run())
    mocks["batch_render"].assert_awaited_once()
    call_kwargs = mocks["batch_render"].call_args.kwargs
    assert call_kwargs["cameras"] == ["Camera", "TopCam"]
    assert mocks["turntable"].await_count == 0


def test_turntable_branch_invokes_camera_turntable(tmp_path) -> None:
    """turntable_target 非空(cameras 留空)走 turntable;frames 透传。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            turntable_target="M0Cube",
            turntable_frames=6,
        )

    asyncio.run(run())
    mocks["turntable"].assert_awaited_once()
    call_kwargs = mocks["turntable"].call_args.kwargs
    assert call_kwargs["target"] == "M0Cube"
    assert call_kwargs["frames"] == 6
    assert mocks["batch_render"].await_count == 0


def test_no_cameras_no_turntable_only_viewport(tmp_path) -> None:
    """cameras 与 turntable_target 都空:只用视口截图评分(验收图缺省,不推荐但允许)。"""
    client, mocks = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
        )

    result = asyncio.run(run())
    assert result.converged is True
    assert mocks["batch_render"].await_count == 0
    assert mocks["turntable"].await_count == 0
    assert mocks["screenshot_or_render"].await_count == 1  # 只有视口截图


# ---------- HTML 验收页 ----------


def test_html_report_generated_per_batch(tmp_path) -> None:
    """每批结束 write_html_report 出 HTML 文件,路径回传 RenderLoopResult.html_report。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
            batch_label="hero",
        )

    result = asyncio.run(run())
    assert result.html_report is not None
    assert result.html_report.is_file()
    assert result.html_report.name == "blender_acceptance_hero.html"
    content = result.html_report.read_text(encoding="utf-8")
    # HTML 含三视角截图(base64 内嵌)、评分表、返工指令区
    assert "<table>" in content and "data:image/png;base64," in content
    assert "Blender 环验收页 · hero" in content


def test_html_report_includes_rework_command_when_low_score(tmp_path) -> None:
    """任一维 <8 分时返工指令区标红(.rework 无 .ok class);>8 时标绿(.rework.ok)。"""
    # 第 1 轮低分(返工指令标红),第 2 轮达标(标绿)
    client, _ = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=2,
            client=client,
            critic=MockCritic([6.0, 9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    result = asyncio.run(run())
    assert result.converged is True and result.iters == 2
    # 最终 HTML 是第 2 轮(达标),返工指令区应为 .rework.ok
    content = result.html_report.read_text(encoding="utf-8")
    assert 'class="rework ok"' in content


# ---------- 事件链(SessionStore) ----------


def test_session_event_chain_screenshot_score_patch_snapshot(tmp_path) -> None:
    """screenshot/score/patch/snapshot 四类 custom 事件落 SessionStore,parentId 链式挂载可回放。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])
    store = SessionStore.create(tmp_path / "sessions", title="render_loop 测试")

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=2,
            client=client,
            critic=MockCritic([6.0, 9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
            session=store,
        )

    result = asyncio.run(run())
    assert result.converged is True and result.iters == 2

    events = store.load()
    by_type: dict[str, list] = {}
    for event in events:
        assert event.type is EventType.CUSTOM
        by_type.setdefault(event.payload.customType, []).append(event)
    assert set(by_type) == {"screenshot", "score", "patch", "snapshot"}

    # snapshot:每轮 execute_code 后一条(2 轮 = 2 条)
    assert len(by_type["snapshot"]) == 2
    snap = by_type["snapshot"][0].payload
    assert snap.blend_file_path.endswith(".blend")  # type: ignore[attr-defined]

    # screenshot:每轮视口 1 条 + batch_render 每相机 1 条;2 轮 × (1 + 1) = 4
    assert len(by_type["screenshot"]) == 4
    shot = by_type["screenshot"][0].payload
    assert shot.phase == "blender"  # type: ignore[attr-defined]
    assert shot.camera_view in ("viewport", "Camera")  # type: ignore[attr-defined]
    assert shot.image_path.endswith(".png")  # type: ignore[attr-defined]

    # score:每轮一条,phase=blender,六维
    assert len(by_type["score"]) == 2
    score = by_type["score"][0].payload
    assert set(score.rubric_scores) == {d.value for d in BLENDER_DIMENSIONS}  # type: ignore[attr-defined]
    assert score.reasoning and score.anchor_ref and score.actionable_feedback  # type: ignore[attr-defined]
    # 第 2 轮起 A/B swap 引用 best-so-far 快照
    assert by_type["score"][1].payload.ab_swap_ref  # type: ignore[attr-defined]

    # patch:每未达标轮一条(第 1 轮低分 → patch;第 2 轮达标 break 前未发 patch)
    assert len(by_type["patch"]) == 1
    patch = by_type["patch"][0].payload
    assert patch.status == "rework_command"  # type: ignore[attr-defined]
    assert "0.8" in patch.diff  # MockCritic 量化返工指令  # type: ignore[attr-defined]

    # parentId 链式:除首条外每条挂前一条之下(可回放成线性 trace)
    assert events[0].parentId is None
    for prev, cur in zip(events, events[1:]):
        assert cur.parentId == prev.id


def test_session_not_injected_skips_event_logging(tmp_path) -> None:
    """session=None 时事件不落盘(纯算路径,联调用);render_loop 仍正常返回。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
            session=None,
        )

    result = asyncio.run(run())
    assert result.converged is True
    # 没有 session 文件被创建
    assert not (tmp_path / "sessions").exists() or not list((tmp_path / "sessions").iterdir())


# ---------- 黑图断言 ----------


def test_black_screenshot_raises_and_aborts_iteration(tmp_path) -> None:
    """screenshot_or_render 抛 BlenderClientError(黑图)→ render_loop 不吞错,本轮终止。

    fork 改造 f 在 addon 层已断言非黑;客户端层复检 brightness < 0.01 抛错,防止黑图进评分。
    """
    client, _ = _make_mock_client(tmp_path=tmp_path, brightness=0.005)  # 黑图
    builder = _builder_factory([])

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
        )

    with pytest.raises(BlenderClientError, match="黑图"):
        asyncio.run(run())


# ---------- builder_fn 上下文 ----------


def test_builder_fn_receives_batch_context(tmp_path) -> None:
    """builder_fn 第二参数 batch_ctx 含 batch / blend_path / ir / label(只读快照)。"""
    client, _ = _make_mock_client(tmp_path=tmp_path)
    captured_ctx: list[dict[str, Any]] = []

    def builder(prev_critique, batch_ctx) -> str:
        captured_ctx.append(dict(batch_ctx))
        return "import bpy"

    ir = {"version": "0.1", "assets": [{"id": "M0Cube", "category": "prop"}]}

    async def run() -> RenderLoopResult:
        return await run_render_loop(
            batch=["M0Cube"],
            blend_path=tmp_path / "scene.blend",
            min_score=8.0,
            max_iters=1,
            client=client,
            critic=MockCritic([9.0]),
            builder_fn=builder,
            work_dir=tmp_path / "work",
            cameras=["Camera"],
            ir=ir,
            batch_label="hero",
        )

    asyncio.run(run())
    ctx = captured_ctx[0]
    assert ctx["batch"] == ["M0Cube"]
    assert ctx["blend_path"].endswith("scene.blend")
    assert ctx["label"] == "hero"
    assert ctx["ir"] == ir
