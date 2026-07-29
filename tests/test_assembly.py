"""装配层测试(M0 阶段4;ARCH §2 完整生命周期;COMPONENTS §2)。

覆盖:
- builder:确定性模板(无 registry)、LLM 路径成功、LLM 失败回退模板、FIX rework 进注释。
- batch_executor:SCAD 环跳过(无几何 IR)/ SCAD 未收敛 FIX / 审批门拒绝 ESCALATE /
  Blender 环 perfect_score→PASS / hard_limit→FIX / divergence_fallback→ESCALATE / HTML 回调。
- pipeline:全链路状态机(load → clarify → plan → orchestrate → deliver)用 fake registry /
  fake client / fake critic 走通;Ctrl+C 中断落 checkpoint;deliver 审批门拒绝;--yes 跳过审批。

全程 mock:禁真实 LLM 请求、禁连真实 Blender。fake render_loop_fn / fake scad_loop_fn 注入。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from openbimagent.assembly.batch_executor import make_batch_executor
from openbimagent.assembly.builder import make_builder_fn
from openbimagent.assembly.pipeline import run_pipeline
from openbimagent.orchestrator.dispatch import Verdict
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.vision.render_loop import RenderLoopResult
from openbimagent.vision.rubric import CritiqueResult, MockCritic
from openbimagent.vision.scad_loop import ScadLoopResult

PACKS = Path(__file__).resolve().parents[1] / "domain_packs"
SINGLE = PACKS / "single_asset_hero" / "playbook.md"

# 1x1 PNG(fake screenshot 用;合法头 + 非空)
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ---------- fakes ----------


class _FakeRegistry:
    """providers registry 桩:按队列吐出 content 字符串/异常;记录调用供断言。"""

    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, role, messages, **kwargs):
        self.calls.append({"role": role, "messages": messages, "kwargs": kwargs})
        if not self._replies:
            raise RuntimeError("FakeRegistry replies exhausted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            reply.setdefault("model_resolved", "test-model")
            return reply
        return {
            "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "model_resolved": "test-model",
        }


def _make_render_result(
    *,
    converged: bool = True,
    best_score: float = 9.0,
    terminate_reason: str = "perfect_score",
    iters: int = 1,
    html_report: Path | None = None,
) -> RenderLoopResult:
    """构造 RenderLoopResult 桩(测试不跑真实 render_loop)。"""
    return RenderLoopResult(
        converged=converged,
        best_score=best_score,
        best_snapshot=Path("/tmp/fake.blend"),
        iters=iters,
        terminate_reason=terminate_reason,
        scores=(best_score,),
        html_report=html_report or Path("/tmp/fake.html"),
    )


def _make_scad_result(
    *,
    converged: bool = True,
    best_score: float = 9.0,
    terminate_reason: str = "perfect_score",
    iters: int = 1,
) -> ScadLoopResult:
    return ScadLoopResult(
        converged=converged,
        best_score=best_score,
        best_snapshot=Path("/tmp/best_ir.json"),
        iters=iters,
        terminate_reason=terminate_reason,
        scores=(best_score,),
    )


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_1PX)
    return path


def _make_fake_async_render_fn(result: RenderLoopResult):
    """构造 async fake render_loop_fn(签名与 run_render_loop 一致;返回预设 result)。"""

    async def fake_fn(*args, **kwargs):
        return result

    return fake_fn


def _make_fake_scad_fn(result: ScadLoopResult):
    """构造 sync fake scad_loop_fn(签名与 run_scad_loop 一致)。"""

    def fake_fn(*args, **kwargs):
        return result

    return fake_fn


def _make_mock_blender_client(tmp_path: Path) -> tuple[Any, dict[str, AsyncMock]]:
    """构造全 mock 的 BlenderMCPClient(与 test_render_loop 同模式;不连真实 Blender)。"""
    from openbimagent.mcp_clients.blender import BlenderMCPClient

    client = BlenderMCPClient.transport_socket(port=9887)  # 不真连
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    async def _set_editable_scope(*, objects=None, collections=None, enabled=True):
        return {"enabled": enabled, "objects": list(objects or [])}

    async def _execute_code(code: str):
        snap = str(snap_dir / "snap.blend")
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

    async def _turntable(*, output_dir, target, frames=4, width=256):
        results = [{"filepath": f"{output_dir}/tt_{i:03d}.png", "brightness": 0.3} for i in range(frames)]
        for r in results:
            _write_png(Path(r["filepath"]))
        return {"frames": frames, "all_nonblack": True, "results": results}

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
        "turntable": AsyncMock(side_effect=_turntable),
        "restore_snapshot": AsyncMock(side_effect=_restore_snapshot),
        "close": AsyncMock(side_effect=_close),
        "connect": AsyncMock(side_effect=_connect),
    }
    client.set_editable_scope = mocks["set_editable_scope"]  # type: ignore[method-assign]
    client.execute_code = mocks["execute_code"]  # type: ignore[method-assign]
    client.screenshot_or_render = mocks["screenshot_or_render"]  # type: ignore[method-assign]
    client.batch_render = mocks["batch_render"]  # type: ignore[method-assign]
    client.turntable = mocks["turntable"]  # type: ignore[method-assign]
    client.restore_snapshot = mocks["restore_snapshot"]  # type: ignore[method-assign]
    client.close = mocks["close"]  # type: ignore[method-assign]
    client.connect = mocks["connect"]  # type: ignore[method-assign]
    client._connected = True  # 标记已连(batch_executor 的 is_connected 守护跳过真 connect)
    return client, mocks


def _semantic_ir() -> dict[str, Any]:
    """planner 默认模板产出的语义 IR(无 primitive/size/position,SCAD 环应跳过)。"""
    return {
        "version": "0.1",
        "assets": [
            {"id": "batch_01_主体", "category": "placeholder", "description": "占位资产", "count": 1, "tags": ["placeholder"]},
        ],
        "spatial_constraints": [],
        "batches": [["batch_01_主体"]],
    }


def _geometric_ir() -> dict[str, Any]:
    """含 primitive/size/position 的几何 IR(SCAD 环可跑)。"""
    return {
        "version": "0.1",
        "assets": [
            {"id": "base", "primitive": "cube", "size": [4, 2, 0.5], "position": [0, 0, 0.25], "color": "lightgray"},
        ],
        "spatial_constraints": [],
        "batches": [["base"]],
    }


# ---------- builder 测试 ----------


def test_builder_template_fallback_no_registry() -> None:
    """无 registry:走确定性模板,产 bpy 代码,含 import bpy + primitive_cube_add。"""
    builder = make_builder_fn(registry=None, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "import bpy" in code
    assert "primitive_cube_add" in code
    assert "M0Cube" in code


def test_builder_template_handles_empty_batch() -> None:
    """batch 为空 / IR 资产不在 batch 内:兜底产一个 cube(不让 builder 空返回)。"""
    builder = make_builder_fn(registry=None, role_brief="test brief")
    code = builder(None, {"batch": [], "ir": {"assets": []}})
    assert "import bpy" in code
    assert "primitive_cube_add" in code


def test_builder_llm_path_success() -> None:
    """registry 非空 + LLM 输出合法 bpy 代码:走 LLM 路径,代码原样采用。"""
    registry = _FakeRegistry(["```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```"])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "import bpy" in code and "primitive_cube_add" in code
    assert len(registry.calls) == 1
    assert registry.calls[0]["role"] == "modeler"


def test_builder_llm_path_retry_then_valid() -> None:
    """首次输出非 bpy(缺 bpy 关键字)→ 重试 1 次 → 合法即采用。"""
    registry = _FakeRegistry([
        "这是说明文字,没有代码",
        "import bpy\nbpy.ops.mesh.primitive_cube_add()",
    ])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "primitive_cube_add" in code
    assert len(registry.calls) == 2


def test_builder_llm_path_fallback_on_persistent_invalid() -> None:
    """连续 2 次非法输出 → BuilderError → 外层 except 捕获 → 回退确定性模板(含失败注释)。"""
    registry = _FakeRegistry(["no code", "still no code"])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code  # 回退注释
    assert "import bpy" in code  # 模板代码仍在


def test_builder_llm_path_fallback_on_registry_exception() -> None:
    """registry.chat 抛异常(熔断/缺 key)→ 回退模板,不让整批死掉。"""

    class _BoomRegistry:
        def chat(self, role, messages, **kwargs):
            raise RuntimeError("circuit breaker open")

    builder = make_builder_fn(registry=_BoomRegistry(), role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code
    assert "import bpy" in code


def test_builder_rework_instruction_in_comments() -> None:
    """FIX 时 prev_critique.actionable_feedback 写进模板代码注释(供 review)。"""
    builder = make_builder_fn(registry=None, role_brief="test brief")
    critique = CritiqueResult(
        rubric_scores={"geometry": 6.0, "style": 6.0, "material": 6.0, "wear": 6.0, "lighting": 6.0, "composition": 6.0},
        reasoning="mock", anchor_ref="mock", actionable_feedback="Object A 缩放 0.8 并沿 Z 降 0.2",
        critic_model="mock",
    )
    code = builder(critique, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "# rework" in code
    assert "0.8" in code


def test_builder_rejects_forbidden_tokens() -> None:
    """LLM 输出含 os./subprocess. → 软校验拒绝 → 重试 → 仍犯 → 回退模板。"""
    registry = _FakeRegistry([
        "import bpy\nimport os\nos.system('rm -rf /')",
        "import bpy\nimport subprocess\nsubprocess.run(['ls'])",
    ])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code  # 两次都含禁用 token,回退


def test_builder_rejects_syntax_error_then_falls_back() -> None:
    """LLM 输出含 SyntaxError(line 48 缺逗号等)→ compile() 客户端拦下 → 重试 → 仍坏 → 回退模板。

    回归用例:真实 agentrouter 跑通时 Claude modeler 第二轮返工代码 line 48 缺逗号,
    坏代码一路送到 addon AST allowlist 才被 ast.parse 拒,既烧 token 又让 render_loop
    整批死。客户端 compile() 先拦 → 触发重试 1 次 → 仍坏 BuilderError → 回退模板。
    """
    # 缺逗号的非法代码(line 2: location=(0 0 1) 少了逗号)
    bad_code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0 location=(0, 0, 1))\n"
    registry = _FakeRegistry([bad_code, bad_code])  # 两次都坏
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code  # 连续两次语法错误 → 回退模板
    assert "import bpy" in code  # 模板代码兜底
    assert len(registry.calls) == 2  # 重试 1 次


def test_builder_syntax_error_retry_then_valid() -> None:
    """首次语法错误 → 重试 → 第二次合法即采用(不死板回退)。"""
    bad_code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0 location=(0, 0, 1))\n"
    good_code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))\n"
    registry = _FakeRegistry([bad_code, good_code])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "primitive_cube_add" in code
    assert "LLM 路径失败" not in code  # 第二次合法,不走回退
    assert len(registry.calls) == 2


def test_builder_rejects_banned_builtin_dir_then_falls_back() -> None:
    """LLM 输出含 banned builtin 'dir' → 客户端 AST 镜像拦下 → 重试 → 仍犯 → 回退模板。

    回归用例:真实 agentrouter 跑通时 Claude modeler 第三轮代码用了 dir(),addon AST
    allowlist 拒('use of banned builtin name dir')让 render_loop 整批死。客户端 ast.walk
    镜像 addon BANNED_BUILTIN_NAMES 先拦 → 触发重试 1 次 → 仍坏 BuilderError → 回退模板。
    """
    bad_code = "import bpy\nfor o in dir(bpy.data.objects):\n    print(o)\n"
    registry = _FakeRegistry([bad_code, bad_code])  # 两次都含 dir
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code  # 连续两次含 dir → 回退模板
    assert "import bpy" in code  # 模板代码兜底
    assert len(registry.calls) == 2


def test_builder_rejects_non_allowed_import_then_falls_back() -> None:
    """LLM 输出含 import os → 客户端 AST 镜像拦下( addon 仅允许 bpy/bmesh/mathutils/math)→ 回退。"""
    bad_code = "import bpy\nimport os\nos.system('echo hi')\n"
    registry = _FakeRegistry([bad_code, bad_code])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "LLM 路径失败" in code


def test_builder_accepts_valid_modeler_code() -> None:
    """合法 modeler 代码(只用 bpy + math,无禁用内置/导入)直接通过,不误杀。"""
    good_code = (
        "import bpy\nimport math\n"
        "coll = bpy.data.collections.new('M0Cube')\n"
        "bpy.context.scene.collection.children.link(coll)\n"
        "bpy.ops.mesh.primitive_cube_add(size=math.sqrt(4.0), location=(0, 0, 1))\n"
        "obj = bpy.context.active_object\nobj.name = 'M0Cube'\n"
        "coll.objects.link(obj)\n"
    )
    registry = _FakeRegistry([good_code])
    builder = make_builder_fn(registry=registry, role_brief="test brief")
    code = builder(None, {"batch": ["M0Cube"], "ir": _semantic_ir()})
    assert "primitive_cube_add" in code
    assert "LLM 路径失败" not in code
    assert len(registry.calls) == 1


# ---------- batch_executor 测试 ----------


def test_batch_executor_skips_scad_no_geometric_ir(tmp_path) -> None:
    """语义 IR(无 primitive 字段):SCAD 环跳过,直接进 Blender 环。"""
    render_result = _make_render_result(converged=True, terminate_reason="perfect_score")
    client, _ = _make_mock_blender_client(tmp_path)
    html_calls: list[tuple[str, str]] = []

    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"scad_loop": {"min_score": 8.0, "max_iters": 6}, "blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        scad_critic=MockCritic([9.0]),  # 注入了但 IR 无几何 → SCAD 环应跳过
        render_critic=MockCritic([9.0]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
        on_html_report=lambda path, label: html_calls.append((str(path), label)),
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.PASS
    assert len(html_calls) == 1
    assert html_calls[0][1] == "主体"


def test_batch_executor_scad_not_converged_returns_fix(tmp_path) -> None:
    """SCAD 环未收敛(hard_limit)→ FIX,带返工指令(不进 Blender 环)。"""
    scad_result = _make_scad_result(converged=False, best_score=5.0, terminate_reason="hard_limit", iters=3)
    render_result = _make_render_result(converged=True)  # 不应被调用
    client, _ = _make_mock_blender_client(tmp_path)
    render_fn = _make_fake_async_render_fn(render_result)
    render_calls: list = []
    orig_fn = render_fn

    async def tracking_fn(*args, **kwargs):
        render_calls.append(1)
        return await orig_fn(*args, **kwargs)

    agent_fn = make_batch_executor(
        ir=_geometric_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"scad_loop": {"min_score": 8.0, "max_iters": 6}, "blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        scad_critic=MockCritic([5.0]),
        render_critic=MockCritic([9.0]),
        scad_loop_fn=_make_fake_scad_fn(scad_result),
        render_loop_fn=tracking_fn,
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.FIX
    assert "SCAD 环" in (report.rework_instruction or "")
    assert len(render_calls) == 0  # SCAD 未收敛不进 Blender 环


def test_batch_executor_approval_denied_returns_escalate(tmp_path) -> None:
    """审批门拒绝 execute_code → ESCALATE,不进 Blender 环。"""
    render_result = _make_render_result(converged=True)
    client, _ = _make_mock_blender_client(tmp_path)
    render_calls: list = []

    async def tracking_fn(*args, **kwargs):
        render_calls.append(1)
        return await render_result

    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"scad_loop": {"min_score": 8.0, "max_iters": 6}, "blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([9.0]),
        render_loop_fn=tracking_fn,
        approval_fn=lambda op, params: False,  # 拒绝
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.ESCALATE
    assert "拒绝" in report.hint
    assert len(render_calls) == 0


def test_batch_executor_render_perfect_score_returns_pass(tmp_path) -> None:
    """Blender 环 perfect_score → PASS。"""
    render_result = _make_render_result(converged=True, best_score=9.5, terminate_reason="perfect_score")
    client, _ = _make_mock_blender_client(tmp_path)
    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([9.5]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.PASS
    assert "perfect_score" in report.hint


def test_batch_executor_render_hard_limit_returns_fix(tmp_path) -> None:
    """Blender 环 hard_limit(未达标耗尽)→ FIX,带返工指令交 builder 重改。"""
    render_result = _make_render_result(converged=False, best_score=6.0, terminate_reason="hard_limit", iters=4)
    client, _ = _make_mock_blender_client(tmp_path)
    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([6.0]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.FIX
    assert report.rework_instruction is not None
    assert "hard_limit" in report.rework_instruction


def test_batch_executor_render_divergence_returns_escalate(tmp_path) -> None:
    """Blender 环 divergence_fallback(已回滚 best)→ ESCALATE(人审接管)。"""
    render_result = _make_render_result(converged=False, best_score=7.0, terminate_reason="divergence_fallback", iters=3)
    client, _ = _make_mock_blender_client(tmp_path)
    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([7.0]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.ESCALATE
    assert "divergence_fallback" in report.hint


def test_batch_executor_html_report_callback(tmp_path) -> None:
    """每批结束调 on_html_report(path, label);CLI 用来打印路径。"""
    html_path = tmp_path / "fake_report.html"
    render_result = _make_render_result(converged=True, html_report=html_path)
    client, _ = _make_mock_blender_client(tmp_path)
    received: list[tuple[Path, str]] = []

    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([9.0]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
        on_html_report=lambda path, label: received.append((path, label)),
    )
    agent_fn("主体", None)
    assert len(received) == 1
    assert received[0] == (html_path, "主体")


def test_batch_executor_approval_yes_proceeds(tmp_path) -> None:
    """审批门同意 → 进 Blender 环正常执行。"""
    render_result = _make_render_result(converged=True)
    client, _ = _make_mock_blender_client(tmp_path)
    agent_fn = make_batch_executor(
        ir=_semantic_ir(),
        batch_names=["主体"],
        work_dir=tmp_path / "work",
        acceptance={"blender_loop": {"min_score": 8.5, "max_iters": 4}},
        client=client,
        builder_fn=make_builder_fn(registry=None, role_brief="test"),
        render_critic=MockCritic([9.0]),
        render_loop_fn=_make_fake_async_render_fn(render_result),
        approval_fn=lambda op, params: True,
    )
    report = agent_fn("主体", None)
    assert report.verdict is Verdict.PASS


# ---------- pipeline 全链路状态机 ----------


def test_pipeline_no_blender_escalates_and_deliver_missing(tmp_path) -> None:
    """无 blender_client:orchestrator 跑空批次全 ESCALATE;deliver 缺失产物 → ok=False。

    覆盖全链路:load playbook → clarify(回车接受默认)→ plan(模板)→ orchestrate(escalate)
    → deliver(缺失)。clarify 的 input_func 注入,全部回车接受默认值(completion_score=100)。
    """
    answers = iter(["", "", ""])  # 三个槽位全回车接受默认
    phases: list[tuple[str, str]] = []

    result = run_pipeline(
        playbook_path=SINGLE,
        out_dir=tmp_path / "out",
        blender_client=None,  # 不连 Blender
        input_func=lambda p: next(answers),
        on_phase=lambda name, payload: phases.append((name, payload.get("note", ""))),
        sessions_dir=tmp_path / "sessions",
        yes=True,  # 跳过审批门
    )
    assert result.ok is False
    assert result.plan_run is not None
    assert result.plan_run.ok is False  # 全 ESCALATE
    assert result.delivery is not None
    assert result.delivery.ok is False  # 缺产物 + 未 accepted
    # 阶段序列覆盖全链路
    phase_names = [p[0] for p in phases]
    assert "load_playbook" in phase_names
    assert "clarify" in phase_names
    assert "planner_instantiate" in phase_names
    assert "orchestrate" in phase_names
    assert "deliver" in phase_names


def test_pipeline_with_mock_blender_full_success(tmp_path) -> None:
    """全 mock Blender:builder 模板 + render_loop fake perfect_score → PASS → deliver accepted。

    deliver 产物按 deliverable 子串匹配:`.blend 工程` 按后缀匹配 scene.blend;
    `英雄镜头渲染 x1` 按归一化子串匹配文件名,故预放同名 png。accepted 需 session
    有 score 事件,测试通过 monkey-patch make_acceptance_fn 直接返回 True(隔离
    deliver 门禁的 found 逻辑,不依赖 session 落 score)。
    """
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.blend").write_bytes(b"mock-blend")
    (out / "英雄镜头渲染 x1.png").write_bytes(b"mock-png")  # 文件名含 deliverable 子串

    client, _ = _make_mock_blender_client(tmp_path)
    render_result = _make_render_result(converged=True, best_score=9.5, terminate_reason="perfect_score")

    # 注入 fake render_loop_fn + accepted_fn(避免跑真实 Blender / 不依赖 session score 事件)
    import openbimagent.assembly.pipeline as pipeline_mod

    orig_make = pipeline_mod.make_batch_executor
    orig_acc = pipeline_mod.make_acceptance_fn

    def patched_make(**kwargs):
        kwargs["render_loop_fn"] = _make_fake_async_render_fn(render_result)
        kwargs["render_critic"] = MockCritic([9.5])
        kwargs["scad_critic"] = None  # 语义 IR,跳过 SCAD 环
        return orig_make(**kwargs)

    pipeline_mod.make_batch_executor = patched_make
    pipeline_mod.make_acceptance_fn = lambda *a, **k: lambda: True  # accepted=True(隔离 found 逻辑)
    try:
        result = run_pipeline(
            playbook_path=SINGLE,
            out_dir=out,
            blender_client=client,
            input_func=lambda p: "",  # 全回车
            sessions_dir=tmp_path / "sessions",
            yes=True,
        )
    finally:
        pipeline_mod.make_batch_executor = orig_make
        pipeline_mod.make_acceptance_fn = orig_acc

    assert result.ok is True
    assert result.plan_run is not None
    assert result.plan_run.ok is True
    assert result.delivery is not None
    assert result.delivery.ok is True


def test_pipeline_ctrl_c_drops_checkpoint(tmp_path) -> None:
    """Ctrl+C(KeyboardInterrupt)中断 → 落 checkpoint MESSAGE 事件到 session → interrupted=True。"""
    import openbimagent.assembly.pipeline as pipeline_mod

    orig_run_plan = pipeline_mod.run_plan

    def boom_run_plan(*args, **kwargs):
        raise KeyboardInterrupt

    pipeline_mod.run_plan = boom_run_plan
    try:
        result = run_pipeline(
            playbook_path=SINGLE,
            out_dir=tmp_path / "out",
            blender_client="fake-non-none",  # 触发进 orchestrate 分支
            input_func=lambda p: "",
            sessions_dir=tmp_path / "sessions",
            yes=True,
        )
    finally:
        pipeline_mod.run_plan = orig_run_plan

    assert result.interrupted is True
    assert result.ok is False
    assert result.error == "Ctrl+C"
    # checkpoint 事件落 session:MESSAGE 形态,content 含 [checkpoint]
    events = result.session.load()
    checkpoint_events = [
        e for e in events
        if e.type is EventType.MESSAGE and "[checkpoint]" in getattr(e.payload, "content", "")
    ]
    assert len(checkpoint_events) == 1


def test_pipeline_deliver_approval_denied(tmp_path) -> None:
    """deliver 审批门拒绝 → ok=False,error 含「拒绝 deliver 审批门」。"""
    denials: list[str] = []

    def deny(op, params):
        denials.append(op)
        if op == "deliver":
            return False
        return True  # execute_code 等放行

    result = run_pipeline(
        playbook_path=SINGLE,
        out_dir=tmp_path / "out",
        blender_client=None,
        input_func=lambda p: "",
        approval_fn=deny,
        sessions_dir=tmp_path / "sessions",
    )
    assert result.ok is False
    assert "deliver 审批门" in (result.error or "")
    assert "deliver" in denials


def test_pipeline_yes_skips_all_approvals(tmp_path) -> None:
    """--yes(yes=True):approval_fn 不被调用(跳过所有审批门)。"""
    calls: list[str] = []

    def tracking_approval(op, params):
        calls.append(op)
        return True

    run_pipeline(
        playbook_path=SINGLE,
        out_dir=tmp_path / "out",
        blender_client=None,
        input_func=lambda p: "",
        approval_fn=tracking_approval,
        sessions_dir=tmp_path / "sessions",
        yes=True,
    )
    assert len(calls) == 0  # yes=True → effective_approval = None,approval_fn 不被调用


def test_pipeline_clarify_below_threshold_aborts(tmp_path) -> None:
    """clarify 未达放行阈值(<85)→ 流程中止,error 含 completion_score。

    single_asset_hero 有 3 个槽位全带默认值,completion_score=100(全默认)。
    构造一个无默认值的 playbook 让回车=未填,触发 <85。
    """
    pb = tmp_path / "playbook.md"
    pb.write_text(
        "---\n"
        "name: no_default_test\n"
        "targets: [blender]\n"
        "slots:\n"
        "  - { id: a, question: Q1 }\n"
        "  - { id: b, question: Q2 }\n"
        "  - { id: c, question: Q3 }\n"
        "  - { id: d, question: Q4 }\n"  # 4 个无默认槽位,全空 = 0%
        "phases:\n"
        "  - id: asset_batches\n"
        "    batches: [主体]\n"
        "    per_batch: [scad_check]\n"
        "acceptance:\n"
        "  scad_loop: { min_score: 8.0, max_iters: 6 }\n"
        "  blender_loop: { min_score: 8.5, max_iters: 4 }\n"
        "deliverables: [.blend 工程]\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )
    result = run_pipeline(
        playbook_path=pb,
        out_dir=tmp_path / "out",
        blender_client=None,
        input_func=lambda p: "",  # 全回车 = 全未填
        sessions_dir=tmp_path / "sessions",
        yes=True,
    )
    assert result.ok is False
    assert "completion_score" in (result.error or "")


def test_pipeline_session_recorded_with_playbook(tmp_path) -> None:
    """run_pipeline 创建 session,登记 playbook 路径与 title(index.json 可见)。"""
    result = run_pipeline(
        playbook_path=SINGLE,
        out_dir=tmp_path / "out",
        blender_client=None,
        input_func=lambda p: "",
        sessions_dir=tmp_path / "sessions",
        yes=True,
    )
    assert result.session is not None
    entries = SessionStore.list_sessions(tmp_path / "sessions")
    assert len(entries) >= 1
    entry = next(e for e in entries if e["id"] == result.session.session_id)
    assert entry["playbook"] == str(SINGLE)
    assert "single_asset_hero" in entry["title"]


def test_pipeline_clarify_qa_recorded_as_message_events(tmp_path) -> None:
    """clarify 一问一答落成 message 事件:assistant 问 / user 答成对,补验收 e 缺的 message 类(修复 1)。

    注入 3 个明确答案(非默认值),断言 session 里 MESSAGE 事件 ≥6 条且按问答顺序成对:
    奇数位 role=assistant 且 content 含槽位问题文本,偶数位 role=user 且 content == 注入答案原文。
    """
    answers = iter(["复古售货机", "江户x赛博", "7"])
    result = run_pipeline(
        playbook_path=SINGLE,
        out_dir=tmp_path / "out",
        blender_client=None,  # 不连 Blender,走 escalate,不中断无 checkpoint message
        input_func=lambda p: next(answers),
        sessions_dir=tmp_path / "sessions",
        yes=True,
    )
    assert result.session is not None
    messages = [e for e in result.session.load() if e.type == EventType.MESSAGE]
    assert len(messages) >= 6  # 3 对 = 6 条
    # 前 6 条按问答成对:assistant 问(含槽位关键词)→ user 答(== 注入原文,非默认值)
    expected_answers = ["复古售货机", "江户x赛博", "7"]
    question_subs = ["做什么资产", "风格锚点", "磨损程度"]
    for i in range(3):
        assistant_ev = messages[2 * i]
        user_ev = messages[2 * i + 1]
        assert assistant_ev.payload.role == "assistant"
        assert question_subs[i] in assistant_ev.payload.content
        assert user_ev.payload.role == "user"
        assert user_ev.payload.content == expected_answers[i]  # 原文,不是默认值


def test_modeler_messages_include_style_anchors() -> None:
    """modeler prompt 含五条风格锚点关键词(修复 3 白盒)。

    冒烟 finding #4:modeler 退化灰盒 → builder._build_modeler_messages 注入结构拆分/PBR/磨损/霓虹/三点布光。
    """
    from openbimagent.assembly.builder import _build_modeler_messages

    batch_ctx = {
        "batch": ["vending"],
        "ir": {"assets": [{"id": "vending", "category": "prop"}]},
    }
    messages = _build_modeler_messages(brief="brief", batch_ctx=batch_ctx, prev_critique=None)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_content = messages[1]["content"]
    for kw in ("风格锚点", "Emission", "三点", "metallic"):
        assert kw in user_content, f"modeler prompt 缺风格锚点关键词:{kw!r}"


# ---------- M1 集成测试(Relay 013 任务 C1) ----------


def _fake_scad_render(scad_path, out_dir):
    """离线 SCAD 渲染桩:三视角各写 1x1 PNG(与 test_scad_loop._fake_render 同形态)。"""
    paths = {}
    for view in ("iso", "front", "top"):
        png = out_dir / f"{scad_path.stem}_{view}.png"
        png.write_bytes(_PNG_1PX)
        paths[view] = png
    return paths


def test_pipeline_with_scad_loop_convergence(tmp_path) -> None:
    """SCAD 环递增评分 [6.0, 7.5, 8.5] 收敛:session 留 ≥3 个 score 事件(3 轮 SCAD)。

    注入几何 IR 触发 SCAD 环;包装 run_scad_loop 注入离线 fake render_fn(不依赖 openscad CLI);
    mock render_loop_fn 跳过真实 Blender;deliver 预放产物 + accepted=True 隔离门禁。
    """
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.blend").write_bytes(b"mock-blend")
    (out / "英雄镜头渲染 x1.png").write_bytes(b"mock-png")

    client, _ = _make_mock_blender_client(tmp_path)
    render_result = _make_render_result(converged=True, best_score=9.5, terminate_reason="perfect_score")

    import openbimagent.assembly.pipeline as pipeline_mod
    from openbimagent.vision.scad_loop import run_scad_loop

    orig_make = pipeline_mod.make_batch_executor
    orig_acc = pipeline_mod.make_acceptance_fn

    def _scad_loop_with_fake_render(*args, **kwargs):
        kwargs["render_fn"] = _fake_scad_render
        return run_scad_loop(*args, **kwargs)

    def patched_make(**kwargs):
        kwargs["ir"] = _geometric_ir()  # 几何 IR 触发 SCAD 环
        kwargs["scad_critic"] = MockCritic([6.0, 7.5, 8.5])
        kwargs["scad_loop_fn"] = _scad_loop_with_fake_render
        kwargs["render_critic"] = MockCritic([9.5])
        kwargs["render_loop_fn"] = _make_fake_async_render_fn(render_result)
        return orig_make(**kwargs)

    pipeline_mod.make_batch_executor = patched_make
    pipeline_mod.make_acceptance_fn = lambda *a, **k: lambda: True
    try:
        result = run_pipeline(
            playbook_path=SINGLE,
            out_dir=out,
            blender_client=client,
            input_func=lambda p: "",
            sessions_dir=tmp_path / "sessions",
            yes=True,
        )
    finally:
        pipeline_mod.make_batch_executor = orig_make
        pipeline_mod.make_acceptance_fn = orig_acc

    assert result.ok is True
    assert result.plan_run is not None and result.plan_run.ok is True
    # SCAD 环跑 3 轮(6.0→7.5→8.5 达 8.0 阈值 perfect_score)→ session 至少 3 个 score 事件
    events = result.session.load()
    score_events = [
        e for e in events
        if e.type is EventType.CUSTOM and getattr(e.payload, "customType", "") == "score"
    ]
    assert len(score_events) >= 3, f"SCAD 环未留足 score 事件,实得 {len(score_events)}"


def test_pipeline_with_orchestrator_concurrent(tmp_path) -> None:
    """orchestrator 并发:3 批次 playbook,每批 sleep 0.1s,concurrent=True 总耗时 < 1.5s。

    patch run_plan 注入 concurrent=True;patch make_batch_executor 返回 sleep agent_fn;
    3 批顺序则 ≥0.3s,并发 ~0.1s,< 1.5s 证明并发生效。
    """
    import time

    pb = tmp_path / "playbook.md"
    pb.write_text(
        "---\n"
        "name: concurrent_test\n"
        "targets: [blender]\n"
        "slots:\n"
        "  - { id: a, question: Q1, default: x }\n"
        "phases:\n"
        "  - id: asset_batches\n"
        "    batches: [b1, b2, b3]\n"
        "    per_batch: [blender_build]\n"
        "acceptance:\n"
        "  blender_loop: { min_score: 8.5, max_iters: 4 }\n"
        "deliverables: [.blend 工程]\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )

    import openbimagent.assembly.pipeline as pipeline_mod
    from openbimagent.orchestrator.dispatch import BatchReport, Verdict

    orig_run_plan = pipeline_mod.run_plan
    orig_make = pipeline_mod.make_batch_executor

    def concurrent_run_plan(*args, **kwargs):
        kwargs["concurrent"] = True
        return orig_run_plan(*args, **kwargs)

    def slow_agent_fn(batch: str, rework: str | None) -> BatchReport:
        time.sleep(0.1)  # 模拟耗时
        return BatchReport(Verdict.PASS, hint=f"{batch} done")

    def patched_make(**kwargs):
        return slow_agent_fn  # 忽略 ir/client,直接返回慢速 agent_fn

    pipeline_mod.run_plan = concurrent_run_plan
    pipeline_mod.make_batch_executor = patched_make
    try:
        start = time.monotonic()
        result = run_pipeline(
            playbook_path=pb,
            out_dir=tmp_path / "out",
            blender_client="fake-non-none",  # 非 None 触发进 orchestrate 分支
            input_func=lambda p: "",
            sessions_dir=tmp_path / "sessions",
            yes=True,
        )
        elapsed = time.monotonic() - start
    finally:
        pipeline_mod.run_plan = orig_run_plan
        pipeline_mod.make_batch_executor = orig_make

    # 3 批 × 0.1s:顺序 ≥0.3s,并发 ~0.1s;< 1.5s 证明并发生效
    assert elapsed < 1.5, f"并发未生效:耗时 {elapsed:.3f}s"
    assert result.plan_run is not None
    assert result.plan_run.ok is True
    assert len(result.plan_run.outcomes) == 3
    assert all(o.verdict is Verdict.PASS for o in result.plan_run.outcomes)
