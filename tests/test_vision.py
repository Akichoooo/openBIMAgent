"""render_loop 单元测试(Relay 015 任务 C1)。

覆盖收敛四选一(perfect_score/convergence_delta/divergence_fallback/hard_limit)、
A/B swap 上下文、best_snapshot、scope_lock、HTML 报告生成。
全程 mock:禁真实 Blender;MockCritic 注入;asyncio.run 跑 async render_loop。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openbimagent.mcp_clients.blender import BlenderMCPClient
from openbimagent.vision.render_loop import run_render_loop
from openbimagent.vision.rubric import MockCritic

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_CAMERAS = ["iso", "front", "top"]
_BATCH = ["主体"]


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_1PX)
    return path


def _builder(prev_critique: Any, batch_ctx: dict[str, Any]) -> str:
    """简单 builder_fn:返回固定 bpy 代码(不跑真实 Blender)。"""
    return "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0)"


def _make_mock_client(tmp_path: Path) -> tuple[Any, dict[str, AsyncMock]]:
    """构造全 mock 的 BlenderMCPClient(与 test_assembly._make_mock_blender_client 同模式;不连真实 Blender)。"""
    client = BlenderMCPClient.transport_socket(port=9887)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_counter = [0]

    async def _set_editable_scope(*, objects=None, collections=None, enabled=True):
        return {"enabled": enabled, "objects": list(objects or [])}

    async def _execute_code(code: str):
        snap_counter[0] += 1
        snap = str(snap_dir / f"snap_{snap_counter[0]}.blend")
        Path(snap).write_bytes(b"mock")
        return {"executed": True, "result": "ok", "snapshot": snap, "scope_checked": True}

    async def _screenshot(*, filepath, max_size=512, format="png"):
        _write_png(Path(filepath))
        return {"brightness": 0.282, "method": "render_fallback", "filepath": filepath}

    async def _batch_render(*, output_dir, cameras, width=512, height=512):
        results = [{"filepath": f"{output_dir}/batch_{i:03d}_{c}.png", "brightness": 0.3}
                   for i, c in enumerate(cameras)]
        for r in results:
            _write_png(Path(r["filepath"]))
        return {"count": len(cameras), "all_nonblack": True, "results": results}

    async def _restore_snapshot(*, snapshot_path):
        return {"restored": True, "snapshot_path": snapshot_path}

    async def _close():
        return None

    async def _connect():
        return None

    mocks = {
        "set_editable_scope": AsyncMock(side_effect=_set_editable_scope),
        "execute_code": AsyncMock(side_effect=_execute_code),
        "screenshot_or_render": AsyncMock(side_effect=_screenshot),
        "batch_render": AsyncMock(side_effect=_batch_render),
        "restore_snapshot": AsyncMock(side_effect=_restore_snapshot),
        "close": AsyncMock(side_effect=_close),
        "connect": AsyncMock(side_effect=_connect),
    }
    client.set_editable_scope = mocks["set_editable_scope"]  # type: ignore[method-assign]
    client.execute_code = mocks["execute_code"]  # type: ignore[method-assign]
    client.screenshot_or_render = mocks["screenshot_or_render"]  # type: ignore[method-assign]
    client.batch_render = mocks["batch_render"]  # type: ignore[method-assign]
    client.restore_snapshot = mocks["restore_snapshot"]  # type: ignore[method-assign]
    client.close = mocks["close"]  # type: ignore[method-assign]
    client.connect = mocks["connect"]  # type: ignore[method-assign]
    client._connected = True  # type: ignore[attr-defined]
    return client, mocks


def test_render_loop_perfect_score_converges(tmp_path: Path) -> None:
    """iter1 score=9.5 >= min_score=8.0 → perfect_score, converged=True。"""
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([9.5])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.0, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.converged is True
    assert result.terminate_reason == "perfect_score"
    assert result.iters == 1
    assert result.best_score == 9.5


def test_render_loop_convergence_delta(tmp_path: Path) -> None:
    """连续 2 轮 delta<0.5 且非下降 [7.0, 7.3, 7.4] → convergence_delta(与 scad_loop 一致)。"""
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([7.0, 7.3, 7.4])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.5, max_iters=5, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.terminate_reason == "convergence_delta"
    assert result.converged is False
    assert result.iters == 3
    assert result.scores == pytest.approx((7.0, 7.3, 7.4))


def test_render_loop_divergence_fallback(tmp_path: Path) -> None:
    """连续 2 轮降分 [8.0, 7.0, 6.0] → divergence_fallback,restore_snapshot 被调用。"""
    client, mocks = _make_mock_client(tmp_path)
    critic = MockCritic([8.0, 7.0, 6.0])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.5, max_iters=5, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.terminate_reason == "divergence_fallback"
    assert result.iters == 3
    mocks["restore_snapshot"].assert_called_once()


def test_render_loop_hard_limit(tmp_path: Path) -> None:
    """波动分数 [6.0, 6.2, 5.9] 不触发 convergence/fallback → hard_limit(max_iters=3)。

    注:连续相同分数会触发 convergence_delta(连续 2 轮 delta=0<0.5),
    故 hard_limit 测试须用波动分数让 convergence_delta 不满足(末轮下降 → score>=prev_score 不成立)。
    """
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([6.0, 6.2, 5.9])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.5, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.terminate_reason == "hard_limit"
    assert result.converged is False
    assert result.iters == 3


def test_render_loop_ab_swap_context(tmp_path: Path) -> None:
    """第 2 轮 critic 调用的 context 含 previous_image_paths(非空)和 ab_swap_ref(非 None)。"""
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([7.0, 7.5])
    asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.5, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert len(critic.calls) >= 2
    ctx2 = critic.calls[1]["context"]
    assert ctx2["previous_image_paths"], "第 2 轮 context 应含非空 previous_image_paths"
    assert ctx2["ab_swap_ref"] is not None, "第 2 轮 context 应含 ab_swap_ref"


def test_render_loop_saves_best_snapshot(tmp_path: Path) -> None:
    """递增后降 [6.0, 8.0, 7.0] → best_snapshot 指向第 2 轮(8.0 最高分)。"""
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([6.0, 8.0, 7.0])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.5, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.best_score == 8.0
    assert result.best_snapshot is not None
    assert "snap_2" in str(result.best_snapshot)  # iter2 的 snapshot(8.0 最高分)


def test_render_loop_scope_lock_enabled(tmp_path: Path) -> None:
    """范围锁:set_editable_scope 被调用,参数含 batch 对象列表,enabled=True。"""
    client, mocks = _make_mock_client(tmp_path)
    critic = MockCritic([9.5])
    asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.0, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    mocks["set_editable_scope"].assert_called_once()
    call_kwargs = mocks["set_editable_scope"].call_args.kwargs
    assert call_kwargs["enabled"] is True
    assert _BATCH[0] in call_kwargs["objects"]


def test_render_loop_html_report_generated(tmp_path: Path) -> None:
    """完整循环后 HTML 验收页文件生成,result.html_report 非 None。"""
    client, _ = _make_mock_client(tmp_path)
    critic = MockCritic([9.5])
    result = asyncio.run(run_render_loop(
        batch=_BATCH, blend_path=tmp_path / "scene.blend",
        min_score=8.0, max_iters=3, client=client, critic=critic,
        builder_fn=_builder, work_dir=tmp_path / "work", cameras=_CAMERAS,
    ))
    assert result.html_report is not None
    assert Path(result.html_report).is_file()
