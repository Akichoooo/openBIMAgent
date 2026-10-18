"""T6 宿主和 IFC 共享的规则证据投影身份。

``RuleProjectionIdentity`` 继续表示“单条 RuleEvaluation”的宿主投影；
``RuleDeliveryEvidence`` 是模型级（whole-model）聚合的显式版本化扩展，
用于 IFC 交付证据。两者身份与字段名严格区分，不得互相冒充。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.utility.rule_evidence import RuleEvaluation

_HASH_PATTERN = r"^[0-9a-f]{64}$"
RULE_DELIVERY_EVIDENCE_VERSION = "1.0"
RULE_DELIVERY_IDENTITY_KIND = "model_rule_evidence_aggregate"


class RuleProjectionIdentity(BaseModel):
    """只投影可验证身份和决策，不向宿主复制规范正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_evidence_bundle_sha256: str = Field(pattern=_HASH_PATTERN)
    rule_evaluation_sha256: str = Field(pattern=_HASH_PATTERN)
    rule_decision_status: Literal["pass", "fail", "unknown", "review_required"]
    production_verification: Literal["eligible", "review_required"]
    exception_approval_id: str | None = Field(default=None, min_length=1, max_length=256)
    exception_approval_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_identity(self) -> "RuleProjectionIdentity":
        if (self.exception_approval_id is None) is not (
            self.exception_approval_sha256 is None
        ):
            raise ValueError("exception approval ID 与 SHA-256 必须同时存在或同时缺失")
        if (
            self.production_verification == "review_required"
            and self.rule_decision_status in {"pass", "fail"}
        ):
            raise ValueError("PASS/FAIL 只允许绑定 eligible production verification")
        return self

    @classmethod
    def from_rule_evaluation(cls, evaluation: RuleEvaluation) -> "RuleProjectionIdentity":
        return cls(
            rule_evidence_bundle_sha256=evaluation.rule_set_sha256,
            rule_evaluation_sha256=evaluation.canonical_sha256,
            rule_decision_status=evaluation.status.value,
            production_verification=evaluation.production_verification.value,
            exception_approval_id=evaluation.exception_approval_id,
            exception_approval_sha256=evaluation.exception_approval_sha256,
        )

    def domain_properties(self) -> dict[str, str]:
        values = {
            "rule_evidence_bundle_sha256": self.rule_evidence_bundle_sha256,
            "rule_evaluation_sha256": self.rule_evaluation_sha256,
            "rule_decision_status": self.rule_decision_status,
            "production_verification": self.production_verification,
        }
        if self.exception_approval_id is not None:
            values["exception_approval_id"] = self.exception_approval_id
            values["exception_approval_sha256"] = str(self.exception_approval_sha256)
        return values


class RuleDeliveryEvidence(BaseModel):
    """模型级规则证据聚合（IFC 交付专用，版本化）。

    - 不挑选单条评估代表整个模型；
    - 只聚合实际验证结果（确认后的 validation issues 或 IR 内嵌规则证据）；
    - ``rule_context_sha256`` 是冻结规则上下文身份，绝不冒充证据包哈希；
    - PASS/FAIL 只在 production_verification=eligible 时允许，unknown/
      review_required 不会被升级。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=RULE_DELIVERY_EVIDENCE_VERSION, pattern=r"^1\.0$")
    identity_kind: Literal["model_rule_evidence_aggregate"] = RULE_DELIVERY_IDENTITY_KIND
    evidence_source: Literal["confirmed_validation_result", "compiled_ir_evidence_only"]
    rule_context_sha256: str = Field(pattern=_HASH_PATTERN)
    source_ir_sha256: str = Field(pattern=_HASH_PATTERN)
    evidence_aggregate_sha256: str = Field(pattern=_HASH_PATTERN)
    decision_status: Literal["pass", "fail", "unknown", "review_required"]
    production_verification: Literal["eligible", "review_required"]
    checker_version: str = Field(min_length=1, max_length=128)
    checked_rule_count: int = Field(ge=0)
    candidate_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    validation_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _validate_delivery_evidence(self) -> "RuleDeliveryEvidence":
        if (
            self.production_verification == "review_required"
            and self.decision_status in {"pass", "fail"}
        ):
            raise ValueError("review_required production 不得声明 PASS/FAIL")
        if self.evidence_source == "confirmed_validation_result" and (
            self.candidate_sha256 is None or self.validation_sha256 is None
        ):
            raise ValueError("confirmed_validation_result 必须绑定 candidate/validation 哈希")
        if self.evidence_source == "compiled_ir_evidence_only" and (
            self.candidate_sha256 is not None or self.validation_sha256 is not None
        ):
            raise ValueError("compiled_ir_evidence_only 不得伪称已确认的 candidate/validation")
        return self

    @classmethod
    def from_validation(
        cls,
        *,
        rule_context_sha256: str,
        source_ir_sha256: str,
        issues: Any,
        checker_version: str,
        candidate_sha256: str | None,
        validation_sha256: str | None,
        rules: Any = None,
        evidence_source: Literal["confirmed_validation_result", "compiled_ir_evidence_only"] = "confirmed_validation_result",
    ) -> "RuleDeliveryEvidence":
        checks = _normalized_checks(issues)
        if not checks:
            raise ValueError("规则证据为空：不能在没有实际检查结果时生成交付规则身份")
        eligible = _rules_are_production_eligible(rules)
        decision = _aggregate_decision(checks, eligible)
        payload = {
            "protocol_version": RULE_DELIVERY_EVIDENCE_VERSION,
            "identity_kind": RULE_DELIVERY_IDENTITY_KIND,
            "evidence_source": evidence_source,
            "rule_context_sha256": rule_context_sha256,
            "source_ir_sha256": source_ir_sha256,
            "checker_version": checker_version,
            "candidate_sha256": candidate_sha256,
            "validation_sha256": validation_sha256,
            "decision_status": decision,
            "production_verification": "eligible" if eligible else "review_required",
            "checks": checks,
        }
        aggregate = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        return cls(
            evidence_source=evidence_source,
            rule_context_sha256=rule_context_sha256,
            source_ir_sha256=source_ir_sha256,
            evidence_aggregate_sha256=aggregate,
            decision_status=decision,
            production_verification="eligible" if eligible else "review_required",
            checker_version=checker_version,
            checked_rule_count=len({check["rule_id"] for check in checks}),
            candidate_sha256=candidate_sha256,
            validation_sha256=validation_sha256,
        )

    def ifc_properties(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {
            "DeliveryRuleIdentityVersion": self.protocol_version,
            "DeliveryRuleIdentityKind": self.identity_kind,
            "DeliveryEvidenceSource": self.evidence_source,
            "DeliveryRuleContextSHA256": self.rule_context_sha256,
            "DeliverySourceIRSHA256": self.source_ir_sha256,
            "DeliveryRuleEvidenceAggregateSHA256": self.evidence_aggregate_sha256,
            "DeliveryRuleDecisionStatus": self.decision_status,
            "DeliveryProductionVerification": self.production_verification,
            "DeliveryCheckerVersion": self.checker_version,
            "DeliveryCheckedRuleCount": self.checked_rule_count,
        }
        if self.candidate_sha256 is not None:
            values["DeliveryCandidateSHA256"] = self.candidate_sha256
        if self.validation_sha256 is not None:
            values["DeliveryValidationSHA256"] = self.validation_sha256
        return values

    @classmethod
    def from_ifc_properties(cls, properties: dict[str, Any]) -> "RuleDeliveryEvidence":
        def required(name: str) -> str:
            value = properties.get(name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"IFC 交付规则证据缺少 {name}")
            return value

        kind = required("DeliveryRuleIdentityKind")
        if kind != RULE_DELIVERY_IDENTITY_KIND:
            raise ValueError(
                f"IFC 交付规则身份类型不受支持: {kind!r}（仅接受 {RULE_DELIVERY_IDENTITY_KIND!r}）"
            )
        version = required("DeliveryRuleIdentityVersion")
        if version != RULE_DELIVERY_EVIDENCE_VERSION:
            raise ValueError(
                f"IFC 交付规则身份版本不受支持: {version!r}（仅接受 {RULE_DELIVERY_EVIDENCE_VERSION!r}）"
            )
        return cls(
            protocol_version=version,
            evidence_source=required("DeliveryEvidenceSource"),
            rule_context_sha256=required("DeliveryRuleContextSHA256"),
            evidence_aggregate_sha256=required("DeliveryRuleEvidenceAggregateSHA256"),
            source_ir_sha256=required("DeliverySourceIRSHA256"),
            decision_status=required("DeliveryRuleDecisionStatus"),
            production_verification=required("DeliveryProductionVerification"),
            checker_version=required("DeliveryCheckerVersion"),
            checked_rule_count=int(properties.get("DeliveryCheckedRuleCount") or 0),
            candidate_sha256=properties.get("DeliveryCandidateSHA256"),
            validation_sha256=properties.get("DeliveryValidationSHA256"),
        )


def _normalized_checks(issues: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for raw in issues or ():
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise ValueError("规则证据项必须是 dict 或 model")
        rule_id = str(raw.get("rule_id") or "")
        if not rule_id:
            continue
        status = str(raw.get("status") or "")
        if status not in {"pass", "fail", "unknown", "review_required"}:
            raise ValueError(f"规则证据状态不受支持: {status!r} ({rule_id})")
        subject_ids = raw.get("object_ids")
        if subject_ids is None:
            subject = raw.get("subject_id")
            subject_ids = [subject] if subject else []
        checks.append(
            {
                "rule_id": rule_id,
                "check_name": str(raw.get("check_name") or ""),
                "status": status,
                "subject_ids": sorted(str(item) for item in subject_ids),
                "measured": _json_value(raw.get("measured_value")),
                "limit": _json_value(raw.get("required_value", raw.get("limit_value"))),
                "unit": raw.get("unit"),
            }
        )
    checks.sort(
        key=lambda item: (
            item["rule_id"],
            item["check_name"],
            tuple(item["subject_ids"]),
            item["status"],
            json.dumps(item["measured"], sort_keys=True, default=str),
        )
    )
    return checks


def _json_value(value: Any) -> Any:
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def _aggregate_decision(checks: list[dict[str, Any]], eligible: bool) -> str:
    if not eligible:
        return "review_required"
    if any(check["status"] == "fail" for check in checks):
        return "fail"
    if any(check["status"] == "review_required" for check in checks):
        return "review_required"
    if any(check["status"] == "unknown" for check in checks):
        return "unknown"
    return "pass"


def _rules_are_production_eligible(rules: Any) -> bool:
    if rules is None:
        return False
    items = getattr(rules, "rules", None)
    if not items:
        return False
    try:
        return all(
            rule.verification.production_eligible() for rule in items
        )
    except TypeError:
        return False

__all__ = [
    "RULE_DELIVERY_EVIDENCE_VERSION",
    "RuleDeliveryEvidence",
    "RuleProjectionIdentity",
]
