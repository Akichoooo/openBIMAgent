#!/usr/bin/env python
"""市政管网四大架构升级的自动化论文消融实验。

构造覆盖 4 种管径（DN300/DN400/DN500/DN600）× 5 类典型空间病害的 12 个标准
测试场景，对每个场景执行「首检 → CEGIS 沙箱多轮修复 → 终检」，统计：

- 首次合规率 / 最终合规率；
- 平均工程造价指数 J(S)（``compute_engineering_cost``）；
- 帕累托候选数（沙箱每轮推演分支数）；
- 主动相机与纵断面触发次数（``compute_clash_normal_nbv`` / 纵断面渲染）；
- 本体自进化前后的算子命中率（冷启动 vs 归档 6 个案例后的两跳检索）。

输出 ``output/benchmark/ablation_report.md`` 与 ``ablation_metrics.json``。
全程确定性（无 VLM/宿主调用）；纵断面图由 Matplotlib 真实渲染。
"""
from __future__ import annotations

import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openbimagent.utility.candidate import (  # noqa: E402
    UtilityCandidate,
    check_candidate_state,
    compute_engineering_cost,
    evaluate_repair_sandbox,
)
from openbimagent.utility.wiki_ontology import (  # noqa: E402
    compile_and_archive_wiki_case,
    retrieve_ontology_recipes,
    update_recipe_probe_feedback,
)

OUTPUT_DIR = REPO_ROOT / "output" / "benchmark"
COORDINATE_REFERENCE = {
    "crs_id": "LOCAL:BENCH",
    "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
    "horizontal_unit": "m",
    "vertical_unit": "m",
    "vertical_datum": "bench datum",
}


def _node(node_id: str, x: float, ground: float, anchor: float | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "node_id": node_id, "node_type": "manhole", "x_m": x, "y_m": 0.0,
        "ground_elevation_m": ground, "invert_anchor_m": anchor,
    }
    payload.update(extra)
    return payload


def _segment(segment_id: str, start: str, end: str, diameter: float, slope: float, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "segment_id": segment_id, "start_node_id": start, "end_node_id": end,
        "diameter_mm": diameter, "material": "concrete", "design_slope": slope,
        "surface_context": "driveway",
        "inner_diameter_mm": diameter, "outer_diameter_mm": diameter + 60.0,
    }
    payload.update(extra)
    return payload


def base_candidate(diameter: float, *, length: float = 10.0, slope: float | None = None,
                   anchor: float = 9.8, ground: float = 11.0, nodes: int = 2) -> dict[str, Any]:
    """构造 n 节点直线链（每段 length 米），坡度缺省取该管径分档最小坡度。"""
    if slope is None:
        from openbimagent.utility.network_solver import lookup_pipe_normative_limits
        slope = float(lookup_pipe_normative_limits(diameter)["min_slope"])
    node_list = [_node("W1", 0.0, ground, anchor)]
    for index in range(2, nodes + 1):
        node_list.append(_node(f"W{index}", length * (index - 1), ground, None))
    segment_list = [
        _segment(f"P{i}", f"W{i}", f"W{i + 1}", diameter, slope)
        for i in range(1, nodes)
    ]
    return {
        "protocol_version": "0.1", "request_id": "bench", "source_ir_sha256": "c" * 64,
        "coordinate_reference": deepcopy(COORDINATE_REFERENCE),
        "system_id": "sys-bench", "system_name": "benchmark gravity wastewater",
        "nodes": node_list, "segments": segment_list,
        "collision_context": {"coverage": "complete", "obstacles": []},
    }


def scenario_definitions() -> list[dict[str, Any]]:
    """12 个标准场景：4 管径 × 5 类病害全覆盖（每管径 ≥2、每病害 ≥2）。"""
    scenarios: list[dict[str, Any]] = []

    def add(name: str, diameter: float, disease: str, mutate: Any) -> None:
        scenarios.append({"name": name, "diameter_mm": diameter, "disease": disease, "mutate": mutate})

    # 1-3 坡度不足（DN300/400/500）
    def slope_deficit(diameter: float, factor: float = 0.4):
        def mutate(candidate: dict[str, Any]) -> None:
            for segment in candidate["segments"]:
                segment["design_slope"] = round(segment["design_slope"] * factor, 6)
        return mutate
    add("S01_DN300_slope_deficit", 300.0, "slope_deficit", slope_deficit(300.0))
    add("S02_DN400_slope_deficit", 400.0, "slope_deficit", slope_deficit(400.0))
    add("S03_DN500_slope_deficit", 500.0, "slope_deficit", slope_deficit(500.0))

    # 4-5 下游浅埋（终点覆土不足，DN300/DN500）
    def downstream_shallow(diameter: float):
        def mutate(candidate: dict[str, Any]) -> None:
            candidate["nodes"][0]["invert_anchor_m"] = 10.18  # 覆土 ≈ 0.49m < 0.7m
        return mutate
    add("S04_DN300_downstream_shallow", 300.0, "downstream_shallow", downstream_shallow(300.0))
    add("S05_DN500_downstream_shallow", 500.0, "downstream_shallow", downstream_shallow(500.0))

    # 6-7 跨中障碍物碰撞（建筑包络盒，DN300/DN600）
    def midspan_clash(diameter: float):
        def mutate(candidate: dict[str, Any]) -> None:
            candidate["nodes"][1]["x_m"] = 20.0
            candidate["segments"][0]["start_node_id"] = "W1"
            candidate["segments"][0]["end_node_id"] = "W2"
            candidate["segments"][0].pop("inner_diameter_mm", None)
            candidate["segments"][0]["inner_diameter_mm"] = diameter
            candidate["collision_context"]["obstacles"] = [{
                "obstacle_id": "OB-BLD", "kind": "aabb", "category": "building",
                "min_corner": {"x_m": 9.0, "y_m": -0.5, "z_m": 8.0},
                "max_corner": {"x_m": 11.0, "y_m": 0.5, "z_m": 10.6},
            }]
        return mutate
    add("S06_DN300_midspan_clash", 300.0, "midspan_clash", midspan_clash(300.0))
    add("S07_DN600_midspan_clash", 600.0, "midspan_clash", midspan_clash(600.0))

    # 8-9 超长管段缺井（DN300/DN600）
    def overlength(diameter: float, length: float):
        def mutate(candidate: dict[str, Any]) -> None:
            candidate["nodes"][1]["x_m"] = length
        return mutate
    add("S08_DN300_overlength_span", 300.0, "overlength_span", overlength(300.0, 95.0))
    add("S09_DN600_overlength_span", 600.0, "overlength_span", overlength(600.0, 115.0))

    # 10-11 起点锁定受限浅埋（Tier-2 特批，DN300/DN400）
    def locked_shallow(diameter: float):
        def mutate(candidate: dict[str, Any]) -> None:
            candidate["nodes"][0]["invert_anchor_m"] = 10.02  # 覆土 ≈ 0.65m ∈ [0.45, 0.7)
            candidate["constraints"] = {"allow_exception_approval": True}
        return mutate
    add("S10_DN300_locked_shallow_encasement", 300.0, "locked_shallow", locked_shallow(300.0))
    add("S11_DN400_locked_shallow_encasement", 400.0, "locked_shallow", locked_shallow(400.0))

    # 12 跨越上方电力管线（跌水井下穿，DN600）
    def drop_crossing(diameter: float):
        def mutate(candidate: dict[str, Any]) -> None:
            candidate["nodes"] = [
                _node("W1", 0.0, 11.0, 9.8),
                _node("W2", 10.0, 11.0, None),
                _node("W3", 20.0, 10.9, None),
            ]
            candidate["segments"] = [
                _segment("P1", "W1", "W2", diameter, 0.003 if diameter <= 300 else 0.002),
                _segment("P2", "W2", "W3", diameter, 0.003 if diameter <= 300 else 0.002),
            ]
            z_top = 10.08
            candidate["collision_context"]["obstacles"] = [{
                "obstacle_id": "OB-PWR", "kind": "existing_pipe", "category": "power",
                "start_center": {"x_m": 15.0, "y_m": -4.0, "z_m": z_top},
                "end_center": {"x_m": 15.0, "y_m": 4.0, "z_m": z_top},
                "outer_diameter_mm": 200.0,
            }]
        return mutate
    add("S12_DN600_overhead_conduit_drop", 600.0, "overhead_conduit_drop", drop_crossing(600.0))
    return scenarios


LOCKED_SCENARIOS = {"locked_shallow"}


def apply_patches(candidate: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    """按 JSON Pointer 顺序应用补丁（含整表 /nodes、/segments 替换）。"""
    work = deepcopy(candidate)

    def tokens(path: str) -> list[str]:
        return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]

    for patch in patches:
        keys = tokens(patch["path"])
        parent: Any = work
        for key in keys[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        last = keys[-1]
        if isinstance(parent, list):
            parent[int(last)] = deepcopy(patch["value"])
        else:
            parent[last] = deepcopy(patch["value"])
    return work


def run_repair_loop(candidate: dict[str, Any], *, max_rounds: int = 4, ontology_base: Path | None = None, locked_paths: tuple[str, ...] = ()) -> dict[str, Any]:
    """确定性 CEGIS 多轮修复：返回终态与逐轮统计。"""
    current = deepcopy(candidate)
    rounds: list[dict[str, Any]] = []
    winning_operators: list[str] = []
    pareto_total = 0
    for round_index in range(1, max_rounds + 1):
        violations = [
            {"rule_id": item.get("rule_id"), "check_name": item.get("check_name")}
            for item in check_candidate_state(current)["issues"]
            if item.get("status") == "fail" and item.get("severity") != "info"
        ]
        recipes = None
        if ontology_base is not None:
            recipes = retrieve_ontology_recipes(current, violations, base_dir=ontology_base, top_k=3)
        ledger = evaluate_repair_sandbox(
            UtilityCandidate(solver_input=current),
            operator_priority=(recipes or {}).get("operator_priority"),
            locked_paths=locked_paths,
        )
        pareto_total += len(ledger["pareto_proposals"])
        rounds.append({
            "round": round_index,
            "pareto_candidates": len(ledger["pareto_proposals"]),
            "winning_operator": ledger["winning_operator"],
            "violations_before": ledger["baseline"]["violation_count"],
            "violations_after": ledger["pareto_proposals"][0]["violation_count"] if ledger["pareto_proposals"] else ledger["baseline"]["violation_count"],
            "cited_cases": [case.get("case_id") for case in (recipes or {}).get("cases", [])],
        })
        if not ledger["winner_patches"]:
            break
        current = apply_patches(current, ledger["winner_patches"])
        if ledger["winning_operator"]:
            winning_operators.append(ledger["winning_operator"])
        if check_candidate_state(current)["violation_count"] == 0:
            break
    final = check_candidate_state(current)
    return {
        "final_candidate": current,
        "rounds": rounds,
        "winning_operators": winning_operators,
        "pareto_total": pareto_total,
        "final_report": final,
    }


def camera_triggers(candidate: dict[str, Any], output_dir: Path, scenario_name: str) -> dict[str, Any]:
    """主动相机触发统计：NBV 解算 + 纵断面真实渲染。"""
    from openbimagent.vision.municipal import compute_clash_normal_nbv, render_longitudinal_profile_view

    nbv_ok = False
    try:
        nbv = compute_clash_normal_nbv(candidate)
        nbv_ok = nbv.get("azimuth_deg") is not None
    except Exception:  # noqa: BLE001 — 触发统计必须吞掉单个场景的解算失败
        nbv_ok = False
    profile_ok = False
    try:
        profile = render_longitudinal_profile_view(candidate, output_dir / f"{scenario_name}_profile.png")
        profile_ok = profile.get("status") == "rendered"
    except Exception:  # noqa: BLE001
        profile_ok = False
    return {"nbv": nbv_ok, "longitudinal_profile": profile_ok}


def make_audit(run_result: dict[str, Any], camera: dict[str, Any]) -> dict[str, Any]:
    """把基准运行结果整理成 compile_and_archive_wiki_case 需要的审计结构。"""
    trajectory = []
    if camera.get("longitudinal_profile"):
        trajectory.append({"view_id": "longitudinal_profile", "mode": "longitudinal_profile", "status": "rendered", "camera": None})
    if camera.get("nbv"):
        trajectory.append({"view_id": "nbv_clash_normal", "mode": "nbv_clash_normal", "status": "rendered", "camera": None})
    return {"runs": [{"rounds": [
        {"winning_operator": operator, "active_camera_trajectory": trajectory}
        for operator in run_result["winning_operators"] or [None]
    ]}]}


def hit_rate(entry: dict[str, Any]) -> bool:
    """命中 = 胜出算子排在检索给出的算子优先级第一位。"""
    violations = [
        {"rule_id": item.get("rule_id"), "check_name": item.get("check_name")}
        for item in entry["first_report"]["issues"]
        if item.get("status") == "fail" and item.get("severity") != "info"
    ]
    recipes = entry["recipes"]
    priority = (recipes or {}).get("operator_priority") or []
    winner = entry["winning_operator"]
    return bool(priority and winner and winner.split("+")[0] == priority[0])


def main() -> int:
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ontology_warm = OUTPUT_DIR / "wiki_ontology_warm"
    scenarios = scenario_definitions()
    results: list[dict[str, Any]] = []

    for index, scenario in enumerate(scenarios):
        diameter = scenario["diameter_mm"]
        candidate = base_candidate(diameter, length=10.0, anchor=9.8, nodes=3 if scenario["disease"] == "overhead_conduit_drop" else 2)
        scenario["mutate"](candidate)
        # 场景专属锁定：起点锁定受限浅埋必须锁住 W1 锚点。
        locked_paths = ("/nodes/0/invert_anchor_m",) if scenario["disease"] in LOCKED_SCENARIOS else ()
        first = check_candidate_state(candidate)
        # 冷启动阶段（前 6 个场景）不接本体检索；暖启动阶段接归档后的本体。
        ontology_base = None if index < 6 else ontology_warm
        run = run_repair_loop(candidate, max_rounds=4, ontology_base=ontology_base, locked_paths=locked_paths)
        camera = camera_triggers(run["final_candidate"], OUTPUT_DIR, scenario["name"])
        violations = [
            {"rule_id": item.get("rule_id"), "check_name": item.get("check_name")}
            for item in first["issues"] if item.get("status") == "fail" and item.get("severity") != "info"
        ]
        recipes = retrieve_ontology_recipes(candidate, violations, base_dir=ontology_base, top_k=3) if ontology_base else None
        winning_operator = run["winning_operators"][0] if run["winning_operators"] else None
        cost = compute_engineering_cost(run["final_candidate"])
        entry = {
            "scenario": scenario["name"],
            "diameter_mm": diameter,
            "disease": scenario["disease"],
            "first_compliant": first["violation_count"] == 0,
            "final_compliant": run["final_report"]["violation_count"] == 0,
            "rounds": len(run["rounds"]),
            "winning_operator": winning_operator,
            "all_winning_operators": run["winning_operators"],
            "pareto_candidates": run["pareto_total"],
            "total_cost_score": cost["total_cost_score"],
            "excavation_cost_index": cost["excavation_cost_index"],
            "material_cost_index": cost["material_cost_index"],
            "min_cover_margin_m": cost["min_cover_margin_m"],
            "min_clearance_margin_m": cost["min_clearance_margin_m"],
            "camera": camera,
            "first_report": {"issues": first["issues"], "violation_count": first["violation_count"]},
            "recipes": recipes,
            "final_violations": run["final_report"]["violation_count"],
        }
        results.append(entry)

        # 本体自进化：前 6 个场景的结果沉淀为 Wiki 案例（供后 6 个场景暖检索）。
        if index < 6 and entry["final_compliant"]:
            audit = make_audit(run, camera)
            final_state = {
                "candidate": run["final_candidate"],
                "validation": {"issues": run["final_report"]["issues"]},
                "requirement_brief": f"benchmark {scenario['name']}",
                "history": [],
            }
            compile_and_archive_wiki_case(
                f"bench-{scenario['name']}", {"validation": {"issues": first["issues"]}}, final_state, audit, base_dir=ontology_warm,
            )
            update_recipe_probe_feedback(f"CASE-bench-{scenario['name']}", True, base_dir=ontology_warm)

    total = len(results)
    compliant_first = sum(1 for r in results if r["first_compliant"])
    compliant_final = sum(1 for r in results if r["final_compliant"])
    avg_cost = sum(r["total_cost_score"] for r in results) / total
    avg_pareto = sum(r["pareto_candidates"] for r in results) / total
    nbv_triggers = sum(1 for r in results if r["camera"]["nbv"])
    profile_triggers = sum(1 for r in results if r["camera"]["longitudinal_profile"])
    cold = [r for r in results[:6]]
    warm = [r for r in results[6:]]
    cold_hits = sum(1 for r in cold if hit_rate(r))
    warm_hits = sum(1 for r in warm if hit_rate(r))

    metrics = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scenarios": [{k: v for k, v in r.items() if k not in {"first_report", "recipes"}} for r in results],
        "summary": {
            "scenario_count": total,
            "first_pass_compliance": compliant_first / total,
            "final_compliance": compliant_final / total,
            "avg_total_cost_score": round(avg_cost, 4),
            "avg_pareto_candidates": round(avg_pareto, 2),
            "nbv_trigger_count": nbv_triggers,
            "longitudinal_profile_trigger_count": profile_triggers,
            "operator_hit_rate_cold": round(cold_hits / max(1, len(cold)), 4),
            "operator_hit_rate_warm": round(warm_hits / max(1, len(warm)), 4),
            "winning_operator_distribution": _distribution([op for r in results for op in r["all_winning_operators"]]),
            "disease_coverage": _distribution([r["disease"] for r in results]),
        },
    }
    (OUTPUT_DIR / "ablation_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT_DIR / "ablation_report.md").write_text(_markdown_report(metrics, elapsed=round(time.perf_counter() - started, 1)), encoding="utf-8")
    print(f"benchmark complete: {compliant_final}/{total} 最终合规；报告见 {OUTPUT_DIR / 'ablation_report.md'}")
    return 0


def _distribution(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))


def _markdown_report(metrics: dict[str, Any], *, elapsed: float) -> str:
    summary = metrics["summary"]
    lines = [
        "# 市政管网四大架构升级 · 消融实验报告",
        "",
        f"- 生成时间：{metrics['generated_at']}（耗时 {elapsed}s，全程确定性，无 VLM/宿主调用）",
        f"- 场景数：{summary['scenario_count']}（DN300/DN400/DN500/DN600 × 5 类空间病害）",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 首次合规率 | {summary['first_pass_compliance']:.1%} |",
        f"| 最终合规率（CEGIS ≤4 轮） | {summary['final_compliance']:.1%} |",
        f"| 平均工程造价指数 J(S) | {summary['avg_total_cost_score']:.2f} |",
        f"| 平均帕累托候选数/场景 | {summary['avg_pareto_candidates']:.1f} |",
        f"| NBV 主动相机触发 | {summary['nbv_trigger_count']}/{summary['scenario_count']} |",
        f"| 纵断面渲染触发 | {summary['longitudinal_profile_trigger_count']}/{summary['scenario_count']} |",
        f"| 算子命中率（本体冷启动） | {summary['operator_hit_rate_cold']:.1%} |",
        f"| 算子命中率（本体自进化后） | {summary['operator_hit_rate_warm']:.1%} |",
        "",
        "## 逐场景结果",
        "",
        "| 场景 | 管径 | 病害 | 首次合规 | 最终合规 | 轮次 | 胜出算子 | J(S) | 帕累托候选 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in metrics["scenarios"]:
        lines.append(
            f"| {row['scenario']} | DN{row['diameter_mm']:.0f} | {row['disease']} |"
            f" {'✓' if row['first_compliant'] else '✗'} | {'✓' if row['final_compliant'] else '✗'} |"
            f" {row['rounds']} | {row['winning_operator'] or '-'} | {row['total_cost_score']:.2f} | {row['pareto_candidates']} |"
        )
    lines += [
        "",
        "## 胜出算子分布",
        "",
        "| 算子 | 次数 |",
        "| --- | --- |",
        *[
            f"| {operator} | {count} |"
            for operator, count in metrics["summary"]["winning_operator_distribution"].items()
        ],
        "",
        "## 病害覆盖",
        "",
        "| 病害 | 场景数 |",
        "| --- | --- |",
        *[f"| {disease} | {count} |" for disease, count in metrics["summary"]["disease_coverage"].items()],
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
