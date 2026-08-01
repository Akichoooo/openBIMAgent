"""市政 Solver v0 的求解、证据与失败关闭测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openbimagent.domain_gate import GateStatus
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import UtilitySolverError, solve_straight_gravity_utility



def solver_payload() -> dict:
    return {
        "protocol_version": "0.1",
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
    with pytest.raises(UtilitySolverError, match="Solver v0 输入未通过门禁"):
        solve_straight_gravity_utility(payload)



def test_solver_output_is_canonical_and_deterministic() -> None:
    first = solve_straight_gravity_utility(solver_payload()).compiled_ir
    second_payload = deepcopy(solver_payload())
    second = solve_straight_gravity_utility(second_payload).compiled_ir
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256() == second.canonical_sha256()
