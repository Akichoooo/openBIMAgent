"""环 1 · SCAD 结构快检环(M0,移植自 openBIMForge forge_core/vision_loop)。

对应文档:
- docs/architecture/ARCHITECTURE.md §3 环 1、§9 M0 里程碑(SCAD 环移植)
- docs/architecture/COMPONENTS.md §2.5 vision(收敛四选一 + best-so-far,ADR-0004)
- relay_workspace/m0_spikes/openscad_spike.md(OpenSCAD CLI 实测:--camera translate 形态、--viewall 常驻)

IR(Scene Graph JSON:assets[{id, primitive(cube/cylinder/sphere/cone), size, position, color?}])
→ OpenSCAD 代码(确定性,无 LLM)→ 三视角白模 → critic 评分(只评几何正确性 + 基础构图两维)
→ JSON patch(校验 old_value,不符即拒绝)→ 收敛四选一 + best-so-far 回退(ADR-0004)。
毫秒级迭代,把结构错误挡在 Blender 之外;阈值在 playbook `acceptance.scad_loop`;
超限 ESCALATE 不死循环。每轮 screenshot/score/patch 三类 custom 事件落 SessionStore。
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore
from openbimagent.vision.rubric import SCAD_DIMENSIONS, Critic, CritiqueResult, check_score_payload

DEFAULT_OPENSCAD = Path(r"C:/Program Files/OpenSCAD/openscad.exe")
"""OpenSCAD 2021.01 CLI 默认路径(openscad_spike.md 实测环境)。"""

IMAGE_SIZE: tuple[int, int] = (512, 512)

CAMERA_VIEWS: dict[str, str] = {
    "iso": "0,0,0,55,0,25,140",
    "front": "0,0,0,90,0,0,120",
    "top": "0,0,0,0,0,0,120",
}
"""三视角 --camera 参数(translate 形态:transx,transy,transz,rotx,roty,rotz,distance;spike 实测值)。
--viewall 常驻自动调距包住整个模型,故 distance 只是初值。"""

CONVERGENCE_DELTA: float = 0.5
"""收敛 delta 阈值(0-10 分制,对应 openBIMForge 0-1 分制的 0.05;ADR-0004)。"""

FALLBACK_CONSECUTIVE_DROPS: int = 2
"""连续降分轮数达到该值即 divergence_fallback 回退 best-so-far(先于 delta 判定,防缓慢下降误判)。"""

TERMINATE_REASONS: tuple[str, ...] = ("perfect_score", "convergence_delta", "hard_limit", "divergence_fallback")
"""收敛判定四选一(ADR-0004 语义;COMPONENTS §2.5)。"""

PRIMITIVES: tuple[str, ...] = ("cube", "cylinder", "sphere", "cone")
"""IR 资产支持的图元;size 形态:cube=[x,y,z],cylinder=[r,h],sphere=r,cone=[r1,r2,h]。"""

RenderFn = Callable[[Path, Path], dict[str, Path]]
"""渲染函数形态:(scad_path, out_dir) → {view: png 路径};缺省走 openscad CLI 三视角。"""

PatcherFn = Callable[[CritiqueResult, dict[str, Any]], list[dict[str, Any]]]
"""patch 生成器形态:(critique, 当前 IR) → patch ops;M0 由外部注入(真实 LLM patch 在阶段3b 接线)。"""


# ---------- IR → OpenSCAD(确定性,无 LLM) ----------


def _fmt(value: float) -> str:
    return f"{float(value):.4f}"


def _float_seq(value: Any, length: int, *, field_name: str, asset_id: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"asset {asset_id!r} 的 {field_name} 须为 {length} 元素数组,实收 {value!r}")
    return [float(v) for v in value]


def _asset_scad(asset: dict[str, Any]) -> str:
    asset_id = str(asset.get("id") or "?")
    primitive = asset.get("primitive")
    position = _float_seq(asset.get("position"), 3, field_name="position", asset_id=asset_id)
    # M1 健壮性:position 边界检查(任一维度绝对值 > 1000 视为异常坐标,防 LLM 产出离谱值)
    if any(abs(p) > 1000 for p in position):
        raise ValueError(f"asset {asset_id!r} 的 position 超出合理范围(±1000),实收 {position}")
    size = asset.get("size")
    if primitive == "cube":
        dims = _float_seq(size, 3, field_name="size", asset_id=asset_id)
        body = f"cube([{','.join(map(_fmt, dims))}], center=true)"
    elif primitive == "cylinder":
        radius, height = _float_seq(size, 2, field_name="size", asset_id=asset_id)
        body = f"cylinder(r={_fmt(radius)}, h={_fmt(height)}, center=true)"
    elif primitive == "sphere":
        radius = float(size[0]) if isinstance(size, (list, tuple)) and len(size) == 1 else float(size)
        body = f"sphere(r={_fmt(radius)})"
    elif primitive == "cone":
        r1, r2, height = _float_seq(size, 3, field_name="size", asset_id=asset_id)
        body = f"cylinder(r1={_fmt(r1)}, r2={_fmt(r2)}, h={_fmt(height)}, center=true)"
    else:
        raise ValueError(f"asset {asset_id!r} 的不支持的图元: primitive={primitive!r}(须为 {list(PRIMITIVES)})")
    stmt = f"translate([{','.join(map(_fmt, position))}]) {body};"
    color = asset.get("color")
    if color is not None:
        if isinstance(color, (list, tuple)):
            # M1:RGB 三元组(0-1) → color([r,g,b])
            rgb = _float_seq(color, 3, field_name="color", asset_id=asset_id)
            stmt = f"color([{','.join(map(_fmt, rgb))}]) {stmt}"
        else:
            # 字符串颜色名 → color("name")(M0 行为保留)
            stmt = f'color("{color}") {stmt}'
    return stmt


def ir_to_scad(ir: dict[str, Any]) -> str:
    """Scene Graph IR → OpenSCAD 代码(确定性,无 LLM;同输入必同输出)。

    资产按输入顺序输出;position 为图元中心(center=true);float 定点 %.4f。
    """
    assets = ir.get("assets") if isinstance(ir, dict) else None
    if not isinstance(assets, list) or not assets:
        raise ValueError("IR 须含非空 assets 数组")
    ids = [a.get("id") if isinstance(a, dict) else None for a in assets]
    if any(not i for i in ids):
        raise ValueError("每个 asset 须含非空 id")
    if len(set(ids)) != len(ids):
        raise ValueError(f"asset id 重复: {sorted({i for i in ids if ids.count(i) > 1})}")
    lines = [
        "// SceneGraphIR → OpenSCAD(deterministic, no LLM).",
        f"// assets: {len(assets)}",
        "$fn = 48;",
        "",
    ]
    for asset in assets:
        lines.append(f"// asset: {asset['id']} ({asset.get('primitive')})")
        lines.append(_asset_scad(asset))
    return "\n".join(lines) + "\n"


# ---------- openscad CLI 三视角渲染 ----------


def render_views(
    scad_path: Path,
    out_dir: Path,
    *,
    tag: str,
    openscad: Path | None = None,
    imgsize: tuple[int, int] = IMAGE_SIZE,
    timeout: float = 60.0,
) -> dict[str, Path]:
    """openscad CLI 三视角渲染(--viewall 常驻),返回 {view: png 路径}。

    退出码 0 且产物非空即成功;stderr 的 "Compiling design / CSG tree" 是正常日志(spike 实测)。
    """
    exe = Path(openscad) if openscad is not None else DEFAULT_OPENSCAD
    if not exe.is_file():
        raise FileNotFoundError(f"OpenSCAD 不存在: {exe}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for view, camera in CAMERA_VIEWS.items():
        png = out / f"{tag}_{view}.png"
        cmd = [
            str(exe),
            "-o",
            str(png),
            f"--imgsize={imgsize[0]},{imgsize[1]}",
            f"--camera={camera}",
            "--viewall",
            str(scad_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"openscad 渲染失败({view}):exit={proc.returncode} stderr={proc.stderr[-500:]}")
        if not png.is_file() or png.stat().st_size == 0:
            raise RuntimeError(f"openscad 未产出有效文件: {png}")
        results[view] = png
    return results


# ---------- JSON patch(replace 语义,校验 old_value) ----------


class PatchRejectedError(ValueError):
    """old_value 与当前 IR 不符(或目标缺失/patch 后不可渲染),patch 被整批拒绝。"""


_PATCH_FIELDS: tuple[str, ...] = ("size", "position", "color", "primitive")


def _values_equal(a: Any, b: Any, *, tol: float = 1e-6) -> bool:
    """递归等值比较;数值允许 tol 误差(坐标浮点容差,参考 json_patch_applier._VALUE_TOLERANCE_MM)。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), abs_tol=tol)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_values_equal(x, y, tol=tol) for x, y in zip(a, b))
    return a == b


def apply_ir_patch(ir: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """对 IR 应用 JSON patch(M0 仅 replace:asset_id + field + old_value + new_value)。

    逐 op 校验 old_value,任一不符整批拒绝(PatchRejectedError),入参不被改动;
    全部通过才原子应用,且 patch 后 IR 必须仍可渲染(ir_to_scad 复验)。
    """
    patched = deepcopy(ir)
    by_id = {a.get("id"): a for a in (patched.get("assets") or []) if isinstance(a, dict)}
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") != "replace":
            raise PatchRejectedError(f"op[{i}] 仅支持 replace(M0),实收 {None if not isinstance(op, dict) else op.get('op')!r}")
        asset_id = op.get("asset_id")
        field_name = op.get("field")
        if asset_id not in by_id:
            raise PatchRejectedError(f"op[{i}] 目标 asset {asset_id!r} 不存在")
        if field_name not in _PATCH_FIELDS:
            raise PatchRejectedError(f"op[{i}] field 须为 {list(_PATCH_FIELDS)},实收 {field_name!r}")
        if "old_value" not in op or "new_value" not in op:
            raise PatchRejectedError(f"op[{i}] 缺 old_value/new_value(防 LLM 误判当前状态)")
        current = by_id[asset_id].get(field_name)
        if not _values_equal(current, op["old_value"]):
            raise PatchRejectedError(
                f"op[{i}] old_value 校验失败:{asset_id}.{field_name} 当前值 {current!r} ≠ 声明 {op['old_value']!r}"
            )
    for op in ops:
        by_id[op["asset_id"]][op["field"]] = deepcopy(op["new_value"])
    try:
        ir_to_scad(patched)
    except (ValueError, TypeError) as exc:
        raise PatchRejectedError(f"patch 后 IR 不可渲染,整批拒绝: {exc}") from exc
    return patched


# ---------- JSON Patch(RFC 6902 子集,M1) ----------


class PatchValidationError(ValueError):
    """JSON Patch(RFC 6902)校验失败:old_value 不匹配 / 路径不存在 / 操作非法 / op 后不可渲染。"""


def _parse_pointer(pointer: str) -> list[int | str]:
    """解析 JSON Pointer(RFC 6901):/assets/0/position/1 → ['assets', 0, 'position', 1]。"""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise PatchValidationError(f"JSON Pointer 须以 / 开头,实收 {pointer!r}")
    if pointer == "/":
        return []
    tokens: list[int | str] = []
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if token.lstrip("-").isdigit():
            tokens.append(int(token))
        else:
            tokens.append(token)
    return tokens


def _resolve_pointer(doc: Any, tokens: list[int | str]) -> tuple[Any, int | str | None, Any, bool]:
    """沿 tokens 解析;返回 (parent, last_key, current_value, exists)。

    exists=False 表示路径中断(parent 仍指向最后可达容器,last_key 为断点 token)。
    """
    if not tokens:
        return doc, None, doc, True
    current = doc
    parent: Any = None
    last_key: int | str | None = None
    for token in tokens:
        parent = current
        last_key = token
        if isinstance(parent, list):
            if not isinstance(token, int) or token < 0 or token >= len(parent):
                return parent, last_key, None, False
            current = parent[token]
        elif isinstance(parent, dict):
            if not isinstance(token, str) or token not in parent:
                return parent, last_key, None, False
            current = parent[token]
        else:
            return parent, last_key, None, False
    return parent, last_key, current, True


def apply_patch(ir: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """对 IR 应用 JSON Patch(RFC 6902 子集:replace / add / remove;M1 任务 B2)。

    操作语义:
    - replace:path 须存在;old_value 须与当前值相等(浮点容差 1e-6),否则 PatchValidationError。
    - add:path 不存在则插入(list 末尾追加 / dict 设键),存在则覆盖。
    - remove:path 须存在,删除。
    原子性:deepcopy 入参,任一 op 失败整批拒绝(抛 PatchValidationError,入参不被改动);
    全部 op 应用后再用 ir_to_scad 复验可渲染性,不可渲染亦整批拒绝。
    """
    patched = deepcopy(ir)
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise PatchValidationError(f"op[{i}] 须为 dict,实收 {type(op).__name__}")
        op_type = op.get("op")
        path = op.get("path")
        if not isinstance(path, str):
            raise PatchValidationError(f"op[{i}] 缺 path 或类型非 str,实收 {path!r}")
        tokens = _parse_pointer(path)
        if op_type == "replace":
            if "old_value" not in op:
                raise PatchValidationError(f"op[{i}] replace 缺 old_value(防 LLM 误判当前状态)")
            if "value" not in op:
                raise PatchValidationError(f"op[{i}] replace 缺 value")
            parent, last_key, current, exists = _resolve_pointer(patched, tokens)
            if not exists or last_key is None:
                raise PatchValidationError(f"op[{i}] replace path {path!r} 不存在")
            if not _values_equal(current, op["old_value"]):
                raise PatchValidationError(
                    f"op[{i}] old_value 不匹配:path {path!r} 当前值 {current!r} ≠ 声明 {op['old_value']!r}"
                )
            if isinstance(parent, (list, dict)):
                parent[last_key] = deepcopy(op["value"])
            else:
                raise PatchValidationError(f"op[{i}] replace 目标 {path!r} 父节点非容器")
        elif op_type == "add":
            if "value" not in op:
                raise PatchValidationError(f"op[{i}] add 缺 value")
            parent, last_key, current, exists = _resolve_pointer(patched, tokens)
            if isinstance(parent, list):
                if exists and isinstance(last_key, int):
                    parent[last_key] = deepcopy(op["value"])  # 已存在则覆盖
                else:
                    parent.append(deepcopy(op["value"]))  # 不存在则末尾追加
            elif isinstance(parent, dict):
                parent[str(last_key) if last_key is not None else ""] = deepcopy(op["value"])
            else:
                raise PatchValidationError(f"op[{i}] add 目标 {path!r} 父节点非容器")
        elif op_type == "remove":
            parent, last_key, current, exists = _resolve_pointer(patched, tokens)
            if not exists or last_key is None:
                raise PatchValidationError(f"op[{i}] remove path {path!r} 不存在")
            if isinstance(parent, (list, dict)):
                del parent[last_key]
            else:
                raise PatchValidationError(f"op[{i}] remove 目标 {path!r} 父节点非容器")
        else:
            raise PatchValidationError(f"op[{i}] 不支持的操作: {op_type!r}(仅 replace/add/remove)")
    try:
        ir_to_scad(patched)
    except (ValueError, TypeError) as exc:
        raise PatchValidationError(f"patch 后 IR 不可渲染,整批拒绝: {exc}") from exc
    return patched


# ---------- 主循环 ----------


@dataclass(frozen=True)
class ScadLoopResult:
    """SCAD 环收敛结果;best_snapshot 为 best-so-far 回退点(ADR-0004)。"""

    converged: bool
    best_score: float
    best_snapshot: Path | None
    iters: int
    terminate_reason: str = ""  # TERMINATE_REASONS 四选一
    scores: tuple[float, ...] = ()  # 每轮 overall_score(两维均值)


def run_scad_loop(
    ir_path: Path,
    work_dir: Path,
    *,
    min_score: float,
    max_iters: int,
    critic: Critic | None = None,
    patcher: PatcherFn | None = None,
    session: SessionStore | None = None,
    render_fn: RenderFn | None = None,
    openscad: Path | None = None,
) -> ScadLoopResult:
    """执行 SCAD 快检环直到收敛(≥min_score)或耗尽 max_iters。

    每次迭代:渲染三视角 → critic 评分落盘(check_score_payload 防放水校验)
    → JSON patch 应用(校验 old_value,拒绝则回退 best-so-far)。
    收敛判定四选一:perfect_score(≥min_score)/ convergence_delta / hard_limit / divergence_fallback;
    best-so-far 快照随轮更新(best_ir.json),divergence_fallback 时把它写回 ir_path(ADR-0004)。
    session 非空时每轮落 screenshot(×3)/ score/ patch 三类 custom 事件。
    """
    if critic is None:
        raise NotImplementedError("critic 未注入:真实场景注入 VLMCritic(vision.critic),测试/联调注入 MockCritic")
    ir_path = Path(ir_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    ir = json.loads(ir_path.read_text(encoding="utf-8"))

    best_ir = deepcopy(ir)
    best_score = -1.0
    best_snapshot: Path | None = None
    prev_score: float | None = None
    consecutive_drops = 0
    scores: list[float] = []
    delta_history: list[float] = []  # M1:连续 2 轮 delta < CONVERGENCE_DELTA 才判 convergence_delta(ADR-0004)
    prev_images: list[Path] = []
    terminate_reason = ""
    converged = False
    iteration = 0

    for iteration in range(1, max(1, max_iters) + 1):
        # 1. IR → scad → 三视角白模
        scad_path = work_dir / f"scene_iter{iteration}.scad"
        scad_path.write_text(ir_to_scad(ir), encoding="utf-8")
        if render_fn is not None:
            images = render_fn(scad_path, work_dir)
        else:
            images = render_views(scad_path, work_dir, tag=f"iter{iteration}", openscad=openscad)
        image_paths = [images[v] for v in CAMERA_VIEWS if v in images]

        # 2. screenshot 事件落盘(每视角一条)
        if session is not None:
            for view, png in images.items():
                session.append_new(
                    EventType.CUSTOM,
                    {
                        "customType": "screenshot",
                        "camera_view": view,
                        "image_path": str(png),
                        "phase": "scad",
                        "iteration": iteration,
                    },
                )

        # 3. critic 评分(注入;A/B swap 上下文 = 上轮截图 + best-so-far 快照引用)
        context = {
            "iteration": iteration,
            "ir": deepcopy(ir),
            "previous_image_paths": prev_images,
            "ab_swap_ref": str(best_snapshot) if best_snapshot is not None else None,
        }
        critique = critic.critique(image_paths, SCAD_DIMENSIONS, context)
        payload = critique.to_score_payload(phase="scad")
        payload["iteration"] = iteration
        payload["overall_score"] = critique.overall_score
        if "ab_swap_ref" not in payload and best_snapshot is not None:
            payload["ab_swap_ref"] = str(best_snapshot)
        check_score_payload(payload, phase="scad")  # 防放水留痕校验:不过即失败(拒放水评分进环)
        if session is not None:
            session.append_new(EventType.CUSTOM, payload)

        score = critique.overall_score
        scores.append(score)

        # 4. best-so-far 快照(ADR-0004)
        if score > best_score:
            best_score = score
            best_ir = deepcopy(ir)
            best_snapshot = work_dir / "best_ir.json"
            best_snapshot.write_text(json.dumps(best_ir, ensure_ascii=False, indent=2), encoding="utf-8")

        # 5. 收敛判定(四选一;顺序同 ADR-0004:fallback 先于 delta,防缓慢下降误判)
        if score >= min_score:
            terminate_reason, converged = "perfect_score", True
            break
        if prev_score is not None:
            consecutive_drops = consecutive_drops + 1 if score < prev_score else 0
            if consecutive_drops >= FALLBACK_CONSECUTIVE_DROPS:
                terminate_reason = "divergence_fallback"
                ir_path.write_text(json.dumps(best_ir, ensure_ascii=False, indent=2), encoding="utf-8")
                break
            delta = abs(score - prev_score)
            delta_history.append(delta)
            # M1:连续 2 轮 delta < CONVERGENCE_DELTA 且非下降才判 convergence_delta
            # (ADR-0004:单轮 delta 小可能是 patch 微动,连续 2 轮停滞才视为真正收敛)
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

        # 6. JSON patch(校验 old_value;拒绝则回退 best-so-far 继续,不崩环)
        ops = patcher(critique, deepcopy(ir)) if patcher is not None else []
        if ops:
            try:
                ir = apply_ir_patch(ir, ops)
                patch_status, patch_error = "applied", None
            except PatchRejectedError as exc:
                ir = deepcopy(best_ir)
                patch_status, patch_error = "rejected", str(exc)
            if session is not None:
                patch_payload: dict[str, Any] = {
                    "customType": "patch",
                    "target_file": str(ir_path),
                    "diff": json.dumps(ops, ensure_ascii=False),
                    "iteration": iteration,
                    "status": patch_status,
                }
                if patch_error:
                    patch_payload["error"] = patch_error
                session.append_new(EventType.CUSTOM, patch_payload)
        prev_images = image_paths

    return ScadLoopResult(
        converged=converged,
        best_score=best_score,
        best_snapshot=best_snapshot,
        iters=iteration,
        terminate_reason=terminate_reason,
        scores=tuple(scores),
    )
