"""SCAD 快检环测试(ARCH §3 环 1;ADR-0004 收敛语义;openscad_spike.md CLI 实测形态)。

覆盖:IR→scad 确定性快照、openscad 真实三视角渲染、patch old_value 校验拒绝、
MockCritic 驱动收敛(perfect_score / hard_limit / divergence_fallback)、session 事件落盘可回放。
真实 CLI 用例允许调本机 openscad.exe;其余用注入的 fake_render 保持离线、毫秒级。
"""

import base64
import json

import pytest

from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.vision.rubric import MockCritic
from openbimagent.vision.scad_loop import (
    CAMERA_VIEWS,
    DEFAULT_OPENSCAD,
    PatchRejectedError,
    PatchValidationError,
    apply_ir_patch,
    apply_patch,
    ir_to_scad,
    render_views,
    run_scad_loop,
)

# 1x1 PNG(离线 fake_render 用;只要求合法 PNG 头 + 非空)
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

IR = {
    "version": "0.1",
    "assets": [
        {"id": "base", "primitive": "cube", "size": [4, 2, 0.5], "position": [0, 0, 0.25], "color": "lightgray"},
        {"id": "pole", "primitive": "cylinder", "size": [0.3, 3], "position": [1, 0.5, 2]},
        {"id": "ball", "primitive": "sphere", "size": 0.6, "position": [-1, -0.5, 3]},
        {"id": "roof", "primitive": "cone", "size": [1.2, 0.0, 1.0], "position": [1, 0.5, 4]},
    ],
}

EXPECTED_SCAD = """// SceneGraphIR → OpenSCAD(deterministic, no LLM).
// assets: 4
$fn = 48;

// asset: base (cube)
color("lightgray") translate([0.0000,0.0000,0.2500]) cube([4.0000,2.0000,0.5000], center=true);
// asset: pole (cylinder)
translate([1.0000,0.5000,2.0000]) cylinder(r=0.3000, h=3.0000, center=true);
// asset: ball (sphere)
translate([-1.0000,-0.5000,3.0000]) sphere(r=0.6000);
// asset: roof (cone)
translate([1.0000,0.5000,4.0000]) cylinder(r1=1.2000, r2=0.0000, h=1.0000, center=true);
"""

requires_openscad = pytest.mark.skipif(not DEFAULT_OPENSCAD.is_file(), reason="OpenSCAD 未安装,跳过真实 CLI 渲染")


def _fake_render(scad_path, out_dir):
    """离线渲染桩:三视角各写一张 1x1 PNG,路径形态与 render_views 一致。"""
    paths = {}
    for view in CAMERA_VIEWS:
        png = out_dir / f"{scad_path.stem}_{view}.png"
        png.write_bytes(_PNG_1PX)
        paths[view] = png
    return paths


@pytest.fixture()
def ir_path(tmp_path):
    path = tmp_path / "ir.json"
    path.write_text(json.dumps(IR, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- IR → OpenSCAD ----------


def test_ir_to_scad_snapshot() -> None:
    """IR→scad 确定性快照:定点浮点、资产顺序、color/center 形态逐字节一致。"""
    assert ir_to_scad(IR) == EXPECTED_SCAD
    assert ir_to_scad(IR) == ir_to_scad(json.loads(json.dumps(IR)))  # 确定性:同输入必同输出


def test_ir_to_scad_rejects_bad_ir() -> None:
    """空 assets / 重复 id / 未知图元 / 形态错误的 size 一律 ValueError。"""
    with pytest.raises(ValueError, match="assets"):
        ir_to_scad({"assets": []})
    with pytest.raises(ValueError, match="重复"):
        ir_to_scad({"assets": [IR["assets"][0], IR["assets"][0]]})
    with pytest.raises(ValueError, match="primitive"):
        ir_to_scad({"assets": [{"id": "x", "primitive": "torus", "size": [1], "position": [0, 0, 0]}]})
    with pytest.raises(ValueError, match="size"):
        ir_to_scad({"assets": [{"id": "x", "primitive": "cube", "size": [1, 2], "position": [0, 0, 0]}]})


# ---------- openscad CLI 真实渲染 ----------


@requires_openscad
def test_render_views_real_cli(tmp_path) -> None:
    """真实调 openscad CLI:三视角各产出非空合法 PNG(--viewall 常驻,spike 参数形态)。"""
    scad_path = tmp_path / "scene.scad"
    scad_path.write_text(ir_to_scad(IR), encoding="utf-8")
    images = render_views(scad_path, tmp_path, tag="t")
    assert set(images) == {"iso", "front", "top"}
    for png in images.values():
        data = png.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(data) > 1000  # 白模三视图实测 5-10KB(spike)


@requires_openscad
def test_run_scad_loop_real_cli_converges(ir_path, tmp_path) -> None:
    """端到端:真实渲染 + MockCritic 满分 → perfect_score 一轮收敛。"""
    result = run_scad_loop(
        ir_path, tmp_path / "artifacts", min_score=8.0, max_iters=3, critic=MockCritic([9.0])
    )
    assert result.converged and result.terminate_reason == "perfect_score" and result.iters == 1
    for view in CAMERA_VIEWS:
        assert (tmp_path / "artifacts" / f"iter1_{view}.png").is_file()


# ---------- JSON patch old_value 校验 ----------


def test_apply_ir_patch_replaces_with_old_value_check() -> None:
    """old_value 相符(数值容差内)即应用;返回新对象,入参不被改动。"""
    ops = [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": [1.0, 0.5, 2.0], "new_value": [1, 0.5, 3]}]
    patched = apply_ir_patch(IR, ops)
    assert patched["assets"][1]["position"] == [1, 0.5, 3]
    assert IR["assets"][1]["position"] == [1, 0.5, 2]  # 原 IR 不变
    assert "3.0000" in ir_to_scad(patched)


def test_apply_ir_patch_rejects_mismatched_old_value() -> None:
    """old_value 与当前值不符 → 整批拒绝(PatchRejectedError),IR 原样。"""
    ops = [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": [9, 9, 9], "new_value": [1, 0.5, 3]}]
    with pytest.raises(PatchRejectedError, match="old_value"):
        apply_ir_patch(IR, ops)
    assert IR["assets"][1]["position"] == [1, 0.5, 2]


def test_apply_ir_patch_rejects_unknown_target_and_bad_op() -> None:
    """目标资产不存在 / 非 replace op / 非法 field / patch 后不可渲染 → 拒绝。"""
    with pytest.raises(PatchRejectedError, match="不存在"):
        apply_ir_patch(IR, [{"op": "replace", "asset_id": "ghost", "field": "position", "old_value": [], "new_value": [0, 0, 0]}])
    with pytest.raises(PatchRejectedError, match="replace"):
        apply_ir_patch(IR, [{"op": "add", "asset_id": "pole", "field": "position", "old_value": [1, 0.5, 2], "new_value": [0, 0, 0]}])
    with pytest.raises(PatchRejectedError, match="field"):
        apply_ir_patch(IR, [{"op": "replace", "asset_id": "pole", "field": "owner", "old_value": None, "new_value": "x"}])
    with pytest.raises(PatchRejectedError, match="不可渲染"):
        apply_ir_patch(IR, [{"op": "replace", "asset_id": "pole", "field": "primitive", "old_value": "cylinder", "new_value": "torus"}])


# ---------- 收敛路径(MockCritic 驱动,离线 fake_render) ----------


def test_loop_converges_perfect_score(ir_path, tmp_path) -> None:
    """perfect_score 路径:首轮 ≥min_score 即收敛,best-so-far 快照落盘。"""
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=5, critic=MockCritic([9.0]), render_fn=_fake_render
    )
    assert result.converged is True
    assert result.terminate_reason == "perfect_score"
    assert result.iters == 1 and result.best_score == 9.0 and result.scores == (9.0,)
    assert result.best_snapshot is not None and result.best_snapshot.is_file()
    assert json.loads(result.best_snapshot.read_text(encoding="utf-8"))["assets"] == IR["assets"]


def test_loop_hard_limit_when_never_reaches_threshold(ir_path, tmp_path) -> None:
    """hard_limit 路径:分数持续上涨(无 delta 收敛、无连续降分)但始终 <min_score,耗尽 max_iters。"""
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=3, critic=MockCritic([5.0, 6.0, 7.0]), render_fn=_fake_render
    )
    assert result.converged is False
    assert result.terminate_reason == "hard_limit"
    assert result.iters == 3 and result.best_score == 7.0 and result.scores == (5.0, 6.0, 7.0)


def test_loop_divergence_fallback_restores_best(ir_path, tmp_path) -> None:
    """divergence_fallback 路径:连续 2 轮降分 → 回退 best-so-far 并写回 ir_path(ADR-0004)。"""
    def shrink(_critique, ir):
        return [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": ir["assets"][1]["position"], "new_value": [1, 0.5, 9]}]

    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=5,
        critic=MockCritic([7.0, 6.0, 5.0]), patcher=shrink, render_fn=_fake_render,
    )
    assert result.converged is False
    assert result.terminate_reason == "divergence_fallback"
    assert result.best_score == 7.0 and result.iters == 3
    restored = json.loads(ir_path.read_text(encoding="utf-8"))  # best-so-far(首轮 IR)已写回
    assert restored["assets"][1]["position"] == [1, 0.5, 2]


def test_loop_patch_applied_then_converges(ir_path, tmp_path) -> None:
    """patch 应用成功路径:首轮低分触发 patch,第二轮新 IR 渲染后达标收敛。"""
    ops = [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": [1, 0.5, 2], "new_value": [1, 0.5, 3]}]
    seen_ops = []

    def patcher(_critique, _ir):
        seen_ops.append(ops)
        return ops

    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=4,
        critic=MockCritic([6.0, 9.0]), patcher=patcher, render_fn=_fake_render,
    )
    assert result.converged and result.terminate_reason == "perfect_score" and result.iters == 2
    assert len(seen_ops) == 1  # 只在未达标轮生成 patch
    assert "translate([1.0000,0.5000,3.0000])" in (tmp_path / "a" / "scene_iter2.scad").read_text(encoding="utf-8")


def test_loop_rejected_patch_falls_back_to_best(ir_path, tmp_path) -> None:
    """patch old_value 不符被拒:回退 best-so-far 继续跑,环不崩;IR 始终未被污染。"""
    bad_ops = [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": [0, 0, 0], "new_value": [1, 0.5, 3]}]
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=3,
        critic=MockCritic([5.0, 6.0, 7.0]), patcher=lambda _c, _ir: bad_ops, render_fn=_fake_render,
    )
    assert result.converged is False and result.terminate_reason == "hard_limit"
    iter1 = (tmp_path / "a" / "scene_iter1.scad").read_text(encoding="utf-8")
    iter3 = (tmp_path / "a" / "scene_iter3.scad").read_text(encoding="utf-8")
    assert iter1 == iter3  # 拒绝后回退 best-so-far,IR 未被污染


# ---------- session 事件落盘可回放 ----------


def test_loop_events_replayable_from_session(ir_path, tmp_path) -> None:
    """screenshot/score/patch 三类 custom 事件落 SessionStore;load() 全量回放且字段完整。"""
    store = SessionStore.create(tmp_path / "sessions", title="scad 环测试")
    ops = [{"op": "replace", "asset_id": "pole", "field": "position", "old_value": [1, 0.5, 2], "new_value": [1, 0.5, 3]}]
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=8.0, max_iters=4,
        critic=MockCritic([6.0, 9.0]), patcher=lambda _c, _ir: ops, render_fn=_fake_render, session=store,
    )
    assert result.converged and result.iters == 2

    events = store.load()  # 回放:文件顺序全量读出
    by_type: dict[str, list] = {}
    for event in events:
        assert event.type is EventType.CUSTOM
        by_type.setdefault(event.payload.customType, []).append(event)
    assert set(by_type) == {"screenshot", "score", "patch"}
    assert len(by_type["screenshot"]) == 6  # 2 轮 × 3 视角
    assert len(by_type["score"]) == 2
    assert len(by_type["patch"]) == 1

    shot = by_type["screenshot"][0].payload
    assert shot.phase == "scad" and shot.camera_view in CAMERA_VIEWS and shot.image_path.endswith(".png")
    score = by_type["score"][0].payload
    assert set(score.rubric_scores) == {"geometry", "composition"}
    assert score.reasoning and score.anchor_ref and score.actionable_feedback and score.critic_model
    assert by_type["score"][1].payload.ab_swap_ref  # 第 2 轮起 A/B swap 引用 best-so-far 快照
    patch = by_type["patch"][0].payload
    assert patch.status == "applied" and "pole" in patch.diff and patch.target_file.endswith("ir.json")

    # parentId 链式挂载:除首条外每条挂前一条之下(可回放成线性 trace)
    assert events[0].parentId is None
    for prev, cur in zip(events, events[1:]):
        assert cur.parentId == prev.id


# ---------- M1 增强:IR→OpenSCAD 健壮性(Relay 013 任务 B1) ----------


def test_ir_to_scad_unsupported_primitive() -> None:
    """不支持图元(pyramid)→ ValueError,消息含「不支持的图元」。"""
    ir = {"assets": [{"id": "x", "primitive": "pyramid", "size": [1, 1, 1], "position": [0, 0, 0]}]}
    with pytest.raises(ValueError, match="不支持的图元"):
        ir_to_scad(ir)


def test_ir_to_scad_invalid_position() -> None:
    """position 任一维度绝对值 > 1000 → ValueError,消息含「超出合理范围」。"""
    ir = {"assets": [{"id": "x", "primitive": "cube", "size": [1, 1, 1], "position": [2000, 0, 0]}]}
    with pytest.raises(ValueError, match="超出合理范围"):
        ir_to_scad(ir)


def test_ir_to_scad_with_color() -> None:
    """color 为 RGB 三元组(0-1)→ OpenSCAD 代码含 color([r,g,b])(M1 RGB 支持)。

    格式与现有 cube([x,y,z]) 一致:逗号无空格,定点 %.4f。
    """
    ir = {"assets": [
        {"id": "red_cube", "primitive": "cube", "size": [1, 1, 1], "position": [0, 0, 0], "color": [1.0, 0.0, 0.0]}
    ]}
    scad = ir_to_scad(ir)
    assert "color([1.0000,0.0000,0.0000])" in scad


# ---------- M1 增强:JSON Patch 严格校验(RFC 6902,Relay 013 任务 B2) ----------


def test_apply_patch_replace_old_value_mismatch() -> None:
    """replace 的 old_value 与当前值不符 → PatchValidationError,消息含「old_value 不匹配」;入参不变。"""
    ir = {"assets": [
        {"id": "base", "primitive": "cube", "size": [1, 1, 1], "position": [0, 0, 0]}
    ]}
    ops = [{"op": "replace", "path": "/assets/0/position/0", "old_value": 99.0, "value": 10.0}]
    with pytest.raises(PatchValidationError, match="old_value 不匹配"):
        apply_patch(ir, ops)
    # 原子性:入参未被改动
    assert ir["assets"][0]["position"][0] == 0


def test_apply_patch_replace_success() -> None:
    """replace 的 old_value 相符(浮点容差内)→ 应用成功,assets[0].position[0] 改为新值;入参不变。"""
    ir = {"assets": [
        {"id": "base", "primitive": "cube", "size": [1, 1, 1], "position": [0, 0, 0]}
    ]}
    ops = [{"op": "replace", "path": "/assets/0/position/0", "old_value": 0.0, "value": 5.0}]
    patched = apply_patch(ir, ops)
    assert patched["assets"][0]["position"][0] == 5.0
    # 原子性:入参未被改动
    assert ir["assets"][0]["position"][0] == 0


def test_apply_patch_add_and_remove_ops() -> None:
    """add/remove 操作:add 在 dict 设键/list 追加,remove 删除;原子性保护。"""
    ir = {"assets": [
        {"id": "base", "primitive": "cube", "size": [1, 1, 1], "position": [0, 0, 0]}
    ], "meta": {"author": "tester"}}
    # add 新键 + remove 旧键 + replace 数值,混合 op
    ops = [
        {"op": "add", "path": "/meta/version", "value": "0.2"},
        {"op": "remove", "path": "/meta/author"},
        {"op": "replace", "path": "/assets/0/size/2", "old_value": 1.0, "value": 2.0},
    ]
    patched = apply_patch(ir, ops)
    assert patched["meta"]["version"] == "0.2"
    assert "author" not in patched["meta"]
    assert patched["assets"][0]["size"][2] == 2.0
    # 入参不变
    assert "version" not in ir["meta"]
    assert ir["assets"][0]["size"][2] == 1


def test_apply_patch_rejects_unknown_op_and_bad_pointer() -> None:
    """非法 op / 非 / 开头的 pointer → PatchValidationError。"""
    ir = {"assets": [{"id": "x", "primitive": "cube", "size": [1, 1, 1], "position": [0, 0, 0]}]}
    with pytest.raises(PatchValidationError, match="不支持的操作"):
        apply_patch(ir, [{"op": "move", "path": "/assets/0", "value": 1}])
    with pytest.raises(PatchValidationError, match="JSON Pointer"):
        apply_patch(ir, [{"op": "replace", "path": "assets/0", "old_value": 0, "value": 1}])


# ---------- M1 增强:收敛判定四选一(Relay 013 任务 B3) ----------


def test_convergence_perfect_score(ir_path, tmp_path) -> None:
    """perfect_score:首轮 overall=9.8 ≥ min_score=9.5 → 第 1 轮收敛。"""
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=9.5, max_iters=3,
        critic=MockCritic([9.8]), render_fn=_fake_render,
    )
    assert result.converged is True
    assert result.terminate_reason == "perfect_score"
    assert result.iters == 1
    assert result.best_score == 9.8


def test_convergence_delta(ir_path, tmp_path) -> None:
    """convergence_delta:连续 2 轮 delta < 0.5 且非下降 → 第 3 轮终止(未达标但停滞)。

    评分序列 [7.0, 7.3, 7.4]:
    - 轮 1: 7.0,prev=None → 不判
    - 轮 2: 7.3,delta=0.3 <0.5,delta_history=[0.3],len=1 <2 不触发
    - 轮 3: 7.4,delta=0.1 <0.5,delta_history=[0.3,0.1],len=2,都<0.5,7.4>=7.3 → convergence_delta
    """
    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=9.5, max_iters=5,
        critic=MockCritic([7.0, 7.3, 7.4]), render_fn=_fake_render,
    )
    assert result.converged is False  # 未达标
    assert result.terminate_reason == "convergence_delta"
    assert result.iters == 3
    assert result.scores == (7.0, 7.3, 7.4)


def test_divergence_fallback(ir_path, tmp_path) -> None:
    """divergence_fallback:连续 2 轮降分 → 回退 best-so-far(第 1 轮 IR,score=8.0 最高)。

    评分序列 [8.0, 7.0, 6.0]:
    - 轮 1: 8.0,best=8.0,prev=None
    - 轮 2: 7.0,consecutive_drops=1
    - 轮 3: 6.0,consecutive_drops=2 ≥2 → divergence_fallback,写回 best_ir(轮1)
    """
    original = json.loads(ir_path.read_text(encoding="utf-8"))

    def patcher(_critique, ir):
        # patch 让 IR 变化(模拟每轮调整),best 仍是轮 1
        return [{"op": "replace", "asset_id": "pole", "field": "position",
                 "old_value": ir["assets"][1]["position"], "new_value": [1, 0.5, 9]}]

    result = run_scad_loop(
        ir_path, tmp_path / "a", min_score=9.5, max_iters=5,
        critic=MockCritic([8.0, 7.0, 6.0]), patcher=patcher, render_fn=_fake_render,
    )
    assert result.converged is False
    assert result.terminate_reason == "divergence_fallback"
    assert result.best_score == 8.0
    assert result.iters == 3
    # best-so-far(轮 1 IR = 原始 IR)写回 ir_path
    restored = json.loads(ir_path.read_text(encoding="utf-8"))
    assert restored["assets"][1]["position"] == original["assets"][1]["position"]
