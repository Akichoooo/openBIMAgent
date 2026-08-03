"""M1.5 T4 规则网格路线、复杂地表标高和网络接入测试。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import (
    GridRouteSolverInput,
    MunicipalRuleSet,
    RouteSolveStatus,
    RouteSolverError,
    apply_grid_route_to_network_input,
    compile_municipal_rule_set,
    solve_grid_route,
    solve_network_gravity_utility,
)
from tests.test_network_utility_solver import network_payload


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


def _review_required_rule_set() -> MunicipalRuleSet:
    payload = compile_municipal_rule_set().model_dump(mode="json")
    target = next(rule for rule in payload["rules"] if rule["rule_key"] == "MU-CLEAR-001:building")
    target["confidence"] = "medium"
    target["enforcement"] = "review_required"
    canonical = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return MunicipalRuleSet.model_validate(payload)


def test_grid_route_schema_is_registered() -> None:
    gate = SchemaGate()
    assert "grid_route_solver_input.schema.json" in gate.schema_names()
    assert gate.validate_artifact("grid_route_solver_input", route_payload()) == []
    result = solve_grid_route(route_payload())
    assert gate.validate_artifact("grid_route_solver_result", result.model_dump(mode="json")) == []


def test_grid_route_uses_stable_tie_break_and_profile() -> None:
    first = solve_grid_route(route_payload())
    assert first.status is RouteSolveStatus.FEASIBLE
    selected = first.selected_candidate()
    assert len(first.candidates) == 3
    assert [candidate.rank for candidate in first.candidates] == [1, 2, 3]
    assert [(cell.x_index, cell.y_index) for cell in selected.cells] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 2),
        (2, 2),
    ]
    assert selected.horizontal_length_m == pytest.approx(4.0)
    assert selected.points[-1].invert_m == pytest.approx(9.988)
    assert selected.constraint_report.cover_depth_in_spec is True
    assert selected.constraint_report.clearance_in_spec is True


def test_grid_route_detours_around_production_rule_obstacle() -> None:
    payload = route_payload(width=9, height=7)
    payload["end"]["cell"] = {"x_index": 8, "y_index": 0}
    payload["obstacles"] = [
        {
            "obstacle_id": "building-001",
            "kind": "aabb",
            "category": "building",
            "min_corner": {"x_m": 3.8, "y_m": -0.2, "z_m": 0.0},
            "max_corner": {"x_m": 4.2, "y_m": 0.2, "z_m": 20.0},
        }
    ]
    result = solve_grid_route(payload)
    selected = result.selected_candidate()
    assert result.status is RouteSolveStatus.FEASIBLE
    assert max(cell.y_index for cell in selected.cells) >= 3
    assert selected.constraint_report.applied_rule_keys == ("MU-CLEAR-001:building",)
    assert selected.constraint_report.minimum_clearance_margin_m >= -1e-6


def test_grid_route_rejects_untrusted_rule_and_rule_set_hash_drift() -> None:
    payload = route_payload()
    payload["obstacles"] = [
        {
            "obstacle_id": "building-001",
            "kind": "aabb",
            "category": "building",
            "min_corner": {"x_m": 1.2, "y_m": 1.2, "z_m": 0.0},
            "max_corner": {"x_m": 1.8, "y_m": 1.8, "z_m": 20.0},
        }
    ]
    review = _review_required_rule_set()
    payload["municipal_rule_set_sha256"] = review.canonical_sha256
    with pytest.raises(RouteSolverError, match="review_required|生产执行资格"):
        solve_grid_route(payload, municipal_rule_set=review)

    drift = route_payload()
    drift["municipal_rule_set_sha256"] = "0" * 64
    with pytest.raises(RouteSolverError, match="RuleSet.*SHA-256|sha256"):
        solve_grid_route(drift)


def test_grid_route_rejects_endpoint_corridor_and_surface_drift() -> None:
    outside = route_payload()
    outside["end"]["cell"] = {"x_index": 3, "y_index": 2}
    with pytest.raises(RouteSolverError, match="网格范围"):
        solve_grid_route(outside)

    forbidden = route_payload()
    forbidden["allowed_cells"] = forbidden["allowed_cells"][:-1]
    forbidden["surface_samples"] = forbidden["surface_samples"][:-1]
    with pytest.raises(RouteSolverError, match="起终点.*走廊"):
        solve_grid_route(forbidden)

    missing_surface = route_payload()
    missing_surface["surface_samples"] = missing_surface["surface_samples"][:-1]
    with pytest.raises(RouteSolverError, match="地表高程.*缺失|surface"):
        solve_grid_route(missing_surface)


def test_grid_route_reports_corridor_obstacle_and_cover_failures() -> None:
    disconnected = route_payload()
    disconnected["allowed_cells"] = [
        {"x_index": 0, "y_index": 0},
        {"x_index": 2, "y_index": 2},
    ]
    disconnected["surface_samples"] = [
        {"cell": cell, "ground_elevation_m": 12.0}
        for cell in disconnected["allowed_cells"]
    ]
    result = solve_grid_route(disconnected)
    assert result.status is RouteSolveStatus.NO_FEASIBLE_ROUTE
    assert result.failure_reason == "corridor_disconnected"

    blocked = route_payload(width=9, height=1)
    blocked["end"]["cell"] = {"x_index": 8, "y_index": 0}
    blocked["obstacles"] = [
        {
            "obstacle_id": "building-001",
            "kind": "aabb",
            "category": "building",
            "min_corner": {"x_m": 3.8, "y_m": -0.2, "z_m": 0.0},
            "max_corner": {"x_m": 4.2, "y_m": 0.2, "z_m": 20.0},
        }
    ]
    result = solve_grid_route(blocked)
    assert result.status is RouteSolveStatus.NO_FEASIBLE_ROUTE
    assert result.failure_reason == "obstacle_blocked"

    cover = route_payload()
    for sample in cover["surface_samples"]:
        sample["ground_elevation_m"] = 10.8
    result = solve_grid_route(cover)
    assert result.status is RouteSolveStatus.NO_FEASIBLE_ROUTE
    assert result.failure_reason == "cover_conflict"


def test_grid_route_is_canonical_across_unordered_input_collections() -> None:
    first_input = GridRouteSolverInput.model_validate(route_payload())
    reordered = deepcopy(route_payload())
    reordered["allowed_cells"].reverse()
    reordered["surface_samples"].reverse()
    second_input = GridRouteSolverInput.model_validate(reordered)
    assert first_input.canonical_sha256() == second_input.canonical_sha256()

    first = solve_grid_route(first_input)
    second = solve_grid_route(second_input)
    assert first.canonical_sha256() == second.canonical_sha256()


def test_grid_route_search_limit_is_unknown_not_no_route() -> None:
    payload = route_payload(width=4, height=4)
    payload["max_search_expansions"] = 1
    result = solve_grid_route(payload)
    assert result.status is RouteSolveStatus.UNKNOWN
    assert result.failure_reason == "search_limit_exceeded"


def test_grid_route_rejects_unknown_fields() -> None:
    payload = route_payload()
    payload["llm_waypoints"] = [[0, 0], [2, 2]]
    with pytest.raises(RouteSolverError, match="未通过门禁"):
        solve_grid_route(payload)


def test_grid_route_rejects_end_anchor_instead_of_ignoring_it() -> None:
    payload = route_payload()
    payload["end"]["invert_anchor_m"] = 9.0
    with pytest.raises(RouteSolverError, match="不接受终点 invert_anchor_m|未通过门禁"):
        solve_grid_route(payload)


def test_route_adapter_rejects_binding_and_candidate_drift() -> None:
    route = route_payload(width=11, height=1)
    route["request_id"] = "pipe-001-binding-drift"
    route["source_ir_sha256"] = "c" * 64
    route["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    route["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route_result = solve_grid_route(route)

    source_drift = deepcopy(route)
    source_drift["source_ir_sha256"] = "e" * 64
    source_result = solve_grid_route(source_drift)
    with pytest.raises(RouteSolverError, match="source_ir_sha256"):
        apply_grid_route_to_network_input(
            network_payload(), segment_id="pipe-001", route_input=source_drift, route_result=source_result
        )

    slope_drift = deepcopy(route)
    slope_drift["design_slope"] = 0.004
    slope_result = solve_grid_route(slope_drift)
    with pytest.raises(RouteSolverError, match="design_slope"):
        apply_grid_route_to_network_input(
            network_payload(), segment_id="pipe-001", route_input=slope_drift, route_result=slope_result
        )

    tampered = route_result.model_copy(
        update={
            "candidates": (
                route_result.selected_candidate().model_copy(update={"horizontal_length_m": 999.0}),
                *route_result.candidates[1:],
            )
        }
    )
    with pytest.raises(RouteSolverError, match="确定性重算候选集"):
        apply_grid_route_to_network_input(
            network_payload(), segment_id="pipe-001", route_input=route, route_result=tampered
        )


def test_route_adapter_allows_explicit_selection_from_untampered_candidates() -> None:
    route = route_payload(width=11, height=2)
    route["request_id"] = "pipe-001-alternate"
    route["source_ir_sha256"] = "c" * 64
    route["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    route["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    for sample in route["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    route_result = solve_grid_route(route)
    alternate = route_result.candidates[1]
    selected = route_result.model_copy(update={"selected_candidate_id": alternate.candidate_id})
    routed = apply_grid_route_to_network_input(
        network_payload(),
        segment_id="pipe-001",
        route_input=route,
        route_result=selected,
    )
    assert len(routed.nodes) > len(network_payload()["nodes"])
    assert any(item.segment_id.startswith("pipe-001-route-") for item in routed.segments)


def test_route_adapter_rejects_invert_anchor_drift() -> None:
    route = route_payload(width=11, height=1)
    route["request_id"] = "pipe-001-anchor-drift"
    route["source_ir_sha256"] = "c" * 64
    route["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 9.9,
    }
    route["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route_result = solve_grid_route(route)
    with pytest.raises(RouteSolverError, match="invert_anchor_m.*不一致"):
        apply_grid_route_to_network_input(
            network_payload(),
            segment_id="pipe-001",
            route_input=route,
            route_result=route_result,
        )


def test_selected_route_is_applied_before_network_solver() -> None:
    route = route_payload(width=11, height=2)
    route["request_id"] = "pipe-001-route"
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
    solved = solve_network_gravity_utility(routed_input)
    routed_segments = [
        segment for segment in solved.compiled_ir.segments
        if segment.segment_id.startswith("pipe-001-route-")
    ]
    assert len(routed_segments) == 3
    assert sum(segment.horizontal_length_m for segment in routed_segments) == pytest.approx(12.0)
    assert routed_segments[-1].end_invert_m == pytest.approx(9.964)
