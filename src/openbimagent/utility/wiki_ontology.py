"""BIM-Wiki-Ontology 三位一体领域本体与素材复用库（属性图 × Obsidian 双链词条 × 探针门控）。

三位一体：
1. **本体属性图** ``ontology_graph.json``——5 类实体节点（Rule / ConflictPattern /
   RepairOperator / ReusableAsset / CaseRecipe）与带权边（triggered_by /
   mitigated_by / materialized_as / solved_by / applied / used_asset）；
2. **Markdown 双链词条**目录 ``rules/ patterns/ assets/ cases/``——YAML
   Frontmatter + ``[[双向链接]]``，人机共读；
3. **Harness² 探针门控**——每次配方引用后按真实结果回写
   ``pass_count / fail_streak``，连续 2 次失败自动熔断（``demoted``）并在词条
   顶部写入 ``> [!WARNING]`` 标注，检索时过滤熔断经验。

存储目录缺省 ``domain_packs/municipal_utility/wiki_ontology/``（可用
``base_dir`` 参数或 ``OPENBIMAGENT_WIKI_ONTOLOGY_DIR`` 覆盖以便测试隔离）。
检索（``retrieve_ontology_recipes``）是**只读**的：目录不存在时返回空配方，
绝不写盘；只有编译归档与探针回写会落盘。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GRAPH_FILE = "ontology_graph.json"
GRAPH_VERSION = 1

DEFAULT_WIKI_ONTOLOGY_DIR = (
    Path(__file__).resolve().parents[3] / "domain_packs" / "municipal_utility" / "wiki_ontology"
)

#: 规则节点：规范条目身份（MU-GRAD-001 / MU-CLASH-001 为本体的坡度/净距别名，
#: 指向 constraints.yaml 的规范条目 MU-DRAIN-004 / MU-CLEAR-001）。
SEED_RULES: dict[str, dict[str, Any]] = {
    "Rule/MU-ELEV-001": {"canonical_rule_id": "MU-ELEV-001", "topic": "车行道最小覆土 0.7m", "source_clause": "GB 50014-2021 §5.3.7"},
    "Rule/MU-GRAD-001": {"canonical_rule_id": "MU-DRAIN-004", "topic": "污水管最小设计坡度（按管径分档）", "source_clause": "GB 50014-2021 §5.2.10 表 5.2.10"},
    "Rule/MU-WELL-001": {"canonical_rule_id": "MU-WELL-001", "topic": "检查井最大间距（按管径分档）", "source_clause": "GB 50014-2021 §5.4.4 表 5.4.4"},
    "Rule/MU-CLASH-001": {"canonical_rule_id": "MU-CLEAR-001", "topic": "与建(构)筑物最小水平净距 2.5m", "source_clause": "GB 50289-2016 §4.1.9 表 4.1.9"},
}

#: 冲突模式节点与其触发条件（违规 rule_id / check_name → 模式）。
PATTERN_TRIGGERS: dict[str, list[str]] = {
    "Pattern/Shallow_Cover": ["MU-ELEV-001", "MU-ELEV-002", "cover_depth_in_spec"],
    "Pattern/Midspan_Obstacle_Clash": ["MU-CLASH-001", "MU-CLEAR-001", "MU-CLEAR-005", "MU-CLEAR-006", "MU-CLEAR-007", "MU-CLEAR-008", "clash_free", "physical_obstacle_clearance", "network_geometry_in_spec", "PHYSICAL-OBSTACLE"],
    "Pattern/Overlength_Span": ["MU-WELL-001", "manhole_spacing_in_spec"],
    "Pattern/Slope_Deficit": ["MU-GRAD-001", "MU-DRAIN-004", "slope_in_spec"],
}

SEED_PATTERNS: dict[str, dict[str, Any]] = {
    "Pattern/Shallow_Cover": {"name": "Shallow_Cover", "description": "管顶覆土不足（车行道 <0.7m / 人行道 <0.6m）", "mitigated_by": ["op_drop_manhole", "op_concrete_encasement_exception", "op_slope_regrade"]},
    "Pattern/Midspan_Obstacle_Clash": {"name": "Midspan_Obstacle_Clash", "description": "跨中与既有管线/建(构)筑物净距或三维外包络冲突", "mitigated_by": ["op_lateral_detour", "op_drop_manhole"]},
    "Pattern/Overlength_Span": {"name": "Overlength_Span", "description": "两井间距超过规范/设计分档上限", "mitigated_by": ["op_insert_intermediate_manhole"]},
    "Pattern/Slope_Deficit": {"name": "Slope_Deficit", "description": "设计坡度低于该管径最小设计坡度", "mitigated_by": ["op_slope_regrade"]},
}

SEED_OPERATORS: dict[str, dict[str, Any]] = {
    "Operator/op_slope_regrade": {"name": "op_slope_regrade", "description": "顺坡传导调坡：提升至分档最小坡度并向下游传导重算锚点"},
    "Operator/op_drop_manhole": {"name": "op_drop_manhole", "description": "局部跌水井：下调未锁定节点锚点并标记 drop_manhole"},
    "Operator/op_lateral_detour": {"name": "op_lateral_detour", "description": "平面正交折线绕行：沿障碍物包络盒法向偏移/插入绕行井对"},
    "Operator/op_insert_intermediate_manhole": {"name": "op_insert_intermediate_manhole", "description": "超长管段自动插井：W_mid 中点线性插值拆分"},
    "Operator/op_concrete_encasement_exception": {"name": "op_concrete_encasement_exception", "description": "受控破限：满包混凝土加固特批（覆土 ≥0.45m）"},
}

SEED_ASSETS: dict[str, dict[str, Any]] = {
    "Asset/Drop_Manhole_SCAD": {
        "name": "Drop_Manhole_SCAD",
        "kind": "parameterized_component",
        "used_by_operator": "op_drop_manhole",
        "parameters": {"shaft_diameter_m": 1.0, "drop_height_m": 0.35, "chamber_height_m": 1.8},
        "scad_template": (
            "// parameterized drop manhole\n"
            "module drop_manhole(drop_h) { translate([0,0,-drop_h]) cylinder(h=drop_h+1.8,r=0.5,$fn=32); "
            "translate([0,0,-drop_h]) sphere(r=0.5,$fn=32); }\n"
        ),
    },
    "Asset/Concrete_Encasement_Sleeve": {
        "name": "Concrete_Encasement_Sleeve",
        "kind": "parameterized_component",
        "used_by_operator": "op_concrete_encasement_exception",
        "parameters": {"encasement_thickness_m": 0.15, "grade": "C25", "approval_id": "EXC-MU-ELEV-001-ENCASEMENT"},
        "scad_template": (
            "// full concrete encasement sleeve\n"
            "module encasement(pipe_od, len) { translate([0,-pipe_od/2-0.15,-pipe_od/2-0.15]) "
            "cube([len, pipe_od+0.3, pipe_od+0.3]); }\n"
        ),
    },
    "Asset/Lateral_Detour_Well_Pair": {
        "name": "Lateral_Detour_Well_Pair",
        "kind": "parameterized_component",
        "used_by_operator": "op_lateral_detour",
        "parameters": {"standoff_m": 3.2, "well_diameter_m": 1.0, "bend_angle_deg": 90},
        "scad_template": (
            "// orthogonal lateral detour well pair\n"
            "module detour_well_pair(offset) { translate([-offset,0,0]) cylinder(h=2.0,r=0.5,$fn=32); "
            "translate([offset,0,0]) cylinder(h=2.0,r=0.5,$fn=32); }\n"
        ),
    },
}

DEMOTE_CALLOUT = "> [!WARNING] DEMOTED BY HARNESS PROBE (fail_streak >= 2)"


def resolve_wiki_base_dir(base_dir: Path | str | None = None) -> Path:
    """解析本体存储目录：显式参数 > 专用覆盖 > 归档沙箱覆盖 > 仓库缺省。

    遵循 ``runs._archive_root`` 的沙箱惯例：测试设置了 ``OPENBIMAGENT_ARCHIVE_DIR``
    时，本体词条随归档一起落在 ``<archive>/wiki_ontology``，保持单测隔离。
    """
    if base_dir is not None:
        return Path(base_dir)
    dedicated = os.environ.get("OPENBIMAGENT_WIKI_ONTOLOGY_DIR")
    if dedicated:
        return Path(dedicated)
    archive = os.environ.get("OPENBIMAGENT_ARCHIVE_DIR")
    if archive:
        return Path(archive) / "wiki_ontology"
    return DEFAULT_WIKI_ONTOLOGY_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_graph() -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for node_id, meta in SEED_RULES.items():
        nodes[node_id] = {"kind": "rule", **meta, "created_at": _now()}
    for node_id, meta in SEED_PATTERNS.items():
        nodes[node_id] = {"kind": "pattern", **meta, "created_at": _now()}
    for node_id, meta in SEED_OPERATORS.items():
        nodes[node_id] = {"kind": "operator", "pass_count": 0, "fail_count": 0, "fail_streak": 0, "demoted": False, **meta, "created_at": _now()}
    for node_id, meta in SEED_ASSETS.items():
        nodes[node_id] = {"kind": "asset", "use_count": 0, **meta, "created_at": _now()}
    edges: list[dict[str, Any]] = []
    for pattern_id, triggers in PATTERN_TRIGGERS.items():
        for trigger in triggers:
            rule_node = next((rid for rid, meta in SEED_RULES.items() if trigger in (meta["canonical_rule_id"], rid.split("/", 1)[1])), None)
            if rule_node:
                edges.append({"from": pattern_id, "to": rule_node, "relation": "triggered_by", "weight": 1})
    for pattern_id, meta in SEED_PATTERNS.items():
        for operator in meta["mitigated_by"]:
            edges.append({"from": pattern_id, "to": f"Operator/{operator}", "relation": "mitigated_by", "weight": 1})
    for asset_id, meta in SEED_ASSETS.items():
        edges.append({"from": f"Operator/{meta['used_by_operator']}", "to": asset_id, "relation": "materialized_as", "weight": 1})
    return {"version": GRAPH_VERSION, "nodes": nodes, "edges": edges}


def _graph_path(base: Path) -> Path:
    return base / GRAPH_FILE


def _load_graph(base: Path) -> dict[str, Any]:
    path = _graph_path(base)
    if not path.is_file():
        return _seed_graph()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _seed_graph()
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
        return _seed_graph()
    return data


def _save_graph(base: Path, graph: dict[str, Any]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    _graph_path(base).write_text(json.dumps(graph, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")


def _bump_edge(graph: dict[str, Any], source: str, target: str, relation: str, delta: float = 1.0) -> None:
    for edge in graph.get("edges", []):
        if edge.get("from") == source and edge.get("to") == target and edge.get("relation") == relation:
            edge["weight"] = float(edge.get("weight", 0)) + delta
            return
    graph.setdefault("edges", []).append({"from": source, "to": target, "relation": relation, "weight": delta})


def _frontmatter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        elif isinstance(value, (list, tuple)):
            rendered = "[" + ", ".join(str(item) for item in value) + "]"
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def _ensure_seed_markdown(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for group in ("rules", "patterns", "assets", "cases"):
        (base / group).mkdir(exist_ok=True)
    for node_id, meta in SEED_RULES.items():
        path = base / "rules" / f"{node_id.split('/', 1)[1]}.md"
        if not path.is_file():
            path.write_text(
                _frontmatter({"rule_id": node_id.split("/", 1)[1], "canonical_rule_id": meta["canonical_rule_id"], "source_clause": meta["source_clause"]})
                + f"\n\n# {node_id}\n\n{meta['topic']}。\n\n相关冲突模式见 [[Pattern/Shallow_Cover]]、[[Pattern/Slope_Deficit]]、[[Pattern/Overlength_Span]]、[[Pattern/Midspan_Obstacle_Clash]]。\n",
                encoding="utf-8",
            )
    for node_id, meta in SEED_PATTERNS.items():
        path = base / "patterns" / f"{meta['name']}.md"
        if not path.is_file():
            operators = "、".join(f"[[Operator/{name}]]" for name in meta["mitigated_by"])
            path.write_text(
                _frontmatter({"pattern": meta["name"], "description": meta["description"]})
                + f"\n\n# Pattern/{meta['name']}\n\n{meta['description']}。\n\n缓解算子：{operators}。历史案例见 [[cases]] 目录。\n",
                encoding="utf-8",
            )
    for node_id, meta in SEED_ASSETS.items():
        path = base / "assets" / f"{meta['name']}.md"
        if not path.is_file():
            path.write_text(
                _frontmatter({"asset": meta["name"], "kind": meta["kind"], "parameters": meta["parameters"], "used_by": f"[[Operator/{meta['used_by_operator']}]]"})
                + f"\n\n# Asset/{meta['name']}\n\n```scad\n{meta['scad_template']}```\n",
                encoding="utf-8",
            )


def _detect_assets(final_state: dict[str, Any]) -> list[str]:
    """从最终候选中提取实际用到的可复用特殊构件。"""
    candidate = final_state.get("candidate") if isinstance(final_state, dict) else None
    if not isinstance(candidate, dict):
        return []
    assets: list[str] = []
    nodes = [n for n in candidate.get("nodes") or [] if isinstance(n, dict)]
    segments = [s for s in candidate.get("segments") or [] if isinstance(s, dict)]
    if any(n.get("manhole_type") == "drop_manhole" for n in nodes):
        assets.append("Asset/Drop_Manhole_SCAD")
    if any(str(n.get("node_id", "")).endswith(("-det-1", "-det-2")) for n in nodes):
        assets.append("Asset/Lateral_Detour_Well_Pair")
    if any(s.get("protection_measure") == "concrete_encasement" for s in segments):
        assets.append("Asset/Concrete_Encasement_Sleeve")
    return assets


def _detect_operators(state_diff_audit: dict[str, Any], final_state: dict[str, Any]) -> list[str]:
    """胜出算子：优先 state_diff_audit 的 winning_operator，其次补丁 reason 前缀。"""
    operators: list[str] = []
    for run in (state_diff_audit or {}).get("runs", []) if isinstance(state_diff_audit, dict) else []:
        for round_entry in run.get("rounds", []):
            winner = round_entry.get("winning_operator")
            if isinstance(winner, str) and winner and winner not in operators:
                operators.append(winner)
    history = final_state.get("history") if isinstance(final_state, dict) else None
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        for patch in entry.get("patches") or []:
            reason = str((patch or {}).get("reason") or "")
            match = re.match(r"(op_[a-z_]+)", reason)
            if match and match.group(1) not in operators:
                operators.append(match.group(1))
    return operators


def _camera_recipe(state_diff_audit: dict[str, Any]) -> list[dict[str, Any]]:
    recipe: list[dict[str, Any]] = []
    if not isinstance(state_diff_audit, dict):
        return recipe
    for run in state_diff_audit.get("runs", []):
        if not isinstance(run, dict):
            continue
        for round_entry in run.get("rounds", []):
            for view in round_entry.get("active_camera_trajectory") or []:
                if isinstance(view, dict) and view.get("status") == "rendered":
                    slim = {key: view.get(key) for key in ("view_id", "mode", "camera")}
                    if slim not in recipe:
                        recipe.append(slim)
    return recipe


def _patterns_for_violations(violations: list[Any]) -> list[str]:
    matched: list[str] = []
    for violation in violations:
        if isinstance(violation, dict):
            tokens = [str(violation.get("rule_id") or ""), str(violation.get("check_name") or "")]
        else:
            tokens = [str(violation)]
        for pattern_id, triggers in PATTERN_TRIGGERS.items():
            if pattern_id in matched:
                continue
            if any(token and (token in triggers or any(trigger in token for trigger in triggers)) for token in tokens):
                matched.append(pattern_id)
    return matched


def compile_and_archive_wiki_case(
    session_id: str,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    state_diff_audit: dict[str, Any],
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """把验证通过的会话编译为 Wiki 案例词条并更新本体属性图。

    - 写 ``cases/{case_id}.md``（YAML Frontmatter + ``[[双向链接]]``）；
    - 提取用到的特殊构件（跌水井/套管加固/绕行井对）更新 ``assets/``；
    - 更新 ``ontology_graph.json`` 中算子/配方的边权重；
    - 同步写入原有 ``exemplars/auto_archive/index.json`` 保持向后兼容。
    """
    base = resolve_wiki_base_dir(base_dir)
    _ensure_seed_markdown(base)
    graph = _load_graph(base)
    case_id = f"CASE-{session_id}"
    node_id = f"Case/{case_id}"

    winning_operators = _detect_operators(state_diff_audit, final_state)
    assets_used = _detect_assets(final_state)
    recipe = _camera_recipe(state_diff_audit)
    total_cost = None
    try:
        from openbimagent.utility.candidate import compute_engineering_cost

        total_cost = compute_engineering_cost(final_state.get("candidate") or {}).get("total_cost_score")
    except Exception:  # noqa: BLE001 — 造价提取失败不阻断归档
        total_cost = None

    initial_violations = []
    final_violations = []
    for state, sink in ((initial_state, initial_violations), (final_state, final_violations)):
        issues = (state or {}).get("validation") or {}
        for issue in issues.get("issues") or []:
            if isinstance(issue, dict) and issue.get("status") in {"fail", "unknown"} and issue.get("severity") != "info" and not issue.get("exception_applied"):
                sink.append(str(issue.get("rule_id") or issue.get("check_name") or "unknown"))
    patterns = _patterns_for_violations([{"rule_id": token} for token in {*initial_violations}]) or [
        "Pattern/Shallow_Cover", "Pattern/Slope_Deficit"
    ]

    previous = graph["nodes"].get(node_id, {})
    case_node = {
        "kind": "case",
        "case_id": case_id,
        "session_id": str(session_id),
        "state_transition": "S0 -> S1 -> S2",
        "winning_operator": winning_operators[0] if winning_operators else None,
        "winning_operators": winning_operators,
        "best_camera_recipe": recipe,
        "total_cost_score": total_cost,
        "failure_patterns": patterns,
        "initial_violations": sorted(set(initial_violations)),
        "final_violation_count": len(final_violations),
        "assets_used": assets_used,
        "pass_count": int(previous.get("pass_count", 0)),
        "fail_streak": int(previous.get("fail_streak", 0)),
        "demoted": bool(previous.get("demoted", False)),
        "created_at": previous.get("created_at") or _now(),
        "updated_at": _now(),
    }
    graph["nodes"][node_id] = case_node
    for pattern in patterns:
        _bump_edge(graph, pattern, node_id, "solved_by")
    for operator in winning_operators:
        _bump_edge(graph, node_id, f"Operator/{operator}" if not operator.startswith("Operator/") else operator, "applied")
    for asset in assets_used:
        _bump_edge(graph, node_id, asset, "used_asset", 0.0)
        asset_node = graph["nodes"].get(asset)
        if isinstance(asset_node, dict):
            asset_node["use_count"] = int(asset_node.get("use_count", 0)) + 1
    _save_graph(base, graph)

    rule_links = "\n".join(f"- 规范依据：[[Rule/{rule.split('/', 1)[1]}]]" for rule in SEED_RULES)
    pattern_links = "\n".join(f"- 冲突模式：[[Pattern/{p.split('/', 1)[1]}]]" for p in patterns)
    operator_links = "\n".join(f"- 胜出算子：[[Operator/{o}]]" for o in winning_operators) or "- 胜出算子：无（人工确认直达）"
    asset_links = "\n".join(f"- 复用构件：[[Asset/{a.split('/', 1)[1]}]]" for a in assets_used) or "- 复用构件：无"
    markdown = "\n".join([
        _frontmatter({
            "case_id": case_id, "session_id": str(session_id), "state_transition": "S0 -> S1 -> S2",
            "winning_operator": case_node["winning_operator"] or "none",
            "total_cost_score": total_cost if total_cost is not None else "unknown",
            "patterns": [p.split("/", 1)[1] for p in patterns],
            "pass_count": case_node["pass_count"], "fail_streak": case_node["fail_streak"], "demoted": case_node["demoted"],
        }),
        "",
        f"# {node_id}",
        "",
        rule_links,
        pattern_links,
        operator_links,
        asset_links,
        f"- 状态转移：S0 -> S1 -> S2（初始违规 {len(set(initial_violations))} 类 → 最终 {len(final_violations)} 项）",
        f"- 最佳相机视角配方：{json.dumps(recipe, ensure_ascii=False) if recipe else '默认三视图'}",
        f"- 造价指标 J(S)：{total_cost if total_cost is not None else 'unknown'}",
        f"- Harness 探针：pass_count={case_node['pass_count']}, fail_streak={case_node['fail_streak']}, demoted={case_node['demoted']}",
        "",
    ])
    case_path = base / "cases" / f"{case_id}.md"
    if case_path.is_file() and DEMOTE_CALLOUT in case_path.read_text(encoding="utf-8"):
        markdown += f"\n{DEMOTE_CALLOUT}\n"
    else:
        markdown += "\n"
    case_path.write_text(markdown, encoding="utf-8")

    # 向后兼容：同步写入原有 exemplars/auto_archive/index.json。
    legacy_root = base.parent / "assets" / "auto_archive"
    legacy_index: list[dict[str, Any]] = []
    legacy_path = legacy_root / "index.json"
    if legacy_path.is_file():
        try:
            loaded = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy_index = loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, OSError):
            legacy_index = []
    legacy_entry = {
        "session_id": str(session_id),
        "brief": str((final_state or {}).get("requirement_brief") or "")[:120],
        "archived_at": _now(),
        "wiki_case_id": case_id,
        "winning_operator": case_node["winning_operator"],
        "total_cost_score": total_cost,
    }
    legacy_index = [entry for entry in legacy_index if isinstance(entry, dict) and entry.get("session_id") != str(session_id)]
    legacy_index.append(legacy_entry)
    legacy_root.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(legacy_index, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    return {
        "case_id": case_id,
        "node_id": node_id,
        "markdown_path": str(case_path),
        "winning_operators": winning_operators,
        "assets_used": assets_used,
        "camera_recipe": recipe,
        "total_cost_score": total_cost,
        "patterns": patterns,
        "legacy_index_updated": True,
        "graph_node_count": len(graph["nodes"]),
    }


def retrieve_ontology_recipes(
    candidate_state: dict[str, Any],
    violations: list[Any],
    base_dir: Path | str | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """两跳本体图谱检索：违规 → 冲突模式 → {历史案例, 算子优先级, 相机配方, 复用构件}。

    只读：本体目录不存在或为空时返回模式驱动的缺省算子优先级，绝不写盘。
    ``demoted == True`` 的熔断经验被自动过滤并记录在 ``demoted_excluded``。
    """
    base = resolve_wiki_base_dir(base_dir)
    graph_available = _graph_path(base).is_file()
    graph = _load_graph(base) if graph_available else _seed_graph()
    matched = _patterns_for_violations(violations) or list(SEED_PATTERNS)

    def operator_node(name: str) -> dict[str, Any]:
        return graph["nodes"].get(f"Operator/{name}", {})

    # 算子优先级 = 模式缓解关系（缺省序） + 历史案例胜出算子的边权重加成：
    # 被越多未熔断案例验证过的算子排得越靠前，实现检索驱动的自进化。
    operator_scores: dict[str, float] = {}
    for edge in graph.get("edges", []):
        if edge.get("relation") != "solved_by" or edge.get("from") not in matched:
            continue
        case_node = graph["nodes"].get(str(edge.get("to")))
        if not isinstance(case_node, dict) or case_node.get("demoted"):
            continue
        winner = case_node.get("winning_operator")
        if isinstance(winner, str) and winner:
            operator_scores[winner] = operator_scores.get(winner, 0.0) + float(edge.get("weight", 0))
    operator_priority: list[str] = []
    for operator, _score in sorted(operator_scores.items(), key=lambda item: (-item[1], item[0])):
        if operator not in operator_priority and not operator_node(operator).get("demoted"):
            operator_priority.append(operator)
    for pattern in matched:
        for operator in (graph["nodes"].get(pattern, {}).get("mitigated_by") or SEED_PATTERNS.get(pattern, {}).get("mitigated_by", [])):
            if operator not in operator_priority and not operator_node(operator).get("demoted"):
                operator_priority.append(operator)
    for operator in ("op_slope_regrade", "op_drop_manhole", "op_lateral_detour", "op_insert_intermediate_manhole", "op_concrete_encasement_exception"):
        if operator not in operator_priority and not operator_node(operator).get("demoted"):
            operator_priority.append(operator)

    case_scores: dict[str, float] = {}
    for edge in graph.get("edges", []):
        if edge.get("relation") != "solved_by" or edge.get("from") not in matched:
            continue
        case_scores[str(edge.get("to"))] = case_scores.get(str(edge.get("to")), 0.0) + float(edge.get("weight", 0))
    cases: list[dict[str, Any]] = []
    demoted_excluded: list[str] = []
    for node_id, score in sorted(case_scores.items(), key=lambda item: (-item[1], item[0])):
        node = graph["nodes"].get(node_id)
        if not isinstance(node, dict) or node.get("kind") != "case":
            continue
        if node.get("demoted"):
            demoted_excluded.append(str(node.get("case_id") or node_id))
            continue
        cases.append({
            "case_id": node.get("case_id") or node_id.split("/", 1)[1],
            "node_id": node_id,
            "score": round(score, 3),
            "winning_operator": node.get("winning_operator"),
            "total_cost_score": node.get("total_cost_score"),
            "camera_recipe": node.get("best_camera_recipe") or [],
            "failure_patterns": node.get("failure_patterns") or [],
            "pass_count": node.get("pass_count", 0),
            "fail_streak": node.get("fail_streak", 0),
        })
        if len(cases) >= max(1, int(top_k)):
            break

    recommended_camera: list[dict[str, Any]] = []
    for case in cases:
        for view in case["camera_recipe"]:
            slim = {key: view.get(key) for key in ("view_id", "mode", "camera")}
            if slim.get("mode") and slim not in recommended_camera:
                recommended_camera.append(slim)
    if "Pattern/Midspan_Obstacle_Clash" in matched and not any(v.get("mode") == "nbv_clash_normal" for v in recommended_camera):
        recommended_camera.append({"view_id": "nbv_clash_normal", "mode": "nbv_clash_normal"})
    if matched and not any(v.get("mode") == "longitudinal_profile" for v in recommended_camera):
        recommended_camera.append({"view_id": "longitudinal_profile", "mode": "longitudinal_profile"})

    reusable_assets: list[dict[str, Any]] = []
    seen_assets: set[str] = set()
    for operator in operator_priority:
        for edge in graph.get("edges", []):
            if edge.get("relation") == "materialized_as" and edge.get("from") == f"Operator/{operator}":
                asset_id = str(edge.get("to"))
                node = graph["nodes"].get(asset_id)
                if asset_id not in seen_assets and isinstance(node, dict) and not node.get("demoted"):
                    seen_assets.add(asset_id)
                    reusable_assets.append({"asset": node.get("name") or asset_id.split("/", 1)[1], "parameters": node.get("parameters", {}), "used_by_operator": operator})
    for case in cases:
        for edge in graph.get("edges", []):
            if edge.get("relation") == "used_asset" and edge.get("from") == case["node_id"]:
                asset_id = str(edge.get("to"))
                node = graph["nodes"].get(asset_id)
                if asset_id not in seen_assets and isinstance(node, dict):
                    seen_assets.add(asset_id)
                    reusable_assets.append({"asset": node.get("name") or asset_id.split("/", 1)[1], "parameters": node.get("parameters", {}), "used_by_operator": node.get("used_by_operator")})

    return {
        "graph_available": graph_available,
        "base_dir": str(base),
        "matched_patterns": matched,
        "operator_priority": operator_priority,
        "cases": cases,
        "recommended_camera_requests": recommended_camera,
        "reusable_assets": reusable_assets,
        "demoted_excluded": demoted_excluded,
        "operator_stats": {
            name: {"pass_count": node.get("pass_count", 0), "fail_count": node.get("fail_count", 0), "fail_streak": node.get("fail_streak", 0), "demoted": node.get("demoted", False)}
            for name, node in graph["nodes"].items() if isinstance(node, dict) and node.get("kind") == "operator"
        },
    }


def update_recipe_probe_feedback(
    case_id: str,
    probe_passed: bool,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Harness² 探针门控：按真实结果回写案例与关联算子的探针统计。

    ``probe_passed=True`` → ``pass_count += 1, fail_streak = 0``；
    ``probe_passed=False`` → ``fail_streak += 1``，达到 2 次自动 ``demoted``
    并在词条 Markdown 中写入 ``> [!WARNING]`` 熔断标注。
    """
    base = resolve_wiki_base_dir(base_dir)
    graph = _load_graph(base)
    normalized = case_id if case_id.startswith("Case/") else f"Case/{case_id}"
    node = graph["nodes"].get(normalized)
    if not isinstance(node, dict):
        return {"case_id": case_id, "updated": False, "reason": "case node not found"}
    passed = bool(probe_passed)
    if passed:
        node["pass_count"] = int(node.get("pass_count", 0)) + 1
        node["fail_streak"] = 0
    else:
        node["fail_streak"] = int(node.get("fail_streak", 0)) + 1
        if node["fail_streak"] >= 2:
            node["demoted"] = True
    node["updated_at"] = _now()

    # 关联胜出算子的全局统计同步回写。
    operator_ids = {
        str(edge.get("to")) for edge in graph.get("edges", [])
        if edge.get("relation") == "applied" and edge.get("from") == normalized
    }
    for operator_id in operator_ids:
        operator = graph["nodes"].get(operator_id)
        if not isinstance(operator, dict):
            continue
        if passed:
            operator["pass_count"] = int(operator.get("pass_count", 0)) + 1
            operator["fail_streak"] = 0
            operator["demoted"] = False
        else:
            operator["fail_count"] = int(operator.get("fail_count", 0)) + 1
            operator["fail_streak"] = int(operator.get("fail_streak", 0)) + 1
            if operator["fail_streak"] >= 2:
                operator["demoted"] = True
    _save_graph(base, graph)

    case_path = base / "cases" / f"{node.get('case_id', normalized.split('/', 1)[1])}.md"
    demoted_now = bool(node.get("demoted"))
    if case_path.is_file():
        text = case_path.read_text(encoding="utf-8")
        text = re.sub(r"pass_count: \d+", f"pass_count: {node['pass_count']}", text)
        text = re.sub(r"fail_streak: \d+", f"fail_streak: {node['fail_streak']}", text)
        text = re.sub(r"demoted: (true|false)", f"demoted: {'true' if demoted_now else 'false'}", text)
        text = re.sub(r"pass_count=\d+, fail_streak=\d+, demoted=(True|False)", f"pass_count={node['pass_count']}, fail_streak={node['fail_streak']}, demoted={demoted_now}", text)
        if demoted_now and DEMOTE_CALLOUT not in text:
            text = f"{DEMOTE_CALLOUT}\n\n" + text
        case_path.write_text(text, encoding="utf-8")
    return {
        "case_id": node.get("case_id", case_id),
        "updated": True,
        "pass_count": node["pass_count"],
        "fail_streak": node["fail_streak"],
        "demoted": demoted_now,
        "operators_updated": sorted(operator_ids),
    }


__all__ = [
    "DEFAULT_WIKI_ONTOLOGY_DIR",
    "GRAPH_FILE",
    "SEED_ASSETS",
    "SEED_OPERATORS",
    "SEED_PATTERNS",
    "SEED_RULES",
    "compile_and_archive_wiki_case",
    "resolve_wiki_base_dir",
    "retrieve_ontology_recipes",
    "update_recipe_probe_feedback",
]
