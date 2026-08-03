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


def branched_payload() -> dict:
    """返回 1 入 2 出 junction 的确定性四节点重力网络。"""
    payload = solved_payload()
    payload["ir_id"] = "network-branch-001"
    payload["nodes"][1]["node_id"] = "j-001"
    payload["nodes"][1]["node_type"] = "junction"
    payload["nodes"][1]["ports"] = [
        {
            "port_id": "j-001-in",
            "direction": "inlet",
            "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
            "ifc_class": "IfcDistributionPort",
        },
        {
            "port_id": "j-001-out-a",
            "direction": "outlet",
            "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
            "ifc_class": "IfcDistributionPort",
        },
        {
            "port_id": "j-001-out-b",
            "direction": "outlet",
            "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
            "ifc_class": "IfcDistributionPort",
        },
    ]
    payload["segments"][0]["end_port_id"] = "j-001-in"
    payload["nodes"].extend(
        [
            {
                "node_id": "mh-002",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 20.0, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-002-in",
                        "direction": "inlet",
                        "position": {"x_m": 20.0, "y_m": 0.0, "z_m": 9.94},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
            {
                "node_id": "mh-003",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 10.0, "y_m": 10.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-003-in",
                        "direction": "inlet",
                        "position": {"x_m": 10.0, "y_m": 10.0, "z_m": 9.94},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
        ]
    )
    for suffix, start_port_id, end_port_id, end_x, end_y in (
        ("002", "j-001-out-a", "mh-002-in", 20.0, 0.0),
        ("003", "j-001-out-b", "mh-003-in", 10.0, 10.0),
    ):
        payload["segments"].append(
            {
                "segment_id": f"pipe-{suffix}",
                "system_id": "sys-sewage",
                "start_port_id": start_port_id,
                "end_port_id": end_port_id,
                "centerline": [
                    {"x_m": 10.0, "y_m": 0.0, "z_m": 9.97},
                    {"x_m": end_x, "y_m": end_y, "z_m": 9.94},
                ],
                "horizontal_length_m": 10.0,
                "start_invert_m": 9.97,
                "end_invert_m": 9.94,
                "slope": 0.003,
                "diameter_mm": 300.0,
                "material": "concrete",
                "min_cover_depth_m": 0.7,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            }
        )
    payload["evidence"] = []
    return payload


def gravity_cycle_payload() -> dict:
    payload = solved_payload()
    payload["ir_id"] = "network-cycle-001"
    payload["nodes"] = []
    payload["segments"] = []
    coordinates = {"n-1": (0.0, 0.0), "n-2": (10.0, 0.0), "n-3": (5.0, 5.0)}
    edges = (("n-1", "n-2"), ("n-2", "n-3"), ("n-3", "n-1"))
    for node_id, (x_m, y_m) in coordinates.items():
        payload["nodes"].append(
            {
                "node_id": node_id,
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": x_m, "y_m": y_m, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": f"{node_id}-in",
                        "direction": "inlet",
                        "position": {"x_m": x_m, "y_m": y_m, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    },
                    {
                        "port_id": f"{node_id}-out",
                        "direction": "outlet",
                        "position": {"x_m": x_m, "y_m": y_m, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    },
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            }
        )
    for index, (start_id, end_id) in enumerate(edges, start=1):
        start_x, start_y = coordinates[start_id]
        end_x, end_y = coordinates[end_id]
        length = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
        payload["segments"].append(
            {
                "segment_id": f"cycle-{index}",
                "system_id": "sys-sewage",
                "start_port_id": f"{start_id}-out",
                "end_port_id": f"{end_id}-in",
                "centerline": [
                    {"x_m": start_x, "y_m": start_y, "z_m": 10.0},
                    {"x_m": end_x, "y_m": end_y, "z_m": 10.0},
                ],
                "horizontal_length_m": length,
                "start_invert_m": 10.0,
                "end_invert_m": 10.0,
                "slope": 0.0,
                "diameter_mm": 300.0,
                "material": "concrete",
                "min_cover_depth_m": 0.7,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            }
        )
    payload["evidence"] = []
    return payload


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


def test_compiled_ir_accepts_connected_branched_gravity_network() -> None:
    compiled = CompiledUtilityIR.model_validate(branched_payload())
    assert len(compiled.nodes) == 4
    assert len(compiled.segments) == 3
    assert SchemaGate().validate_artifact("compiled_utility_ir", compiled.model_dump(mode="json")) == []


def test_compiled_ir_rejects_isolated_node() -> None:
    payload = solved_payload()
    isolated = deepcopy(payload["nodes"][1])
    isolated["node_id"] = "mh-isolated"
    isolated["position"]["x_m"] = 20.0
    isolated["ports"][0]["port_id"] = "mh-isolated-in"
    isolated["ports"][0]["position"]["x_m"] = 20.0
    payload["nodes"].append(isolated)
    with pytest.raises(ValidationError, match="孤立 node"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_disconnected_components_within_system() -> None:
    payload = solved_payload()
    for node_id, x_m, direction in (("mh-003", 20.0, "outlet"), ("mh-004", 30.0, "inlet")):
        payload["nodes"].append(
            {
                "node_id": node_id,
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": x_m, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": f"{node_id}-{'out' if direction == 'outlet' else 'in'}",
                        "direction": direction,
                        "position": {"x_m": x_m, "y_m": 0.0, "z_m": 9.9},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            }
        )
    payload["segments"].append(
        {
            "segment_id": "pipe-002",
            "system_id": "sys-sewage",
            "start_port_id": "mh-003-out",
            "end_port_id": "mh-004-in",
            "centerline": [
                {"x_m": 20.0, "y_m": 0.0, "z_m": 9.9},
                {"x_m": 30.0, "y_m": 0.0, "z_m": 9.9},
            ],
            "horizontal_length_m": 10.0,
            "start_invert_m": 9.9,
            "end_invert_m": 9.9,
            "slope": 0.0,
            "diameter_mm": 300.0,
            "material": "concrete",
            "min_cover_depth_m": 0.7,
            "ifc_class": "IfcPipeSegment",
            "ifc_predefined_type": "RIGIDSEGMENT",
        }
    )
    with pytest.raises(ValidationError, match="存在不连通子图"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_gravity_directed_cycle() -> None:
    with pytest.raises(ValidationError, match="有向环路"):
        CompiledUtilityIR.model_validate(gravity_cycle_payload())


def test_compiled_ir_rejects_branch_or_merge_on_non_junction_node() -> None:
    payload = branched_payload()
    payload["nodes"][1]["node_type"] = "manhole"
    with pytest.raises(ValidationError, match="必须声明为 junction"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_junction_without_branch_or_merge_degree() -> None:
    payload = solved_payload()
    payload["nodes"][1]["node_type"] = "junction"
    with pytest.raises(ValidationError, match="至少连接 3 个 segment"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_rejects_junction_without_both_inflow_and_outflow() -> None:
    payload = branched_payload()
    junction = payload["nodes"][1]
    junction["ports"][0]["direction"] = "outlet"
    payload["nodes"][0]["ports"][0]["direction"] = "inlet"
    payload["segments"][0]["start_port_id"] = "j-001-in"
    payload["segments"][0]["end_port_id"] = "mh-001-out"
    payload["segments"][0]["centerline"].reverse()
    payload["segments"][0]["start_invert_m"] = 9.97
    payload["segments"][0]["end_invert_m"] = 10.0
    payload["segments"][0]["slope"] = -0.003
    payload["systems"][0]["flow_regime"] = "pressure"
    with pytest.raises(ValidationError, match="同时具有入流和出流"):
        CompiledUtilityIR.model_validate(payload)


def test_compiled_ir_accepts_multiple_independent_connected_systems() -> None:
    payload = solved_payload()
    payload["systems"].append(
        {
            "system_id": "sys-storm",
            "name": "雨水重力系统",
            "system_type": "stormwater",
            "flow_regime": "gravity",
            "ifc_class": "IfcDistributionSystem",
            "ifc_predefined_type": "STORMWATER",
        }
    )
    for node_id, x_m, direction in (("sw-001", 0.0, "outlet"), ("sw-002", 10.0, "inlet")):
        payload["nodes"].append(
            {
                "node_id": node_id,
                "system_id": "sys-storm",
                "node_type": "manhole",
                "position": {"x_m": x_m, "y_m": 20.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": f"{node_id}-{'out' if direction == 'outlet' else 'in'}",
                        "direction": direction,
                        "position": {"x_m": x_m, "y_m": 20.0, "z_m": 10.0 - x_m * 0.003},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            }
        )
    payload["segments"].append(
        {
            "segment_id": "storm-pipe-001",
            "system_id": "sys-storm",
            "start_port_id": "sw-001-out",
            "end_port_id": "sw-002-in",
            "centerline": [
                {"x_m": 0.0, "y_m": 20.0, "z_m": 10.0},
                {"x_m": 10.0, "y_m": 20.0, "z_m": 9.97},
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
    )
    compiled = CompiledUtilityIR.model_validate(payload)
    assert {system.system_id for system in compiled.systems} == {"sys-sewage", "sys-storm"}


def test_compiled_ir_rejects_system_without_network_elements() -> None:
    payload = solved_payload()
    payload["systems"].append(
        {
            "system_id": "sys-storm",
            "name": "雨水重力系统",
            "system_type": "stormwater",
            "flow_regime": "gravity",
            "ifc_class": "IfcDistributionSystem",
            "ifc_predefined_type": "STORMWATER",
        }
    )
    with pytest.raises(ValidationError, match="没有 node|没有 segment"):
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
    first_payload = branched_payload()
    second_payload = branched_payload()
    second_payload["systems"].reverse()
    second_payload["nodes"].reverse()
    for node in second_payload["nodes"]:
        node["ports"].reverse()
    second_payload["segments"].reverse()
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
