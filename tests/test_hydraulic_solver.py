"""M1.5 T5 重力污水管网水力计算、协议绑定和失败关闭测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openbimagent.domain_gate import GateStatus, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import (
    HydraulicSolveStatus,
    HydraulicSolverError,
    HydraulicSolverInput,
    ProductionVerificationStatus,
    RuleDecisionStatus,
    apply_grid_route_to_network_input,
    compile_municipal_rule_evidence_bundle,
    compile_municipal_rule_set,
    solve_grid_route,
    solve_hydraulic_network,
    solve_network_gravity_utility,
)
from test_network_utility_solver import network_payload


def route_payload(*, width: int = 3, height: int = 3) -> dict:
    allowed = [
        {"x_index": x_index, "y_index": y_index}
        for x_index in range(width)
        for y_index in range(height)
    ]
    return {
        "protocol_version": "0.1",
        "request_id": "route-case-001",
        "source_ir_sha256": "d" * 64,
        "municipal_rule_set_sha256": compile_municipal_rule_set().canonical_sha256,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "grid": {
            "origin_x_m": 0.0,
            "origin_y_m": 0.0,
            "resolution_m": 1.0,
            "width": width,
            "height": height,
        },
        "start": {
            "node_id": "start",
            "cell": {"x_index": 0, "y_index": 0},
            "invert_anchor_m": 10.0,
        },
        "end": {
            "node_id": "end",
            "cell": {"x_index": width - 1, "y_index": height - 1},
        },
        "allowed_cells": allowed,
        "surface_samples": [
            {"cell": cell, "ground_elevation_m": 12.0}
            for cell in allowed
        ],
        "obstacles": [],
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
        "max_candidates": 3,
        "max_search_expansions": 100000,
    }


def compiled_network():
    return solve_network_gravity_utility(network_payload()).compiled_ir


def hydraulic_payload() -> dict:
    compiled = compiled_network()
    return {
        "protocol_version": "0.1",
        "request_id": "hydraulic-case-001",
        "source_ir_sha256": compiled.canonical_sha256(),
        "rule_evidence_bundle_sha256": compile_municipal_rule_evidence_bundle().canonical_sha256,
        "calculation_model": "manning_uniform_open_channel_si",
        "roughness_inputs": [
            {
                "segment_id": segment.segment_id,
                "manning_n": 0.013,
                "provenance": "designer_input",
                "source_reference": "benchmark explicit input",
            }
            for segment in compiled.segments
        ],
        "scenarios": [
            {
                "scenario_id": "design",
                "scenario_type": "design",
                "segment_flows": [
                    {"segment_id": "pipe-001", "flow_m3_s": 0.010},
                    {"segment_id": "pipe-002", "flow_m3_s": 0.006},
                    {"segment_id": "pipe-003", "flow_m3_s": 0.004},
                ],
            },
            {
                "scenario_id": "check",
                "scenario_type": "check",
                "segment_flows": [
                    {"segment_id": "pipe-001", "flow_m3_s": 0.015},
                    {"segment_id": "pipe-002", "flow_m3_s": 0.009},
                    {"segment_id": "pipe-003", "flow_m3_s": 0.006},
                ],
            },
        ],
    }


def test_hydraulic_schemas_are_registered() -> None:
    gate = SchemaGate()
    assert "hydraulic_solver_input.schema.json" in gate.schema_names()
    assert "hydraulic_solver_result.schema.json" in gate.schema_names()
    payload = hydraulic_payload()
    assert gate.validate_artifact("hydraulic_solver_input", payload) == []
    result = solve_hydraulic_network(compiled_network(), payload)
    assert gate.validate_artifact(
        "hydraulic_solver_result", result.model_dump(mode="json")
    ) == []


def test_hydraulic_solver_computes_capacity_partial_depth_and_velocity() -> None:
    compiled = compiled_network()
    result = solve_hydraulic_network(compiled, hydraulic_payload())
    assert result.status is HydraulicSolveStatus.CALCULATED
    assert result.source_ir_sha256 == compiled.canonical_sha256()
    assert len(result.scenarios) == 2
    design = next(item for item in result.scenarios if item.scenario_id == "design")
    pipe = next(item for item in design.segments if item.segment_id == "pipe-001")
    assert pipe.full_flow_capacity_m3_s == pytest.approx(0.0529651868, rel=1e-8)
    assert 0.0 < pipe.depth_ratio < 1.0
    assert pipe.flow_area_m2 > 0.0
    assert pipe.velocity_m_s > 0.0
    assert pipe.capacity_sufficient is True
    assert pipe.roughness_provenance == "designer_input"
    assert pipe.roughness_source_reference == "benchmark explicit input"
    assert pipe.flow_m3_s == pytest.approx(0.010)
    assert pipe.capacity_margin_m3_s == pytest.approx(pipe.full_flow_capacity_m3_s - pipe.flow_m3_s)
    assert pipe.minimum_velocity_compliance == "fail"
    assert pipe.minimum_velocity_rule_status == "production"
    assert pipe.minimum_velocity_limit_m_s == pytest.approx(0.6)
    assert result.hydraulics_in_spec == "fail"


def test_hydraulic_solver_passes_minimum_velocity_with_sufficient_verified_flow() -> None:
    compiled = compiled_network()
    payload = hydraulic_payload()
    for scenario in payload["scenarios"]:
        scenario["segment_flows"] = [
                {"segment_id": "pipe-001", "flow_m3_s": 0.024},
                {"segment_id": "pipe-002", "flow_m3_s": 0.012},
                {"segment_id": "pipe-003", "flow_m3_s": 0.012},
        ]
    result = solve_hydraulic_network(compiled, payload)
    assert result.hydraulics_in_spec == "pass"
    assert all(
        segment.minimum_velocity_compliance == "pass"
        for scenario in result.scenarios
        for segment in scenario.segments
    )


def test_hydraulic_solver_builds_network_rule_evaluation_from_all_scenarios() -> None:
    compiled = compiled_network()
    bundle = compile_municipal_rule_evidence_bundle()
    result = solve_hydraulic_network(
        compiled,
        hydraulic_payload(),
        rule_evidence_bundle=bundle,
    )
    evaluation = result.rule_evaluation(
        compiled_ir=compiled,
        rule_evidence_bundle=bundle,
    )
    rule = bundle.rule("MU-DRAIN-007")
    assert evaluation.subject_type.value == "network"
    assert evaluation.subject_id == compiled.ir_id
    assert evaluation.rule_set_sha256 == bundle.canonical_sha256
    assert evaluation.rule_sha256 == rule.canonical_sha256
    assert evaluation.verification_sha256 == rule.verification.canonical_sha256
    assert evaluation.production_verification is ProductionVerificationStatus.ELIGIBLE
    assert evaluation.status is RuleDecisionStatus.FAIL
    assert evaluation.measured_value < evaluation.limit_value
    assert len(evaluation.canonical_sha256) == 64


def test_hydraulic_solver_rejects_rule_evidence_bundle_hash_drift() -> None:
    payload = hydraulic_payload()
    payload["rule_evidence_bundle_sha256"] = "0" * 64
    with pytest.raises(HydraulicSolverError, match="rule_evidence_bundle_sha256"):
        solve_hydraulic_network(compiled_network(), payload)


def test_hydraulic_solver_reports_over_capacity_without_mutating_geometry() -> None:
    compiled = compiled_network()
    payload = hydraulic_payload()
    payload["scenarios"][1]["segment_flows"][0]["flow_m3_s"] = 0.1
    payload["scenarios"][1]["segment_flows"][1]["flow_m3_s"] = 0.06
    payload["scenarios"][1]["segment_flows"][2]["flow_m3_s"] = 0.04
    before = compiled.canonical_sha256()
    result = solve_hydraulic_network(compiled, payload)
    check = next(item for item in result.scenarios if item.scenario_id == "check")
    pipe = next(item for item in check.segments if item.segment_id == "pipe-001")
    assert pipe.capacity_sufficient is False
    assert pipe.depth_ratio is None
    assert pipe.velocity_m_s is None
    assert result.status is HydraulicSolveStatus.REWORK_REQUIRED
    assert result.hydraulics_in_spec == "fail"
    assert pipe.minimum_velocity_compliance == "unknown"
    evaluation = result.rule_evaluation(
        compiled_ir=compiled,
        rule_evidence_bundle=compile_municipal_rule_evidence_bundle(),
    )
    assert evaluation.status is RuleDecisionStatus.FAIL
    assert evaluation.measured_value is not None
    assert compiled.canonical_sha256() == before
    assert result.geometry_mutated is False


def test_network_velocity_rule_evaluation_does_not_relabel_capacity_failure() -> None:
    compiled = compiled_network()
    bundle = compile_municipal_rule_evidence_bundle()
    payload = hydraulic_payload()
    payload["scenarios"][0]["segment_flows"] = [
        {"segment_id": "pipe-001", "flow_m3_s": 0.024},
        {"segment_id": "pipe-002", "flow_m3_s": 0.012},
        {"segment_id": "pipe-003", "flow_m3_s": 0.012},
    ]
    payload["scenarios"][1]["segment_flows"] = [
        {"segment_id": "pipe-001", "flow_m3_s": 0.1},
        {"segment_id": "pipe-002", "flow_m3_s": 0.06},
        {"segment_id": "pipe-003", "flow_m3_s": 0.04},
    ]
    result = solve_hydraulic_network(compiled, payload, rule_evidence_bundle=bundle)
    evaluation = result.rule_evaluation(
        compiled_ir=compiled,
        rule_evidence_bundle=bundle,
    )
    assert result.hydraulics_in_spec == "fail"
    assert evaluation.status is RuleDecisionStatus.UNKNOWN
    assert evaluation.measured_value is not None


def test_hydraulic_solver_rejects_ir_hash_and_segment_set_drift() -> None:
    compiled = compiled_network()
    drift = hydraulic_payload()
    drift["source_ir_sha256"] = "0" * 64
    with pytest.raises(HydraulicSolverError, match="source_ir_sha256"):
        solve_hydraulic_network(compiled, drift)

    missing = hydraulic_payload()
    missing["scenarios"][0]["segment_flows"].pop()
    with pytest.raises(HydraulicSolverError, match="segment.*集合|缺少"):
        solve_hydraulic_network(compiled, missing)

    extra = hydraulic_payload()
    extra["roughness_inputs"].append(
        {
            "segment_id": "unknown",
            "manning_n": 0.013,
            "provenance": "designer_input",
            "source_reference": "bad",
        }
    )
    with pytest.raises(HydraulicSolverError, match="segment.*集合|未知"):
        solve_hydraulic_network(compiled, extra)


def test_hydraulic_solver_rejects_non_gravity_or_invalid_geometry() -> None:
    compiled = compiled_network()
    bad_slope = compiled.model_dump(mode="json")
    bad_slope["segments"][0]["slope"] = 0.0
    bad_slope["segments"][0]["end_invert_m"] = bad_slope["segments"][0]["start_invert_m"]
    bad_slope["segments"][0]["centerline"][-1]["z_m"] = bad_slope["segments"][0]["start_invert_m"]
    end_port_id = bad_slope["segments"][0]["end_port_id"]
    for node in bad_slope["nodes"]:
        for port in node["ports"]:
            if port["port_id"] == end_port_id:
                port["position"]["z_m"] = bad_slope["segments"][0]["start_invert_m"]
    from openbimagent.utility import CompiledUtilityIR

    zero_slope = CompiledUtilityIR.model_validate(bad_slope)
    zero_slope_input = hydraulic_payload()
    zero_slope_input["source_ir_sha256"] = zero_slope.canonical_sha256()
    with pytest.raises(HydraulicSolverError, match="正坡|slope"):
        solve_hydraulic_network(zero_slope, zero_slope_input)


def test_hydraulic_solver_rejects_implicit_defaults_and_unknown_fields() -> None:
    missing_n = hydraulic_payload()
    del missing_n["roughness_inputs"][0]["manning_n"]
    with pytest.raises(HydraulicSolverError, match="未通过门禁"):
        solve_hydraulic_network(compiled_network(), missing_n)

    unknown = hydraulic_payload()
    unknown["auto_resize"] = True
    with pytest.raises(HydraulicSolverError, match="未通过门禁"):
        solve_hydraulic_network(compiled_network(), unknown)


def test_hydraulic_solver_is_canonical_across_input_order() -> None:
    compiled = compiled_network()
    first_input = HydraulicSolverInput.model_validate(hydraulic_payload())
    reordered = deepcopy(hydraulic_payload())
    reordered["roughness_inputs"].reverse()
    reordered["scenarios"].reverse()
    for scenario in reordered["scenarios"]:
        scenario["segment_flows"].reverse()
    second_input = HydraulicSolverInput.model_validate(reordered)
    assert first_input.canonical_sha256() == second_input.canonical_sha256()
    first = solve_hydraulic_network(compiled, first_input)
    second = solve_hydraulic_network(compiled, second_input)
    assert first.canonical_sha256() == second.canonical_sha256()


def test_hydraulic_solver_rejects_duplicate_scenarios_and_nonpositive_flow() -> None:
    missing_check = hydraulic_payload()
    missing_check["scenarios"].pop()
    with pytest.raises(HydraulicSolverError, match="design 和 check|未通过门禁"):
        solve_hydraulic_network(compiled_network(), missing_check)

    duplicate = hydraulic_payload()
    duplicate["scenarios"][1]["scenario_id"] = "design"
    with pytest.raises(HydraulicSolverError, match="scenario_id.*重复|未通过门禁"):
        solve_hydraulic_network(compiled_network(), duplicate)

    zero = hydraulic_payload()
    zero["scenarios"][0]["segment_flows"][0]["flow_m3_s"] = 0.0
    with pytest.raises(HydraulicSolverError, match="大于 0|greater than 0|未通过门禁"):
        solve_hydraulic_network(compiled_network(), zero)


def test_hydraulic_solver_rejects_internal_flow_imbalance() -> None:
    imbalance = hydraulic_payload()
    imbalance["scenarios"][0]["segment_flows"][0]["flow_m3_s"] = 0.011
    with pytest.raises(HydraulicSolverError, match="internal node.*junction.*流量不守恒"):
        solve_hydraulic_network(compiled_network(), imbalance)


def test_hydraulic_result_exposes_independent_domain_evidence() -> None:
    compiled = compiled_network()
    before = compiled.canonical_sha256()
    result = solve_hydraulic_network(compiled, hydraulic_payload())
    evidence = result.domain_evidence()

    capacity = evaluate_domain_gate({"hydraulic_capacity_in_spec": True}, evidence)
    overall = evaluate_domain_gate({"hydraulics_in_spec": True}, evidence)
    assert capacity.status is GateStatus.PASS
    assert overall.status is GateStatus.FAIL
    assert compiled.canonical_sha256() == before
    assert result.source_ir_sha256 == before
    assert result.canonical_sha256() in evidence["hydraulics_in_spec"]["detail"]
    rule_evidence = result.rule_evidence(compiled_ir=compiled)
    capacity_items = [
        item for item in rule_evidence if item.check_name == "hydraulic_capacity_in_spec"
    ]
    velocity_items = [item for item in rule_evidence if item.check_name == "hydraulics_in_spec"]
    assert len(capacity_items) == len(compiled.segments) * len(result.scenarios)
    assert all(item.status.value == "pass" for item in capacity_items)
    assert any(item.status.value == "fail" for item in velocity_items)
    assert all(item.subject_type.value == "segment" for item in rule_evidence)
    other_compiled = compiled.model_copy(update={"ir_id": "different-network"})
    with pytest.raises(HydraulicSolverError, match="身份不匹配"):
        result.rule_evidence(compiled_ir=other_compiled)

    over_capacity = hydraulic_payload()
    over_capacity["scenarios"][1]["segment_flows"][0]["flow_m3_s"] = 0.1
    over_capacity["scenarios"][1]["segment_flows"][1]["flow_m3_s"] = 0.06
    over_capacity["scenarios"][1]["segment_flows"][2]["flow_m3_s"] = 0.04
    failed = solve_hydraulic_network(compiled, over_capacity)
    failed_gate = evaluate_domain_gate({"hydraulics_in_spec": True}, failed.domain_evidence())
    assert failed_gate.status is GateStatus.FAIL
    failed_evidence = failed.rule_evidence(compiled_ir=compiled)
    assert any(
        item.subject_type.value == "network"
        and item.status.value == "fail"
        and item.check_name == "hydraulics_in_spec"
        for item in failed_evidence
    )
    assert compiled.canonical_sha256() == before


def test_route_network_hydraulic_e2e_preserves_compiled_geometry_identity() -> None:
    route = route_payload(width=11, height=2)
    route["request_id"] = "pipe-001-hydraulic-e2e"
    route["source_ir_sha256"] = "c" * 64
    route["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    route["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route["allowed_cells"] = [
        {"x_index": 0, "y_index": 0},
        {"x_index": 0, "y_index": 1},
        *[{"x_index": x_index, "y_index": 1} for x_index in range(1, 11)],
        {"x_index": 10, "y_index": 0},
    ]
    route["surface_samples"] = [
        {"cell": cell, "ground_elevation_m": 11.0}
        for cell in route["allowed_cells"]
    ]
    route_result = solve_grid_route(route)
    routed_input = apply_grid_route_to_network_input(
        network_payload(),
        segment_id="pipe-001",
        route_input=route,
        route_result=route_result,
    )
    compiled = solve_network_gravity_utility(routed_input).compiled_ir
    before = compiled.canonical_sha256()
    routed_ids = sorted(segment.segment_id for segment in compiled.segments)
    routed_flows = []
    for segment_id in routed_ids:
        flow = 0.010 if segment_id.startswith("pipe-001-route-") else 0.006
        if segment_id == "pipe-003":
            flow = 0.004
        routed_flows.append({"segment_id": segment_id, "flow_m3_s": flow})
    payload = {
        "protocol_version": "0.1",
        "request_id": "route-network-hydraulic-e2e",
        "source_ir_sha256": before,
        "rule_evidence_bundle_sha256": compile_municipal_rule_evidence_bundle().canonical_sha256,
        "calculation_model": "manning_uniform_open_channel_si",
        "roughness_inputs": [
            {
                "segment_id": segment_id,
                "manning_n": 0.013,
                "provenance": "designer_input",
                "source_reference": "T5 route-network-hydraulic E2E",
            }
            for segment_id in routed_ids
        ],
        "scenarios": [
            {
                "scenario_id": "design",
                "scenario_type": "design",
                "segment_flows": routed_flows,
            },
            {
                "scenario_id": "check",
                "scenario_type": "check",
                "segment_flows": deepcopy(routed_flows),
            },
        ],
    }
    result = solve_hydraulic_network(compiled, payload)
    assert result.status is HydraulicSolveStatus.CALCULATED
    assert result.source_ir_sha256 == before
    assert result.geometry_mutated is False
    assert compiled.canonical_sha256() == before
    assert len(result.scenarios[0].segments) == len(compiled.segments)
