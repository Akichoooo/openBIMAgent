"""Save invalid engineering drafts; validate them against one frozen rule context.

This module stores no revisions or confirmation state and never changes a draft.
Executable means the declared small gravity network checks passed. Hydraulics,
structural design, exact pipe end solids and unprovided terrain are not evaluated.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from openbimagent.utility.contracts import CompiledUtilityIR, StrictFrozenModel
from openbimagent.utility.design_limits import MunicipalDesignLimits, compile_municipal_design_limits
from openbimagent.utility.geometry import closest_segment_box_points, closest_segment_points, execution_geometry, resolve_dimensions
from openbimagent.utility.network_solver import (
    EXCEPTION_MIN_COVER_M,
    INVERT_TOLERANCE_M,
    SUPPORTED_DIAMETER_MM,
    NetworkGravitySolverInput,
    lookup_pipe_normative_limits,
    solve_network_gravity_utility,
)
from openbimagent.utility.rules import MunicipalRuleSet, compile_municipal_rule_set
from openbimagent.utility.solver import AxisAlignedBoxObstacle, CollisionContext, UtilitySolverError
from openbimagent.utility.compiler import UtilityCompileError
from openbimagent.utility.spatial_graph import SpatialBIMGraph


class UtilityCandidate(StrictFrozenModel):
    protocol_version: Literal["1.0"] = "1.0"
    solver_input: dict[str, Any]

    @model_validator(mode="after")
    def _json_only(self) -> "UtilityCandidate":
        json.dumps(self.solver_input, allow_nan=False)
        return self

    def canonical_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class FrozenRuleContext(StrictFrozenModel):
    protocol_version: Literal["1.0"] = "1.0"
    municipal_rule_set: MunicipalRuleSet
    design_limits: MunicipalDesignLimits

    def canonical_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_rule_context() -> FrozenRuleContext:
    """Compile current sources once per run; callers may save and restore this JSON."""
    return FrozenRuleContext(municipal_rule_set=compile_municipal_rule_set(), design_limits=compile_municipal_design_limits())


class UtilityIssue(StrictFrozenModel):
    issue_id: str
    object_ids: tuple[str, ...] = ()
    rule_id: str
    check_name: str
    status: Literal["pass", "fail", "unknown"]
    severity: Literal["error", "warning", "info"]
    detail: str
    measured_value: float | str | bool | None = None
    required_value: float | str | bool | None = None
    unit: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    field_path: str | None = None
    repairable_parameters: tuple[str, ...] = ()


CHECK_SCOPE = ("input_valid", "diameter_in_spec", "slope_in_spec", "cover_depth_in_spec", "manhole_spacing_in_spec", "clash_free", "pipe_geometry_known", "network_geometry_in_spec", "physical_obstacle_clearance")


class CandidateValidationResult(StrictFrozenModel):
    protocol_version: Literal["1.0"] = "1.0"
    candidate_sha256: str
    compiled_ir: CompiledUtilityIR | None = None
    issues: tuple[UtilityIssue, ...]
    rule_context: FrozenRuleContext
    rule_context_sha256: str
    executable: bool
    check_scope: tuple[str, ...] = CHECK_SCOPE
    not_evaluated: tuple[str, ...] = ("hydraulics", "structural_design", "exact_open_pipe_end_solids", "terrain_between_nodes")


def _pointer(loc: tuple[Any, ...]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in loc)


def validate_utility_candidate(candidate: UtilityCandidate | dict[str, Any], *, rule_context: FrozenRuleContext | dict[str, Any] | None = None) -> CandidateValidationResult:
    draft = candidate if isinstance(candidate, UtilityCandidate) else UtilityCandidate.model_validate(candidate)
    context = freeze_rule_context() if rule_context is None else FrozenRuleContext.model_validate(rule_context)
    issues: list[UtilityIssue] = []
    compiled = None
    try:
        request = NetworkGravitySolverInput.model_validate(draft.solver_input)
    except ValidationError as exc:
        for index, error in enumerate(exc.errors(include_url=False, include_context=False)):
            loc = error["loc"]
            path = _pointer(loc)
            object_ids = ()
            if len(loc) > 1 and loc[0] in ("nodes", "segments") and isinstance(loc[1], int):
                item = draft.solver_input.get(loc[0], [])[loc[1]]
                object_ids = (str(item.get("node_id" if loc[0] == "nodes" else "segment_id", path)),) if isinstance(item, dict) else ()
            negative_slope = bool(loc) and loc[-1] == "design_slope"
            issues.append(UtilityIssue(issue_id=f"input-{index}", object_ids=object_ids, rule_id="MU-DRAIN-004" if negative_slope else "INPUT-CONTRACT", check_name="input_valid", status="fail", severity="error", detail=error["msg"], field_path=path, measured_value=error.get("input") if isinstance(error.get("input"), (str, float, int, bool)) else None, required_value=context.design_limits.min_slope_sewage_d300_concrete.limit_value if negative_slope and context.design_limits.min_slope_sewage_d300_concrete else None, repairable_parameters=(path,) if negative_slope else (), evidence={"error_type": error["type"]}))
    else:
        try:
            result = solve_network_gravity_utility(request, municipal_rule_set=context.municipal_rule_set, design_limits=context.design_limits)
            compiled = result.compiled_ir
        except (UtilitySolverError, UtilityCompileError, ValueError) as exc:
            issues.append(UtilityIssue(issue_id="solver-contract", rule_id="NETWORK-CONTRACT", check_name="input_valid", status="fail", severity="error", detail=str(exc)))
        if compiled is not None:
            segment_indices = {segment.segment_id: index for index, segment in enumerate(request.segments)}
            for item in compiled.evidence:
                index = segment_indices.get(item.subject_id)
                base = f"/segments/{index}" if index is not None else None
                field = {"slope_in_spec": "design_slope", "diameter_in_spec": "diameter_mm"}.get(item.check_name)
                paths = (f"{base}/{field}",) if base and field else ()
                if item.check_name == "cover_depth_in_spec":
                    paths = tuple(f"/nodes/{i}/invert_anchor_m" for i, node in enumerate(request.nodes) if node.invert_anchor_m is not None)
                evidence = item.model_dump(mode="json")
                if item.check_name == "clash_free":
                    evidence["clearance_metric"] = "normative_horizontal_surface_clearance"
                issues.append(UtilityIssue(issue_id=item.evidence_id, object_ids=(item.subject_id,), rule_id=item.rule_id, check_name=item.check_name, status=item.status.value, severity="info" if item.status.value == "pass" or item.check_name not in CHECK_SCOPE else "error", detail=item.detail, measured_value=item.measured_value, required_value=item.limit_value, unit=item.unit, evidence=evidence, field_path=paths[0] if len(paths) == 1 else None, repairable_parameters=paths))
            for segment in compiled.segments:
                try:
                    execution_geometry(segment)
                except ValueError as exc:
                    index = segment_indices[segment.segment_id]
                    issues.append(UtilityIssue(issue_id=f"geometry-{segment.segment_id}", object_ids=(segment.segment_id,), rule_id="PIPE-GEOMETRY", check_name="pipe_geometry_known", status="unknown", severity="error", detail=str(exc), field_path=f"/segments/{index}", evidence={"centerline_reference": segment.centerline_reference}))
            graph = SpatialBIMGraph.build_from_ir(compiled)
            connection_radii = {node.node_id: node.connection_radius_m for node in request.nodes if node.connection_radius_m is not None}
            for crossing in graph.find_crossings(connection_exemptions_m=connection_radii):
                issues.append(UtilityIssue(issue_id=f"pair-{crossing.segment_a}-{crossing.segment_b}", object_ids=(crossing.segment_a, crossing.segment_b), rule_id="PHYSICAL-NONOVERLAP", check_name="network_geometry_in_spec", status=crossing.status, severity="info" if crossing.status == "pass" else "error", detail=crossing.detail, measured_value=crossing.surface_clearance_m, required_value=0.0, unit="m", evidence={"axis_distance_3d_m": crossing.euclidean_min_distance_m, "horizontal_axis_distance_m": crossing.horizontal_distance_m, "connection_radii_m": connection_radii}))
            issues.extend(_physical_obstacle_issues(compiled, request.collision_context))
    executable = compiled is not None and not any(item.status != "pass" and item.check_name in CHECK_SCOPE for item in issues)
    return CandidateValidationResult(candidate_sha256=draft.canonical_sha256(), compiled_ir=compiled, issues=tuple(issues), rule_context=context, rule_context_sha256=context.canonical_sha256(), executable=executable)


def _physical_obstacle_issues(compiled: CompiledUtilityIR, context: CollisionContext | None) -> list[UtilityIssue]:
    """Keep 3D envelope nonoverlap independent of horizontal clearance rules."""
    if context is None:
        return [UtilityIssue(issue_id="physical-context", rule_id="PHYSICAL-OBSTACLE", check_name="physical_obstacle_clearance", status="unknown", severity="error", detail="Obstacle coverage is unknown; 3D physical nonoverlap cannot be established.")]
    issues = []
    for segment in compiled.segments:
        try:
            axis, outer = execution_geometry(segment)
        except ValueError:
            axis, outer = None, None
        for obstacle in context.obstacles:
            status = "unknown"
            surface = None
            detail = "Physical dimensions or axis position are unknown."
            evidence: dict[str, Any] = {"clearance_metric": "3d_outer_envelope", "envelope": "capsule with hemispherical end caps", "intersection_volume_computed": False}
            if axis is not None and outer is not None:
                points = [(point.x_m, point.y_m, point.z_m) for point in axis]
                if isinstance(obstacle, AxisAlignedBoxObstacle):
                    minimum = (obstacle.min_corner.x_m, obstacle.min_corner.y_m, obstacle.min_corner.z_m)
                    maximum = (obstacle.max_corner.x_m, obstacle.max_corner.y_m, obstacle.max_corner.z_m)
                    nearest = min((closest_segment_box_points(a, b, minimum, maximum) for a, b in zip(points, points[1:], strict=False)), key=lambda item: item.distance_m)
                    surface = nearest.distance_m - outer / 2000.0
                    interior_axis = 1e-8 < nearest.parameter < 1 - 1e-8
                    on_edges = sum(abs(value - low) < 1e-8 or abs(value - high) < 1e-8 for value, low, high in zip(nearest.box_point, minimum, maximum, strict=True)) >= 2
                    exact_interior_witness = interior_axis and not on_edges
                    evidence.update(axis_distance_m=nearest.distance_m, axis_parameter=nearest.parameter, axis_point=nearest.segment_point, obstacle_point=nearest.box_point)
                else:
                    start = (obstacle.start_center.x_m, obstacle.start_center.y_m, obstacle.start_center.z_m)
                    end = (obstacle.end_center.x_m, obstacle.end_center.y_m, obstacle.end_center.z_m)
                    nearest = min((closest_segment_points(a, b, start, end) for a, b in zip(points, points[1:], strict=False)), key=lambda item: item.distance_m)
                    surface = nearest.distance_m - (outer + obstacle.outer_diameter_mm) / 2000.0
                    exact_interior_witness = 1e-8 < nearest.first_parameter < 1 - 1e-8 and 1e-8 < nearest.second_parameter < 1 - 1e-8
                    evidence.update(axis_distance_m=nearest.distance_m, axis_parameter=nearest.first_parameter, obstacle_parameter=nearest.second_parameter)
                if surface >= -1e-6:
                    status = "pass"
                    detail = "Conservative 3D outer envelopes have no overlap within 1e-6 m tolerance; horizontal code clearance is evaluated separately."
                elif exact_interior_witness:
                    status = "fail"
                    detail = "Interior outer envelopes overlap in 3D; this is an envelope conflict and does not compute material intersection volume."
                else:
                    detail = "Conservative capsule end cap or box edge/corner envelope overlaps; exact pipe end/solid geometry is required to decide physical interference."
            issues.append(UtilityIssue(issue_id=f"physical-{segment.segment_id}-{obstacle.obstacle_id}", object_ids=(segment.segment_id, obstacle.obstacle_id), rule_id="PHYSICAL-OBSTACLE", check_name="physical_obstacle_clearance", status=status, severity="info" if status == "pass" else "error", detail=detail, measured_value=surface, required_value=0.0, unit="m", evidence=evidence))
    if not context.obstacles:
        issues.append(UtilityIssue(issue_id="physical-empty-context", rule_id="PHYSICAL-OBSTACLE", check_name="physical_obstacle_clearance", status="pass", severity="info", detail="The caller declares complete obstacle coverage and an empty obstacle list.", evidence={"clearance_metric": "3d_outer_envelope", "coverage": "complete"}))
    return issues


def compiled_to_candidate(ir: CompiledUtilityIR | dict[str, Any]) -> UtilityCandidate:
    """Import a compiled IR into an editable draft while preserving identities and provenance.

    No ground elevation, pipe physical dimension, obstacle, or connection radius is
    inferred. Fields unavailable in the compiled artifact are explicit ``None`` so
    the next validation report points at the exact fields requiring user input.
    """
    compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
    if len(compiled.systems) != 1 or compiled.systems[0].system_type.value != "wastewater" or compiled.systems[0].flow_regime.value != "gravity":
        raise ValueError("candidate import supports one wastewater gravity system only")
    if any(len(segment.centerline) != 2 for segment in compiled.segments):
        raise ValueError("candidate import cannot flatten polyline routes into straight pipes")
    system = compiled.systems[0]
    node_by_port = {port.port_id: node for node in compiled.nodes for port in node.ports}
    nodes: list[dict[str, Any]] = []
    for node in compiled.nodes:
        outgoing = [port for port in node.ports if getattr(port.direction, "value", port.direction) in ("outlet", "bidirectional")]
        outgoing_inverts = {port.position.z_m for port in outgoing}
        anchor = next(iter(outgoing_inverts)) if len(outgoing_inverts) == 1 else None
        nodes.append({
            "node_id": node.node_id,
            "node_type": getattr(node.node_type, "value", node.node_type),
            "x_m": node.position.x_m,
            "y_m": node.position.y_m,
            "ground_elevation_m": node.ground_elevation_m,
            "invert_anchor_m": anchor,
            "connection_radius_m": node.connection_radius_m,
        })
    segments: list[dict[str, Any]] = []
    for segment in compiled.segments:
        start_node = node_by_port[segment.start_port_id]
        end_node = node_by_port[segment.end_port_id]
        segments.append({
            "segment_id": segment.segment_id,
            "start_node_id": start_node.node_id,
            "end_node_id": end_node.node_id,
            "diameter_mm": segment.diameter_mm,
            "material": segment.material,
            "design_slope": segment.slope,
            "surface_context": None,
            "inner_diameter_mm": segment.inner_diameter_mm,
            "outer_diameter_mm": segment.outer_diameter_mm,
            "wall_thickness_mm": segment.wall_thickness_mm,
        })
    return UtilityCandidate(solver_input={
        "protocol_version": "0.1",
        "request_id": f"import-{compiled.ir_id}",
        "source_ir_sha256": compiled.source_ir_sha256,
        "coordinate_reference": compiled.coordinate_reference.model_dump(mode="json"),
        "system_id": system.system_id,
        "system_name": system.name,
        "nodes": nodes,
        "segments": segments,
        "collision_context": None,
    })


def path_is_locked(path: str, locked_paths: tuple[str, ...] | list[str]) -> bool:
    return any(path == lock or path.startswith(lock.rstrip("/") + "/") or lock.startswith(path.rstrip("/") + "/") for lock in locked_paths)


# =====================================================================================
# CEGIS 多分支拓扑修复沙箱：三级刚柔约束 + 工程综合造价 + 帕累托比选
# =====================================================================================
#
# 三级刚柔约束体系：
#   Tier 1（硬红线）—— 冻结规则上下文中的国标限值（最小坡度/覆土/井距/净距），
#       由 ``validate_utility_candidate`` 失败关闭判定，任何自动修复不得放宽；
#   Tier 2（国标受控特批）—— 满包混凝土加固（concrete_encasement）或显式
#       ``constraints.allow_exception_approval`` 授权时，最小覆土红线可从 0.7m
#       受控放宽至 0.45m，且必须携带 exception_approval_id 举证；
#   Tier 3（工程偏好）—— 不违反 Tier 1/2 的前提下按 ``compute_engineering_cost``
#       的 J(S) 综合造价做帕累托比选（土方 vs 材料 vs 井数）。

DEFAULT_EXCEPTION_APPROVAL_ID = "EXC-MU-ELEV-001-ENCASEMENT"
"""Tier-2 特批默认票据号（可被 segment.exception_approval_id 覆盖）。"""

SANDBOX_OPERATORS: tuple[str, ...] = (
    "op_slope_regrade",
    "op_drop_manhole",
    "op_lateral_detour",
    "op_insert_intermediate_manhole",
    "op_concrete_encasement_exception",
)
"""CEGIS 沙箱的 5 大工程拓扑修复算子（按默认优先级排序）。"""

CLASH_CHECK_NAMES = frozenset({"clash_free", "physical_obstacle_clearance", "network_geometry_in_spec"})

#: 材料造价加权（指数单位）：每米管、每座检查井、每座跌水井、每段满包加固。
_MATERIAL_WEIGHTS = {"pipe_per_m": 0.9, "manhole": 12.0, "drop_manhole": 8.0, "concrete_encasement": 15.0}


def _solver_payload(candidate: UtilityCandidate | dict[str, Any]) -> dict[str, Any]:
    """接受 UtilityCandidate、``{"solver_input": ...}`` 包装或裸 solver_input dict。"""
    if isinstance(candidate, UtilityCandidate):
        return candidate.solver_input
    if isinstance(candidate, dict):
        inner = candidate.get("solver_input")
        if isinstance(inner, dict):
            return inner
        return candidate
    raise TypeError(f"candidate 必须是 dict 或 UtilityCandidate，得到 {type(candidate)!r}")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _solve_network_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    """对裸 solver_input dict 做与 Network Solver 同构的宽容标高传播。

    与 Pydantic 失败关闭路径不同，本函数用于造价/修复沙箱：缺字段按 0 处理、
    环或断图如实返回 ``ok=False``，绝不抛出。返回结构::

        {"ok": bool, "error": str | None,
         "natural_invert": {node_id: min 来管管底},
         "outgoing_invert": {node_id: 出流管底},
         "segments": {segment_id: {"length_m", "start_invert_m", "end_invert_m",
                                   "start_node_id", "end_node_id", "slope", "diameter_mm"}}}
    """
    nodes: dict[str, dict[str, Any]] = {}
    for item in payload.get("nodes") or []:
        if isinstance(item, dict) and isinstance(item.get("node_id"), str):
            nodes[item["node_id"]] = item
    segments: list[dict[str, Any]] = [s for s in payload.get("segments") or [] if isinstance(s, dict)]
    result: dict[str, Any] = {
        "ok": False, "error": None, "natural_invert": {}, "outgoing_invert": {}, "segments": {},
    }
    if len(nodes) < 2 or not segments:
        result["error"] = "nodes/segments 不足"
        return result
    incoming: dict[str, list[dict[str, Any]]] = {nid: [] for nid in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {nid: [] for nid in nodes}
    referenced: set[str] = set()
    for segment in segments:
        start, end = segment.get("start_node_id"), segment.get("end_node_id")
        if start in nodes and end in nodes and start != end:
            incoming[end].append(segment)
            outgoing[start].append(segment)
            referenced.update({start, end})
    if referenced != set(nodes):
        result["error"] = "存在孤立或引用未知节点"
        return result
    remaining = {nid: len(incoming[nid]) for nid in nodes}
    ready = sorted(nid for nid, deg in remaining.items() if deg == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for segment in sorted(outgoing[current], key=lambda s: str(s.get("segment_id"))):
            child = segment["end_node_id"]
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(nodes):
        result["error"] = "存在有向环路"
        return result
    solved: dict[str, dict[str, float]] = {}
    for nid in order:
        node = nodes[nid]
        incoming_inverts = [solved[s["segment_id"]]["end_invert_m"] for s in incoming[nid] if s["segment_id"] in solved]
        natural = min(incoming_inverts) if incoming_inverts else None
        if natural is not None:
            result["natural_invert"][nid] = natural
        anchor = _finite(node.get("invert_anchor_m"))
        if anchor is not None:
            outgoing_invert = anchor
        elif natural is not None:
            outgoing_invert = natural
        else:
            result["error"] = f"源节点 {nid!r} 缺少 invert_anchor_m"
            return result
        result["outgoing_invert"][nid] = outgoing_invert
        for segment in sorted(outgoing[nid], key=lambda s: str(s.get("segment_id"))):
            start_node, end_node = nodes[segment["start_node_id"]], nodes[segment["end_node_id"]]
            length = math.hypot(
                (_finite(end_node.get("x_m")) or 0.0) - (_finite(start_node.get("x_m")) or 0.0),
                (_finite(end_node.get("y_m")) or 0.0) - (_finite(start_node.get("y_m")) or 0.0),
            )
            slope = max(0.0, _finite(segment.get("design_slope")) or 0.0)
            solved[str(segment.get("segment_id"))] = {
                "length_m": length,
                "start_invert_m": outgoing_invert,
                "end_invert_m": outgoing_invert - slope * length,
                "start_node_id": segment["start_node_id"],
                "end_node_id": segment["end_node_id"],
                "slope": slope,
                "diameter_mm": _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM,
            }
    result["segments"] = solved
    result["ok"] = True
    return result


def _segment_crown_offset_m(segment: dict[str, Any]) -> float | None:
    try:
        inner, outer = resolve_dimensions(
            segment.get("inner_diameter_mm"), segment.get("outer_diameter_mm"), segment.get("wall_thickness_mm")
        )
    except ValueError:
        return None
    if inner is None or outer is None:
        return None
    return (inner + outer) / 2000.0


def _obstacle_surface_clearance_m(
    axis: list[tuple[float, float, float]], outer_diameter_mm: float, obstacle: dict[str, Any]
) -> float | None:
    """管轴线（含管顶偏置）与障碍物外包络的最近表面净距（m）；无法计算返回 None。"""
    outer_radius = outer_diameter_mm / 2000.0
    minimum_distance = None
    for a, b in zip(axis, axis[1:], strict=False):
        if obstacle.get("kind") == "aabb" and isinstance(obstacle.get("min_corner"), dict):
            low = obstacle["min_corner"]
            high = obstacle.get("max_corner") or {}
            try:
                nearest = closest_segment_box_points(
                    a, b,
                    (float(low["x_m"]), float(low["y_m"]), float(low["z_m"])),
                    (float(high["x_m"]), float(high["y_m"]), float(high["z_m"])),
                )
            except (KeyError, TypeError, ValueError):
                return None
            distance = nearest.distance_m
        else:
            start, end = obstacle.get("start_center"), obstacle.get("end_center")
            if not isinstance(start, dict) or not isinstance(end, dict):
                return None
            try:
                nearest = closest_segment_points(
                    a, b,
                    (float(start["x_m"]), float(start["y_m"]), float(start["z_m"])),
                    (float(end["x_m"]), float(end["y_m"]), float(end["z_m"])),
                )
            except (KeyError, TypeError, ValueError):
                return None
            distance = nearest.distance_m - (_finite(obstacle.get("outer_diameter_mm")) or 0.0) / 2000.0
        if minimum_distance is None or distance < minimum_distance:
            minimum_distance = distance
    if minimum_distance is None:
        return None
    return minimum_distance - outer_radius


def compute_engineering_cost(candidate: dict[str, Any]) -> dict[str, Any]:
    """工程综合造价函数 J(S)：土方开挖 + 管井/加固材料的加权指数与安全裕度。

    - ``excavation_cost_index``：逐段累加 ``L_e · (D_e + 0.6) · h̄_e²``，其中平均埋深
      ``h̄_e = max(0, z_ground - z̄_invert)``（管底平均高程与地面高程之差）；
    - ``material_cost_index``：总管长、检查井数、跌水井数（``manhole_type ==
      "drop_manhole"``）与满包混凝土加固段数（``protection_measure ==
      "concrete_encasement"``）的加权和；
    - ``total_cost_score = round(excavation + material, 4)``；
    - 同时返回最小覆土裕度与最小障碍物净距裕度（无数据时为 None）。
    """
    payload = _solver_payload(candidate)
    geometry = _solve_network_geometry(payload)
    nodes = {n.get("node_id"): n for n in payload.get("nodes") or [] if isinstance(n, dict) and n.get("node_id")}
    segments = [s for s in payload.get("segments") or [] if isinstance(s, dict)]
    excavation = 0.0
    total_length = 0.0
    cover_margins: list[float] = []
    clearance_margins: list[float] = []
    for segment in segments:
        solved = geometry["segments"].get(str(segment.get("segment_id")))
        surface = str(segment.get("surface_context") or "driveway")
        diameter_mm = _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM
        crown = _segment_crown_offset_m(segment)
        if solved is not None:
            length = solved["length_m"]
            start_node, end_node = nodes.get(solved["start_node_id"]), nodes.get(solved["end_node_id"])
            if start_node and end_node:
                ground = ((_finite(start_node.get("ground_elevation_m")) or 0.0) + (_finite(end_node.get("ground_elevation_m")) or 0.0)) / 2.0
                mean_invert = (solved["start_invert_m"] + solved["end_invert_m"]) / 2.0
                mean_depth = max(0.0, ground - mean_invert)
                excavation += length * (diameter_mm / 1000.0 + 0.6) * mean_depth * mean_depth
            total_length += length
            if crown is not None and start_node and end_node:
                try:
                    norm = lookup_pipe_normative_limits(diameter_mm, surface)
                except ValueError:
                    norm = {"min_cover_m": 0.7}
                covers = (
                    (_finite(start_node.get("ground_elevation_m")) or 0.0) - solved["start_invert_m"],
                    (_finite(end_node.get("ground_elevation_m")) or 0.0) - solved["end_invert_m"],
                )
                cover_margins.append(min(covers) - crown - float(norm["min_cover_m"]))
    manhole_count = sum(1 for n in payload.get("nodes") or [] if isinstance(n, dict) and n.get("node_type") == "manhole")
    drop_count = sum(1 for n in payload.get("nodes") or [] if isinstance(n, dict) and n.get("manhole_type") == "drop_manhole")
    encased = sum(1 for s in segments if s.get("protection_measure") == "concrete_encasement")
    obstacles_raw = ((payload.get("collision_context") or {}).get("obstacles")) if isinstance(payload.get("collision_context"), dict) else None
    if geometry["ok"] and obstacles_raw:
        for segment in segments:
            solved = geometry["segments"].get(str(segment.get("segment_id")))
            crown = _segment_crown_offset_m(segment)
            diameter_mm = _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM
            if solved is None or crown is None:
                continue
            start_node, end_node = nodes.get(solved["start_node_id"]), nodes.get(solved["end_node_id"])
            if not start_node or not end_node:
                continue
            try:
                inner, outer = resolve_dimensions(
                    segment.get("inner_diameter_mm"), segment.get("outer_diameter_mm"), segment.get("wall_thickness_mm")
                )
            except ValueError:
                continue
            axis_radius = (inner or diameter_mm) / 2000.0
            axis = [
                (_finite(start_node.get("x_m")) or 0.0, _finite(start_node.get("y_m")) or 0.0, solved["start_invert_m"] + axis_radius),
                (_finite(end_node.get("x_m")) or 0.0, _finite(end_node.get("y_m")) or 0.0, solved["end_invert_m"] + axis_radius),
            ]
            for obstacle in obstacles_raw:
                if not isinstance(obstacle, dict):
                    continue
                clearance = _obstacle_surface_clearance_m(axis, outer or diameter_mm, obstacle)
                if clearance is not None:
                    clearance_margins.append(clearance - 0.15)
    material = (
        _MATERIAL_WEIGHTS["pipe_per_m"] * total_length
        + _MATERIAL_WEIGHTS["manhole"] * manhole_count
        + _MATERIAL_WEIGHTS["drop_manhole"] * drop_count
        + _MATERIAL_WEIGHTS["concrete_encasement"] * encased
    )
    return {
        "excavation_cost_index": round(excavation, 4),
        "material_cost_index": round(material, 4),
        "total_cost_score": round(excavation + material, 4),
        "min_cover_margin_m": round(min(cover_margins), 4) if cover_margins else None,
        "min_clearance_margin_m": round(min(clearance_margins), 4) if clearance_margins else None,
        "total_length_m": round(total_length, 3),
        "manhole_count": manhole_count,
        "drop_manhole_count": drop_count,
        "concrete_encasement_count": encased,
    }


def _exception_eligible(payload: dict[str, Any], segment: dict[str, Any] | None) -> bool:
    flags = payload.get("constraints")
    if isinstance(flags, dict) and flags.get("allow_exception_approval") is True:
        return True
    return segment is not None and segment.get("protection_measure") == "concrete_encasement"


def check_candidate_state(candidate: dict[str, Any], *, rule_context: FrozenRuleContext | dict[str, Any] | None = None) -> dict[str, Any]:
    """带 Tier-2 受控特批标注的候选状态体检（沙箱与账本的统一评分入口）。

    返回字段：``issues``（含 ``exception_applied`` 标注）、``violation_count``
    （扣除特批豁免后的硬违规数）、``hard_clash_count``（碰撞类硬违规数）、
    ``executable``（仍以确定性校验器为唯一工程判定权威）、``engineering_cost``。
    """
    payload = _solver_payload(candidate)
    context = freeze_rule_context() if rule_context is None else FrozenRuleContext.model_validate(rule_context)
    result = validate_utility_candidate(UtilityCandidate(solver_input=deepcopy(payload)), rule_context=context)
    segments_by_id = {str(s.get("segment_id")): s for s in payload.get("segments") or [] if isinstance(s, dict)}
    issues: list[dict[str, Any]] = []
    violation_count = 0
    hard_clash_count = 0
    exception_applied = False
    pending_exception_count = 0
    global_flag = isinstance(payload.get("constraints"), dict) and payload["constraints"].get("allow_exception_approval") is True
    for issue in result.issues:
        item = issue.model_dump(mode="json")
        if (
            item.get("status") == "fail"
            and item.get("check_name") == "cover_depth_in_spec"
            and isinstance(item.get("measured_value"), (int, float))
            and not isinstance(item.get("measured_value"), bool)
        ):
            segment = next((segments_by_id[oid] for oid in item.get("object_ids", ()) if oid in segments_by_id), None)
            measured = float(item["measured_value"])
            if _exception_eligible(payload, segment) and measured >= EXCEPTION_MIN_COVER_M - 1e-9:
                item["exception_applied"] = True
                item["exception_floor_m"] = EXCEPTION_MIN_COVER_M
                item["protection_measure"] = "concrete_encasement"
                item["exception_approval_id"] = (
                    (segment or {}).get("exception_approval_id") or DEFAULT_EXCEPTION_APPROVAL_ID
                )
                exception_applied = True
                if global_flag and (segment or {}).get("protection_measure") != "concrete_encasement":
                    pending_exception_count += 1
        is_violation = item.get("status") == "fail" and item.get("severity") != "info" and not item.get("exception_applied")
        if is_violation:
            violation_count += 1
            if item.get("check_name") in CLASH_CHECK_NAMES:
                hard_clash_count += 1
        issues.append(item)
    return {
        "issues": issues,
        "violation_count": violation_count,
        "hard_clash_count": hard_clash_count,
        "unknown_count": sum(1 for item in issues if item.get("status") == "unknown" and item.get("severity") != "info"),
        "exception_applied": exception_applied,
        "pending_exception_count": pending_exception_count,
        "executable": bool(result.executable),
        "candidate_sha256": result.candidate_sha256,
        "engineering_cost": compute_engineering_cost(payload),
    }


def _patches_for_list_replacement(path: str, old_list: list[Any], new_list: list[Any], reason: str) -> dict[str, Any]:
    return {"path": path, "old_value": deepcopy(old_list), "value": deepcopy(new_list), "reason": reason}


def _op_slope_regrade(payload: dict[str, Any], *, context: FrozenRuleContext, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """算子 1：顺坡传导调坡（含与既有行为完全兼容的均匀下调锚点级联）。"""
    patches: list[dict[str, Any]] = []
    slope_limit = context.design_limits.min_slope_sewage_d300_concrete
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return patches
    regraded = False
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        value = _finite(segment.get("design_slope"))
        if value is None:
            continue
        diameter = _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM
        if math.isclose(diameter, SUPPORTED_DIAMETER_MM, abs_tol=1e-9):
            if slope_limit is None or not slope_limit.usable_for_verdict:
                continue
            limit, rule_id, provenance = slope_limit.limit_value, slope_limit.rule_id, slope_limit.provenance()
        else:
            norm = lookup_pipe_normative_limits(diameter)
            limit, rule_id = float(norm["min_slope"]), str(norm["min_slope_source_rule"])
            provenance = f"限值来源=动态查表 {rule_id} 分档（constraints.yaml MU-DRAIN-012 管径序列）"
        path = f"/segments/{index}/design_slope"
        if value < limit - 1e-12 and not path_is_locked(path, locked_paths):
            patches.append({
                "path": path, "old_value": value, "value": limit,
                "reason": f"{rule_id}: raise slope to verified minimum; {provenance}",
            })
            segment["design_slope"] = limit
            regraded = True
    if regraded:
        geometry = _solve_network_geometry(payload)
        if geometry["ok"]:
            for node_index, node in enumerate(payload.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                anchor = _finite(node.get("invert_anchor_m"))
                natural = geometry["natural_invert"].get(str(node.get("node_id")))
                path = f"/nodes/{node_index}/invert_anchor_m"
                if (
                    anchor is not None
                    and natural is not None
                    and anchor > natural + INVERT_TOLERANCE
                    and not path_is_locked(path, locked_paths)
                ):
                    patches.append({
                        "path": path, "old_value": anchor, "value": natural,
                        "reason": f"op_slope_regrade: 坡度抬升后向下游未锁定节点传导重算 invert_anchor_m（原锚点 {anchor:.6f} 高于来流管底 {natural:.6f}）",
                    })
                    node["invert_anchor_m"] = natural
    # 端点覆土不足：所有显式锚点均未锁定时整体等量下调（保持跌水相对关系；与既有行为一致）。
    validation = validate_utility_candidate(UtilityCandidate(solver_input=payload), rule_context=context)
    if validation.compiled_ir is not None:
        cover_shortfall = max(
            (float(issue.required_value) - float(issue.measured_value)
             for issue in validation.issues
             if issue.check_name == "cover_depth_in_spec" and issue.status == "fail"
             and isinstance(issue.required_value, (int, float)) and not isinstance(issue.required_value, bool)
             and isinstance(issue.measured_value, (int, float)) and not isinstance(issue.measured_value, bool)),
            default=0.0,
        )
        if cover_shortfall > 1e-9:
            anchors = [
                (index, node["invert_anchor_m"])
                for index, node in enumerate(payload.get("nodes") or [])
                if isinstance(node, dict) and _finite(node.get("invert_anchor_m")) is not None
            ]
            if anchors and not any(path_is_locked(f"/nodes/{index}/invert_anchor_m", locked_paths) for index, _ in anchors):
                for index, value in anchors:
                    patches.append({
                        "path": f"/nodes/{index}/invert_anchor_m", "old_value": value, "value": value - cover_shortfall,
                        "reason": f"endpoint cover shortfall {cover_shortfall:.9g} m: lower every explicit anchor uniformly, preserving relative drops; does not verify terrain between nodes",
                    })
                    payload["nodes"][index]["invert_anchor_m"] = value - cover_shortfall
    return patches


def _node_cover_shortfalls(payload: dict[str, Any]) -> list[tuple[int, dict[str, Any], float]]:
    """覆土不足的未锁定节点：(节点下标, 节点, 缺口 m)。"""
    geometry = _solve_network_geometry(payload)
    if not geometry["ok"]:
        return []
    nodes = payload.get("nodes") or []
    segments = {str(s.get("segment_id")): s for s in payload.get("segments") or [] if isinstance(s, dict)}
    shortfalls: list[tuple[int, dict[str, Any], float]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get("node_type") == "junction":
            continue
        outgoing_ids = [
            sid for sid, solved in geometry["segments"].items() if solved["start_node_id"] == node.get("node_id")
        ]
        worst = None
        for sid in outgoing_ids:
            segment = segments.get(sid)
            if segment is None:
                continue
            crown = _segment_crown_offset_m(segment)
            if crown is None:
                continue
            try:
                norm = lookup_pipe_normative_limits(
                    _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM,
                    str(segment.get("surface_context") or "driveway"),
                )
            except ValueError:
                continue
            cover = (_finite(node.get("ground_elevation_m")) or 0.0) - geometry["outgoing_invert"][str(node.get("node_id"))] - crown
            shortfall = float(norm["min_cover_m"]) - cover
            if shortfall > 1e-9 and (worst is None or shortfall > worst):
                worst = shortfall
        if worst is not None:
            shortfalls.append((index, node, worst))
    return shortfalls


def _op_drop_manhole(payload: dict[str, Any], *, context: FrozenRuleContext, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """算子 2：局部跌水井——覆土不足或跨越障碍物时，只下调未锁定节点并标记跌水。"""
    geometry = _solve_network_geometry(payload)
    if not geometry["ok"]:
        return []
    original_nodes = deepcopy(payload.get("nodes") or [])
    touched = False
    # 触发路径 A：节点出流侧覆土不足。
    for index, node, shortfall in _node_cover_shortfalls(payload):
        path = f"/nodes/{index}/invert_anchor_m"
        if path_is_locked(path, locked_paths):
            continue
        outgoing = geometry["outgoing_invert"].get(str(node.get("node_id")))
        if outgoing is None:
            continue
        delta = shortfall + 0.05
        node["invert_anchor_m"] = round(outgoing - delta, 6)
        node["manhole_type"] = "drop_manhole"
        node["drop_height_m"] = round(delta, 3)
        touched = True
    # 触发路径 B：管段与障碍物三维外包络冲突（跨越障碍物）——把起端未锁定节点
    # 压到障碍物底面以下（净距 = 最小净距 + 包络半径），CEGIS 逐级加余量验证。
    result = validate_utility_candidate(UtilityCandidate(solver_input=payload), rule_context=context)
    physical_targets = [
        (str(ids[0]) if ids else None, str(ids[1]) if len(ids) > 1 else None)
        for ids in (
            tuple(str(item) for item in issue.object_ids)
            for issue in result.issues
            if issue.status == "fail" and issue.check_name == "physical_obstacle_clearance"
        )
    ]
    obstacles_raw = payload.get("collision_context")
    obstacles = obstacles_raw.get("obstacles") if isinstance(obstacles_raw, dict) else None
    obstacle_by_id = {str(o.get("obstacle_id")): o for o in (obstacles or []) if isinstance(o, dict)}
    segments_by_id = {str(s.get("segment_id")): s for s in payload.get("segments") or [] if isinstance(s, dict)}
    for segment_id, obstacle_id in dict.fromkeys(physical_targets):
        segment = segments_by_id.get(segment_id or "")
        obstacle = obstacle_by_id.get(obstacle_id or "")
        if not isinstance(segment, dict) or not isinstance(obstacle, dict):
            continue
        start_index = next(
            (i for i, n in enumerate(payload.get("nodes") or [])
             if isinstance(n, dict) and str(n.get("node_id")) == str(segment.get("start_node_id"))),
            None,
        )
        if start_index is None or path_is_locked(f"/nodes/{start_index}/invert_anchor_m", locked_paths):
            continue
        clearance_line = _obstacle_bottom_z(obstacle)
        if clearance_line is None:
            continue
        try:
            inner, outer = resolve_dimensions(
                segment.get("inner_diameter_mm"), segment.get("outer_diameter_mm"), segment.get("wall_thickness_mm")
            )
        except ValueError:
            continue
        if inner is None or outer is None:
            continue
        required = clearance_line - inner / 2000.0 - outer / 2000.0 - 0.15
        for extra in (0.1, 0.35, 0.6, 0.85, 1.1, 1.6):
            trial = deepcopy(payload)
            node = trial["nodes"][start_index]
            current = geometry["outgoing_invert"].get(str(node.get("node_id")))
            if current is None:
                break
            target = min(_finite(node.get("invert_anchor_m")) or current, required - extra)
            if target >= current - 1e-9:
                break
            node["invert_anchor_m"] = round(target, 6)
            node["manhole_type"] = "drop_manhole"
            node["drop_height_m"] = round(current - target, 3)
            report = check_candidate_state(trial, rule_context=context)
            clash_cleared = not any(
                item.get("status") == "fail" and item.get("check_name") in CLASH_CHECK_NAMES
                for item in report["issues"]
            )
            if clash_cleared and report["violation_count"] == 0:
                payload["nodes"] = trial["nodes"]
                touched = True
                break
    if not touched:
        return []
    return [_patches_for_list_replacement(
        "/nodes", original_nodes, payload["nodes"],
        "op_drop_manhole: 覆土不足或跨越障碍物时，仅下调未锁定节点锚点并设为局部跌水井（manhole_type=drop_manhole）",
    )]


def _obstacle_bottom_z(obstacle: dict[str, Any]) -> float | None:
    if obstacle.get("kind") == "aabb" and isinstance(obstacle.get("min_corner"), dict):
        value = _finite(obstacle["min_corner"].get("z_m"))
        return value
    start, end = obstacle.get("start_center"), obstacle.get("end_center")
    if isinstance(start, dict) and isinstance(end, dict):
        zs = [_finite(start.get("z_m")), _finite(end.get("z_m"))]
        zs = [z for z in zs if z is not None]
        if not zs:
            return None
        return min(zs) - (_finite(obstacle.get("outer_diameter_mm")) or 0.0) / 2000.0
    return None


def _clash_targets(payload: dict[str, Any], issues: list[UtilityIssue]) -> list[tuple[str, str | None]]:
    """从校验问题提取 (segment_id, obstacle_id | None) 冲突对。"""
    targets: list[tuple[str, str | None]] = []
    for issue in issues:
        if issue.status != "fail" or issue.check_name not in CLASH_CHECK_NAMES:
            continue
        ids = [str(item) for item in issue.object_ids]
        segment_id = next((item for item in ids if item and not item.startswith(("OB-", "ob-")) or "-" not in item), ids[0] if ids else None)
        obstacle_id = next((item for item in ids if item != segment_id), None)
        if segment_id:
            targets.append((segment_id, obstacle_id))
    seen: set[tuple[str, str | None]] = set()
    return [t for t in targets if not (t in seen or seen.add(t))]


def _obstacle_center(obstacle: dict[str, Any]) -> tuple[float, float, float] | None:
    if obstacle.get("kind") == "aabb" and isinstance(obstacle.get("min_corner"), dict) and isinstance(obstacle.get("max_corner"), dict):
        low, high = obstacle["min_corner"], obstacle["max_corner"]
        try:
            return (
                (float(low["x_m"]) + float(high["x_m"])) / 2,
                (float(low["y_m"]) + float(high["y_m"])) / 2,
                (float(low["z_m"]) + float(high["z_m"])) / 2,
            )
        except (KeyError, TypeError, ValueError):
            return None
    start = obstacle.get("start_center")
    end = obstacle.get("end_center")
    if isinstance(start, dict) and isinstance(end, dict):
        try:
            return (
                (float(start["x_m"]) + float(end["x_m"])) / 2,
                (float(start["y_m"]) + float(end["y_m"])) / 2,
                (float(start["z_m"]) + float(end["z_m"])) / 2,
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _try_detour_offsets(payload: dict[str, Any], segment: dict[str, Any], obstacle: dict[str, Any], sign: float, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]] | None:
    """沿障碍物包络盒法向构造正交折线绕行（CEGIS 反例驱动：逐步加大偏移直至冲突消除）。

    绕行几何为标准三段式：在障碍物投影点前后各 ``standoff`` 处插入两座绕行井，
    均向障碍物法向反侧偏移 ``standoff``，形成"先偏移—平行跨越—再回归"的折线。
    """
    nodes = {str(n.get("node_id")): n for n in payload.get("nodes") or [] if isinstance(n, dict)}
    start_node, end_node = nodes.get(str(segment.get("start_node_id"))), nodes.get(str(segment.get("end_node_id")))
    if not start_node or not end_node:
        return None
    x1, y1 = _finite(start_node.get("x_m")) or 0.0, _finite(start_node.get("y_m")) or 0.0
    x2, y2 = _finite(end_node.get("x_m")) or 0.0, _finite(end_node.get("y_m")) or 0.0
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return None
    ax, ay = dx / length, dy / length
    ux, uy = -ay, ax  # 法向单位向量
    center = _obstacle_center(obstacle)
    if center is None:
        return None
    t = ((center[0] - x1) * ax + (center[1] - y1) * ay) / length
    t = min(0.85, max(0.15, t))
    norm_limits = None
    try:
        norm_limits = lookup_pipe_normative_limits(_finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM)
    except ValueError:
        norm_limits = None
    base = max(1.2, float((norm_limits or {}).get("min_obstacle_clearance_m", 0.15)) + 1.05)
    original_nodes = deepcopy(payload.get("nodes") or [])
    original_segments = deepcopy(payload.get("segments") or [])
    sid = str(segment["segment_id"])
    for step in range(13):
        standoff = base + 0.5 * step
        work = deepcopy(payload)
        work_nodes = {str(n.get("node_id")): n for n in work.get("nodes") or [] if isinstance(n, dict)}
        s_node, e_node = work_nodes[str(segment["start_node_id"])], work_nodes[str(segment["end_node_id"])]
        sx, sy = _finite(s_node.get("x_m")) or 0.0, _finite(s_node.get("y_m")) or 0.0
        ex, ey = _finite(e_node.get("x_m")) or 0.0, _finite(e_node.get("y_m")) or 0.0
        gs = _finite(s_node.get("ground_elevation_m")) or 0.0
        ge = _finite(e_node.get("ground_elevation_m")) or 0.0
        detour_1, detour_2 = f"{sid}-det-1", f"{sid}-det-2"
        if detour_1 in work_nodes or detour_2 in work_nodes:
            return None
        station_1, station_2 = t * length - standoff, t * length + standoff
        station_1 = min(max(station_1, 0.05 * length), 0.95 * length)
        station_2 = min(max(station_2, station_1 + 0.05 * length), 0.98 * length)
        d1 = {"node_id": detour_1, "node_type": "manhole",
              "x_m": round(x1 + ax * station_1 + ux * standoff * sign, 4),
              "y_m": round(y1 + ay * station_1 + uy * standoff * sign, 4),
              "ground_elevation_m": round(gs + (ge - gs) * (station_1 / length), 4),
              "invert_anchor_m": None, "connection_radius_m": 0.6}
        d2 = {"node_id": detour_2, "node_type": "manhole",
              "x_m": round(x1 + ax * station_2 + ux * standoff * sign, 4),
              "y_m": round(y1 + ay * station_2 + uy * standoff * sign, 4),
              "ground_elevation_m": round(gs + (ge - gs) * (station_2 / length), 4),
              "invert_anchor_m": None, "connection_radius_m": 0.6}
        work["nodes"] = [*(work.get("nodes") or []), d1, d2]
        new_length = (
            math.hypot(d1["x_m"] - sx, d1["y_m"] - sy)
            + math.hypot(d2["x_m"] - d1["x_m"], d2["y_m"] - d1["y_m"])
            + math.hypot(ex - d2["x_m"], ey - d2["y_m"])
        )
        slope = max(0.0, _finite(segment.get("design_slope")) or 0.0)
        adjusted = round(slope * length / new_length, 8) if new_length > 1e-6 else slope
        try:
            min_slope = float(lookup_pipe_normative_limits(
                _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM
            )["min_slope"])
        except ValueError:
            min_slope = 0.0
        adjusted = max(adjusted, min_slope)
        sub = []
        for k, (a, b) in enumerate(((s_node["node_id"], detour_1), (detour_1, detour_2), (detour_2, e_node["node_id"]))):
            sub.append({
                **{key: segment.get(key) for key in ("diameter_mm", "material", "surface_context", "inner_diameter_mm", "outer_diameter_mm", "wall_thickness_mm")},
                "segment_id": f"{sid}-det-{chr(ord('a') + k)}",
                "start_node_id": a, "end_node_id": b,
                "design_slope": adjusted,
            })
        work["segments"] = [s for s in work.get("segments") or [] if s.get("segment_id") != sid] + sub
        report = check_candidate_state(work)
        clash_fixed = not any(
            item.get("status") == "fail" and item.get("check_name") in CLASH_CHECK_NAMES
            for item in report["issues"]
        )
        if clash_fixed and report["violation_count"] == 0:
            patches = [
                _patches_for_list_replacement("/nodes", original_nodes, work["nodes"], f"op_lateral_detour: 沿障碍物包络盒法向插入绕行井对 {detour_1}/{detour_2}（偏移 standoff={standoff:.2f}m 避开障碍物中心）"),
                _patches_for_list_replacement("/segments", original_segments, work["segments"], f"op_lateral_detour: 管段 {sid} 拆分为三段正交折线绕行（总坡降按原值折算）"),
            ]
            payload.clear()
            payload.update(work)
            return patches
    return None


def _op_lateral_detour(payload: dict[str, Any], *, context: FrozenRuleContext, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """算子 3：平面正交折线绕行——针对三维障碍物净距冲突偏移/插入绕行节点。"""
    result = validate_utility_candidate(UtilityCandidate(solver_input=payload), rule_context=context)
    targets = _clash_targets(payload, result.issues)
    if not targets:
        return []
    context_obstacles = payload.get("collision_context")
    obstacles = context_obstacles.get("obstacles") if isinstance(context_obstacles, dict) else None
    obstacle_by_id = {str(o.get("obstacle_id")): o for o in (obstacles or []) if isinstance(o, dict)}
    segments = {str(s.get("segment_id")): s for s in payload.get("segments") or [] if isinstance(s, dict)}
    for segment_id, obstacle_id in targets:
        segment = segments.get(segment_id)
        if segment is None:
            continue
        obstacle = obstacle_by_id.get(str(obstacle_id)) if obstacle_id else ((obstacles or [{}])[0] if obstacles else None)
        if not isinstance(obstacle, dict) or not obstacle:
            continue
        center = _obstacle_center(obstacle)
        nodes = {str(n.get("node_id")): n for n in payload.get("nodes") or [] if isinstance(n, dict)}
        start_node, end_node = nodes.get(str(segment.get("start_node_id"))), nodes.get(str(segment.get("end_node_id")))
        if not start_node or not end_node:
            continue
        sign = 1.0
        if center is not None:
            x1, y1 = _finite(start_node.get("x_m")) or 0.0, _finite(start_node.get("y_m")) or 0.0
            x2, y2 = _finite(end_node.get("x_m")) or 0.0, _finite(end_node.get("y_m")) or 0.0
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1.0
            ux, uy = -dy / length, dx / length
            offset = (center[0] - x1) * ux + (center[1] - y1) * uy
            sign = -1.0 if offset > 0 else 1.0
        patches = _try_detour_offsets(payload, segment, obstacle, sign, locked_paths)
        if patches:
            return patches
    return []


def _op_insert_intermediate_manhole(payload: dict[str, Any], *, context: FrozenRuleContext, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """算子 4：超长管段自动插井——两井连线中点线性插值插入 W_mid 检查井。"""
    original_nodes = deepcopy(payload.get("nodes") or [])
    original_segments = deepcopy(payload.get("segments") or [])
    nodes = {str(n.get("node_id")): n for n in payload.get("nodes") or [] if isinstance(n, dict)}
    existing_ids = set(nodes)
    new_nodes: list[dict[str, Any]] = []
    replacement: dict[str, list[dict[str, Any]]] = {}
    counter = 1
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        start_node = nodes.get(str(segment.get("start_node_id")))
        end_node = nodes.get(str(segment.get("end_node_id")))
        if not start_node or not end_node:
            continue
        length = math.hypot(
            (_finite(end_node.get("x_m")) or 0.0) - (_finite(start_node.get("x_m")) or 0.0),
            (_finite(end_node.get("y_m")) or 0.0) - (_finite(start_node.get("y_m")) or 0.0),
        )
        diameter = _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM
        try:
            spacing = float(lookup_pipe_normative_limits(diameter)["max_well_spacing_m"])
        except ValueError:
            continue
        if length <= spacing + 1e-9:
            continue
        parts = max(2, math.ceil(length / spacing))
        sx, sy = _finite(start_node.get("x_m")) or 0.0, _finite(start_node.get("y_m")) or 0.0
        ex, ey = _finite(end_node.get("x_m")) or 0.0, _finite(end_node.get("y_m")) or 0.0
        gs = _finite(start_node.get("ground_elevation_m")) or 0.0
        ge = _finite(end_node.get("ground_elevation_m")) or 0.0
        chain = [start_node["node_id"]]
        for k in range(1, parts):
            while f"W_mid_{counter}" in existing_ids:
                counter += 1
            mid_id = f"W_mid_{counter}"
            existing_ids.add(mid_id)
            new_nodes.append({
                "node_id": mid_id, "node_type": "manhole",
                "x_m": round(sx + (ex - sx) * k / parts, 4), "y_m": round(sy + (ey - sy) * k / parts, 4),
                "ground_elevation_m": round(gs + (ge - gs) * k / parts, 4), "invert_anchor_m": None,
                "connection_radius_m": 0.6,
            })
            chain.append(mid_id)
            counter += 1
        chain.append(end_node["node_id"])
        sub = []
        for k in range(parts):
            sub.append({
                **{key: segment.get(key) for key in ("diameter_mm", "material", "surface_context", "inner_diameter_mm", "outer_diameter_mm", "wall_thickness_mm")},
                "segment_id": f"{segment['segment_id']}-s{k + 1}",
                "start_node_id": chain[k], "end_node_id": chain[k + 1],
                "design_slope": segment.get("design_slope"),
            })
        replacement[str(segment["segment_id"])] = sub
    if not new_nodes:
        return []
    payload["nodes"] = [*(payload.get("nodes") or []), *new_nodes]
    payload["segments"] = [
        piece
        for segment in payload.get("segments") or []
        for piece in replacement.get(str(segment.get("segment_id")), [segment] if isinstance(segment, dict) else [])
    ]
    return [
        _patches_for_list_replacement("/nodes", original_nodes, payload["nodes"], f"op_insert_intermediate_manhole: 超长管段中点线性插值插入 {len(new_nodes)} 座 W_mid 检查井（坐标与高程线性插值）"),
        _patches_for_list_replacement("/segments", original_segments, payload["segments"], "op_insert_intermediate_manhole: 原超长管段按井位拆分为合法长度管段（坡度沿原值）"),
    ]


def _op_concrete_encasement_exception(payload: dict[str, Any], *, context: FrozenRuleContext, locked_paths: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """算子 5：受控破限——满包混凝土加固特批（需 constraints.allow_exception_approval 显式授权）。"""
    flags = payload.get("constraints")
    if not (isinstance(flags, dict) and flags.get("allow_exception_approval") is True):
        return []  # 国标特批不能由 CEGIS 自授，必须有显式授权
    geometry = _solve_network_geometry(payload)
    if not geometry["ok"]:
        return []
    nodes = {str(n.get("node_id")): n for n in payload.get("nodes") or [] if isinstance(n, dict)}
    original_segments = deepcopy(payload.get("segments") or [])
    touched = False
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict) or segment.get("protection_measure") == "concrete_encasement":
            continue
        solved = geometry["segments"].get(str(segment.get("segment_id")))
        if solved is None:
            continue
        start_node, end_node = nodes.get(solved["start_node_id"]), nodes.get(solved["end_node_id"])
        crown = _segment_crown_offset_m(segment)
        if not start_node or not end_node or crown is None:
            continue
        try:
            norm = lookup_pipe_normative_limits(
                _finite(segment.get("diameter_mm")) or SUPPORTED_DIAMETER_MM,
                str(segment.get("surface_context") or "driveway"),
            )
        except ValueError:
            continue
        cover = min(
            (_finite(start_node.get("ground_elevation_m")) or 0.0) - solved["start_invert_m"],
            (_finite(end_node.get("ground_elevation_m")) or 0.0) - solved["end_invert_m"],
        ) - crown
        if EXCEPTION_MIN_COVER_M - 1e-9 <= cover < float(norm["min_cover_m"]) - 1e-9:
            segment["protection_measure"] = "concrete_encasement"
            segment["exception_approval_id"] = DEFAULT_EXCEPTION_APPROVAL_ID
            touched = True
    if not touched:
        return []
    return [_patches_for_list_replacement(
        "/segments", original_segments, payload["segments"],
        f"op_concrete_encasement_exception: 节点高程受限浅埋管段挂载满包混凝土加固特批（覆土受控放宽至 {EXCEPTION_MIN_COVER_M:g}m，票据 {DEFAULT_EXCEPTION_APPROVAL_ID}）",
    )]


_OPERATORS: dict[str, Any] = {
    "op_slope_regrade": _op_slope_regrade,
    "op_drop_manhole": _op_drop_manhole,
    "op_lateral_detour": _op_lateral_detour,
    "op_insert_intermediate_manhole": _op_insert_intermediate_manhole,
    "op_concrete_encasement_exception": _op_concrete_encasement_exception,
}


def evaluate_repair_sandbox(
    candidate: UtilityCandidate | dict[str, Any],
    *,
    operator_priority: list[str] | None = None,
    rule_context: FrozenRuleContext | dict[str, Any] | None = None,
    locked_paths: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """CEGIS 多分支拓扑沙箱：并行推演 5 大算子并按帕累托字典序选出胜出方案。

    选择键为 ``(violation_count, hard_clash_count, total_cost_score)`` 字典序最小；
    同分时按 ``operator_priority``（缺省 SANDBOX_OPERATORS）与补丁数决出。
    沙箱内全部深拷贝推演，绝不改动传入候选。返回完整帕累托账本。
    """
    payload0 = deepcopy(_solver_payload(candidate))
    context = freeze_rule_context() if rule_context is None else FrozenRuleContext.model_validate(rule_context)
    priority = list(operator_priority) if operator_priority else list(SANDBOX_OPERATORS)
    ordered_names = [name for name in priority if name in _OPERATORS]
    ordered_names += [name for name in SANDBOX_OPERATORS if name not in ordered_names]

    def run_operator(name: str, work: dict[str, Any]) -> list[dict[str, Any]]:
        return _OPERATORS[name](work, context=context, locked_paths=locked_paths)

    baseline_report = check_candidate_state(payload0, rule_context=context)
    branches: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for name in ordered_names:
        work = deepcopy(payload0)
        patches = run_operator(name, work)
        if patches:
            branches.append((name, work, patches))
    regrade = "op_slope_regrade"
    regrade_work = deepcopy(payload0)
    regrade_patches = run_operator(regrade, regrade_work)
    if regrade_patches:
        for name in ordered_names:
            if name == regrade:
                continue
            work = deepcopy(regrade_work)
            extra = run_operator(name, work)
            if extra:
                branches.append((f"{regrade}+{name}", work, [*deepcopy(regrade_patches), *extra]))

    proposals: list[dict[str, Any]] = []
    for name, work, patches in branches:
        report = check_candidate_state(work, rule_context=context)
        proposals.append({
            "operator_name": name,
            "violation_count": report["violation_count"],
            "hard_clash_count": report["hard_clash_count"],
            "pending_exception_count": report.get("pending_exception_count", 0),
            "total_cost_score": report["engineering_cost"]["total_cost_score"],
            "exception_applied": report["exception_applied"],
            "engineering_cost": report["engineering_cost"],
            "patches": deepcopy(patches),
        })
    proposals.sort(key=lambda item: (
        item["violation_count"], item["hard_clash_count"], item["pending_exception_count"],
        item["total_cost_score"],
        ordered_names.index(item["operator_name"].split("+")[0]) if item["operator_name"].split("+")[0] in ordered_names else len(ordered_names),
        len(item["patches"]),
    ))
    baseline = {
        "violation_count": baseline_report["violation_count"],
        "hard_clash_count": baseline_report["hard_clash_count"],
        "pending_exception_count": baseline_report.get("pending_exception_count", 0),
        "total_cost_score": baseline_report["engineering_cost"]["total_cost_score"],
        "exception_applied": baseline_report["exception_applied"],
        "engineering_cost": baseline_report["engineering_cost"],
    }
    winner = proposals[0] if proposals else None
    improved = bool(winner) and (
        (winner["violation_count"], winner["hard_clash_count"], winner["pending_exception_count"])
        < (baseline["violation_count"], baseline["hard_clash_count"], baseline["pending_exception_count"])
    )
    return {
        "baseline": baseline,
        "pareto_proposals": proposals,
        "winning_operator": winner["operator_name"] if improved and winner else None,
        "winner_patches": deepcopy(winner["patches"]) if improved and winner else [],
        "operator_priority_used": ordered_names,
    }


def propose_candidate_repairs_with_ledger(
    candidate: UtilityCandidate | dict[str, Any],
    *,
    operator_priority: list[str] | None = None,
    rule_context: FrozenRuleContext | dict[str, Any] | None = None,
    locked_paths: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """返回完整沙箱帕累托比选账本（含胜出算子与补丁）。"""
    return evaluate_repair_sandbox(
        candidate, operator_priority=operator_priority, rule_context=rule_context, locked_paths=locked_paths
    )


def propose_candidate_repairs(candidate: UtilityCandidate | dict[str, Any], *, rule_context: FrozenRuleContext | dict[str, Any] | None = None, locked_paths: tuple[str, ...] | list[str] = (), operator_priority: list[str] | None = None) -> list[dict[str, Any]]:
    """Propose bounded input edits without mutating or executing the candidate.

    CEGIS 沙箱版：并行推演 5 大拓扑修复算子（顺坡传导调坡 / 局部跌水井 /
    平面正交折线绕行 / 超长管段自动插井 / 满包混凝土加固受控特批），对每个
    分支运行 ``check_candidate_state`` 与 ``compute_engineering_cost``，按
    ``(violation_count, hard_clash_count, total_cost_score)`` 字典序选出胜出
    分支并返回其补丁；无改进时返回空列表。锁定节点绝不整体罢工：所有算子
    只修改未锁定（unlocked）的节点或启用其它拓扑算子。
    """
    return propose_candidate_repairs_with_ledger(
        candidate, operator_priority=operator_priority, rule_context=rule_context, locked_paths=locked_paths
    )["winner_patches"]
