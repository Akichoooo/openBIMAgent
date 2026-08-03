"""T6 规则证据、规则集漂移和减距例外协议测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility.rule_evidence import (
    compile_municipal_rule_evidence_bundle,
    ClearanceExceptionApproval,
    ExceptionApprovalStatus,
    ExceptionScope,
    MunicipalRuleEvidenceBundle,
    RuleApplicability,
    RuleDecisionStatus,
    RuleEnforcement,
    RuleEvidenceSourceTier,
    EvidenceRuleSelectionStatus,
    RuleType,
    StandardEvidence,
    VerifiedMunicipalRule,
    authorize_clearance_reduction,
    build_clearance_exception_approval,
    build_municipal_rule_evidence_bundle,
    build_rule_evaluation,
    build_standard_evidence,
    build_verified_municipal_rule,
    evaluate_municipal_rule,
    select_municipal_rule,
)


def _official_verification() -> StandardEvidence:
    return build_standard_evidence(
        status="verified",
        source_tier="official",
        standard_id="GB 50289-2016",
        standard_title="城市工程管线综合规划规范",
        standard_status="current",
        status_checked_at=date(2026, 8, 1),
        status_source_url="https://www.gongbiaoku.com/book/tb617154atk",
        clause="4.1.9",
        table="4.1.9",
        content_checked_at=date(2026, 8, 1),
        official_copy_url="https://www.yichang.gov.cn/example/gb50289.pdf",
        official_copy_sha256="a" * 64,
        secondary_source_url=None,
        secondary_source_sha256=None,
        evidence_locator="PDF pages 13-15; row wastewater; column building",
        applicability_complete=True,
        production_verification="eligible",
    )


def _production_rule() -> VerifiedMunicipalRule:
    return build_verified_municipal_rule(
        rule_id="MU-CLEAR-001:building",
        source_rule_id="MU-CLEAR-001",
        rule_type="horizontal_clearance",
        parameter="clearance_building_to_sewage_rain",
        operator="minimum",
        value=2.5,
        unit="m",
        source_clause="GB 50289-2016 §4.1.9 表 4.1.9",
        applicability=(
            RuleApplicability(field="design_system", operator="eq", value="wastewater"),
            RuleApplicability(field="obstacle_category", operator="eq", value="building"),
        ),
        confidence="high",
        verification=_official_verification(),
        enforcement="production",
    )


def _bundle(*rules: VerifiedMunicipalRule) -> MunicipalRuleEvidenceBundle:
    return build_municipal_rule_evidence_bundle(
        bundle_id="municipal-t6-rules",
        source_path="knowledge/constraints.yaml",
        source_sha256="b" * 64,
        compiler_name="municipal-rule-evidence-compiler",
        compiler_version="1.0.0",
        rules=rules or (_production_rule(),),
    )


def _valid_exception(
    bundle: MunicipalRuleEvidenceBundle,
    rule: VerifiedMunicipalRule,
) -> ClearanceExceptionApproval:
    approved_at = datetime(2026, 8, 1, tzinfo=UTC)
    return build_clearance_exception_approval(
        exception_id="EXC-2026-001",
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        original_rule_id=rule.rule_id,
        original_clearance_m=2.5,
        approved_clearance_m=2.0,
        safety_measures=("增设钢筋混凝土防护套管",),
        rationale="受既有构筑物边界约束，采用专项防护后减距。",
        risks=("检修空间缩小",),
        approver_id="engineer-001",
        approver_role="chief_engineer",
        approver_authorities=("approve_clearance_reduction",),
        valid_scope=ExceptionScope(
            project_id="project-001",
            subject_ids=("pipe-001",),
            rule_ids=(rule.rule_id,),
        ),
        approved_at=approved_at,
        expires_at=approved_at + timedelta(days=30),
        approval_status="approved",
        audit_references=("approval://project-001/EXC-2026-001",),
    )


# 负向：二手或 legacy 证据不得通过显式字段伪装为 production。
@pytest.mark.parametrize("source_tier", ["secondary", "legacy"])
def test_non_official_evidence_cannot_claim_production_eligibility(source_tier: str) -> None:
    with pytest.raises(ValidationError, match="production_verification"):
        build_standard_evidence(
            status="verified",
            source_tier=source_tier,
            standard_id="GB 50014-2021",
            standard_title="室外排水设计标准",
            standard_status="current",
            status_checked_at=date(2026, 8, 4),
            status_source_url="https://www.mohurd.gov.cn/example/status.html",
            clause="5.2.7 第 1 款",
            table=None,
            content_checked_at=date(2026, 8, 4),
            official_copy_url=None,
            official_copy_sha256=None,
            secondary_source_url="https://example.com/secondary.pdf",
            secondary_source_sha256="c" * 64,
            evidence_locator="PDF clause 5.2.7 item 1",
            applicability_complete=True,
            production_verification="eligible",
        )


def test_legacy_original_text_remains_review_required() -> None:
    verification = build_standard_evidence(
        status="verified",
        source_tier="legacy",
        standard_id="GB 50014-2021",
        standard_title="室外排水设计标准",
        standard_status="current",
        status_checked_at=date(2026, 8, 4),
        status_source_url="https://www.mohurd.gov.cn/example/status.html",
        clause="5.2.7 第 1 款",
        table=None,
        content_checked_at=None,
        official_copy_url=None,
        official_copy_sha256=None,
        secondary_source_url=None,
        secondary_source_sha256=None,
        evidence_locator="legacy verified_by=original_text",
        applicability_complete=True,
        production_verification="review_required",
    )
    rule = build_verified_municipal_rule(
        rule_id="MU-DRAIN-007",
        source_rule_id="MU-DRAIN-007",
        rule_type="hydraulics",
        parameter="min_velocity_sewage",
        operator="minimum",
        value=0.6,
        unit="m/s",
        source_clause="GB 50014-2021 §5.2.7 第 1 款",
        applicability=(RuleApplicability(field="system_type", operator="eq", value="wastewater"),),
        confidence="high",
        verification=verification,
        enforcement="review_required",
    )
    assert rule.enforcement is RuleEnforcement.REVIEW_REQUIRED
    assert rule.verification.source_tier is RuleEvidenceSourceTier.LEGACY


# 负向：任意内容或 canonical 摘要漂移都必须拒绝。
def test_standard_evidence_canonical_drift_is_rejected() -> None:
    verification = _official_verification()
    with pytest.raises(ValidationError, match="canonical_sha256"):
        StandardEvidence.model_validate(
            {**verification.model_dump(mode="json"), "canonical_sha256": "0" * 64}
        )


def test_rule_and_bundle_canonical_drift_or_conflict_is_rejected() -> None:
    rule = _production_rule()
    with pytest.raises(ValidationError, match="canonical_sha256"):
        VerifiedMunicipalRule.model_validate(
            {**rule.model_dump(mode="json"), "canonical_sha256": "0" * 64}
        )
    conflicting = build_verified_municipal_rule(
        **{
            **rule.model_dump(mode="python", exclude={"canonical_sha256"}),
            "value": 2.0,
        }
    )
    with pytest.raises(ValidationError, match="rule_id 不能重复"):
        build_municipal_rule_evidence_bundle(
            bundle_id="conflict",
            source_path="knowledge/constraints.yaml",
            source_sha256="b" * 64,
            compiler_name="municipal-rule-evidence-compiler",
            compiler_version="1.0.0",
            rules=(rule, conflicting),
        )
    bundle = _bundle(rule)
    with pytest.raises(ValidationError, match="canonical_sha256"):
        MunicipalRuleEvidenceBundle.model_validate(
            {**bundle.model_dump(mode="json"), "canonical_sha256": "0" * 64}
        )


# 负向：审批必须完整、已批准、未过期、有授权且与规则和对象范围精确绑定。
def test_incomplete_exception_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClearanceExceptionApproval.model_validate(
            {
                "protocol_version": "1.0",
                "exception_id": "EXC-INCOMPLETE",
                "rule_set_sha256": "a" * 64,
                "rule_sha256": "b" * 64,
                "original_rule_id": "MU-CLEAR-001:building",
            }
        )


def test_expired_unauthorized_and_out_of_scope_exception_fail_closed() -> None:
    rule = _production_rule()
    bundle = _bundle(rule)
    approval = _valid_exception(bundle, rule)
    common = {
        "approval": approval,
        "rule_set_sha256": bundle.canonical_sha256,
        "rule_sha256": rule.canonical_sha256,
        "rule_id": rule.rule_id,
        "project_id": "project-001",
        "subject_id": "pipe-001",
        "requested_clearance_m": 2.0,
    }
    with pytest.raises(ValueError, match="已过期"):
        authorize_clearance_reduction(**common, evaluated_at=approval.expires_at)

    unauthorized = build_clearance_exception_approval(
        **{
            **approval.model_dump(mode="python", exclude={"canonical_sha256"}),
            "approver_authorities": ("review_only",),
        }
    )
    with pytest.raises(ValueError, match="无减距审批权限"):
        authorize_clearance_reduction(
            **{**common, "approval": unauthorized},
            evaluated_at=approval.approved_at + timedelta(days=1),
        )

    with pytest.raises(ValueError, match="subject scope"):
        authorize_clearance_reduction(
            **{**common, "subject_id": "pipe-999"},
            evaluated_at=approval.approved_at + timedelta(days=1),
        )
    with pytest.raises(ValueError, match="低于批准净距"):
        authorize_clearance_reduction(
            **{**common, "requested_clearance_m": 1.9},
            evaluated_at=approval.approved_at + timedelta(days=1),
        )


# 负向：REVIEW_REQUIRED 与 PASS/FAIL 使用不同语义；规范 PASS 必须绑定 production verification。
def test_rule_evaluation_status_and_hash_bindings_fail_closed() -> None:
    rule = _production_rule()
    bundle = _bundle(rule)
    with pytest.raises(ValidationError, match="review_reason"):
        build_rule_evaluation(
            evaluation_id="eval-review",
            rule_set_sha256=bundle.canonical_sha256,
            rule_sha256=rule.canonical_sha256,
            verification_sha256=rule.verification.canonical_sha256,
            production_verification="review_required",
            rule_id=rule.rule_id,
            subject_type="segment",
            subject_id="pipe-001",
            measured_value=2.6,
            limit_value=2.5,
            unit="m",
            status="review_required",
            review_reason=None,
            exception_approval_id=None,
            exception_approval_sha256=None,
        )
    with pytest.raises(ValidationError, match="PASS/FAIL"):
        build_rule_evaluation(
            evaluation_id="eval-false-pass",
            rule_set_sha256=bundle.canonical_sha256,
            rule_sha256=rule.canonical_sha256,
            verification_sha256=rule.verification.canonical_sha256,
            production_verification="review_required",
            rule_id=rule.rule_id,
            subject_type="segment",
            subject_id="pipe-001",
            measured_value=2.6,
            limit_value=2.5,
            unit="m",
            status="pass",
            review_reason=None,
            exception_approval_id=None,
            exception_approval_sha256=None,
        )


def test_repository_rule_evidence_bundle_covers_t6_rule_types_and_is_stable() -> None:
    first = compile_municipal_rule_evidence_bundle()
    second = compile_municipal_rule_evidence_bundle()
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.canonical_json() == second.canonical_json()
    assert {rule.rule_type.value for rule in first.rules} >= {
        "horizontal_clearance",
        "vertical_clearance",
        "road_crossing",
        "rail_crossing",
        "river_crossing",
        "structure_clearance",
        "hydraulics",
    }
    velocity = first.rule("MU-DRAIN-007")
    assert velocity.enforcement is RuleEnforcement.PRODUCTION
    assert velocity.value == pytest.approx(0.6)
    assert velocity.verification.official_copy_sha256 == (
        "c3c3df5ae9ca5bb77c34ee4506e194e4e588c8d5d0e14a3d43704ae710fdf9b1"
    )
    rail = first.rule("MU-RAIL-001:passenger_200_plus")
    assert rail.value == pytest.approx(1.5)
    assert first.rule("MU-RIVER-001:other_river").value == pytest.approx(0.5)
    assert first.rule("MU-VERTICAL-001:water").value == pytest.approx(0.4)
    assert first.rule("MU-CLEAR-001:building").rule_type.value == "structure_clearance"
    assert SchemaGate().validate_artifact(
        "municipal_rule_evidence_bundle", first.model_dump(mode="json")
    ) == []

    expected_variants = {
        RuleType.VERTICAL_CLEARANCE: {
            "water", "wastewater_rainwater", "heat", "gas",
            "telecom_direct_buried", "telecom_protected",
            "power_direct_buried", "power_protected", "reclaimed_water",
            "utility_tunnel", "culvert_base", "tram_track_bottom",
        },
        RuleType.RAIL_CROSSING: {"conventional", "passenger_200_plus"},
        RuleType.RIVER_CROSSING: {
            "navigation_i_to_v", "navigation_vi_to_vii", "other_river",
        },
    }
    fact_field = {
        RuleType.VERTICAL_CLEARANCE: "crossed_asset_type",
        RuleType.RAIL_CROSSING: "rail_class",
        RuleType.RIVER_CROSSING: "waterway_class",
    }
    for rule_type, variants in expected_variants.items():
        for variant in variants:
            selection = select_municipal_rule(
                first,
                rule_type=rule_type,
                facts={fact_field[rule_type]: variant},
            )
            assert selection.status is EvidenceRuleSelectionStatus.SELECTED
            assert selection.rule is not None
            assert selection.rule.rule_id.endswith(f":{variant}")


# 正向：完整 official 规则、有效审批、评估和两个 JSON Schema 全部通过。
def test_official_rule_valid_exception_and_schema_gate_pass() -> None:
    rule = _production_rule()
    bundle = _bundle(rule)
    approval = _valid_exception(bundle, rule)
    authorize_clearance_reduction(
        approval=approval,
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        rule_id=rule.rule_id,
        project_id="project-001",
        subject_id="pipe-001",
        requested_clearance_m=2.0,
        evaluated_at=approval.approved_at + timedelta(days=1),
    )
    evaluation = build_rule_evaluation(
        evaluation_id="eval-pass",
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        verification_sha256=rule.verification.canonical_sha256,
        production_verification="eligible",
        rule_id=rule.rule_id,
        subject_type="segment",
        subject_id="pipe-001",
        measured_value=2.0,
        limit_value=2.0,
        unit="m",
        status="pass",
        review_reason=None,
        exception_approval_id=approval.exception_id,
        exception_approval_sha256=approval.canonical_sha256,
    )
    assert evaluation.status is RuleDecisionStatus.PASS
    assert approval.approval_status is ExceptionApprovalStatus.APPROVED
    gate = SchemaGate()
    assert gate.validate_artifact("municipal_rule_evidence_bundle", bundle.model_dump(mode="json")) == []
    assert gate.validate_artifact("clearance_exception_approval", approval.model_dump(mode="json")) == []


@pytest.mark.parametrize(
    ("rule_type", "facts", "rule_id", "limit"),
    [
        (RuleType.HYDRAULICS, {"system_type": "wastewater", "flow_regime": "gravity"}, "MU-DRAIN-007", 0.6),
        (RuleType.ROAD_CROSSING, {"crossing_target": "road", "crossing_mode": "limited"}, "MU-ROAD-001", 60.0),
        (RuleType.ROAD_CROSSING, {"crossing_target": "road", "crossing_mode": "normal"}, "MU-ROAD-001:perpendicular", 90.0),
        (RuleType.RAIL_CROSSING, {"rail_class": "passenger_200_plus"}, "MU-RAIL-001:passenger_200_plus", 1.5),
        (RuleType.RIVER_CROSSING, {"waterway_class": "navigation_vi_to_vii"}, "MU-RIVER-001:navigation_vi_to_vii", 1.0),
        (RuleType.VERTICAL_CLEARANCE, {"crossed_asset_type": "power_protected"}, "MU-VERTICAL-001:power_protected", 0.25),
        (RuleType.STRUCTURE_CLEARANCE, {"obstacle_category": "building"}, "MU-CLEAR-001:building", 2.5),
        (RuleType.HORIZONTAL_CLEARANCE, {"obstacle_category": "gas", "pressure_class": "sub_high_a"}, "MU-CLEAR-006:sub_high_a", 2.0),
    ],
)
def test_t6_selector_covers_all_rule_types(
    rule_type: RuleType,
    facts: dict[str, object],
    rule_id: str,
    limit: float,
) -> None:
    selection = select_municipal_rule(
        compile_municipal_rule_evidence_bundle(),
        rule_type=rule_type,
        facts=facts,
    )
    assert selection.status is EvidenceRuleSelectionStatus.SELECTED
    assert selection.rule is not None
    assert selection.rule.rule_id == rule_id
    assert selection.rule.value == pytest.approx(limit)


@pytest.mark.parametrize(
    ("rule_type", "facts", "missing"),
    [
        (RuleType.RAIL_CROSSING, {}, "rail_class"),
        (RuleType.RIVER_CROSSING, {}, "waterway_class"),
        (RuleType.VERTICAL_CLEARANCE, {}, "crossed_asset_type"),
        (RuleType.HORIZONTAL_CLEARANCE, {"obstacle_category": "gas"}, "pressure_class"),
    ],
)
def test_t6_selector_missing_attributes_requires_review(
    rule_type: RuleType,
    facts: dict[str, object],
    missing: str,
) -> None:
    selection = select_municipal_rule(
        compile_municipal_rule_evidence_bundle(),
        rule_type=rule_type,
        facts=facts,
    )
    assert selection.status is EvidenceRuleSelectionStatus.REVIEW_REQUIRED
    assert missing in selection.missing_attributes


def test_t6_selector_rejects_unsupported_and_ambiguous_facts() -> None:
    rule = _production_rule()
    duplicate = build_verified_municipal_rule(
        **{
            **rule.model_dump(mode="python", exclude={"canonical_sha256"}),
            "rule_id": "MU-CLEAR-001:building-duplicate",
        }
    )
    ambiguous = select_municipal_rule(
        _bundle(rule, duplicate),
        rule_type=RuleType.HORIZONTAL_CLEARANCE,
        facts={"design_system": "wastewater", "obstacle_category": "building"},
    )
    assert ambiguous.status is EvidenceRuleSelectionStatus.AMBIGUOUS
    assert len(ambiguous.candidate_rule_ids) == 2

    unsupported = select_municipal_rule(
        compile_municipal_rule_evidence_bundle(),
        rule_type=RuleType.RAIL_CROSSING,
        facts={"rail_class": "maglev"},
    )
    assert unsupported.status is EvidenceRuleSelectionStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("rule_type", "facts", "measured", "expected_status"),
    [
        (RuleType.ROAD_CROSSING, {"crossing_target": "road", "crossing_mode": "limited"}, 60.0, RuleDecisionStatus.PASS),
        (RuleType.ROAD_CROSSING, {"crossing_target": "railway", "crossing_mode": "limited"}, 59.9, RuleDecisionStatus.FAIL),
        (RuleType.ROAD_CROSSING, {"crossing_target": "road", "crossing_mode": "normal"}, 90.0, RuleDecisionStatus.PASS),
        (RuleType.ROAD_CROSSING, {"crossing_target": "road", "crossing_mode": "normal"}, 89.0, RuleDecisionStatus.FAIL),
        (RuleType.RAIL_CROSSING, {"rail_class": "conventional"}, 1.2, RuleDecisionStatus.PASS),
        (RuleType.RIVER_CROSSING, {"waterway_class": "navigation_i_to_v"}, 1.9, RuleDecisionStatus.FAIL),
        (RuleType.VERTICAL_CLEARANCE, {"crossed_asset_type": "telecom_direct_buried"}, 0.5, RuleDecisionStatus.PASS),
        (RuleType.HORIZONTAL_CLEARANCE, {"obstacle_category": "water", "outer_diameter_class": "d_gt_200"}, 1.4, RuleDecisionStatus.FAIL),
    ],
)
def test_t6_evaluator_builds_real_pass_fail_evaluations(
    rule_type: RuleType,
    facts: dict[str, object],
    measured: float,
    expected_status: RuleDecisionStatus,
) -> None:
    bundle = compile_municipal_rule_evidence_bundle()
    selection, evaluation = evaluate_municipal_rule(
        bundle,
        evaluation_id=f"eval-{rule_type.value}",
        rule_type=rule_type,
        facts=facts,
        subject_type="segment",
        subject_id="pipe-001",
        measured_value=measured,
    )
    assert selection.status is EvidenceRuleSelectionStatus.SELECTED
    assert evaluation is not None
    assert evaluation.status is expected_status
    assert evaluation.rule_set_sha256 == bundle.canonical_sha256
    assert evaluation.rule_sha256 == selection.rule.canonical_sha256  # type: ignore[union-attr]


def test_t6_evaluator_returns_no_identity_when_selection_is_not_unique() -> None:
    selection, evaluation = evaluate_municipal_rule(
        compile_municipal_rule_evidence_bundle(),
        evaluation_id="eval-missing-rail-class",
        rule_type=RuleType.RAIL_CROSSING,
        facts={},
        subject_type="segment",
        subject_id="pipe-001",
        measured_value=1.5,
    )
    assert selection.status is EvidenceRuleSelectionStatus.REVIEW_REQUIRED
    assert evaluation is None


def test_t6_evaluator_consumes_only_exactly_bound_clearance_approval() -> None:
    rule = _production_rule()
    bundle = _bundle(rule)
    approval = _valid_exception(bundle, rule)
    common = {
        "bundle": bundle,
        "evaluation_id": "eval-clearance-exception",
        "rule_type": RuleType.HORIZONTAL_CLEARANCE,
        "facts": {"design_system": "wastewater", "obstacle_category": "building"},
        "subject_type": "segment",
        "subject_id": "pipe-001",
        "measured_value": 2.0,
        "project_id": "project-001",
        "evaluated_at": approval.approved_at + timedelta(days=1),
    }
    _, without_approval = evaluate_municipal_rule(**common)
    assert without_approval is not None
    assert without_approval.status is RuleDecisionStatus.FAIL
    assert without_approval.limit_value == pytest.approx(2.5)

    _, approved = evaluate_municipal_rule(**common, exception_approval=approval)
    assert approved is not None
    assert approved.status is RuleDecisionStatus.PASS
    assert approved.limit_value == pytest.approx(2.0)
    assert approved.exception_approval_id == approval.exception_id
    assert approved.exception_approval_sha256 == approval.canonical_sha256

    stale_original = build_clearance_exception_approval(
        **{
            **approval.model_dump(mode="python", exclude={"canonical_sha256"}),
            "original_clearance_m": 2.6,
        }
    )
    with pytest.raises(ValueError, match="original_clearance_m"):
        evaluate_municipal_rule(**common, exception_approval=stale_original)
