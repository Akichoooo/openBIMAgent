"""compiled utility IR v1 契约、确定性输出和失败关闭测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from openbimagent.domain_gate import GateStatus, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import CompiledUtilityIR, UtilityCompileError, compile_solved_utility_ir


def solved_payload() -> dict:
    return {
        "protocol_version": "1.0",
        "ir_id": "network-001",
        "source_ir_sha256": "a" * 64,
        "solver_name": "municipal-route-solver",
        "solver_version": "0.1.0",
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "systems": [
            {
                "system_id": "sys-sewage",
                "name": "污水重力系统",
                "system_type": "wastewater",
                "flow_regime": "gravity",
                "ifc_class": "IfcDistributionSystem",
                "ifc_predefined_type": "WASTEWATER",
            }
        ],
        "nodes": [
            {
                "node_id": "mh-001",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 0.0, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-001-out",
                        "direction": "outlet",
                        "position": {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
            {
                "node_id": "mh-002",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-002-in",
                        "direction": "inlet",
                        "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
        ],
        "segments": [
            {
                "segment_id": "pipe-001",
                "system_id": "sys-sewage",
                "start_port_id": "mh-001-out",
                "end_port_id": "mh-002-in",
                "centerline": [
                    {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
                    {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
                ],
                "horizontal_length_m": 10.0,
                "start_invert_m": 10.0,
                "end_invert_m": 9.97,
                "slope": 0.003,
                "diameter_mm": 300.0,
                "material": "concrete",
                "min_cover_depth_m": 0.7,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev-slope-001",
                "rule_id": "MU-DRAIN-004",
                "check_name": "slope_in_spec",
                "status": "pass",
                "subject_type": "segment",
                "subject_id": "pipe-001",
                "detail": "DN300 concrete slope 0.003 meets minimum 0.003",
                "measured_value": 0.003,
                "limit_value": 0.003,
                "unit": "ratio",
                "source_clause": "GB 50014-2021 §5.2.10 表 5.2.10",
            },
            {
                "evidence_id": "ev-clash-001",
                "rule_id": "MU-AVOID-001",
                "check_name": "clash_free",
                "status": "unknown",
                "subject_type": "network",
                "subject_id": "network-001",
                "detail": "尚未执行跨系统碰撞检查",
                "measured_value": None,
                "limit_value": True,
                "unit": None,
                "source_clause": "GB 50289-2016 §3.0.4",
            },
        ],
    }


def test_valid_compiled_ir_passes_contract_and_schema_gate() -> None:
    compiled = CompiledUtilityIR.model_validate(solved_payload())
    assert compiled.segments[0].slope == pytest.approx(0.003)
    assert SchemaGate().validate_artifact("compiled_utility_ir", compiled.model_dump(mode="json")) == []


def test_compile_entry_returns_validated_ir_without_inventing_fields() -> None:
    payload = solved_payload()
    compiled = compile_solved_utility_ir(payload)
    assert compiled.ir_id == payload["ir_id"]
    assert compiled.model_dump(mode="json") == payload


def test_compiled_ir_rejects_duplicate_ids() -> None:
    payload = solved_payload()
    payload["nodes"].append(deepcopy(payload["nodes"][0]))
    with pytest.raises(ValidationError, match="node id 重复"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_unknown_port_reference() -> None:
    payload = solved_payload()
    payload["segments"][0]["end_port_id"] = "missing-port"
    with pytest.raises(ValidationError, match="引用未知 port"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_segment_loop_on_same_node() -> None:
    payload = solved_payload()
    payload["nodes"][0]["ports"].append(
        {
            "port_id": "mh-001-in",
            "direction": "inlet",
            "position": {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
            "ifc_class": "IfcDistributionPort",
        }
    )
    payload["segments"][0]["end_port_id"] = "mh-001-in"
    payload["segments"][0]["centerline"] = [
        {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
        {"x_m": 1.0, "y_m": 0.0, "z_m": 10.0},
        {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
    ]
    payload["segments"][0]["horizontal_length_m"] = 2.0
    payload["segments"][0]["end_invert_m"] = 10.0
    payload["segments"][0]["slope"] = 0.0
    with pytest.raises(ValidationError, match="不能属于同一 node"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_incompatible_port_direction() -> None:
    payload = solved_payload()
    payload["nodes"][0]["ports"][0]["direction"] = "inlet"
    with pytest.raises(ValidationError, match="start port 不能声明为 inlet"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_port_reused_by_multiple_segments() -> None:
    payload = solved_payload()
    second = deepcopy(payload["segments"][0])
    second["segment_id"] = "pipe-002"
    payload["segments"].append(second)
    with pytest.raises(ValidationError, match="被多个 segment 重复占用"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_inconsistent_slope() -> None:
    payload = solved_payload()
    payload["segments"][0]["slope"] = 0.004
    with pytest.raises(ValidationError, match="slope 与标高差/水平长度不一致"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_gravity_reverse_slope() -> None:
    payload = solved_payload()
    payload["nodes"][1]["ports"][0]["position"]["z_m"] = 10.03
    payload["segments"][0]["centerline"][1]["z_m"] = 10.03
    payload["segments"][0]["end_invert_m"] = 10.03
    payload["segments"][0]["slope"] = -0.003
    with pytest.raises(ValidationError, match="不允许逆坡"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_unknown_evidence_subject() -> None:
    payload = solved_payload()
    payload["evidence"][0]["subject_id"] = "pipe-missing"
    with pytest.raises(ValidationError, match="引用未知 segment"):
        CompiledUtilityIR.model_validate(payload)


def test_compile_entry_wraps_contract_error() -> None:
    payload = solved_payload()
    payload["segments"][0]["diameter_mm"] = 0
    with pytest.raises(UtilityCompileError, match="未通过 compiled utility IR v1 门禁"):
        compile_solved_utility_ir(payload)


def test_canonical_serialization_is_stable_across_collection_order() -> None:
    first_payload = solved_payload()
    second_payload = solved_payload()
    second_payload["nodes"].reverse()
    second_payload["evidence"].reverse()
    first = CompiledUtilityIR.model_validate(first_payload)
    second = CompiledUtilityIR.model_validate(second_payload)
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256() == second.canonical_sha256()
    assert len(first.canonical_sha256()) == 64


def test_domain_evidence_projection_preserves_unknown_and_pass() -> None:
    compiled = CompiledUtilityIR.model_validate(solved_payload())
    evidence = compiled.domain_evidence()
    assert evidence["slope_in_spec"]["ok"] is True
    assert evidence["clash_free"]["ok"] is None

    report = evaluate_domain_gate(
        {"slope_in_spec": True, "clash_free": True},
        evidence,
    )
    assert report.status is GateStatus.UNKNOWN
    assert report.passed == ("slope_in_spec",)
    assert any(item.startswith("clash_free:") for item in report.unknown)


def test_schema_rejects_unknown_fields() -> None:
    payload = solved_payload()
    payload["solver_guess"] = True
    errors = SchemaGate().validate_artifact("compiled_utility_ir", payload)
    assert any("solver_guess" in error for error in errors)
