"""市政 Solver v0 的求解、证据与失败关闭测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openbimagent.domain_gate import GateStatus
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import UtilitySolverError, solve_straight_gravity_utility



def solver_payload() -> dict:
    return {
        "protocol_version": "0.3",
        "request_id": "case-001",
        "source_ir_sha256": "b" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "start": {"node_id": "mh-001", "x_m": 0.0, "y_m": 0.0, "ground_elevation_m": 11.0},
        "end": {"node_id": "mh-002", "x_m": 10.0, "y_m": 0.0, "ground_elevation_m": 11.0},
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
        "start_invert_m": None,
        "collision_context": None,
    }



def test_solver_input_schema_is_registered_and_accepts_supported_slice() -> None:
    gate = SchemaGate()
    assert "utility_solver_input.schema.json" in gate.schema_names()
    assert gate.validate_artifact("utility_solver_input", solver_payload()) == []



def test_solver_computes_shallowest_cover_compliant_profile() -> None:
    result = solve_straight_gravity_utility(solver_payload())
    segment = result.compiled_ir.segments[0]
    assert segment.horizontal_length_m == pytest.approx(10.0)
    assert segment.start_invert_m == pytest.approx(10.0)
    assert segment.end_invert_m == pytest.approx(9.97)
    assert segment.slope == pytest.approx(0.003)
    assert segment.min_cover_depth_m == pytest.approx(0.7)
    assert result.compiled_ir.nodes[0].ground_elevation_m == pytest.approx(11.0)



def test_solver_generates_high_confidence_rule_evidence() -> None:
    result = solve_straight_gravity_utility(solver_payload())
    evidence = result.compiled_ir.domain_evidence()
    assert evidence["diameter_in_spec"]["ok"] is True
    assert evidence["slope_in_spec"]["ok"] is True
    assert evidence["cover_depth_in_spec"]["ok"] is True
    assert evidence["manhole_spacing_in_spec"]["ok"] is True
    assert evidence["clash_free"]["ok"] is None
    assert evidence["hydraulics_in_spec"]["ok"] is None



def test_default_domain_gate_is_unknown_until_clash_evidence_exists() -> None:
    result = solve_straight_gravity_utility(solver_payload())
    assert result.domain_gate.status is GateStatus.UNKNOWN
    assert set(result.domain_gate.passed) == {
        "cover_depth_in_spec",
        "diameter_in_spec",
        "manhole_spacing_in_spec",
        "slope_in_spec",
    }
    assert any(item.startswith("clash_free:") for item in result.domain_gate.unknown)



def test_complete_empty_collision_context_proves_clash_free() -> None:
    payload = solver_payload()
    payload["collision_context"] = {"coverage": "complete", "obstacles": []}
    result = solve_straight_gravity_utility(payload)
    assert result.compiled_ir.domain_evidence()["clash_free"]["ok"] is True
    assert result.domain_gate.status is GateStatus.PASS
    clash = [item for item in result.compiled_ir.evidence if item.check_name == "clash_free"]
    assert len(clash) == 1
    assert clash[0].measured_value == 0.0
    assert "清单为空" in clash[0].detail



def _aabb_obstacle(*, obstacle_id: str = "foundation-001", y_min: float = 2.65) -> dict:
    return {
        "obstacle_id": obstacle_id,
        "kind": "aabb",
        "category": "building",
        "min_corner": {"x_m": 4.0, "y_m": y_min, "z_m": 9.0},
        "max_corner": {"x_m": 6.0, "y_m": y_min + 0.2, "z_m": 11.0},
    }



def test_aabb_surface_clearance_equal_to_limit_passes() -> None:
    payload = solver_payload()
    payload["collision_context"] = {
        "coverage": "complete",
        "obstacles": [_aabb_obstacle(y_min=2.65)],
    }
    result = solve_straight_gravity_utility(payload)
    clash = next(item for item in result.compiled_ir.evidence if item.check_name == "clash_free")
    assert clash.status.value == "pass"
    assert clash.measured_value == pytest.approx(2.5)
    assert clash.limit_value == pytest.approx(2.5)
    assert result.domain_gate.status is GateStatus.PASS



def test_aabb_clearance_shortfall_and_intersection_fail() -> None:
    shortfall = solver_payload()
    shortfall["collision_context"] = {
        "coverage": "complete",
        "obstacles": [_aabb_obstacle(y_min=2.64)],
    }
    shortfall_result = solve_straight_gravity_utility(shortfall)
    shortfall_evidence = next(
        item for item in shortfall_result.compiled_ir.evidence if item.check_name == "clash_free"
    )
    assert shortfall_evidence.status.value == "fail"
    assert shortfall_evidence.measured_value == pytest.approx(2.49)
    assert shortfall_result.domain_gate.status is GateStatus.FAIL

    intersection = solver_payload()
    intersection["collision_context"] = {
        "coverage": "complete",
        "obstacles": [_aabb_obstacle(y_min=-0.1)],
    }
    intersection["collision_context"]["obstacles"][0]["max_corner"]["y_m"] = 0.1
    intersection_result = solve_straight_gravity_utility(intersection)
    intersection_evidence = next(
        item for item in intersection_result.compiled_ir.evidence if item.check_name == "clash_free"
    )
    assert intersection_evidence.status.value == "fail"
    assert intersection_evidence.measured_value == pytest.approx(-0.15)



def test_medium_confidence_existing_pipe_rule_is_unknown_even_when_geometry_is_clear() -> None:
    payload = solver_payload()
    payload["collision_context"] = {
        "coverage": "complete",
        "obstacles": [
            {
                "obstacle_id": "water-001",
                "kind": "existing_pipe",
                "category": "water",
                "start_center": {"x_m": 0.0, "y_m": 0.75, "z_m": 10.15},
                "end_center": {"x_m": 10.0, "y_m": 0.75, "z_m": 10.12},
                "outer_diameter_mm": 200.0,
                "pressure_class": None,
                "burial_method": None,
                "voltage_kv": None,
            }
        ],
    }
    result = solve_straight_gravity_utility(payload)
    clash = next(item for item in result.compiled_ir.evidence if item.check_name == "clash_free")
    assert clash.status.value == "unknown"
    assert clash.measured_value is None
    assert "review_required" in clash.detail
    assert result.domain_gate.status is GateStatus.UNKNOWN



def test_multiple_obstacles_fail_dominates_and_input_order_is_canonical() -> None:
    pass_obstacle = _aabb_obstacle(obstacle_id="z-pass", y_min=2.65)
    fail_obstacle = _aabb_obstacle(obstacle_id="a-fail", y_min=2.64)
    first_payload = solver_payload()
    first_payload["collision_context"] = {
        "coverage": "complete",
        "obstacles": [pass_obstacle, fail_obstacle],
    }
    second_payload = solver_payload()
    second_payload["collision_context"] = {
        "coverage": "complete",
        "obstacles": [fail_obstacle, pass_obstacle],
    }
    first = solve_straight_gravity_utility(first_payload)
    second = solve_straight_gravity_utility(second_payload)
    assert first.compiled_ir.domain_evidence()["clash_free"]["ok"] is False
    assert first.domain_gate.status is GateStatus.FAIL
    assert first.compiled_ir.canonical_json() == second.compiled_ir.canonical_json()
    assert first.compiled_ir.canonical_sha256() == second.compiled_ir.canonical_sha256()



def test_collision_context_rejects_invalid_geometry_caller_rules_and_duplicate_ids() -> None:
    invalid_box = solver_payload()
    obstacle = _aabb_obstacle()
    obstacle["max_corner"]["x_m"] = obstacle["min_corner"]["x_m"]
    invalid_box["collision_context"] = {"coverage": "complete", "obstacles": [obstacle]}
    with pytest.raises(UtilitySolverError, match="min < max"):
        solve_straight_gravity_utility(invalid_box)

    caller_rule = solver_payload()
    obstacle = _aabb_obstacle()
    obstacle["clearance_rule"] = {
        "rule_id": "MU-CLEAR-001",
        "required_clearance_m": 0.0,
        "source_clause": "caller override",
    }
    caller_rule["collision_context"] = {"coverage": "complete", "obstacles": [obstacle]}
    with pytest.raises(UtilitySolverError, match="extra_forbidden|Additional properties"):
        solve_straight_gravity_utility(caller_rule)

    duplicate = solver_payload()
    duplicate["collision_context"] = {
        "coverage": "complete",
        "obstacles": [_aabb_obstacle(), _aabb_obstacle()],
    }
    with pytest.raises(UtilitySolverError, match="obstacle_id 不能重复"):
        solve_straight_gravity_utility(duplicate)



def test_domain_gate_passes_for_v0_checks_when_clash_is_not_required() -> None:
    result = solve_straight_gravity_utility(
        solver_payload(),
        domain_requirements={
            "diameter_in_spec": True,
            "slope_in_spec": True,
            "cover_depth_in_spec": True,
            "manhole_spacing_in_spec": True,
        },
    )
    assert result.domain_gate.status is GateStatus.PASS



def test_solver_reports_fail_when_design_slope_is_below_rule() -> None:
    payload = solver_payload()
    payload["design_slope"] = 0.002
    result = solve_straight_gravity_utility(
        payload,
        domain_requirements={"slope_in_spec": True},
    )
    assert result.compiled_ir.segments[0].slope == pytest.approx(0.002)
    assert result.compiled_ir.domain_evidence()["slope_in_spec"]["ok"] is False
    assert result.domain_gate.status is GateStatus.FAIL



def test_solver_reports_fail_when_fixed_start_invert_breaks_cover() -> None:
    payload = solver_payload()
    payload["start_invert_m"] = 10.2
    result = solve_straight_gravity_utility(
        payload,
        domain_requirements={"cover_depth_in_spec": True},
    )
    assert result.compiled_ir.segments[0].min_cover_depth_m == pytest.approx(0.5)
    assert result.domain_gate.status is GateStatus.FAIL



def test_solver_reports_fail_when_manhole_spacing_exceeds_limit() -> None:
    payload = solver_payload()
    payload["end"]["x_m"] = 80.0
    result = solve_straight_gravity_utility(
        payload,
        domain_requirements={"manhole_spacing_in_spec": True},
    )
    assert result.compiled_ir.segments[0].horizontal_length_m == pytest.approx(80.0)
    assert result.domain_gate.status is GateStatus.FAIL



def test_solver_rejects_unsupported_diameter_without_guessing_rules() -> None:
    payload = solver_payload()
    payload["diameter_mm"] = 400.0
    with pytest.raises(UtilitySolverError, match="仅支持 DN300"):
        solve_straight_gravity_utility(payload)



def test_solver_rejects_same_xy_endpoint() -> None:
    payload = solver_payload()
    payload["end"]["x_m"] = 0.0
    with pytest.raises(UtilitySolverError, match="平面位置不能相同"):
        solve_straight_gravity_utility(payload)



def test_solver_rejects_unknown_fields_at_input_schema_gate() -> None:
    payload = solver_payload()
    payload["route_guess"] = True
    with pytest.raises(UtilitySolverError, match="Solver v0 输入或 MunicipalRuleSet 未通过门禁"):
        solve_straight_gravity_utility(payload)



def test_solver_output_is_canonical_and_deterministic() -> None:
    first = solve_straight_gravity_utility(solver_payload()).compiled_ir
    second_payload = deepcopy(solver_payload())
    second = solve_straight_gravity_utility(second_payload).compiled_ir
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256() == second.canonical_sha256()
