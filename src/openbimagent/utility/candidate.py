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
from openbimagent.utility.geometry import closest_segment_box_points, closest_segment_points, execution_geometry
from openbimagent.utility.network_solver import NetworkGravitySolverInput, solve_network_gravity_utility
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


def propose_candidate_repairs(candidate: UtilityCandidate | dict[str, Any], *, rule_context: FrozenRuleContext | dict[str, Any] | None = None, locked_paths: tuple[str, ...] | list[str] = ()) -> list[dict[str, Any]]:
    """Propose bounded input edits without mutating or executing the candidate.

    Only raise subminimum slopes to the current verified minimum, then lower all
    explicit invert anchors uniformly to satisfy endpoint cover. The uniform
    shift preserves anchor offsets and existing drop relations. If any anchor is
    locked the entire shift is withheld. Missing dimensions/anchors, topology,
    spacing, and obstacle avoidance are never guessed.
    """
    draft = candidate if isinstance(candidate, UtilityCandidate) else UtilityCandidate.model_validate(candidate)
    context = freeze_rule_context() if rule_context is None else FrozenRuleContext.model_validate(rule_context)
    payload = deepcopy(draft.solver_input)
    proposals: list[dict[str, Any]] = []
    slope_limit = context.design_limits.min_slope_sewage_d300_concrete
    if slope_limit is not None and slope_limit.usable_for_verdict:
        for index, segment in enumerate(payload.get("segments", []) if isinstance(payload.get("segments"), list) else []):
            if not isinstance(segment, dict):
                continue
            value = segment.get("design_slope")
            path = f"/segments/{index}/design_slope"
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value < slope_limit.limit_value and not path_is_locked(path, locked_paths):
                proposals.append({"path": path, "old_value": value, "value": slope_limit.limit_value, "reason": f"{slope_limit.rule_id}: raise slope to verified minimum; {slope_limit.provenance()}"})
                segment["design_slope"] = slope_limit.limit_value
    validation = validate_utility_candidate(UtilityCandidate(solver_input=payload), rule_context=context)
    if validation.compiled_ir is None:
        return proposals
    cover_shortfall = max((float(issue.required_value) - float(issue.measured_value) for issue in validation.issues if issue.check_name == "cover_depth_in_spec" and issue.status == "fail" and isinstance(issue.required_value, (int, float)) and isinstance(issue.measured_value, (int, float))), default=0.0)
    if cover_shortfall > 1e-9:
        anchors = [(index, node["invert_anchor_m"]) for index, node in enumerate(payload.get("nodes", [])) if isinstance(node, dict) and isinstance(node.get("invert_anchor_m"), (int, float)) and not isinstance(node.get("invert_anchor_m"), bool)]
        if anchors and not any(path_is_locked(f"/nodes/{index}/invert_anchor_m", locked_paths) for index, _ in anchors):
            for index, value in anchors:
                proposals.append({"path": f"/nodes/{index}/invert_anchor_m", "old_value": value, "value": value - cover_shortfall, "reason": f"endpoint cover shortfall {cover_shortfall:.9g} m: lower every explicit anchor uniformly, preserving relative drops; does not verify terrain between nodes"})
    return proposals
