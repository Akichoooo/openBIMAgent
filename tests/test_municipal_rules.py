"""MunicipalRuleSet 编译、选择与失败关闭语义测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import (
    MunicipalRuleError,
    RuleSelectionStatus,
    UtilitySolverError,
    compile_municipal_rule_set,
    select_clearance_rule,
    solve_straight_gravity_utility,
)

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "domain_packs" / "municipal_utility" / "knowledge" / "constraints.yaml"


def _minimal_solver_payload() -> dict:
    return {
        "protocol_version": "0.3",
        "request_id": "rules-test",
        "source_ir_sha256": "d" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": None,
        },
        "start": {"node_id": "a", "x_m": 0.0, "y_m": 0.0, "ground_elevation_m": 11.0},
        "end": {"node_id": "b", "x_m": 10.0, "y_m": 0.0, "ground_elevation_m": 11.0},
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
        "start_invert_m": None,
        "collision_context": {"coverage": "complete", "obstacles": []},
    }



def test_rule_set_compiles_with_source_and_canonical_hashes() -> None:
    first = compile_municipal_rule_set(CONSTRAINTS)
    second = compile_municipal_rule_set(CONSTRAINTS)
    assert first.protocol_version == "1.0"
    assert first.source_sha256 == hashlib.sha256(CONSTRAINTS.read_bytes()).hexdigest()
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256 == second.canonical_sha256
    assert len(first.rules) == 6
    assert SchemaGate().validate_artifact("municipal_rule_set", first.model_dump(mode="json")) == []


def test_only_high_confidence_building_rule_is_production_executable() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    building = select_clearance_rule(
        rule_set,
        obstacle_kind="aabb",
        obstacle_category="building",
    )
    assert building.status is RuleSelectionStatus.SELECTED
    assert building.rule is not None
    assert building.rule.source_rule_id == "MU-CLEAR-001"
    assert building.rule.required_clearance_m == pytest.approx(2.5)
    assert building.rule.enforcement.value == "production"

    water = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="water",
        attributes={"outer_diameter_mm": 200.0},
    )
    assert water.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert water.rule is not None
    assert water.rule.required_clearance_m == pytest.approx(1.0)


def test_water_range_selection_is_diameter_deterministic_but_review_required() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    small = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="water",
        attributes={"outer_diameter_mm": 200.0},
    )
    large = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="water",
        attributes={"outer_diameter_mm": 201.0},
    )
    assert small.rule is not None and small.rule.rule_key.endswith("d_le_200")
    assert small.rule.required_clearance_m == pytest.approx(1.0)
    assert large.rule is not None and large.rule.rule_key.endswith("d_gt_200")
    assert large.rule.required_clearance_m == pytest.approx(1.5)
    assert small.status is large.status is RuleSelectionStatus.REVIEW_REQUIRED


def test_ambiguous_matching_rules_fail_closed_and_unvalidated_rule_set_cannot_enter_solver() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    building = next(rule for rule in rule_set.rules if rule.rule_key == "MU-CLEAR-001:building")
    duplicate = building.model_copy(update={"rule_key": "MU-CLEAR-001:building:duplicate"})
    injected = rule_set.model_copy(update={"rules": (*rule_set.rules, duplicate)})
    result = select_clearance_rule(
        injected,
        obstacle_kind="aabb",
        obstacle_category="building",
    )
    assert result.status is RuleSelectionStatus.AMBIGUOUS
    assert set(result.candidate_rule_keys) == {
        "MU-CLEAR-001:building",
        "MU-CLEAR-001:building:duplicate",
    }
    with pytest.raises(UtilitySolverError, match="MunicipalRuleSet 未通过门禁"):
        solve_straight_gravity_utility(
            _minimal_solver_payload(),
            municipal_rule_set=injected,
        )



def test_missing_attributes_and_unknown_category_fail_closed() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    water = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="water",
        attributes={},
    )
    assert water.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert water.missing_attributes == ("outer_diameter_mm",)

    gas = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="gas",
        attributes={"outer_diameter_mm": 100.0},
    )
    assert gas.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert gas.missing_attributes == ("pressure_class",)

    unsupported = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="steam",
    )
    assert unsupported.status is RuleSelectionStatus.UNSUPPORTED


def test_medium_rules_for_gas_power_and_telecom_never_silently_produce_pass_fail() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    cases = [
        ("gas", {"pressure_class": "low", "outer_diameter_mm": 100.0}),
        (
            "power",
            {"burial_method": "direct_buried", "voltage_kv": 10.0, "outer_diameter_mm": 100.0},
        ),
        ("telecom", {"burial_method": "direct_buried", "outer_diameter_mm": 100.0}),
    ]
    for category, attributes in cases:
        result = select_clearance_rule(
            rule_set,
            obstacle_kind="existing_pipe",
            obstacle_category=category,
            attributes=attributes,
        )
        assert result.status is RuleSelectionStatus.REVIEW_REQUIRED
        assert result.rule is not None
        assert result.rule.confidence.value == "medium"


def test_compiler_rejects_source_schema_drift(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    target = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-001")
    target["value"] = "caller-controlled"
    drifted = tmp_path / "constraints.yaml"
    drifted.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(MunicipalRuleError, match="value 必须是有限数值"):
        compile_municipal_rule_set(drifted)


def test_compiler_rejects_missing_required_source_rule(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    payload["constraints"] = [
        item for item in payload["constraints"] if item["rule_id"] != "MU-CLEAR-008"
    ]
    drifted = tmp_path / "constraints.yaml"
    drifted.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(MunicipalRuleError, match="缺少必需规则 MU-CLEAR-008"):
        compile_municipal_rule_set(drifted)
