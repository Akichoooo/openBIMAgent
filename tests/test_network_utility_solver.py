"""M1.5 多节点重力管网 Solver 的协议、求解和失败关闭测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openbimagent.domain_gate import GateStatus
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import UtilitySolverError, solve_network_gravity_utility


def network_payload() -> dict:
    return {
        "protocol_version": "0.1",
        "request_id": "network-case-001",
        "source_ir_sha256": "c" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "system_id": "sys-wastewater",
        "system_name": "污水重力系统",
        "nodes": [
            {
                "node_id": "source",
                "node_type": "manhole",
                "x_m": 0.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": 10.0,
            },
            {
                "node_id": "junction",
                "node_type": "junction",
                "x_m": 10.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
            {
                "node_id": "out-a",
                "node_type": "manhole",
                "x_m": 20.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
            {
                "node_id": "out-b",
                "node_type": "manhole",
                "x_m": 10.0,
                "y_m": 10.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
        ],
        "segments": [
            {
                "segment_id": "pipe-001",
                "start_node_id": "source",
                "end_node_id": "junction",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
            {
                "segment_id": "pipe-002",
                "start_node_id": "junction",
                "end_node_id": "out-a",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
            {
                "segment_id": "pipe-003",
                "start_node_id": "junction",
                "end_node_id": "out-b",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
        ],
        "collision_context": {"coverage": "complete", "obstacles": []},
    }


def merge_payload() -> dict:
    payload = network_payload()
    payload["request_id"] = "network-merge-001"
    payload["nodes"] = [
        {
            "node_id": "source-a",
            "node_type": "manhole",
            "x_m": 0.0,
            "y_m": 0.0,
            "ground_elevation_m": 11.0,
            "invert_anchor_m": 10.0,
        },
        {
            "node_id": "source-b",
            "node_type": "manhole",
            "x_m": 0.0,
            "y_m": 10.0,
            "ground_elevation_m": 11.0,
            "invert_anchor_m": 9.8,
        },
        {
            "node_id": "junction",
            "node_type": "junction",
            "x_m": 10.0,
            "y_m": 5.0,
            "ground_elevation_m": 11.0,
            "invert_anchor_m": None,
        },
        {
            "node_id": "out",
            "node_type": "manhole",
            "x_m": 20.0,
            "y_m": 5.0,
            "ground_elevation_m": 11.0,
            "invert_anchor_m": None,
        },
    ]
    payload["segments"] = [
        {
            "segment_id": "in-a",
            "start_node_id": "source-a",
            "end_node_id": "junction",
            "diameter_mm": 300.0,
            "material": "concrete",
            "design_slope": 0.003,
            "surface_context": "driveway",
        },
        {
            "segment_id": "in-b",
            "start_node_id": "source-b",
            "end_node_id": "junction",
            "diameter_mm": 300.0,
            "material": "concrete",
            "design_slope": 0.003,
            "surface_context": "driveway",
        },
        {
            "segment_id": "out-main",
            "start_node_id": "junction",
            "end_node_id": "out",
            "diameter_mm": 300.0,
            "material": "concrete",
            "design_slope": 0.003,
            "surface_context": "driveway",
        },
    ]
    return payload


def test_network_solver_schema_is_registered() -> None:
    gate = SchemaGate()
    assert "network_utility_solver_input.schema.json" in gate.schema_names()
    assert gate.validate_artifact("network_utility_solver_input", network_payload()) == []


def test_network_solver_solves_branched_profile_deterministically() -> None:
    result = solve_network_gravity_utility(network_payload())
    segments = {item.segment_id: item for item in result.compiled_ir.segments}
    assert segments["pipe-001"].start_invert_m == pytest.approx(10.0)
    assert segments["pipe-001"].end_invert_m == pytest.approx(9.97)
    assert segments["pipe-002"].start_invert_m == pytest.approx(9.97)
    assert segments["pipe-002"].end_invert_m == pytest.approx(9.94)
    assert segments["pipe-003"].end_invert_m == pytest.approx(9.94)
    assert len(result.compiled_ir.nodes) == 4
    assert result.domain_gate.status is GateStatus.PASS


def test_network_solver_merge_uses_lowest_incoming_invert() -> None:
    result = solve_network_gravity_utility(merge_payload())
    segments = {item.segment_id: item for item in result.compiled_ir.segments}
    expected_in_b = 9.8 - 0.003 * (125.0**0.5)
    assert segments["out-main"].start_invert_m == pytest.approx(expected_in_b)
    assert segments["out-main"].start_invert_m < segments["in-a"].end_invert_m


def test_network_solver_allows_explicit_downward_drop_anchor() -> None:
    payload = network_payload()
    payload["nodes"][1]["invert_anchor_m"] = 9.8
    result = solve_network_gravity_utility(payload)
    segments = {item.segment_id: item for item in result.compiled_ir.segments}
    assert segments["pipe-001"].end_invert_m == pytest.approx(9.97)
    assert segments["pipe-002"].start_invert_m == pytest.approx(9.8)


def test_network_solver_rejects_upward_anchor_conflict() -> None:
    payload = network_payload()
    payload["nodes"][1]["invert_anchor_m"] = 10.1
    with pytest.raises(UtilitySolverError, match="高于来流管底|抬升冲突"):
        solve_network_gravity_utility(payload)


def test_network_solver_rejects_missing_source_anchor() -> None:
    payload = network_payload()
    payload["nodes"][0]["invert_anchor_m"] = None
    with pytest.raises(UtilitySolverError, match="源节点.*invert_anchor_m"):
        solve_network_gravity_utility(payload)


def test_network_solver_rejects_cycle_disconnected_and_unknown_reference() -> None:
    cycle = network_payload()
    cycle["segments"].append(
        {
            "segment_id": "cycle",
            "start_node_id": "out-b",
            "end_node_id": "source",
            "diameter_mm": 300.0,
            "material": "concrete",
            "design_slope": 0.003,
            "surface_context": "driveway",
        }
    )
    with pytest.raises(UtilitySolverError, match="有向环路"):
        solve_network_gravity_utility(cycle)

    disconnected = network_payload()
    disconnected["nodes"].extend(
        [
            {
                "node_id": "isolated-a",
                "node_type": "manhole",
                "x_m": 100.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": 10.0,
            },
            {
                "node_id": "isolated-b",
                "node_type": "manhole",
                "x_m": 110.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
        ]
    )
    disconnected["segments"].append(
        {
            "segment_id": "isolated-pipe",
            "start_node_id": "isolated-a",
            "end_node_id": "isolated-b",
            "diameter_mm": 300.0,
            "material": "concrete",
            "design_slope": 0.003,
            "surface_context": "driveway",
        }
    )
    with pytest.raises(UtilitySolverError, match="不连通"):
        solve_network_gravity_utility(disconnected)

    unknown = network_payload()
    unknown["segments"][0]["end_node_id"] = "missing"
    with pytest.raises(UtilitySolverError, match="未知 node"):
        solve_network_gravity_utility(unknown)


def test_network_solver_rejects_wrong_junction_semantics_and_unsupported_diameter() -> None:
    wrong_type = network_payload()
    wrong_type["nodes"][1]["node_type"] = "manhole"
    with pytest.raises(UtilitySolverError, match="必须声明为 junction"):
        solve_network_gravity_utility(wrong_type)

    unsupported = network_payload()
    unsupported["segments"][0]["diameter_mm"] = 400.0
    with pytest.raises(UtilitySolverError, match="300.0 was expected|仅支持 DN300"):
        solve_network_gravity_utility(unsupported)


def test_network_solver_rejects_cover_conflict_and_preserves_unknown_hydraulics() -> None:
    conflict = network_payload()
    conflict["nodes"][0]["invert_anchor_m"] = 10.2
    result = solve_network_gravity_utility(conflict)
    assert result.compiled_ir.domain_evidence()["cover_depth_in_spec"]["ok"] is False
    assert result.domain_gate.status is GateStatus.FAIL
    assert result.compiled_ir.domain_evidence()["hydraulics_in_spec"]["ok"] is None


def test_network_solver_output_is_canonical_across_input_order() -> None:
    first = solve_network_gravity_utility(network_payload()).compiled_ir
    reordered = deepcopy(network_payload())
    reordered["nodes"].reverse()
    reordered["segments"].reverse()
    second = solve_network_gravity_utility(reordered).compiled_ir
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256() == second.canonical_sha256()


def test_network_solver_rejects_unknown_fields() -> None:
    payload = network_payload()
    payload["route_guess"] = True
    with pytest.raises(UtilitySolverError, match="未通过门禁"):
        solve_network_gravity_utility(payload)
