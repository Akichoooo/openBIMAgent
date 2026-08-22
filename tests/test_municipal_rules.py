"""MunicipalRuleSet 编译、核验证据、选择与失败关闭语义测试。"""

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
        "protocol_version": "0.4",
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


def _write_payload(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "constraints.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _rule(payload: dict, rule_id: str) -> dict:
    return next(item for item in payload["constraints"] if item["rule_id"] == rule_id)


def test_rule_set_compiles_with_source_canonical_hashes_and_verification() -> None:
    first = compile_municipal_rule_set(CONSTRAINTS)
    second = compile_municipal_rule_set(CONSTRAINTS)
    assert first.protocol_version == "1.2"
    assert first.compiler_version == "0.3.0"
    assert first.source_sha256 == hashlib.sha256(CONSTRAINTS.read_bytes()).hexdigest()
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256 == second.canonical_sha256
    assert len(first.rules) == 12
    assert all(rule.verification.production_eligible() for rule in first.rules)
    assert SchemaGate().validate_artifact("municipal_rule_set", first.model_dump(mode="json")) == []


def test_verified_rules_are_production_executable_with_exact_table_values() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    cases = [
        ("building", {}, 2.5, "MU-CLEAR-001:building"),
        ("water", {"outer_diameter_mm": 200.0}, 1.0, "MU-CLEAR-005:water:d_le_200"),
        ("water", {"outer_diameter_mm": 201.0}, 1.5, "MU-CLEAR-005:water:d_gt_200"),
        ("gas", {"pressure_class": "low"}, 1.0, "MU-CLEAR-006:gas:low"),
        ("gas", {"pressure_class": "medium_b"}, 1.2, "MU-CLEAR-006:gas:medium_b"),
        ("gas", {"pressure_class": "medium_a"}, 1.2, "MU-CLEAR-006:gas:medium_a"),
        ("gas", {"pressure_class": "sub_high_b"}, 1.5, "MU-CLEAR-006:gas:sub_high_b"),
        ("gas", {"pressure_class": "sub_high_a"}, 2.0, "MU-CLEAR-006:gas:sub_high_a"),
        ("telecom", {"burial_method": "direct_buried"}, 1.0, "MU-CLEAR-007:telecom:direct_buried"),
        ("telecom", {"burial_method": "duct"}, 1.0, "MU-CLEAR-007:telecom:duct"),
        ("power", {"burial_method": "direct_buried"}, 0.5, "MU-CLEAR-008:power:direct_buried"),
        ("power", {"burial_method": "protective_conduit"}, 0.5, "MU-CLEAR-008:power:protective_conduit"),
    ]
    for category, attributes, clearance, key in cases:
        result = select_clearance_rule(
            rule_set,
            obstacle_kind="aabb" if category == "building" else "existing_pipe",
            obstacle_category=category,
            attributes=attributes,
        )
        assert result.status is RuleSelectionStatus.SELECTED
        assert result.rule is not None
        assert result.rule.rule_key == key
        assert result.rule.required_clearance_m == pytest.approx(clearance)
        assert result.rule.source_clause == "GB 50289-2016 §4.1.9 表 4.1.9"
        assert result.rule.enforcement.value == "production"


def test_power_rule_does_not_require_voltage_not_present_in_cross_cell() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    result = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="power",
        attributes={"burial_method": "direct_buried", "voltage_kv": None},
    )
    assert result.status is RuleSelectionStatus.SELECTED
    assert result.rule is not None
    assert result.rule.required_attributes == ("burial_method",)


def test_missing_attributes_and_unmodelled_variants_fail_closed() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    gas = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="gas",
        attributes={},
    )
    assert gas.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert gas.missing_attributes == ("pressure_class",)

    unsupported = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="telecom",
        attributes={"burial_method": "tunnel"},
    )
    assert unsupported.status is RuleSelectionStatus.UNSUPPORTED


def test_high_confidence_alone_cannot_promote_rule_without_complete_verification(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    target = _rule(payload, "MU-CLEAR-007")
    target["verification"]["applicability_complete"] = False
    rule_set = compile_municipal_rule_set(_write_payload(tmp_path, payload))
    selected = select_clearance_rule(
        rule_set,
        obstacle_kind="existing_pipe",
        obstacle_category="telecom",
        attributes={"burial_method": "direct_buried"},
    )
    assert selected.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert selected.rule is not None
    assert selected.rule.confidence.value == "high"
    assert selected.rule.enforcement.value == "review_required"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "unverified"),
        ("source_tier", "authoritative_secondary"),
        ("standard_status", "unknown"),
        ("standard_id", "GB 50289-1998"),
        ("clause", "B.0.1"),
        ("table", "B.0.1"),
        ("crosscheck_source", None),
    ],
)
def test_verification_drift_forces_review_required(tmp_path, field, value) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    target = _rule(payload, "MU-CLEAR-001")
    target["verification"][field] = value
    rule_set = compile_municipal_rule_set(_write_payload(tmp_path, payload))
    building = select_clearance_rule(
        rule_set,
        obstacle_kind="aabb",
        obstacle_category="building",
    )
    assert building.status is RuleSelectionStatus.REVIEW_REQUIRED
    assert building.rule is not None
    assert building.rule.enforcement.value == "review_required"


def test_malformed_or_missing_verification_is_rejected(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    del _rule(payload, "MU-CLEAR-005")["verification"]
    with pytest.raises(MunicipalRuleError, match="verification 非法"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))


def test_compiler_rejects_wrong_value_shape_and_missing_variant(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    target = _rule(payload, "MU-CLEAR-006")
    del target["values"]["medium_a"]
    with pytest.raises(MunicipalRuleError, match="values 必须精确包含"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))


def test_compiler_rejects_source_schema_drift(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    target = _rule(payload, "MU-CLEAR-001")
    target["value"] = "caller-controlled"
    with pytest.raises(MunicipalRuleError, match="value 必须是有限数值"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))


def test_compiler_rejects_missing_required_source_rule(tmp_path) -> None:
    payload = yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))
    payload["constraints"] = [
        item for item in payload["constraints"] if item["rule_id"] != "MU-CLEAR-008"
    ]
    with pytest.raises(MunicipalRuleError, match="缺少必需规则 MU-CLEAR-008"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))


def test_ambiguous_rules_fail_closed_and_unvalidated_rule_set_cannot_enter_solver() -> None:
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
    with pytest.raises(UtilitySolverError, match="MunicipalRuleSet 未通过门禁"):
        solve_straight_gravity_utility(
            _minimal_solver_payload(),
            municipal_rule_set=injected,
        )
