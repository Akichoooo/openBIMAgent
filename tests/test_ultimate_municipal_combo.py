"""四大核心架构升级的一体化验收测试。

覆盖：DN300~DN600 动态查表求解、5 大拓扑算子与 Lock 节点传导、严格回滚门禁、
NBV 公垂线视角、纵断面 PNG 渲染与自由相机请求、BIM-Wiki-Ontology 双链词条、
两跳图谱检索与 fail_streak >= 2 探针熔断闭环。
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from openbimagent.server.municipal_workflow import MunicipalWorkflowStore, WorkflowError
from openbimagent.utility.candidate import (
    DEFAULT_EXCEPTION_APPROVAL_ID,
    SANDBOX_OPERATORS,
    UtilityCandidate,
    check_candidate_state,
    compute_engineering_cost,
    evaluate_repair_sandbox,
    validate_utility_candidate,
)
from openbimagent.utility.network_solver import (
    SUPPORTED_DIAMETER_RANGE_MM,
    lookup_pipe_normative_limits,
    solve_network_gravity_utility,
)
from openbimagent.utility.wiki_ontology import (
    compile_and_archive_wiki_case,
    retrieve_ontology_recipes,
    update_recipe_probe_feedback,
)


def chain_candidate(
    diameter: float = 300.0,
    slopes: tuple[float, ...] | float | None = None,
    anchors: tuple[float | None, ...] = (9.8, None),
    grounds: tuple[float, ...] | None = None,
    length: float = 10.0,
    obstacles: list[dict] | None = None,
    constraints: dict | None = None,
) -> dict:
    count = len(anchors)
    grounds = grounds or tuple(11.0 for _ in range(count))
    if slopes is None:
        slopes = tuple(lookup_pipe_normative_limits(diameter)["min_slope"] for _ in range(count - 1))
    elif isinstance(slopes, (int, float)):
        slopes = tuple(float(slopes) for _ in range(count - 1))
    return {
        "protocol_version": "0.1",
        "request_id": "ultimate-combo",
        "source_ir_sha256": "c" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:TEST",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "test datum",
        },
        "system_id": "sys-test",
        "system_name": "ultimate combo gravity wastewater",
        "nodes": [
            {
                "node_id": f"W{index + 1}",
                "node_type": "manhole",
                "x_m": length * index,
                "y_m": 0.0,
                "ground_elevation_m": grounds[index],
                "invert_anchor_m": anchors[index],
            }
            for index in range(count)
        ],
        "segments": [
            {
                "segment_id": f"P{index + 1}",
                "start_node_id": f"W{index + 1}",
                "end_node_id": f"W{index + 2}",
                "diameter_mm": diameter,
                "material": "concrete",
                "design_slope": slopes[index],
                "surface_context": "driveway",
                "inner_diameter_mm": diameter,
                "outer_diameter_mm": diameter + 60.0,
            }
            for index in range(count - 1)
        ],
        "collision_context": {"coverage": "complete", "obstacles": obstacles or []},
        **({"constraints": constraints} if constraints is not None else {}),
    }


# =====================================================================================
# Phase 1 —— 动态查表（Table-Driven Lookup）
# =====================================================================================


@pytest.mark.parametrize("diameter,min_slope,spacing", [
    (300.0, 0.003, 40.0),
    (400.0, 0.002, 40.0),
    (500.0, 0.0015, 40.0),
    (600.0, 0.0010, 40.0),
    (800.0, 0.0010, 50.0),
])
def test_lookup_pipe_normative_limits_band_table(diameter, min_slope, spacing):
    limits = lookup_pipe_normative_limits(diameter)
    assert limits["min_slope"] == min_slope
    assert limits["max_well_spacing_m"] == spacing
    assert limits["min_cover_m"] == pytest.approx(0.7)
    assert limits["min_obstacle_clearance_m"] == pytest.approx(0.15)
    assert limits["exception_min_cover_m"] == pytest.approx(0.45)
    sidewalk = lookup_pipe_normative_limits(diameter, surface_type="sidewalk")
    green = lookup_pipe_normative_limits(diameter, surface_type="green")
    assert sidewalk["min_cover_m"] == green["min_cover_m"] == pytest.approx(0.6)


def test_lookup_rejects_unsupported_diameters_only_outside_capability_range():
    for bad in (0.0, -300.0, 100.0, 2500.0):
        with pytest.raises(ValueError, match="unsupported_diameter"):
            lookup_pipe_normative_limits(bad)
    low, high = SUPPORTED_DIAMETER_RANGE_MM
    lookup_pipe_normative_limits(low)
    lookup_pipe_normative_limits(high)


@pytest.mark.parametrize("diameter", [300.0, 400.0, 500.0, 600.0])
def test_network_solver_solves_each_diameter_and_costs_are_real(diameter):
    payload = chain_candidate(diameter)
    result = solve_network_gravity_utility(payload)
    assert result.compiled_ir is not None
    assert all(segment.diameter_mm == diameter for segment in result.compiled_ir.segments)
    slope_evidence = [item for item in result.compiled_ir.evidence if item.check_name == "slope_in_spec"]
    assert slope_evidence and all(item.status.value == "pass" for item in slope_evidence)
    cost = compute_engineering_cost(payload)
    assert cost["total_cost_score"] == pytest.approx(cost["excavation_cost_index"] + cost["material_cost_index"], abs=1e-4)
    assert cost["excavation_cost_index"] > 0 and cost["total_length_m"] == pytest.approx(10.0)


def test_engineering_cost_excavation_formula_follows_depth_square():
    shallow = chain_candidate(300.0, anchors=(9.8, None))
    deeper = chain_candidate(300.0, anchors=(8.8, None))
    shallow_cost = compute_engineering_cost(shallow)["excavation_cost_index"]
    deeper_cost = compute_engineering_cost(deeper)["excavation_cost_index"]
    diameter_m, work_width = 0.3, 0.6
    length = 10.0
    mean_invert = (9.8 + (9.8 - 0.003 * length)) / 2  # 与求解器一致的管底平均高程
    expected_shallow = length * (diameter_m + work_width) * (11.0 - mean_invert) ** 2
    assert shallow_cost == pytest.approx(expected_shallow, rel=0.03)
    assert deeper_cost > shallow_cost * 1.5


# =====================================================================================
# Phase 1 —— 5 大拓扑算子 + Lock 节点传导
# =====================================================================================


def test_op_slope_regrade_uses_per_diameter_minimum_and_locks_block_only_locked_paths():
    candidate = chain_candidate(400.0, slopes=0.001)
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate))
    assert ledger["winning_operator"] == "op_slope_regrade"
    patch = ledger["winner_patches"][0]
    assert patch["path"] == "/segments/0/design_slope" and patch["value"] == 0.002
    locked = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate), locked_paths=("/segments",))
    assert locked["winner_patches"] == []


def test_op_drop_manhole_wins_overhead_conduit_crossing_and_only_touches_unlocked_nodes():
    obstacle = {
        "obstacle_id": "OB-PWR", "kind": "existing_pipe", "category": "power",
        "start_center": {"x_m": 15.0, "y_m": -4.0, "z_m": 9.55},
        "end_center": {"x_m": 15.0, "y_m": 4.0, "z_m": 9.55},
        "outer_diameter_mm": 200.0,
    }
    # DN600 管顶冠高 0.63m：W1 锚点取 9.0 保证起点覆土合规且被锁定，
    # 电力管线横穿 P2 上方形成三维外包络冲突 → 只能靠 W2 局部跌水下穿。
    candidate = chain_candidate(600.0, anchors=(9.0, None, None), obstacles=[obstacle])
    baseline = check_candidate_state(candidate)
    assert baseline["hard_clash_count"] >= 1
    assert baseline["violation_count"] == baseline["hard_clash_count"]  # 除碰撞外无其他病害
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate), locked_paths=("/nodes/0/invert_anchor_m",))
    assert ledger["winning_operator"] == "op_drop_manhole"
    winner = ledger["pareto_proposals"][0]
    assert winner["hard_clash_count"] == 0
    assert winner["violation_count"] == 0
    dropped = [node for node in ledger["winner_patches"][0]["value"] if node.get("manhole_type") == "drop_manhole"]
    assert len(dropped) == 1 and dropped[0]["node_id"] == "W2"  # W1 锁定 → 绝不动 W1
    assert dropped[0]["drop_height_m"] > 0
    # Lock 一刀切罢工 Bug 回归：锁定上游后仍必须给出修复而不是空手而归。
    assert ledger["winner_patches"]


def test_op_lateral_detour_inserts_orthogonal_route_around_building():
    obstacle = {
        "obstacle_id": "OB-BLD", "kind": "aabb", "category": "building",
        "min_corner": {"x_m": 9.0, "y_m": -0.5, "z_m": 8.0},
        "max_corner": {"x_m": 11.0, "y_m": 0.5, "z_m": 10.6},
    }
    candidate = chain_candidate(300.0, anchors=(9.8, None), length=20.0, obstacles=[obstacle])
    candidate["nodes"][1]["x_m"] = 20.0
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate))
    assert check_candidate_state(candidate)["hard_clash_count"] >= 1
    winner = ledger["pareto_proposals"][0]
    assert winner["hard_clash_count"] == 0
    assert "op_lateral_detour" in ledger["winning_operator"]
    detour_nodes = [n for n in ledger["winner_patches"][0]["value"] if n["node_id"].endswith(("-det-1", "-det-2"))]
    assert len(detour_nodes) == 2
    assert abs(detour_nodes[0]["y_m"]) >= 1.2  # 沿包络盒法向偏移避开障碍物中心


def test_op_insert_intermediate_manhole_splits_overlength_span():
    candidate = chain_candidate(300.0, anchors=(9.8, None), length=95.0)
    baseline = check_candidate_state(candidate)
    assert baseline["violation_count"] >= 1
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate))
    assert ledger["winning_operator"] == "op_insert_intermediate_manhole"
    new_segments = next(p["value"] for p in ledger["winner_patches"] if p["path"] == "/segments")
    lengths = []
    nodes = {n["node_id"]: n for n in next(p["value"] for p in ledger["winner_patches"] if p["path"] == "/nodes")}
    for segment in new_segments:
        a, b = nodes[segment["start_node_id"]], nodes[segment["end_node_id"]]
        lengths.append(math.hypot(b["x_m"] - a["x_m"], b["y_m"] - a["y_m"]))
    assert max(lengths) <= 40.0 + 1e-6  # MU-WELL-001 设计保守分档
    mid_nodes = [n for n in nodes if n.startswith("W_mid_")]
    assert mid_nodes
    winner = ledger["pareto_proposals"][0]
    assert winner["violation_count"] == 0


def test_op_concrete_encasement_exception_requires_explicit_approval():
    candidate = chain_candidate(300.0, anchors=(10.02, None))
    # 未授权：CEGIS 绝不能自授国标特批 → 无补丁。
    unapproved = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate), locked_paths=("/nodes/0/invert_anchor_m",))
    assert unapproved["winner_patches"] == []
    # 显式授权 + 起点锁定：满包混凝土加固受控特批胜出。
    approved = chain_candidate(300.0, anchors=(10.02, None), constraints={"allow_exception_approval": True})
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=approved), locked_paths=("/nodes/0/invert_anchor_m",))
    assert ledger["winning_operator"] == "op_concrete_encasement_exception"
    segments = next(p["value"] for p in ledger["winner_patches"] if p["path"] == "/segments")
    assert all(s.get("protection_measure") == "concrete_encasement" for s in segments)
    patched = deepcopy(approved)
    patched["segments"] = deepcopy(segments)
    report = check_candidate_state(patched)
    assert report["exception_applied"] is True
    cover_issues = [i for i in report["issues"] if i["check_name"] == "cover_depth_in_spec"]
    assert cover_issues and all(i.get("exception_applied") and i.get("exception_approval_id") == DEFAULT_EXCEPTION_APPROVAL_ID for i in cover_issues)
    assert report["violation_count"] == 0


def test_pareto_ledger_ranks_lexicographically_and_keeps_compat_fields():
    candidate = chain_candidate(300.0, slopes=0.001)
    ledger = evaluate_repair_sandbox(UtilityCandidate(solver_input=candidate))
    assert ledger["pareto_proposals"]
    for proposal in ledger["pareto_proposals"]:
        for key in ("operator_name", "violation_count", "hard_clash_count", "total_cost_score", "exception_applied", "patches"):
            assert key in proposal
    keys = [(p["violation_count"], p["hard_clash_count"], p["total_cost_score"]) for p in ledger["pareto_proposals"]]
    assert keys == sorted(keys)
    baseline = ledger["baseline"]
    assert baseline["violation_count"] >= 1 and "engineering_cost" in baseline


# =====================================================================================
# Phase 1 —— 严格回滚门禁（杜绝修旧引新）
# =====================================================================================


def _store(tmp_path: Path, candidate: dict, ontology: Path | None = None) -> MunicipalWorkflowStore:
    store = MunicipalWorkflowStore(tmp_path / "sessions", ontology_base_dir=ontology)
    store.save("rollback", {"candidate": deepcopy(candidate), "source": "correction"})
    return store


def test_refine_rolls_back_patches_introducing_new_violation_codes(tmp_path):
    from test_iterative_refinement_and_experience import _candidate, _validator

    store = MunicipalWorkflowStore(tmp_path / "sessions", validator=_validator)
    draft = _candidate()
    draft["segments"][0]["design_slope"] = 0.003  # 坡度已合规
    store.save("sid", {"candidate": draft, "source": "correction"})

    def visual(state, output_dir, **kwargs):
        slope = state["candidate"]["segments"][0]["design_slope"]
        return {
            "status": "review_required", "failure_code": None, "issues": [], "preview": {},
            # 同数硬违规但引入修改前不存在的新违规码（cover 失败）
            "patches": [{"path": "/nodes/0/invert_anchor_m", "old_value": state["candidate"]["nodes"][0]["invert_anchor_m"],
                         "value": 9.99, "reason": "bad visual lowering", "constraint_ids": ["R-COVER"]}],
        }

    result = store.iterative_refine("sid", {}, lambda _: [], visual, max_rounds=2)
    assert result["rolled_back"] is True and result["degradation_rollback"] is True
    assert "新违规码" in result["rollback_reason"]
    state = store.get("sid")
    assert state["candidate"]["nodes"][0]["invert_anchor_m"] == pytest.approx(8.5)


def test_refine_default_rounds_is_now_four_and_records_audit(tmp_path):
    from test_iterative_refinement_and_experience import _candidate, _proposer, _validator, _visual_success

    store = MunicipalWorkflowStore(tmp_path / "sessions", validator=_validator)
    store.save("sid", {"candidate": _candidate(), "source": "correction"})
    result = store.iterative_refine("sid", {}, _proposer, _visual_success)
    assert result["max_rounds"] == 4
    audit_path = tmp_path / "sessions" / "sid" / "state_diff_audit.json"
    assert audit_path.is_file()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    last_run = audit["runs"][-1]
    assert last_run["max_rounds"] == 4
    first_round = last_run["rounds"][0]
    # 桩校验器候选无法产出真实沙箱账本，但轮次审计仍须记录应用补丁与本体轨迹。
    assert "applied_patches" in first_round and "ontology_evolution_trace" in first_round


def test_refine_rolls_back_obstacle_clash_introduction(tmp_path):
    from test_iterative_refinement_and_experience import _candidate

    clash_validator_calls = {"count": 0}

    def validator(candidate: dict, context) -> dict:
        clash_validator_calls["count"] += 1
        issues = [{
            "rule_id": "R-SLOPE", "check_name": "min_slope", "status": "pass", "severity": "info",
            "object_ids": ["P-1"], "field_path": "design_slope", "message": "ok",
        }]
        if candidate["nodes"][0]["invert_anchor_m"] > 9.9:
            issues.append({
                "rule_id": "PHYSICAL-OBSTACLE", "check_name": "physical_obstacle_clearance",
                "status": "fail", "severity": "error", "object_ids": ["P-1"],
                "message": "obstacle clash introduced",
            })
        return {
            "executable": not any(i["status"] == "fail" for i in issues), "issues": issues,
            "compiled_ir": {"protocol_version": "0.1"}, "rule_context": {},
            "rule_context_sha256": "x", "candidate_sha256": "y",
        }

    store = MunicipalWorkflowStore(tmp_path / "sessions", validator=validator)
    store.save("sid", {"candidate": _candidate(), "source": "correction"})

    def visual(state, output_dir, **kwargs):
        return {
            "status": "review_required", "failure_code": None, "issues": [], "preview": {},
            "patches": [{"path": "/nodes/0/invert_anchor_m", "old_value": state["candidate"]["nodes"][0]["invert_anchor_m"],
                         "value": 9.99, "reason": "raises pipe into obstacle", "constraint_ids": ["R-COVER"]}],
        }

    result = store.iterative_refine("sid", {}, lambda _: [], visual, max_rounds=1)
    assert result["rolled_back"] is True
    assert "obstacle_clearance" in result["rollback_reason"] or "新违规码" in result["rollback_reason"]
    assert store.get("sid")["candidate"]["nodes"][0]["invert_anchor_m"] == pytest.approx(8.5)


# =====================================================================================
# Phase 2 —— NBV 公垂线视角 / 纵断面 / 自由相机
# =====================================================================================


def test_nbv_common_perpendicular_is_orthogonal_to_both_axes(tmp_path):
    from openbimagent.vision.municipal import compute_clash_normal_nbv

    obstacle = {
        "obstacle_id": "OB-PWR", "kind": "existing_pipe", "category": "power",
        "start_center": {"x_m": 15.0, "y_m": -4.0, "z_m": 10.35},
        "end_center": {"x_m": 15.0, "y_m": 4.0, "z_m": 10.35},
        "outer_diameter_mm": 200.0,
    }
    candidate = chain_candidate(300.0, anchors=(9.8, None, None), obstacles=[obstacle])
    nbv = compute_clash_normal_nbv(candidate)
    normal = nbv["normal_unit"]
    for axis in (nbv["pipe_axis_unit"], nbv["obstacle_axis_unit"]):
        assert abs(sum(a * b for a, b in zip(normal, axis))) < 1e-9
    assert math.isclose(math.sqrt(sum(v * v for v in normal)), 1.0, abs_tol=1e-9)
    assert -90 <= nbv["azimuth_deg"] < 360 and -90 <= nbv["elevation_deg"] <= 90
    assert len(nbv["focus_center_m"]) == 3
    shifted = compute_clash_normal_nbv(candidate, delta_azimuth_deg=20.0, delta_elevation_deg=-10.0)
    assert shifted["azimuth_deg"] == pytest.approx((nbv["azimuth_deg"] + 20.0) % 360, abs=1e-6)
    box = {
        "obstacle_id": "OB-BOX", "kind": "aabb", "category": "building",
        "min_corner": {"x_m": 9.0, "y_m": -0.5, "z_m": 8.0},
        "max_corner": {"x_m": 11.0, "y_m": 0.5, "z_m": 10.6},
    }
    box_nbv = compute_clash_normal_nbv(chain_candidate(300.0, anchors=(9.8, None), length=20.0, obstacles=[box]), segment_id="P1", obstacle_id="OB-BOX")
    assert box_nbv["segment_id"] == "P1" and box_nbv["obstacle_id"] == "OB-BOX"
    assert box_nbv["fallback_no_obstacle"] is False


def test_longitudinal_profile_renders_real_png(tmp_path):
    from openbimagent.vision.municipal import render_longitudinal_profile_view

    obstacle = {
        "obstacle_id": "OB-PWR", "kind": "existing_pipe", "category": "power",
        "start_center": {"x_m": 15.0, "y_m": -4.0, "z_m": 10.1},
        "end_center": {"x_m": 15.0, "y_m": 4.0, "z_m": 10.1},
        "outer_diameter_mm": 200.0,
    }
    candidate = chain_candidate(300.0, anchors=(9.8, None, None), obstacles=[obstacle])
    candidate["nodes"][1]["manhole_type"] = "drop_manhole"
    candidate["nodes"][1]["drop_height_m"] = 0.35
    candidate["segments"][1]["protection_measure"] = "concrete_encasement"
    out = tmp_path / "profile.png"
    meta = render_longitudinal_profile_view(candidate, out)
    assert meta["status"] == "rendered"
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 10_000
    assert meta["profile_length_m"] == pytest.approx(20.0)
    assert meta["obstacles_projected"] == 1
    assert meta["exception_segments"] == ["P2"]
    assert meta["chain_nodes"] == ["W1", "W2", "W3"]


def test_custom_camera_views_three_modes(tmp_path):
    from openbimagent.vision.municipal import render_custom_camera_views

    obstacle = {
        "obstacle_id": "OB-PWR", "kind": "existing_pipe", "category": "power",
        "start_center": {"x_m": 15.0, "y_m": -4.0, "z_m": 10.35},
        "end_center": {"x_m": 15.0, "y_m": 4.0, "z_m": 10.35},
        "outer_diameter_mm": 200.0,
    }
    candidate = chain_candidate(300.0, anchors=(9.8, None, None), obstacles=[obstacle])

    def fake_render(scad_path, output_dir, focus, distance, view_id):
        path = Path(output_dir) / f"{view_id}.png"
        path.write_bytes(_minimal_png())
        return path

    records = render_custom_camera_views(
        None, candidate,
        [
            {"mode": "free_orbit", "view_id": "orbit", "azimuth_deg": 40.0, "elevation_deg": 25.0, "zoom_margin_m": 2.0},
            {"mode": "nbv_clash_normal", "view_id": "nbv"},
            {"mode": "longitudinal_profile", "view_id": "prof"},
            {"mode": "free_orbit", "view_id": "no_scad"},
        ],
        tmp_path, render_fn=fake_render,
    )
    by_id = {record["view_id"]: record for record in records}
    # free_orbit/nbv 无 compiled IR → 明确 render_unavailable，绝不伪造。
    assert by_id["orbit"]["status"] == "render_unavailable"
    assert by_id["nbv"]["status"] == "render_unavailable"
    assert by_id["no_scad"]["status"] == "render_unavailable"
    assert by_id["prof"]["status"] == "rendered" and Path(by_id["prof"]["path"]).is_file()

    compiled = validate_utility_candidate(UtilityCandidate(solver_input=chain_candidate(300.0, anchors=(9.8, None)))).compiled_ir
    records = render_custom_camera_views(
        compiled, chain_candidate(300.0, anchors=(9.8, None)),
        [{"mode": "free_orbit", "view_id": "orbit2", "azimuth_deg": 40.0, "elevation_deg": 25.0}],
        tmp_path, render_fn=fake_render,
    )
    assert records[0]["status"] == "rendered"
    assert records[0]["camera"]["azimuth_deg"] == 40.0
    unsupported = render_custom_camera_views(None, candidate, [{"mode": "wormhole", "view_id": "x"}], tmp_path)
    assert unsupported[0]["status"] == "unsupported_mode"


def _minimal_png() -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x40\x80\xc0" * 8 for _ in range(8))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_som_layer_annotates_compass_and_flow_arrows(tmp_path):
    from openbimagent.vision.municipal import municipal_to_scad

    compiled = validate_utility_candidate(UtilityCandidate(solver_input=chain_candidate(300.0, anchors=(9.8, None)))).compiled_ir
    source, geometry = municipal_to_scad(compiled)
    assert "+X = E (East)" in source and "+Y = N (North)" in source and "+Z = Up (Elev. m)" in source
    assert "W1 -> W2" in source
    som = geometry["som_layer"]
    assert som["flow_arrows"] and som["flow_arrows"][0]["from_node"] == "W1"
    assert som.get("elevation_ruler_m")


# =====================================================================================
# Phase 3 —— BIM-Wiki-Ontology 双链词条 / 两跳检索 / 探针熔断
# =====================================================================================


def _compile_case(base: Path, session: str, operator: str, pattern_rules: list[str]) -> dict:
    final_state = {
        "candidate": chain_candidate(300.0, anchors=(9.8, None)),
        "validation": {"issues": []},
        "requirement_brief": f"combo {session}",
        "history": [],
    }
    audit = {"runs": [{"rounds": [{
        "winning_operator": operator,
        "active_camera_trajectory": [
            {"view_id": "nbv_clash_normal", "mode": "nbv_clash_normal", "status": "rendered", "camera": {"azimuth_deg": 12.0}},
        ],
    }]}]}
    initial = {"validation": {"issues": [{"rule_id": rule, "status": "fail", "severity": "error"} for rule in pattern_rules]}}
    return compile_and_archive_wiki_case(session, initial, final_state, audit, base_dir=base)


def test_wiki_case_compiles_bilinks_and_syncs_legacy_index(tmp_path):
    base = tmp_path / "wiki"
    result = _compile_case(base, "combo-1", "op_drop_manhole", ["MU-ELEV-001", "MU-DRAIN-004"])
    case_md = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert case_md.startswith("---")
    assert "[[Rule/MU-ELEV-001]]" in case_md
    assert "[[Pattern/Shallow_Cover]]" in case_md
    assert "[[Operator/op_drop_manhole]]" in case_md
    assert "S0 -> S1 -> S2" in case_md
    assert "nbv_clash_normal" in case_md
    legacy = json.loads((base.parent / "assets" / "auto_archive" / "index.json").read_text(encoding="utf-8"))
    assert legacy[0]["session_id"] == "combo-1" and legacy[0]["wiki_case_id"] == "CASE-combo-1"
    graph = json.loads((base / "ontology_graph.json").read_text(encoding="utf-8"))
    assert "Case/CASE-combo-1" in graph["nodes"]
    assert any(e["relation"] == "applied" and e["to"] == "Operator/op_drop_manhole" for e in graph["edges"])


def test_two_hop_retrieval_returns_recipes_and_filters_demoted(tmp_path):
    base = tmp_path / "wiki"
    _compile_case(base, "combo-2", "op_insert_intermediate_manhole", ["MU-WELL-001"])
    recipes = retrieve_ontology_recipes({}, [{"rule_id": "MU-WELL-001", "check_name": "manhole_spacing_in_spec"}], base_dir=base, top_k=3)
    assert recipes["matched_patterns"] == ["Pattern/Overlength_Span"]
    assert [case["case_id"] for case in recipes["cases"]] == ["CASE-combo-2"]
    assert "op_insert_intermediate_manhole" in recipes["operator_priority"]
    assert recipes["operator_priority"][0] == "op_insert_intermediate_manhole"  # 案例权重驱动
    modes = {view["mode"] for view in recipes["recommended_camera_requests"]}
    assert "longitudinal_profile" in modes
    assert any(asset["asset"] == "Drop_Manhole_SCAD" or asset["asset"] == "Lateral_Detour_Well_Pair" for asset in recipes["reusable_assets"])
    # 熔断闭环：连续 2 次探针失败 → demoted，检索自动过滤。
    update_recipe_probe_feedback("CASE-combo-2", False, base_dir=base)
    feedback = update_recipe_probe_feedback("CASE-combo-2", False, base_dir=base)
    assert feedback["fail_streak"] == 2 and feedback["demoted"] is True
    markdown = (base / "cases" / "CASE-combo-2.md").read_text(encoding="utf-8")
    assert "> [!WARNING] DEMOTED BY HARNESS PROBE (fail_streak >= 2)" in markdown
    after = retrieve_ontology_recipes({}, [{"rule_id": "MU-WELL-001"}], base_dir=base)
    assert after["cases"] == [] and after["demoted_excluded"] == ["CASE-combo-2"]


def test_probe_pass_path_resets_streak_and_pass_counts(tmp_path):
    base = tmp_path / "wiki"
    _compile_case(base, "combo-3", "op_slope_regrade", ["MU-DRAIN-004"])
    first = update_recipe_probe_feedback("CASE-combo-3", True, base_dir=base)
    assert first["pass_count"] == 1 and first["fail_streak"] == 0 and first["demoted"] is False
    update_recipe_probe_feedback("CASE-combo-3", False, base_dir=base)
    reset = update_recipe_probe_feedback("CASE-combo-3", True, base_dir=base)
    assert reset["fail_streak"] == 0 and reset["pass_count"] == 2


def test_refine_records_ontology_evolution_trace_and_audit(tmp_path):
    from test_iterative_refinement_and_experience import _visual_success

    store = MunicipalWorkflowStore(tmp_path / "sessions", ontology_base_dir=tmp_path / "wiki")
    candidate = chain_candidate(300.0, slopes=0.001)
    store.save("ontology-sid", {"candidate": candidate, "source": "correction"})
    result = store.iterative_refine("ontology-sid", {}, None, _visual_success)
    assert result["converged"] is True
    audit = json.loads((tmp_path / "sessions" / "ontology-sid" / "state_diff_audit.json").read_text(encoding="utf-8"))
    last_run = audit["runs"][-1]
    trace = last_run.get("ontology_evolution_trace", [])
    assert any(entry.get("event") == "retrieve" for entry in trace)
    first_round = next(round_entry for round_entry in last_run["rounds"] if round_entry.get("pareto_proposals_ledger"))
    assert first_round["pareto_proposals_ledger"]  # 真实候选 → 沙箱账本必须落盘
    assert "engineering_cost" in first_round
    state = store.get("ontology-sid")
    # 确定性补丁升到 0.003 后，视觉轮又收紧到 0.004（_visual_success 的既定行为）。
    assert state["candidate"]["segments"][0]["design_slope"] == pytest.approx(0.004)
    # 检索阶段只读：本体目录在 refine 后仍不存在（只有归档/探针才写盘）。
    assert not (tmp_path / "wiki").exists() or not any((tmp_path / "wiki").glob("ontology_graph.json"))
