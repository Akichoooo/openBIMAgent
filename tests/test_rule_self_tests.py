"""规则自检样例 (self_tests) 测试：加载即单测，对标 Codex execpolicy 语义。

覆盖：真实知识源全量重放通过、production 治理、投毒样例失败关闭、
边界值语义 (le 含端点 / gt 不含)、缺属性失败关闭计入 not_match。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openbimagent.utility import (
    MunicipalRuleError,
    RuleEnforcement,
    RuleSelectionStatus,
    compile_municipal_rule_set,
    run_rule_self_tests,
    select_clearance_rule,
    validate_rule_self_tests,
)

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "domain_packs" / "municipal_utility" / "knowledge" / "constraints.yaml"


def _load_payload() -> dict:
    return yaml.safe_load(CONSTRAINTS.read_text(encoding="utf-8"))


def _write_payload(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "constraints.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_real_rule_set_self_tests_all_pass_and_cover_production() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    outcomes = run_rule_self_tests(rule_set)
    assert len(outcomes) >= 24  # 12 条规则 × (≥1 match + ≥1 not_match)
    assert all(outcome.ok for outcome in outcomes)
    # 治理：production 规则两类样例缺一不可
    for rule in rule_set.rules:
        if rule.enforcement is RuleEnforcement.PRODUCTION:
            assert rule.self_tests.match, f"{rule.rule_key} 缺 match 样例"
            assert rule.self_tests.not_match, f"{rule.rule_key} 缺 not_match 样例"
    assert validate_rule_self_tests(rule_set) == outcomes


def test_self_tests_cover_boundary_and_fail_closed_semantics() -> None:
    rule_set = compile_municipal_rule_set(CONSTRAINTS)
    cases = [
        # le 含端点：d=200 恰好落在 d_le_200
        ("water", {"outer_diameter_mm": 200.0}, "MU-CLEAR-005:water:d_le_200"),
        # gt 不含端点：d=200 不属于 d_gt_200
        ("water", {"outer_diameter_mm": 200.1}, "MU-CLEAR-005:water:d_gt_200"),
        ("gas", {"pressure_class": "medium_a"}, "MU-CLEAR-006:gas:medium_a"),
    ]
    for category, attributes, expected_key in cases:
        selection = select_clearance_rule(
            rule_set,
            obstacle_kind="existing_pipe",
            obstacle_category=category,
            attributes=attributes,
        )
        assert selection.status is RuleSelectionStatus.SELECTED
        assert selection.rule is not None
        assert selection.rule.rule_key == expected_key

    # 表外档位与缺属性均失败关闭（不选中任何规则）
    for attributes in ({"pressure_class": "ultra_high"}, {}):
        selection = select_clearance_rule(
            rule_set,
            obstacle_kind="existing_pipe",
            obstacle_category="gas",
            attributes=attributes,
        )
        assert selection.rule is None
        assert selection.status is not RuleSelectionStatus.SELECTED


def test_poisoned_match_case_rejects_compilation(tmp_path: Path) -> None:
    """把跨界值塞进 match 样例 → 重放选不中本规则 → 整个规则集拒绝编译。"""
    payload = _load_payload()
    rule = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-005")
    rule["self_tests"]["d_le_200"]["match"].append(
        {"obstacle_kind": "existing_pipe", "obstacle_category": "water", "attributes": {"outer_diameter_mm": 300}}
    )
    with pytest.raises(MunicipalRuleError) as exc_info:
        compile_municipal_rule_set(_write_payload(tmp_path, payload))
    assert "MU-CLEAR-005:water:d_le_200" in str(exc_info.value)
    assert "match" in str(exc_info.value)


def test_poisoned_not_match_case_rejects_compilation(tmp_path: Path) -> None:
    """把本档触发值塞进 not_match 样例 → 重放命中本规则 → 拒绝编译。"""
    payload = _load_payload()
    rule = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-006")
    rule["self_tests"]["low"]["not_match"].append(
        {"obstacle_kind": "existing_pipe", "obstacle_category": "gas", "attributes": {"pressure_class": "low"}}
    )
    with pytest.raises(MunicipalRuleError) as exc_info:
        compile_municipal_rule_set(_write_payload(tmp_path, payload))
    assert "MU-CLEAR-006:gas:low" in str(exc_info.value)
    assert "not_match" in str(exc_info.value)


def test_production_rule_without_self_tests_rejected(tmp_path: Path) -> None:
    """production 规则缺任一极性样例 → 治理失败关闭。"""
    payload = _load_payload()
    rule = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-001")
    del rule["self_tests"]
    with pytest.raises(MunicipalRuleError) as exc_info:
        compile_municipal_rule_set(_write_payload(tmp_path, payload))
    assert "MU-CLEAR-001:building" in str(exc_info.value)
    assert "production" in str(exc_info.value)


def test_unknown_variant_key_in_self_tests_rejected(tmp_path: Path) -> None:
    payload = _load_payload()
    rule = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-007")
    rule["self_tests"]["nonexistent_variant"] = {"match": [], "not_match": []}
    with pytest.raises(MunicipalRuleError, match="self_tests"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))


def test_malformed_self_tests_rejected(tmp_path: Path) -> None:
    payload = _load_payload()
    rule = next(item for item in payload["constraints"] if item["rule_id"] == "MU-CLEAR-008")
    rule["self_tests"] = {"match": [{"obstacle_kind": ""}], "not_match": []}
    with pytest.raises(MunicipalRuleError, match="self_tests"):
        compile_municipal_rule_set(_write_payload(tmp_path, payload))
