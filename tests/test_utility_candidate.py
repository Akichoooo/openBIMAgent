"""Engineering drafts, explicit dimensions, finite geometry, and host agreement."""
from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from openbimagent.assembly.blender_plan import BlenderBuilder, BlenderOperation, BlenderPlanError
from openbimagent.assembly.vectorworks_plan import (
    VectorworksBuilder,
    VectorworksOperation,
    VectorworksPlanError,
)
from openbimagent.utility.candidate import FrozenRuleContext, UtilityCandidate, freeze_rule_context, validate_utility_candidate
from openbimagent.utility.contracts import CompiledUtilityIR
from openbimagent.utility.geometry import axis_centerline, closest_segment_box_points, closest_segment_points, execution_geometry, resolve_dimensions, upgrade_legacy_utility_ir
from openbimagent.utility.network_solver import solve_network_gravity_utility
from openbimagent.utility.spatial_graph import SpatialBIMGraph, SpatialSegment
from test_compiled_utility_ir import solved_payload
from test_network_utility_solver import network_payload


def physical_candidate() -> UtilityCandidate:
    payload = network_payload()
    payload["nodes"][0]["invert_anchor_m"] = 9.8
    payload["nodes"][1]["connection_radius_m"] = 0.6
    for segment in payload["segments"]:
        segment.update(inner_diameter_mm=300.0, outer_diameter_mm=360.0)
    return UtilityCandidate(solver_input=payload)


def test_invalid_draft_roundtrip_and_negative_slope_pointer() -> None:
    candidate = physical_candidate()
    candidate.solver_input["segments"][1]["design_slope"] = -0.01
    restored = UtilityCandidate.model_validate_json(candidate.model_dump_json())
    result = validate_utility_candidate(restored)
    assert result.compiled_ir is None and not result.executable
    issue = next(issue for issue in result.issues if issue.field_path == "/segments/1/design_slope")
    assert issue.object_ids == ("pipe-002",)
    assert issue.repairable_parameters == ("/segments/1/design_slope",)
    assert issue.required_value == 0.003


def test_missing_draft_can_be_saved_and_fails_validation() -> None:
    assert not validate_utility_candidate(UtilityCandidate(solver_input={})).executable


def test_frozen_context_roundtrip_and_per_run_limits() -> None:
    context = freeze_rule_context()
    assert FrozenRuleContext.model_validate_json(context.model_dump_json()) == context
    slope = context.design_limits.min_slope_sewage_d300_concrete
    assert slope is not None
    changed = context.model_copy(update={"design_limits": replace(context.design_limits, min_slope_sewage_d300_concrete=replace(slope, limit_value=0.006))})
    result = validate_utility_candidate(physical_candidate(), rule_context=changed)
    assert not result.executable
    assert any(issue.check_name == "slope_in_spec" and issue.status == "fail" and issue.required_value == 0.006 for issue in result.issues)
    assert result.rule_context_sha256 != context.canonical_sha256()


def test_executable_scope_keeps_hydraulics_unknown() -> None:
    result = validate_utility_candidate(physical_candidate())
    assert result.executable
    assert "hydraulics" in result.not_evaluated
    assert any(issue.check_name == "hydraulics_in_spec" and issue.status == "unknown" for issue in result.issues)
    assert type(result).model_validate_json(result.model_dump_json()) == result


def test_unknown_dimensions_do_not_become_nominal_envelopes() -> None:
    candidate = physical_candidate()
    for segment in candidate.solver_input["segments"]:
        segment.pop("inner_diameter_mm")
        segment.pop("outer_diameter_mm")
    result = validate_utility_candidate(candidate)
    assert result.compiled_ir is not None and not result.executable
    assert all(segment.min_cover_depth_m is None for segment in result.compiled_ir.segments)
    assert any(issue.check_name == "pipe_geometry_known" and issue.status == "unknown" for issue in result.issues)


def test_nominal_pipe_dimension_and_host_axis_agree() -> None:
    compiled = solve_network_gravity_utility(physical_candidate().solver_input).compiled_ir
    segment = compiled.segments[0]
    axis, outer = execution_geometry(segment)
    assert segment.diameter_mm == 300 and outer == 360
    assert axis[0].z_m == pytest.approx(segment.start_invert_m + 0.15)
    assert segment.min_cover_depth_m == pytest.approx(11 - 9.8 - 0.33)
    for builder in (VectorworksBuilder(), BlenderBuilder()):
        plan = builder.build(compiled)
        pipe = next(op for op in plan.operations if op.operation.value == "create_object" and op.object_id == segment.segment_id)
        port = next(op for op in plan.operations if op.operation.value == "create_object" and op.object_id == segment.start_port_id)
        if isinstance(pipe, VectorworksOperation):
            assert pipe.nominal_diameter_mm == segment.diameter_mm == 300
            assert pipe.inner_diameter_mm == 300
            assert pipe.outer_diameter_mm == outer == 360
            assert pipe.geometry_kind == "hollow_tube_mesh"
            assert pipe.circumferential_segments == 24
        else:
            assert isinstance(pipe, BlenderOperation)
            assert pipe.diameter_mm == outer == 360
            properties = next(
                op for op in plan.operations
                if op.operation.value == "set_properties" and op.object_id == segment.segment_id
            )
            record = {item.property_name: item.value for item in properties.properties}
            assert record["openbim_geometry_diameter_mm"] == segment.diameter_mm == 300
            assert record["openbim_geometry_outer_diameter_mm"] == outer == 360
        assert pipe.material == segment.material
        assert pipe.centerline[0].z_m == axis[0].z_m == port.position.z_m


def test_legacy_requires_explicit_upgrade_and_preserves_old_hash_payload() -> None:
    payload = solved_payload()
    legacy = CompiledUtilityIR.model_validate(payload)
    assert legacy.model_dump(mode="json") == payload
    assert axis_centerline(legacy.segments[0]) is None
    with pytest.raises(VectorworksPlanError, match="known inner/outer"):
        VectorworksBuilder().build(legacy)
    with pytest.raises(BlenderPlanError, match="known inner/outer"):
        BlenderBuilder().build(legacy)
    upgraded = upgrade_legacy_utility_ir(payload, dimensions_by_segment={"pipe-001": {"inner_diameter_mm": 300, "outer_diameter_mm": 360}})
    assert upgraded.protocol_version == "1.1"
    assert upgraded.segments[0].start_invert_m == legacy.segments[0].start_invert_m
    assert execution_geometry(upgraded.segments[0])[0][0].z_m == 10.15


def test_v11_rejects_unmarked_legacy_and_inconsistent_dimensions() -> None:
    payload = solved_payload()
    payload["protocol_version"] = "1.1"
    with pytest.raises(ValidationError, match="explicit centerline_reference"):
        CompiledUtilityIR.model_validate(payload)
    with pytest.raises(ValueError, match="outer diameter"):
        resolve_dimensions(300, 360, 40)
    assert resolve_dimensions(300, None, 30) == (300, 360)


@pytest.mark.parametrize("points,expected", [
    (((0, 0, 0), (100, 0, 0), (2, -1, 0), (2, 1, 0)), 0),
    (((0, 0, 0), (10, 0, 0), (0, 2, 0), (10, 2, 0)), 2),
    (((0, 0, 0), (1, 0, 0), (2, 1, 0), (2, 2, 0)), 2 ** 0.5),
    (((1, 2, 3), (1, 2, 3), (1, 2, 5), (1, 2, 5)), 2),
    (((0, 0, 0), (0, 0, 0), (-2, 1, 0), (2, 1, 0)), 1),
    (((0, 0, 0), (10, 0, 0), (5, -5, 2), (5, 5, 2)), 2),
])
def test_closed_segment_distance(points: tuple, expected: float) -> None:
    assert closest_segment_points(*points).distance_m == pytest.approx(expected)
    assert closest_segment_points(points[2], points[3], points[0], points[1]).distance_m == pytest.approx(expected)


def graph_pair(path_a: tuple, path_b: tuple, *, shared: bool = False, diameter: float | None = 300) -> SpatialBIMGraph:
    graph = SpatialBIMGraph()
    graph.segments["a"] = SpatialSegment("a", "joint" if shared else "a0", "a1", 300, 0, 100, path_a[0], path_a[-1], diameter, True, path_a)
    graph.segments["b"] = SpatialSegment("b", "joint" if shared else "b0", "b1", 300, 0, 10, path_b[0], path_b[-1], diameter, True, path_b)
    return graph


def test_crossing_far_from_midpoints_is_detected() -> None:
    crossing = graph_pair(((0, 0, 0), (100, 0, 0)), ((2, -1, 0), (2, 1, 0))).find_crossings()[0]
    assert crossing.status == "fail" and crossing.is_clash is True
    assert crossing.horizontal_distance_m == 0
    assert crossing.euclidean_min_distance_m == 0


def test_clearance_boundary_and_separate_horizontal_vs_3d() -> None:
    graph = graph_pair(((0, 0, 0), (10, 0, 0)), ((0, 0.5, 0), (10, 0.5, 0)))
    assert graph.find_crossings(required_clearance_m=0.2)[0].status == "pass"
    assert graph.find_crossings(required_clearance_m=0.20001)[0].status == "fail"
    crossing = graph_pair(((0, 0, 0), (10, 0, 0)), ((5, -5, 2), (5, 5, 2))).find_crossings()[0]
    assert crossing.horizontal_distance_m == 0 and crossing.euclidean_min_distance_m == 2
    assert crossing.status == "pass"


def test_connection_is_only_exempted_inside_declared_local_region() -> None:
    graph = graph_pair(((0, 0, 0), (10, 0, 0)), ((0, 0, 0), (0, 10, 0)), shared=True)
    assert graph.find_crossings()[0].status == "unknown"
    assert graph.find_crossings(connection_exemptions_m={"joint": 0.5})[0].status == "pass"
    remote = graph_pair(((0, 0, 0), (10, 0, 0)), ((0, 0, 0), (0, 5, 0), (8, 5, 0), (8, -5, 0)), shared=True)
    assert remote.find_crossings(connection_exemptions_m={"joint": 0.5})[0].status == "fail"


def test_unknown_outer_and_capsule_end_overlap_are_unknown() -> None:
    assert graph_pair(((0, 0, 0), (10, 0, 0)), ((5, -5, 0), (5, 5, 0)), diameter=None).find_crossings()[0].status == "unknown"
    end_contact = graph_pair(((0, 0, 0), (10, 0, 0)), ((10.2, 0, 0), (20, 0, 0))).find_crossings()[0]
    assert end_contact.status == "unknown"
    assert "end cap" in end_contact.detail


@pytest.mark.parametrize("points,expected", [
    (((-2, 0, 0), (2, 0, 0), (-1, -1, -1), (1, 1, 1)), 0),
    (((-2, 3, 0), (2, 3, 0), (-1, -1, -1), (1, 1, 1)), 2),
    (((-2, 2, 0), (2, -2, 0), (1, 1, -1), (2, 2, 1)), 2 ** 0.5),
    (((-2, 2, 2), (2, -2, 2), (1, 1, -1), (2, 2, 1)), 3 ** 0.5),
    (((3, 0, 0), (3, 0, 0), (-1, -1, -1), (1, 1, 1)), 2),
    (((0, 0, 0), (0, 0, 0), (-1, -1, -1), (1, 1, 1)), 0),
])
def test_finite_segment_box_distance_goldens(points, expected):
    assert closest_segment_box_points(*points).distance_m == pytest.approx(expected)
    assert closest_segment_box_points(points[1], points[0], points[2], points[3]).distance_m == pytest.approx(expected)


def test_oversized_connection_regions_never_silently_skip_checks():
    graph = graph_pair(((0, 0, 0), (10, 0, 0)), ((0, 0, 0), (0, 10, 0)), shared=True)
    assert graph.find_crossings(connection_exemptions_m={"joint": 100})[0].status == "unknown"
    candidate = physical_candidate()
    candidate.solver_input["nodes"][1]["connection_radius_m"] = 100
    result = validate_utility_candidate(candidate)
    assert not result.executable
    assert any(issue.check_name == "network_geometry_in_spec" and issue.status == "unknown" for issue in result.issues)


@pytest.mark.parametrize("payload", [{"nodes": [None]}, {"nodes": [False]}, {"segments": [None]}, {"segments": "invalid"}])
def test_malformed_draft_reports_input_issues_without_crashing(payload):
    result = validate_utility_candidate(UtilityCandidate(solver_input=payload))
    assert not result.executable
    assert any(issue.check_name == "input_valid" for issue in result.issues)


def test_physical_obstacle_evidence_is_independent_of_horizontal_rule():
    candidate = physical_candidate()
    candidate.solver_input["collision_context"]["obstacles"] = [{"obstacle_id": "high-building", "kind": "aabb", "category": "building", "min_corner": {"x_m": 4, "y_m": -0.1, "z_m": 15}, "max_corner": {"x_m": 6, "y_m": 0.1, "z_m": 17}}]
    result = validate_utility_candidate(candidate)
    assert any(issue.check_name == "clash_free" and issue.status == "fail" for issue in result.issues)
    physical = [issue for issue in result.issues if issue.rule_id == "PHYSICAL-OBSTACLE"]
    assert physical and all(issue.status == "pass" for issue in physical)
    assert all(issue.evidence["intersection_volume_computed"] is False for issue in physical)


def test_box_interior_envelope_conflict_and_corner_uncertainty():
    candidate = physical_candidate()
    obstacle = {"obstacle_id": "box", "kind": "aabb", "category": "building", "min_corner": {"x_m": 4, "y_m": -0.1, "z_m": 9.7}, "max_corner": {"x_m": 6, "y_m": 0.1, "z_m": 10.1}}
    candidate.solver_input["collision_context"]["obstacles"] = [obstacle]
    result = validate_utility_candidate(candidate)
    assert any(issue.rule_id == "PHYSICAL-OBSTACLE" and issue.status == "fail" for issue in result.issues)
    obstacle.update(min_corner={"x_m": 10.1, "y_m": 0.1, "z_m": 9.8}, max_corner={"x_m": 11, "y_m": 1, "z_m": 10.5})
    result = validate_utility_candidate(candidate)
    issue = next(issue for issue in result.issues if issue.issue_id == "physical-pipe-001-box")
    assert issue.status == "unknown" and "end cap or box edge/corner" in issue.detail


def test_existing_pipe_physical_distance_uses_3d_finite_axes():
    candidate = physical_candidate()
    candidate.solver_input["collision_context"]["obstacles"] = [{"obstacle_id": "water", "kind": "existing_pipe", "category": "water", "start_center": {"x_m": 5, "y_m": -5, "z_m": 12}, "end_center": {"x_m": 5, "y_m": 5, "z_m": 12}, "outer_diameter_mm": 200}]
    result = validate_utility_candidate(candidate)
    issue = next(issue for issue in result.issues if issue.issue_id == "physical-pipe-001-water")
    assert issue.status == "pass" and issue.measured_value > 1.5
