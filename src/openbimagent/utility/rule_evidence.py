"""M1.5 T6 通用市政规则证据、评估与减距例外审批协议。

本模块与旧 ``MunicipalRuleSet v1.1`` 并行：旧工件保持可读，新协议负责多标准
规则身份、规范副本证据、production verification、canonical hash、评估绑定和例外
审批。任何 secondary/legacy 证据、摘要漂移、过期或越权审批均失败关闭。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError

from openbimagent.utility.contracts import EvidenceSubjectType, StrictFrozenModel

RULE_EVIDENCE_PROTOCOL_VERSION = "1.0"
CLEARANCE_EXCEPTION_PROTOCOL_VERSION = "1.0"
DEFAULT_MUNICIPAL_RULE_EVIDENCE_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "domain_packs"
    / "municipal_utility"
    / "knowledge"
    / "constraints.yaml"
)
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_URL_PATTERN = r"^https?://[^\s]+$"


class RuleEvidenceSourceTier(StrEnum):
    OFFICIAL = "official"
    SECONDARY = "secondary"
    LEGACY = "legacy"


class StandardEvidenceStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class ProductionVerificationStatus(StrEnum):
    ELIGIBLE = "eligible"
    REVIEW_REQUIRED = "review_required"


class RuleConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RuleEnforcement(StrEnum):
    PRODUCTION = "production"
    REVIEW_REQUIRED = "review_required"


class RuleType(StrEnum):
    HORIZONTAL_CLEARANCE = "horizontal_clearance"
    VERTICAL_CLEARANCE = "vertical_clearance"
    ROAD_CROSSING = "road_crossing"
    RAIL_CROSSING = "rail_crossing"
    RIVER_CROSSING = "river_crossing"
    STRUCTURE_CLEARANCE = "structure_clearance"
    HYDRAULICS = "hydraulics"


class RuleValueOperator(StrEnum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    EXACT = "exact"
    REQUIRED = "required"
    PROHIBITED = "prohibited"


class ApplicabilityOperator(StrEnum):
    EQ = "eq"
    IN = "in"
    LE = "le"
    LT = "lt"
    GE = "ge"
    GT = "gt"


class ExceptionApprovalStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class RuleDecisionStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    REVIEW_REQUIRED = "review_required"


class EvidenceRuleSelectionStatus(StrEnum):
    SELECTED = "selected"
    REVIEW_REQUIRED = "review_required"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


class RuleApplicability(StrictFrozenModel):
    field: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    operator: ApplicabilityOperator
    value: str | float | bool | tuple[str | float | bool, ...]

    @model_validator(mode="after")
    def _validate_value_shape(self) -> "RuleApplicability":
        if self.operator is ApplicabilityOperator.IN:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError("applicability operator=in 时 value 必须是非空数组")
        elif isinstance(self.value, tuple):
            raise ValueError("只有 applicability operator=in 可使用数组 value")
        return self


class StandardEvidence(StrictFrozenModel):
    protocol_version: str = Field(default=RULE_EVIDENCE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    status: StandardEvidenceStatus
    source_tier: RuleEvidenceSourceTier
    standard_id: str = Field(min_length=1, max_length=128)
    standard_title: str = Field(min_length=1, max_length=512)
    standard_status: Literal["current", "superseded", "unknown"]
    status_checked_at: date | None
    status_source_url: str | None = Field(default=None, max_length=2048, pattern=_URL_PATTERN)
    clause: str = Field(min_length=1, max_length=128)
    table: str | None = Field(default=None, min_length=1, max_length=128)
    content_checked_at: date | None
    official_copy_url: str | None = Field(default=None, max_length=2048, pattern=_URL_PATTERN)
    official_copy_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    secondary_source_url: str | None = Field(default=None, max_length=2048, pattern=_URL_PATTERN)
    secondary_source_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    evidence_locator: str = Field(min_length=1, max_length=2048)
    applicability_complete: bool
    production_verification: ProductionVerificationStatus
    canonical_sha256: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_evidence(self) -> "StandardEvidence":
        if (self.official_copy_url is None) is not (self.official_copy_sha256 is None):
            raise ValueError("official_copy_url 与 official_copy_sha256 必须同时存在或同时缺失")
        if (self.secondary_source_url is None) is not (self.secondary_source_sha256 is None):
            raise ValueError("secondary_source_url 与 secondary_source_sha256 必须同时存在或同时缺失")
        if self.source_tier is RuleEvidenceSourceTier.SECONDARY and self.secondary_source_url is None:
            raise ValueError("secondary 证据必须包含 secondary_source_url/SHA-256")
        if self.production_verification is ProductionVerificationStatus.ELIGIBLE:
            eligible = all(
                (
                    self.status is StandardEvidenceStatus.VERIFIED,
                    self.source_tier is RuleEvidenceSourceTier.OFFICIAL,
                    self.standard_status == "current",
                    self.status_checked_at is not None,
                    self.status_source_url is not None,
                    self.content_checked_at is not None,
                    self.official_copy_url is not None,
                    self.official_copy_sha256 is not None,
                    self.applicability_complete,
                )
            )
            if not eligible:
                raise ValueError(
                    "production_verification=eligible 只允许完整、现行、已核验的 official 证据"
                )
        _validate_canonical_hash(self, "StandardEvidence")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class VerifiedMunicipalRule(StrictFrozenModel):
    protocol_version: str = Field(default=RULE_EVIDENCE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    rule_id: str = Field(min_length=1, max_length=256)
    source_rule_id: str = Field(min_length=1, max_length=256)
    rule_type: RuleType
    parameter: str = Field(min_length=1, max_length=256, pattern=r"^[a-z][a-z0-9_]*$")
    operator: RuleValueOperator
    value: float | str | bool
    unit: str = Field(min_length=1, max_length=64)
    source_clause: str = Field(min_length=1, max_length=1024)
    applicability: tuple[RuleApplicability, ...] = Field(min_length=1)
    confidence: RuleConfidence
    verification: StandardEvidence
    enforcement: RuleEnforcement
    canonical_sha256: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_rule(self) -> "VerifiedMunicipalRule":
        fields = [item.field for item in self.applicability]
        if fields != sorted(fields) or len(fields) != len(set(fields)):
            raise ValueError("rule applicability 必须按 field 排序且 field 不能重复")
        expected = (
            RuleEnforcement.PRODUCTION
            if self.confidence is RuleConfidence.HIGH
            and self.verification.production_verification
            is ProductionVerificationStatus.ELIGIBLE
            else RuleEnforcement.REVIEW_REQUIRED
        )
        if self.enforcement is not expected:
            raise ValueError(
                "rule enforcement 与 confidence/production_verification 不一致: "
                f"expected={expected.value}"
            )
        _validate_canonical_hash(self, "VerifiedMunicipalRule")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class MunicipalRuleEvidenceBundle(StrictFrozenModel):
    protocol_version: str = Field(default=RULE_EVIDENCE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    bundle_id: str = Field(min_length=1, max_length=256)
    source_path: str = Field(min_length=1, max_length=1024)
    source_sha256: str = Field(pattern=_HASH_PATTERN)
    compiler_name: str = Field(min_length=1, max_length=256)
    compiler_version: str = Field(min_length=1, max_length=128)
    rules: tuple[VerifiedMunicipalRule, ...] = Field(min_length=1)
    canonical_sha256: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_bundle(self) -> "MunicipalRuleEvidenceBundle":
        rule_ids = [item.rule_id for item in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("MunicipalRuleEvidenceBundle rule_id 不能重复或冲突")
        if rule_ids != sorted(rule_ids):
            raise ValueError("MunicipalRuleEvidenceBundle rules 必须按 rule_id 排序")
        _validate_canonical_hash(self, "MunicipalRuleEvidenceBundle")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def rule(self, rule_id: str) -> VerifiedMunicipalRule:
        matches = [item for item in self.rules if item.rule_id == rule_id]
        if len(matches) != 1:
            raise ValueError(f"规则包无法唯一定位 rule_id={rule_id!r}")
        return matches[0]


class ExceptionScope(StrictFrozenModel):
    project_id: str = Field(min_length=1, max_length=256)
    subject_ids: tuple[str, ...] = Field(min_length=1)
    rule_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_scope(self) -> "ExceptionScope":
        for label, values in (("subject_ids", self.subject_ids), ("rule_ids", self.rule_ids)):
            if tuple(sorted(values)) != values or len(values) != len(set(values)):
                raise ValueError(f"ExceptionScope {label} 必须排序且不得重复")
        return self


class ClearanceExceptionApproval(StrictFrozenModel):
    protocol_version: str = Field(default=CLEARANCE_EXCEPTION_PROTOCOL_VERSION, pattern=r"^1\.0$")
    exception_id: str = Field(min_length=1, max_length=256)
    rule_set_sha256: str = Field(pattern=_HASH_PATTERN)
    rule_sha256: str = Field(pattern=_HASH_PATTERN)
    original_rule_id: str = Field(min_length=1, max_length=256)
    original_clearance_m: float = Field(gt=0)
    approved_clearance_m: float = Field(gt=0)
    safety_measures: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4096)
    risks: tuple[str, ...] = Field(min_length=1)
    approver_id: str = Field(min_length=1, max_length=256)
    approver_role: str = Field(min_length=1, max_length=256)
    approver_authorities: tuple[str, ...] = Field(min_length=1)
    valid_scope: ExceptionScope
    approved_at: datetime
    expires_at: datetime
    approval_status: ExceptionApprovalStatus
    audit_references: tuple[str, ...] = Field(min_length=1)
    canonical_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("审批时间必须包含时区")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_approval(self) -> "ClearanceExceptionApproval":
        if self.approved_clearance_m >= self.original_clearance_m:
            raise ValueError("减距审批的 approved_clearance_m 必须小于 original_clearance_m")
        if self.expires_at <= self.approved_at:
            raise ValueError("减距审批 expires_at 必须晚于 approved_at")
        if self.original_rule_id not in self.valid_scope.rule_ids:
            raise ValueError("减距审批 original_rule_id 必须包含在 valid_scope.rule_ids")
        for label, values in (
            ("safety_measures", self.safety_measures),
            ("risks", self.risks),
            ("approver_authorities", self.approver_authorities),
            ("audit_references", self.audit_references),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{label} 不得包含空值")
        _validate_canonical_hash(self, "ClearanceExceptionApproval")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class EvidenceRuleSelectionResult(StrictFrozenModel):
    status: EvidenceRuleSelectionStatus
    rule: VerifiedMunicipalRule | None = None
    candidate_rule_ids: tuple[str, ...] = ()
    missing_attributes: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _validate_selection(self) -> "EvidenceRuleSelectionResult":
        if self.status is EvidenceRuleSelectionStatus.SELECTED and self.rule is None:
            raise ValueError("selected 规则选择必须包含唯一 rule")
        if self.status is not EvidenceRuleSelectionStatus.SELECTED and self.rule is not None:
            raise ValueError(f"{self.status.value} 规则选择不得携带已选 rule")
        if self.status is EvidenceRuleSelectionStatus.AMBIGUOUS and len(self.candidate_rule_ids) < 2:
            raise ValueError("ambiguous 规则选择必须包含至少两个候选 rule")
        return self


class RuleEvaluation(StrictFrozenModel):
    protocol_version: str = Field(default=RULE_EVIDENCE_PROTOCOL_VERSION, pattern=r"^1\.0$")
    evaluation_id: str = Field(min_length=1, max_length=256)
    rule_set_sha256: str = Field(pattern=_HASH_PATTERN)
    rule_sha256: str = Field(pattern=_HASH_PATTERN)
    verification_sha256: str = Field(pattern=_HASH_PATTERN)
    production_verification: ProductionVerificationStatus
    rule_id: str = Field(min_length=1, max_length=256)
    subject_type: EvidenceSubjectType
    subject_id: str = Field(min_length=1, max_length=256)
    measured_value: float | str | bool | None
    limit_value: float | str | bool | None
    unit: str | None = Field(default=None, max_length=64)
    status: RuleDecisionStatus
    review_reason: str | None = Field(default=None, min_length=1, max_length=4096)
    exception_approval_id: str | None = Field(default=None, min_length=1, max_length=256)
    exception_approval_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    canonical_sha256: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_evaluation(self) -> "RuleEvaluation":
        if (self.exception_approval_id is None) is not (self.exception_approval_sha256 is None):
            raise ValueError("exception approval ID 与 SHA-256 必须同时存在或同时缺失")
        if self.status is RuleDecisionStatus.REVIEW_REQUIRED and self.review_reason is None:
            raise ValueError("review_required 评估必须包含 review_reason")
        if self.status is not RuleDecisionStatus.REVIEW_REQUIRED and self.review_reason is not None:
            raise ValueError("仅 review_required 评估可包含 review_reason")
        if (
            self.status in {RuleDecisionStatus.PASS, RuleDecisionStatus.FAIL}
            and self.production_verification is not ProductionVerificationStatus.ELIGIBLE
        ):
            raise ValueError("PASS/FAIL 只允许绑定 eligible production verification")
        if (
            self.production_verification is ProductionVerificationStatus.REVIEW_REQUIRED
            and self.status not in {RuleDecisionStatus.UNKNOWN, RuleDecisionStatus.REVIEW_REQUIRED}
        ):
            raise ValueError("review_required 规则只能产生 UNKNOWN/REVIEW_REQUIRED")
        _validate_canonical_hash(self, "RuleEvaluation")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


def compile_municipal_rule_evidence_bundle(
    source_path: Path = DEFAULT_MUNICIPAL_RULE_EVIDENCE_SOURCE,
    *,
    logical_source_path: str = "knowledge/constraints.yaml",
    schema_gate: SchemaGate | None = None,
) -> MunicipalRuleEvidenceBundle:
    """确定性编译 T6 范围规则；不编译未建模或只有旧知识文本的条目。"""
    path = Path(source_path)
    try:
        source_bytes = path.read_bytes()
        payload = yaml.safe_load(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"T6 规则知识源读取失败: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("constraints"), list):
        raise ValueError("T6 规则知识源必须包含 constraints 数组")
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in payload["constraints"]:
        if not isinstance(item, dict) or not item.get("rule_id"):
            raise ValueError("T6 constraints 条目必须是包含 rule_id 的 mapping")
        rule_id = str(item["rule_id"])
        if rule_id in indexed:
            raise ValueError(f"T6 constraints rule_id 重复: {rule_id}")
        indexed[rule_id] = item

    rules: list[VerifiedMunicipalRule] = []
    rules.append(
        _compile_evidence_rule(
            _require_raw_rule(indexed, "MU-DRAIN-007"),
            rule_id="MU-DRAIN-007",
            rule_type=RuleType.HYDRAULICS,
            applicability={"system_type": "wastewater", "flow_regime": "gravity"},
        )
    )
    road = _require_raw_rule(indexed, "MU-ROAD-001")
    rules.append(
        _compile_evidence_rule(
            road,
            rule_id="MU-ROAD-001",
            rule_type=RuleType.ROAD_CROSSING,
            applicability={"crossing_target": ("road", "railway"), "crossing_mode": "limited"},
        )
    )
    rules.append(
        _compile_evidence_rule(
            road,
            rule_id="MU-ROAD-001:perpendicular",
            rule_type=RuleType.ROAD_CROSSING,
            applicability={"crossing_target": ("road", "railway"), "crossing_mode": "normal"},
            value=90.0,
            operator=RuleValueOperator.EXACT,
        )
    )
    rules.extend(
        _compile_variant_rules(
            _require_raw_rule(indexed, "MU-RAIL-001"),
            rule_type=RuleType.RAIL_CROSSING,
            applicability_field="rail_class",
        )
    )
    rules.extend(
        _compile_variant_rules(
            _require_raw_rule(indexed, "MU-RIVER-001"),
            rule_type=RuleType.RIVER_CROSSING,
            applicability_field="waterway_class",
        )
    )
    rules.extend(
        _compile_variant_rules(
            _require_raw_rule(indexed, "MU-VERTICAL-001"),
            rule_type=RuleType.VERTICAL_CLEARANCE,
            applicability_field="crossed_asset_type",
        )
    )

    horizontal = (
        "MU-CLEAR-001",
        "MU-CLEAR-005",
        "MU-CLEAR-006",
        "MU-CLEAR-007",
        "MU-CLEAR-008",
    )
    for source_rule_id in horizontal:
        raw = _require_raw_rule(indexed, source_rule_id)
        rule_type = (
            RuleType.STRUCTURE_CLEARANCE
            if source_rule_id == "MU-CLEAR-001"
            else RuleType.HORIZONTAL_CLEARANCE
        )
        if "value" in raw:
            rules.append(
                _compile_evidence_rule(
                    raw,
                    rule_id=f"{source_rule_id}:building",
                    rule_type=rule_type,
                    applicability={"obstacle_category": "building"},
                )
            )
            continue
        category = {
            "MU-CLEAR-005": "water",
            "MU-CLEAR-006": "gas",
            "MU-CLEAR-007": "telecom",
            "MU-CLEAR-008": "power",
        }[source_rule_id]
        variant_field = {
            "MU-CLEAR-005": "outer_diameter_class",
            "MU-CLEAR-006": "pressure_class",
            "MU-CLEAR-007": "burial_method",
            "MU-CLEAR-008": "burial_method",
        }[source_rule_id]
        rules.extend(
            _compile_variant_rules(
                raw,
                rule_type=rule_type,
                applicability_field=variant_field,
                common_applicability={"obstacle_category": category},
            )
        )

    bundle = build_municipal_rule_evidence_bundle(
        bundle_id="municipal-utility-t6-rule-evidence",
        source_path=logical_source_path,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        compiler_name="municipal-rule-evidence-compiler",
        compiler_version="1.0.0",
        rules=rules,
    )
    try:
        (schema_gate or SchemaGate()).gate_or_fix(
            "municipal_rule_evidence_bundle", bundle.model_dump(mode="json")
        )
    except SchemaGateError as exc:
        raise ValueError(f"T6 MunicipalRuleEvidenceBundle 未通过 Schema Gate: {exc}") from exc
    return bundle


def build_standard_evidence(**data: Any) -> StandardEvidence:
    return StandardEvidence.model_validate(_with_canonical_hash(data))


def build_verified_municipal_rule(**data: Any) -> VerifiedMunicipalRule:
    payload = dict(data)
    applicability = tuple(
        item if isinstance(item, RuleApplicability) else RuleApplicability.model_validate(item)
        for item in payload["applicability"]
    )
    payload["applicability"] = tuple(sorted(applicability, key=lambda item: item.field))
    return VerifiedMunicipalRule.model_validate(_with_canonical_hash(payload))


def build_municipal_rule_evidence_bundle(
    *,
    bundle_id: str,
    source_path: str,
    source_sha256: str,
    compiler_name: str,
    compiler_version: str,
    rules: Iterable[VerifiedMunicipalRule],
) -> MunicipalRuleEvidenceBundle:
    payload = {
        "bundle_id": bundle_id,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "compiler_name": compiler_name,
        "compiler_version": compiler_version,
        "rules": tuple(sorted(rules, key=lambda item: item.rule_id)),
    }
    return MunicipalRuleEvidenceBundle.model_validate(_with_canonical_hash(payload))


def build_clearance_exception_approval(**data: Any) -> ClearanceExceptionApproval:
    payload = dict(data)
    for field in (
        "safety_measures",
        "risks",
        "approver_authorities",
        "audit_references",
    ):
        payload[field] = tuple(sorted(payload[field]))
    return ClearanceExceptionApproval.model_validate(_with_canonical_hash(payload))


def build_rule_evaluation(**data: Any) -> RuleEvaluation:
    return RuleEvaluation.model_validate(_with_canonical_hash(data))


def select_municipal_rule(
    bundle: MunicipalRuleEvidenceBundle,
    *,
    rule_type: RuleType | str,
    facts: Mapping[str, Any],
) -> EvidenceRuleSelectionResult:
    """按显式工程事实唯一选择 T6 规则；缺属性、歧义和越域均失败关闭。"""
    selected_type = RuleType(rule_type)
    candidates = tuple(rule for rule in bundle.rules if rule.rule_type is selected_type)
    if not candidates:
        return EvidenceRuleSelectionResult(
            status=EvidenceRuleSelectionStatus.UNSUPPORTED,
            detail=f"规则包不支持 rule_type={selected_type.value!r}",
        )

    complete: list[VerifiedMunicipalRule] = []
    missing: set[str] = set()
    for rule in candidates:
        absent = {
            condition.field
            for condition in rule.applicability
            if condition.field not in facts or facts[condition.field] is None
        }
        if absent:
            missing.update(absent)
            continue
        if all(_applicability_matches(condition, facts[condition.field]) for condition in rule.applicability):
            complete.append(rule)

    if len(complete) > 1:
        ids = tuple(rule.rule_id for rule in complete)
        return EvidenceRuleSelectionResult(
            status=EvidenceRuleSelectionStatus.AMBIGUOUS,
            candidate_rule_ids=ids,
            detail=f"多个 T6 规则同时适用，拒绝任意选择: {list(ids)}",
        )
    if len(complete) == 1:
        rule = complete[0]
        return EvidenceRuleSelectionResult(
            status=EvidenceRuleSelectionStatus.SELECTED,
            rule=rule,
            candidate_rule_ids=(rule.rule_id,),
            detail=(
                f"唯一选择规则 {rule.rule_id}; enforcement={rule.enforcement.value}; "
                f"production_verification={rule.verification.production_verification.value}"
            ),
        )
    if missing:
        missing_fields = tuple(sorted(missing))
        return EvidenceRuleSelectionResult(
            status=EvidenceRuleSelectionStatus.REVIEW_REQUIRED,
            candidate_rule_ids=tuple(rule.rule_id for rule in candidates),
            missing_attributes=missing_fields,
            detail=f"规则选择缺少工程属性: {list(missing_fields)}",
        )
    return EvidenceRuleSelectionResult(
        status=EvidenceRuleSelectionStatus.UNSUPPORTED,
        candidate_rule_ids=tuple(rule.rule_id for rule in candidates),
        detail=f"工程事实不在 {selected_type.value!r} 规则适用范围",
    )


def evaluate_municipal_rule(
    bundle: MunicipalRuleEvidenceBundle,
    *,
    evaluation_id: str,
    rule_type: RuleType | str,
    facts: Mapping[str, Any],
    subject_type: EvidenceSubjectType | str,
    subject_id: str,
    measured_value: float | str | bool | None,
    project_id: str | None = None,
    evaluated_at: datetime | None = None,
    exception_approval: ClearanceExceptionApproval | None = None,
) -> tuple[EvidenceRuleSelectionResult, RuleEvaluation | None]:
    """选择并评估唯一 T6 规则；不能唯一选规时不伪造 RuleEvaluation 身份。"""
    selection = select_municipal_rule(bundle, rule_type=rule_type, facts=facts)
    if selection.status is not EvidenceRuleSelectionStatus.SELECTED or selection.rule is None:
        return selection, None
    rule = selection.rule
    if rule.enforcement is RuleEnforcement.REVIEW_REQUIRED:
        evaluation = build_rule_evaluation(
            evaluation_id=evaluation_id,
            rule_set_sha256=bundle.canonical_sha256,
            rule_sha256=rule.canonical_sha256,
            verification_sha256=rule.verification.canonical_sha256,
            production_verification=rule.verification.production_verification,
            rule_id=rule.rule_id,
            subject_type=subject_type,
            subject_id=subject_id,
            measured_value=measured_value,
            limit_value=rule.value,
            unit=rule.unit,
            status=RuleDecisionStatus.REVIEW_REQUIRED,
            review_reason="唯一适用规则尚未获得 production verification",
            exception_approval_id=None,
            exception_approval_sha256=None,
        )
        return selection, evaluation

    status = _evaluate_rule_value(rule, measured_value)
    applied_approval: ClearanceExceptionApproval | None = None
    if (
        status is RuleDecisionStatus.FAIL
        and exception_approval is not None
        and rule.rule_type
        in {
            RuleType.HORIZONTAL_CLEARANCE,
            RuleType.VERTICAL_CLEARANCE,
            RuleType.STRUCTURE_CLEARANCE,
        }
    ):
        if not isinstance(measured_value, int | float) or isinstance(measured_value, bool):
            raise ValueError("净距例外审批只允许有限数值 measured_value")
        if project_id is None or evaluated_at is None:
            raise ValueError("消费净距例外审批必须提供 project_id 和 evaluated_at")
        authorize_clearance_reduction(
            approval=exception_approval,
            rule_set_sha256=bundle.canonical_sha256,
            rule_sha256=rule.canonical_sha256,
            rule_id=rule.rule_id,
            project_id=project_id,
            subject_id=subject_id,
            requested_clearance_m=float(measured_value),
            evaluated_at=evaluated_at,
            expected_original_clearance_m=float(rule.value),
        )
        status = RuleDecisionStatus.PASS
        applied_approval = exception_approval

    evaluation = build_rule_evaluation(
        evaluation_id=evaluation_id,
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        verification_sha256=rule.verification.canonical_sha256,
        production_verification=rule.verification.production_verification,
        rule_id=rule.rule_id,
        subject_type=subject_type,
        subject_id=subject_id,
        measured_value=measured_value,
        limit_value=(
            applied_approval.approved_clearance_m if applied_approval is not None else rule.value
        ),
        unit=rule.unit,
        status=status,
        review_reason=None,
        exception_approval_id=(applied_approval.exception_id if applied_approval is not None else None),
        exception_approval_sha256=(
            applied_approval.canonical_sha256 if applied_approval is not None else None
        ),
    )
    return selection, evaluation


def authorize_clearance_reduction(
    *,
    approval: ClearanceExceptionApproval,
    rule_set_sha256: str,
    rule_sha256: str,
    rule_id: str,
    project_id: str,
    subject_id: str,
    requested_clearance_m: float,
    evaluated_at: datetime,
    expected_original_clearance_m: float | None = None,
) -> None:
    """验证某次减距使用是否被完整、有效且有权限的审批精确授权。"""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("减距评估时间必须包含时区")
    current = evaluated_at.astimezone(UTC)
    if approval.approval_status is not ExceptionApprovalStatus.APPROVED:
        raise ValueError("减距审批状态不是 approved")
    if current < approval.approved_at:
        raise ValueError("减距审批尚未生效")
    if current >= approval.expires_at:
        raise ValueError("减距审批已过期")
    if "approve_clearance_reduction" not in approval.approver_authorities:
        raise ValueError("审批人无减距审批权限")
    if approval.rule_set_sha256 != rule_set_sha256:
        raise ValueError("减距审批 rule_set_sha256 漂移")
    if approval.rule_sha256 != rule_sha256:
        raise ValueError("减距审批 rule_sha256 漂移")
    if approval.original_rule_id != rule_id or rule_id not in approval.valid_scope.rule_ids:
        raise ValueError("减距审批 rule scope 不匹配")
    if (
        expected_original_clearance_m is not None
        and not math.isclose(
            approval.original_clearance_m,
            expected_original_clearance_m,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise ValueError("减距审批 original_clearance_m 与当前规则值不一致")
    if approval.valid_scope.project_id != project_id:
        raise ValueError("减距审批 project scope 不匹配")
    if subject_id not in approval.valid_scope.subject_ids:
        raise ValueError("减距审批 subject scope 不匹配")
    if requested_clearance_m < approval.approved_clearance_m:
        raise ValueError("请求净距低于批准净距")
    if requested_clearance_m >= approval.original_clearance_m:
        raise ValueError("请求值未发生减距，不应消费例外审批")


def _applicability_matches(condition: RuleApplicability, actual: Any) -> bool:
    expected = condition.value
    if condition.operator is ApplicabilityOperator.EQ:
        return actual == expected
    if condition.operator is ApplicabilityOperator.IN:
        return actual in expected
    if isinstance(actual, bool) or not isinstance(actual, int | float):
        return False
    if isinstance(expected, bool) or not isinstance(expected, int | float):
        return False
    actual_float = float(actual)
    expected_float = float(expected)
    if not math.isfinite(actual_float) or not math.isfinite(expected_float):
        return False
    return {
        ApplicabilityOperator.LE: actual_float <= expected_float,
        ApplicabilityOperator.LT: actual_float < expected_float,
        ApplicabilityOperator.GE: actual_float >= expected_float,
        ApplicabilityOperator.GT: actual_float > expected_float,
    }.get(condition.operator, False)


def _evaluate_rule_value(
    rule: VerifiedMunicipalRule,
    measured_value: float | str | bool | None,
) -> RuleDecisionStatus:
    if measured_value is None:
        return RuleDecisionStatus.UNKNOWN
    if rule.operator in {RuleValueOperator.MINIMUM, RuleValueOperator.MAXIMUM}:
        if isinstance(measured_value, bool) or not isinstance(measured_value, int | float):
            return RuleDecisionStatus.UNKNOWN
        if isinstance(rule.value, bool) or not isinstance(rule.value, int | float):
            return RuleDecisionStatus.UNKNOWN
        measured = float(measured_value)
        limit = float(rule.value)
        if not math.isfinite(measured) or not math.isfinite(limit):
            return RuleDecisionStatus.UNKNOWN
        if rule.operator is RuleValueOperator.MINIMUM:
            return RuleDecisionStatus.PASS if measured >= limit else RuleDecisionStatus.FAIL
        return RuleDecisionStatus.PASS if measured <= limit else RuleDecisionStatus.FAIL
    if rule.operator is RuleValueOperator.EXACT:
        return RuleDecisionStatus.PASS if measured_value == rule.value else RuleDecisionStatus.FAIL
    if rule.operator is RuleValueOperator.REQUIRED:
        return RuleDecisionStatus.PASS if measured_value == rule.value else RuleDecisionStatus.FAIL
    if rule.operator is RuleValueOperator.PROHIBITED:
        return RuleDecisionStatus.FAIL if measured_value == rule.value else RuleDecisionStatus.PASS
    return RuleDecisionStatus.UNKNOWN


def _normalize_verification_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把已结构化的 MunicipalRuleSet v1.1 official_copy 证据迁移到 T6 协议。"""
    data = dict(raw)
    if data.get("source_tier") != "official_copy":
        return data
    official_complete = all(
        (
            data.get("status") == "verified",
            data.get("standard_status") == "current",
            data.get("status_checked_at") is not None,
            data.get("status_source_url") is not None,
            data.get("content_checked_at") is not None,
            data.get("content_source_url") is not None,
            data.get("content_sha256") is not None,
            data.get("evidence_locator") is not None,
            data.get("applicability_complete") is True,
        )
    )
    return {
        "status": data.get("status"),
        "source_tier": "official",
        "standard_id": data.get("standard_id"),
        "standard_title": data.get("standard_title"),
        "standard_status": data.get("standard_status"),
        "status_checked_at": data.get("status_checked_at"),
        "status_source_url": data.get("status_source_url"),
        "clause": data.get("clause"),
        "table": data.get("table"),
        "content_checked_at": data.get("content_checked_at"),
        "official_copy_url": data.get("content_source_url"),
        "official_copy_sha256": data.get("content_sha256"),
        "secondary_source_url": None,
        "secondary_source_sha256": None,
        "evidence_locator": data.get("evidence_locator"),
        "applicability_complete": bool(data.get("applicability_complete")),
        "production_verification": "eligible" if official_complete else "review_required",
    }


def _require_raw_rule(
    indexed: Mapping[str, Mapping[str, Any]], rule_id: str
) -> Mapping[str, Any]:
    try:
        return indexed[rule_id]
    except KeyError as exc:
        raise ValueError(f"T6 规则知识源缺少必需规则 {rule_id}") from exc


def _compile_variant_rules(
    raw: Mapping[str, Any],
    *,
    rule_type: RuleType,
    applicability_field: str,
    common_applicability: Mapping[str, str | float | bool] | None = None,
) -> list[VerifiedMunicipalRule]:
    values = raw.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError(f"T6 规则 {raw.get('rule_id')} values 必须是非空 mapping")
    result = []
    for variant, value in sorted(values.items()):
        applicability = dict(common_applicability or {})
        applicability[applicability_field] = str(variant)
        result.append(
            _compile_evidence_rule(
                raw,
                rule_id=f"{raw['rule_id']}:{variant}",
                rule_type=rule_type,
                applicability=applicability,
                value=value,
            )
        )
    return result


def _compile_evidence_rule(
    raw: Mapping[str, Any],
    *,
    rule_id: str,
    rule_type: RuleType,
    applicability: Mapping[str, str | float | bool | tuple[str | float | bool, ...]],
    value: Any | None = None,
    operator: RuleValueOperator = RuleValueOperator.MINIMUM,
) -> VerifiedMunicipalRule:
    verification_raw = raw.get("verification")
    if not isinstance(verification_raw, dict):
        raise ValueError(
            f"T6 规则 {raw.get('rule_id')} 缺少结构化 verification；旧 verified_by 不得自动晋级"
        )
    verification_data = _normalize_verification_payload(verification_raw)
    try:
        verification = build_standard_evidence(**verification_data)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"T6 规则 {raw.get('rule_id')} verification 非法: {exc}") from exc
    actual_value = raw.get("value") if value is None else value
    if isinstance(actual_value, bool) or not isinstance(actual_value, int | float | str):
        raise ValueError(f"T6 规则 {rule_id} value 必须是标量")
    if isinstance(actual_value, int | float):
        actual_value = float(actual_value)
    confidence = str(raw.get("confidence") or "")
    enforcement = (
        RuleEnforcement.PRODUCTION
        if confidence == RuleConfidence.HIGH.value
        and verification.production_verification is ProductionVerificationStatus.ELIGIBLE
        else RuleEnforcement.REVIEW_REQUIRED
    )
    applicability_models = tuple(
        RuleApplicability(
            field=field,
            operator=("in" if isinstance(item, tuple) else "eq"),
            value=item,
        )
        for field, item in sorted(applicability.items())
    )
    return build_verified_municipal_rule(
        rule_id=rule_id,
        source_rule_id=str(raw["rule_id"]),
        rule_type=rule_type,
        parameter=str(raw.get("parameter") or ""),
        operator=operator,
        value=actual_value,
        unit=str(raw.get("unit") or ""),
        source_clause=str(raw.get("source_clause") or ""),
        applicability=applicability_models,
        confidence=confidence,
        verification=verification,
        enforcement=enforcement,
    )


def _with_canonical_hash(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("protocol_version", RULE_EVIDENCE_PROTOCOL_VERSION)
    payload.pop("canonical_sha256", None)
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_canonical_hash(model: StrictFrozenModel, label: str) -> None:
    payload = model.model_dump(mode="json", exclude={"canonical_sha256"})
    expected = _canonical_sha256(payload)
    actual = str(getattr(model, "canonical_sha256"))
    if actual != expected:
        raise ValueError(
            f"{label} canonical_sha256 不匹配: supplied={actual}, expected={expected}"
        )


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        _json_compatible(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, StrictFrozenModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_compatible(item) for item in value]
    return value


__all__ = [
    "ApplicabilityOperator",
    "CLEARANCE_EXCEPTION_PROTOCOL_VERSION",
    "ClearanceExceptionApproval",
    "DEFAULT_MUNICIPAL_RULE_EVIDENCE_SOURCE",
    "ExceptionApprovalStatus",
    "ExceptionScope",
    "MunicipalRuleEvidenceBundle",
    "ProductionVerificationStatus",
    "RULE_EVIDENCE_PROTOCOL_VERSION",
    "RuleApplicability",
    "RuleConfidence",
    "RuleDecisionStatus",
    "RuleEnforcement",
    "EvidenceRuleSelectionResult",
    "EvidenceRuleSelectionStatus",
    "RuleEvaluation",
    "RuleEvidenceSourceTier",
    "RuleType",
    "RuleValueOperator",
    "StandardEvidence",
    "StandardEvidenceStatus",
    "VerifiedMunicipalRule",
    "authorize_clearance_reduction",
    "build_clearance_exception_approval",
    "compile_municipal_rule_evidence_bundle",
    "build_municipal_rule_evidence_bundle",
    "build_rule_evaluation",
    "build_standard_evidence",
    "build_verified_municipal_rule",
    "evaluate_municipal_rule",
    "select_municipal_rule",
]
