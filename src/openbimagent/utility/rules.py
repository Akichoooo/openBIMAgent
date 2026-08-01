"""市政管网约束源到版本化可执行净距规则集的确定性编译器。

原始 ``constraints.yaml`` 是知识源，不直接等同于生产规则。本模块只编译当前
DN300 重力污水直管切片需要的水平净距规则，并保留源文件 SHA-256、编译器身份和
规则集 canonical SHA-256。高置信规则可进入生产判定；中低置信规则及条件不足均
失败关闭为 review_required/unsupported，不得静默放行。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import Field, ValidationError, model_validator

from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.contracts import StrictFrozenModel

MUNICIPAL_RULE_SET_VERSION = "1.0"
MUNICIPAL_RULE_SET_ID = "municipal-utility-dn300-wastewater-clearance"
MUNICIPAL_RULE_COMPILER_NAME = "municipal-constraints-compiler"
MUNICIPAL_RULE_COMPILER_VERSION = "0.1.0"
DEFAULT_MUNICIPAL_CONSTRAINTS_PATH = (
    Path(__file__).resolve().parents[3]
    / "domain_packs"
    / "municipal_utility"
    / "knowledge"
    / "constraints.yaml"
)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RANGE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*-\s*([0-9]+(?:\.[0-9]+)?)\s*$")


class MunicipalRuleError(ValueError):
    """约束源、编译规则集或规则选择未通过确定性门禁。"""


class RuleConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RuleEnforcement(StrEnum):
    PRODUCTION = "production"
    REVIEW_REQUIRED = "review_required"


class RuleSelectionStatus(StrEnum):
    SELECTED = "selected"
    REVIEW_REQUIRED = "review_required"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


class RuleConditionOperator(StrEnum):
    EQ = "eq"
    LE = "le"
    GT = "gt"


class RuleCondition(StrictFrozenModel):
    field: Literal["outer_diameter_mm", "pressure_class", "burial_method", "voltage_kv"]
    operator: RuleConditionOperator
    value: str | float


class CompiledClearanceRule(StrictFrozenModel):
    rule_key: str = Field(min_length=1, max_length=256)
    source_rule_id: str = Field(pattern=r"^MU-CLEAR-[0-9]{3}$")
    design_system: Literal["wastewater"] = "wastewater"
    design_diameter_mm: float = Field(default=300.0, gt=0)
    obstacle_kind: Literal["aabb", "existing_pipe"]
    obstacle_category: Literal["building", "water", "gas", "power", "telecom"]
    required_clearance_m: float = Field(gt=0)
    unit: Literal["m"] = "m"
    source_clause: str = Field(min_length=1, max_length=1024)
    verified_by: str = Field(min_length=1, max_length=128)
    confidence: RuleConfidence
    enforcement: RuleEnforcement
    required_attributes: tuple[
        Literal["outer_diameter_mm", "pressure_class", "burial_method", "voltage_kv"], ...
    ] = ()
    conditions: tuple[RuleCondition, ...] = ()

    @model_validator(mode="after")
    def _validate_enforcement(self) -> "CompiledClearanceRule":
        expected = (
            RuleEnforcement.PRODUCTION
            if self.confidence is RuleConfidence.HIGH
            else RuleEnforcement.REVIEW_REQUIRED
        )
        if self.enforcement is not expected:
            raise ValueError(
                f"规则 {self.rule_key!r} enforcement 必须与 confidence 对齐: {self.confidence.value} -> {expected.value}"
            )
        condition_fields = {condition.field for condition in self.conditions}
        if not condition_fields.issubset(set(self.required_attributes)):
            raise ValueError(f"规则 {self.rule_key!r} conditions 字段必须同时列入 required_attributes")
        return self


class MunicipalRuleSet(StrictFrozenModel):
    protocol_version: str = Field(default=MUNICIPAL_RULE_SET_VERSION, pattern=r"^1\.0$")
    rule_set_id: str = Field(default=MUNICIPAL_RULE_SET_ID, min_length=1, max_length=256)
    source_path: str = Field(min_length=1, max_length=1024)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_name: str = Field(default=MUNICIPAL_RULE_COMPILER_NAME, pattern=r"^municipal-constraints-compiler$")
    compiler_version: str = Field(default=MUNICIPAL_RULE_COMPILER_VERSION, pattern=r"^0\.1\.0$")
    rules: tuple[CompiledClearanceRule, ...]
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_rule_set(self) -> "MunicipalRuleSet":
        keys = [rule.rule_key for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("MunicipalRuleSet rule_key 不能重复")
        if keys != sorted(keys):
            raise ValueError("MunicipalRuleSet rules 必须按 rule_key 排序")
        expected = _canonical_sha256(self.model_dump(mode="json", exclude={"canonical_sha256"}))
        if self.canonical_sha256 != expected:
            raise ValueError(
                f"MunicipalRuleSet canonical_sha256 不匹配: supplied={self.canonical_sha256}, expected={expected}"
            )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class RuleSelectionResult(StrictFrozenModel):
    status: RuleSelectionStatus
    rule: CompiledClearanceRule | None = None
    candidate_rule_keys: tuple[str, ...] = ()
    missing_attributes: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _validate_selection(self) -> "RuleSelectionResult":
        if self.status in {RuleSelectionStatus.SELECTED, RuleSelectionStatus.REVIEW_REQUIRED}:
            if self.rule is None and not self.missing_attributes:
                raise ValueError(f"{self.status.value} 必须包含规则或缺失属性")
        elif self.rule is not None:
            raise ValueError(f"{self.status.value} 不得携带已选择规则")
        return self


def compile_municipal_rule_set(
    source_path: Path = DEFAULT_MUNICIPAL_CONSTRAINTS_PATH,
    *,
    logical_source_path: str = "knowledge/constraints.yaml",
    schema_gate: SchemaGate | None = None,
) -> MunicipalRuleSet:
    """把受信任 YAML 知识源编译为当前切片的严格 MunicipalRuleSet。"""
    path = Path(source_path)
    try:
        source_bytes = path.read_bytes()
        payload = yaml.safe_load(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MunicipalRuleError(f"市政约束源读取或 YAML 解析失败: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("constraints"), list):
        raise MunicipalRuleError("市政约束源根必须是包含 constraints 数组的 mapping")
    raw_rules = payload["constraints"]
    if not all(isinstance(item, dict) for item in raw_rules):
        raise MunicipalRuleError("constraints 每一项必须是 mapping")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in raw_rules:
        rule_id = str(raw.get("rule_id") or "")
        if not rule_id:
            raise MunicipalRuleError("constraints 存在缺少 rule_id 的条目")
        if rule_id in indexed:
            raise MunicipalRuleError(f"constraints rule_id 重复: {rule_id}")
        indexed[rule_id] = raw

    compiled = [
        _compile_single_value_rule(
            _require_source_rule(indexed, "MU-CLEAR-001", "clearance_building_to_sewage_rain"),
            rule_key="MU-CLEAR-001:building",
            obstacle_kind="aabb",
            obstacle_category="building",
        ),
        *_compile_water_rules(
            _require_source_rule(indexed, "MU-CLEAR-005", "clearance_water_to_sewage_rain")
        ),
        _compile_single_value_rule(
            _require_source_rule(indexed, "MU-CLEAR-006", "clearance_sewage_rain_to_gas_low_pressure"),
            rule_key="MU-CLEAR-006:gas:low",
            obstacle_kind="existing_pipe",
            obstacle_category="gas",
            required_attributes=("pressure_class",),
            conditions=(RuleCondition(field="pressure_class", operator="eq", value="low"),),
        ),
        _compile_single_value_rule(
            _require_source_rule(indexed, "MU-CLEAR-007", "clearance_sewage_rain_to_telecom_direct"),
            rule_key="MU-CLEAR-007:telecom:direct_buried",
            obstacle_kind="existing_pipe",
            obstacle_category="telecom",
            required_attributes=("burial_method",),
            conditions=(RuleCondition(field="burial_method", operator="eq", value="direct_buried"),),
        ),
        _compile_single_value_rule(
            _require_source_rule(indexed, "MU-CLEAR-008", "clearance_sewage_rain_to_power_direct_buried"),
            rule_key="MU-CLEAR-008:power:direct_buried",
            obstacle_kind="existing_pipe",
            obstacle_category="power",
            required_attributes=("burial_method", "voltage_kv"),
            conditions=(RuleCondition(field="burial_method", operator="eq", value="direct_buried"),),
        ),
    ]
    data: dict[str, Any] = {
        "protocol_version": MUNICIPAL_RULE_SET_VERSION,
        "rule_set_id": MUNICIPAL_RULE_SET_ID,
        "source_path": logical_source_path,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "compiler_name": MUNICIPAL_RULE_COMPILER_NAME,
        "compiler_version": MUNICIPAL_RULE_COMPILER_VERSION,
        "rules": [rule.model_dump(mode="json") for rule in sorted(compiled, key=lambda item: item.rule_key)],
    }
    data["canonical_sha256"] = _canonical_sha256(data)
    try:
        rule_set = MunicipalRuleSet.model_validate(data)
        (schema_gate or SchemaGate()).gate_or_fix("municipal_rule_set", rule_set.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise MunicipalRuleError(f"编译后的 MunicipalRuleSet 未通过门禁: {exc}") from exc
    return rule_set


def select_clearance_rule(
    rule_set: MunicipalRuleSet,
    *,
    obstacle_kind: str,
    obstacle_category: str,
    attributes: Mapping[str, Any] | None = None,
) -> RuleSelectionResult:
    """按障碍物工程事实选择净距规则；不完整、低置信或歧义时失败关闭。"""
    facts = dict(attributes or {})
    candidates = [
        rule
        for rule in rule_set.rules
        if rule.obstacle_kind == obstacle_kind and rule.obstacle_category == obstacle_category
    ]
    candidate_keys = tuple(rule.rule_key for rule in candidates)
    if not candidates:
        return RuleSelectionResult(
            status=RuleSelectionStatus.UNSUPPORTED,
            candidate_rule_keys=(),
            detail=f"规则集不支持 obstacle kind={obstacle_kind!r}, category={obstacle_category!r}",
        )

    complete: list[CompiledClearanceRule] = []
    missing: set[str] = set()
    for rule in candidates:
        absent = {field for field in rule.required_attributes if facts.get(field) is None}
        if absent:
            missing.update(absent)
            continue
        if all(_condition_matches(condition, facts) for condition in rule.conditions):
            complete.append(rule)

    if len(complete) > 1:
        keys = tuple(rule.rule_key for rule in complete)
        return RuleSelectionResult(
            status=RuleSelectionStatus.AMBIGUOUS,
            candidate_rule_keys=keys,
            detail=f"多个净距规则同时适用，拒绝任意选择: {list(keys)}",
        )
    if len(complete) == 1:
        selected = complete[0]
        status = (
            RuleSelectionStatus.SELECTED
            if selected.enforcement is RuleEnforcement.PRODUCTION
            else RuleSelectionStatus.REVIEW_REQUIRED
        )
        return RuleSelectionResult(
            status=status,
            rule=selected,
            candidate_rule_keys=(selected.rule_key,),
            detail=(
                f"选择规则 {selected.source_rule_id}/{selected.rule_key}；"
                f"confidence={selected.confidence.value}, enforcement={selected.enforcement.value}"
            ),
        )
    if missing:
        missing_tuple = tuple(sorted(missing))
        return RuleSelectionResult(
            status=RuleSelectionStatus.REVIEW_REQUIRED,
            candidate_rule_keys=candidate_keys,
            missing_attributes=missing_tuple,
            detail=f"规则选择缺少工程属性: {list(missing_tuple)}",
        )
    return RuleSelectionResult(
        status=RuleSelectionStatus.UNSUPPORTED,
        candidate_rule_keys=candidate_keys,
        detail=f"已有类别规则但工程属性不在其适用范围: category={obstacle_category!r}",
    )


def _require_source_rule(
    indexed: Mapping[str, Mapping[str, Any]],
    rule_id: str,
    parameter: str,
) -> Mapping[str, Any]:
    raw = indexed.get(rule_id)
    if raw is None:
        raise MunicipalRuleError(f"约束源缺少必需规则 {rule_id}")
    if raw.get("category") != "horizontal_clearance" or raw.get("parameter") != parameter:
        raise MunicipalRuleError(f"规则 {rule_id} category/parameter 与编译器契约不匹配")
    if raw.get("unit") != "m":
        raise MunicipalRuleError(f"规则 {rule_id} unit 必须为 m")
    return raw


def _compile_single_value_rule(
    raw: Mapping[str, Any],
    *,
    rule_key: str,
    obstacle_kind: Literal["aabb", "existing_pipe"],
    obstacle_category: Literal["building", "water", "gas", "power", "telecom"],
    required_attributes: tuple[
        Literal["outer_diameter_mm", "pressure_class", "burial_method", "voltage_kv"], ...
    ] = (),
    conditions: tuple[RuleCondition, ...] = (),
) -> CompiledClearanceRule:
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise MunicipalRuleError(f"规则 {raw.get('rule_id')} value 必须是有限数值")
    return _compiled_rule(
        raw,
        rule_key=rule_key,
        required_clearance_m=float(value),
        obstacle_kind=obstacle_kind,
        obstacle_category=obstacle_category,
        required_attributes=required_attributes,
        conditions=conditions,
    )


def _compile_water_rules(raw: Mapping[str, Any]) -> list[CompiledClearanceRule]:
    match = _RANGE_PATTERN.match(str(raw.get("range") or ""))
    if match is None:
        raise MunicipalRuleError("规则 MU-CLEAR-005 range 必须是形如 '1.0-1.5' 的数值区间")
    lower, upper = (float(match.group(1)), float(match.group(2)))
    if lower <= 0 or upper < lower:
        raise MunicipalRuleError("规则 MU-CLEAR-005 range 必须是正数递增区间")
    return [
        _compiled_rule(
            raw,
            rule_key="MU-CLEAR-005:water:d_le_200",
            required_clearance_m=lower,
            obstacle_kind="existing_pipe",
            obstacle_category="water",
            required_attributes=("outer_diameter_mm",),
            conditions=(RuleCondition(field="outer_diameter_mm", operator="le", value=200.0),),
        ),
        _compiled_rule(
            raw,
            rule_key="MU-CLEAR-005:water:d_gt_200",
            required_clearance_m=upper,
            obstacle_kind="existing_pipe",
            obstacle_category="water",
            required_attributes=("outer_diameter_mm",),
            conditions=(RuleCondition(field="outer_diameter_mm", operator="gt", value=200.0),),
        ),
    ]


def _compiled_rule(
    raw: Mapping[str, Any],
    *,
    rule_key: str,
    required_clearance_m: float,
    obstacle_kind: Literal["aabb", "existing_pipe"],
    obstacle_category: Literal["building", "water", "gas", "power", "telecom"],
    required_attributes: tuple[
        Literal["outer_diameter_mm", "pressure_class", "burial_method", "voltage_kv"], ...
    ],
    conditions: tuple[RuleCondition, ...],
) -> CompiledClearanceRule:
    try:
        confidence = RuleConfidence(str(raw.get("confidence") or ""))
    except ValueError as exc:
        raise MunicipalRuleError(f"规则 {raw.get('rule_id')} confidence 非法") from exc
    enforcement = (
        RuleEnforcement.PRODUCTION
        if confidence is RuleConfidence.HIGH
        else RuleEnforcement.REVIEW_REQUIRED
    )
    source_clause = str(raw.get("source_clause") or "")
    verified_by = str(raw.get("verified_by") or "")
    if not source_clause or not verified_by:
        raise MunicipalRuleError(f"规则 {raw.get('rule_id')} 缺少 source_clause/verified_by")
    return CompiledClearanceRule(
        rule_key=rule_key,
        source_rule_id=str(raw["rule_id"]),
        obstacle_kind=obstacle_kind,
        obstacle_category=obstacle_category,
        required_clearance_m=required_clearance_m,
        source_clause=source_clause,
        verified_by=verified_by,
        confidence=confidence,
        enforcement=enforcement,
        required_attributes=required_attributes,
        conditions=conditions,
    )


def _condition_matches(condition: RuleCondition, facts: Mapping[str, Any]) -> bool:
    actual = facts.get(condition.field)
    if condition.operator is RuleConditionOperator.EQ:
        return actual == condition.value
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    expected = float(condition.value)
    return float(actual) <= expected if condition.operator is RuleConditionOperator.LE else float(actual) > expected


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if not _HASH_PATTERN.match(digest):  # 防御性断言，避免未来替换实现返回非 SHA-256。
        raise AssertionError("canonical hash implementation did not return SHA-256")
    return digest


__all__ = [
    "CompiledClearanceRule",
    "DEFAULT_MUNICIPAL_CONSTRAINTS_PATH",
    "MUNICIPAL_RULE_COMPILER_NAME",
    "MUNICIPAL_RULE_COMPILER_VERSION",
    "MUNICIPAL_RULE_SET_ID",
    "MUNICIPAL_RULE_SET_VERSION",
    "MunicipalRuleError",
    "MunicipalRuleSet",
    "RuleCondition",
    "RuleConditionOperator",
    "RuleConfidence",
    "RuleEnforcement",
    "RuleSelectionResult",
    "RuleSelectionStatus",
    "compile_municipal_rule_set",
    "select_clearance_rule",
]
