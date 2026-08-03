"""T6 宿主和 IFC 共享的规则证据投影身份。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.utility.rule_evidence import RuleEvaluation

_HASH_PATTERN = r"^[0-9a-f]{64}$"


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


__all__ = ["RuleProjectionIdentity"]
