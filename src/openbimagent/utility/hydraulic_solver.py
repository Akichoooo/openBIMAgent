"""M1.5 T5 重力污水管网确定性水力校核 v0.1。

模块消费已编译且不可变的 ``CompiledUtilityIR v1``、逐段显式流量和逐段显式
Manning 粗糙系数。首版只计算 DN300 混凝土正坡重力污水管的均匀流满流能力、
部分充满度、流速和容量裕量；不优化或改写管径、坡度、坐标和管底标高。

``MU-DRAIN-007`` 通过版本化规则证据包绑定 production verification；只有完整、
一致且 eligible 的规则证据才能产生 PASS/FAIL，review-required 规则保持 UNKNOWN。
物理流量超过满流能力则确定性要求返工。
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.contracts import (
    CompiledUtilityIR,
    EvidenceStatus,
    EvidenceSubjectType,
    RuleEvidence,
    StrictFrozenModel,
)
from openbimagent.utility.rule_evidence import (
    MunicipalRuleEvidenceBundle,
    ProductionVerificationStatus,
    RuleDecisionStatus,
    RuleEnforcement,
    RuleEvaluation,
    VerifiedMunicipalRule,
    build_rule_evaluation,
    compile_municipal_rule_evidence_bundle,
)
from openbimagent.utility.solver import MIN_SEWAGE_DIAMETER_MM

HYDRAULIC_SOLVER_INPUT_VERSION = "0.1"
HYDRAULIC_SOLVER_RESULT_VERSION = "0.1"
HYDRAULIC_SOLVER_NAME = "municipal-gravity-hydraulic-solver"
HYDRAULIC_SOLVER_VERSION = "0.1.0"
HYDRAULIC_CALCULATION_MODEL = "manning_uniform_open_channel_si"
HYDRAULIC_TOLERANCE = 1e-9
MAX_HYDRAULIC_SCENARIOS = 20
MAX_HYDRAULIC_SEGMENTS = 10000


class HydraulicSolverError(ValueError):
    """水力输入、几何绑定或计算结果未通过失败关闭门禁。"""


class HydraulicSolveStatus(StrEnum):
    CALCULATED = "calculated"
    REWORK_REQUIRED = "rework_required"


class HydraulicComplianceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class RoughnessInput(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    manning_n: float = Field(gt=0.0, le=0.1)
    provenance: Literal["designer_input", "approved_catalog", "measured"]
    source_reference: str = Field(min_length=1, max_length=1024)


class SegmentFlowInput(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    flow_m3_s: float = Field(gt=0.0)


class HydraulicScenarioInput(StrictFrozenModel):
    scenario_id: str = Field(min_length=1, max_length=256)
    scenario_type: Literal["design", "check"]
    segment_flows: tuple[SegmentFlowInput, ...] = Field(
        min_length=1,
        max_length=MAX_HYDRAULIC_SEGMENTS,
    )

    @model_validator(mode="after")
    def _validate_flows(self) -> "HydraulicScenarioInput":
        _unique_by(self.segment_flows, "segment_id", "segment flow")
        return self


class HydraulicSolverInput(StrictFrozenModel):
    protocol_version: str = Field(default=HYDRAULIC_SOLVER_INPUT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calculation_model: Literal["manning_uniform_open_channel_si"] = HYDRAULIC_CALCULATION_MODEL
    roughness_inputs: tuple[RoughnessInput, ...] = Field(
        min_length=1,
        max_length=MAX_HYDRAULIC_SEGMENTS,
    )
    scenarios: tuple[HydraulicScenarioInput, ...] = Field(
        min_length=1,
        max_length=MAX_HYDRAULIC_SCENARIOS,
    )

    @model_validator(mode="after")
    def _validate_input(self) -> "HydraulicSolverInput":
        _unique_by(self.roughness_inputs, "segment_id", "roughness input")
        _unique_by(self.scenarios, "scenario_id", "scenario")
        scenario_types = {scenario.scenario_type for scenario in self.scenarios}
        if scenario_types != {"design", "check"}:
            raise ValueError("hydraulic Solver v0.1 必须同时包含 design 和 check 工况")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["roughness_inputs"] = sorted(
            payload["roughness_inputs"], key=lambda item: item["segment_id"]
        )
        payload["scenarios"] = sorted(payload["scenarios"], key=lambda item: item["scenario_id"])
        for scenario in payload["scenarios"]:
            scenario["segment_flows"] = sorted(
                scenario["segment_flows"], key=lambda item: item["segment_id"]
            )
        return payload

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.canonical_dict())


class HydraulicSegmentResult(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    diameter_mm: float = Field(gt=0.0)
    slope: float = Field(gt=0.0)
    manning_n: float = Field(gt=0.0, le=0.1)
    roughness_provenance: Literal["designer_input", "approved_catalog", "measured"]
    roughness_source_reference: str = Field(min_length=1, max_length=1024)
    flow_m3_s: float = Field(gt=0.0)
    full_flow_capacity_m3_s: float = Field(gt=0.0)
    capacity_margin_m3_s: float
    capacity_sufficient: bool
    depth_ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    flow_area_m2: float | None = Field(default=None, gt=0.0)
    hydraulic_radius_m: float | None = Field(default=None, gt=0.0)
    velocity_m_s: float | None = Field(default=None, gt=0.0)
    minimum_velocity_compliance: HydraulicComplianceStatus
    minimum_velocity_rule_id: Literal["MU-DRAIN-007"] = "MU-DRAIN-007"
    minimum_velocity_rule_status: Literal["production", "review_required"]
    minimum_velocity_limit_m_s: float | None = Field(default=None, gt=0.0)
    rule_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_capacity_result(self) -> "HydraulicSegmentResult":
        computed = self.full_flow_capacity_m3_s - self.flow_m3_s
        if not math.isclose(self.capacity_margin_m3_s, computed, abs_tol=HYDRAULIC_TOLERANCE):
            raise ValueError("capacity_margin_m3_s 与 capacity-flow 不一致")
        expected_sufficient = computed >= -HYDRAULIC_TOLERANCE
        if self.capacity_sufficient is not expected_sufficient:
            raise ValueError("capacity_sufficient 与容量裕量不一致")
        hydraulic_values = (
            self.depth_ratio,
            self.flow_area_m2,
            self.hydraulic_radius_m,
            self.velocity_m_s,
        )
        if self.capacity_sufficient and any(value is None for value in hydraulic_values):
            raise ValueError("容量满足时必须包含部分充满度、面积、水力半径和流速")
        if not self.capacity_sufficient and any(value is not None for value in hydraulic_values):
            raise ValueError("超容量时不得伪造部分充满度或流速")
        if self.minimum_velocity_rule_status == "production":
            if self.minimum_velocity_limit_m_s is None:
                raise ValueError("production 最小流速规则必须包含 limit")
            if self.velocity_m_s is None:
                expected_compliance = HydraulicComplianceStatus.UNKNOWN
            else:
                expected_compliance = (
                    HydraulicComplianceStatus.PASS
                    if self.velocity_m_s + HYDRAULIC_TOLERANCE
                    >= self.minimum_velocity_limit_m_s
                    else HydraulicComplianceStatus.FAIL
                )
            if self.minimum_velocity_compliance is not expected_compliance:
                raise ValueError("minimum_velocity_compliance 与 production rule/velocity 不一致")
        else:
            if self.minimum_velocity_limit_m_s is not None:
                raise ValueError("review_required 最小流速规则不得输出 production limit")
            if self.minimum_velocity_compliance is not HydraulicComplianceStatus.UNKNOWN:
                raise ValueError("review_required 最小流速规则必须保持 unknown")
        return self


class HydraulicScenarioResult(StrictFrozenModel):
    scenario_id: str = Field(min_length=1, max_length=256)
    scenario_type: Literal["design", "check"]
    segments: tuple[HydraulicSegmentResult, ...] = Field(min_length=1)
    capacity_sufficient: bool

    @model_validator(mode="after")
    def _validate_scenario(self) -> "HydraulicScenarioResult":
        _unique_by(self.segments, "segment_id", "hydraulic segment result")
        if self.capacity_sufficient is not all(item.capacity_sufficient for item in self.segments):
            raise ValueError("scenario capacity_sufficient 与逐段结果不一致")
        return self


class HydraulicSolverResult(StrictFrozenModel):
    protocol_version: str = Field(default=HYDRAULIC_SOLVER_RESULT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    solver_name: str = Field(default=HYDRAULIC_SOLVER_NAME, pattern=r"^municipal-gravity-hydraulic-solver$")
    solver_version: str = Field(default=HYDRAULIC_SOLVER_VERSION, pattern=r"^0\.1\.0$")
    calculation_model: Literal["manning_uniform_open_channel_si"] = HYDRAULIC_CALCULATION_MODEL
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: HydraulicSolveStatus
    scenarios: tuple[HydraulicScenarioResult, ...] = Field(min_length=1)
    hydraulics_in_spec: HydraulicComplianceStatus
    geometry_mutated: Literal[False] = False
    detail: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _validate_result(self) -> "HydraulicSolverResult":
        _unique_by(self.scenarios, "scenario_id", "hydraulic scenario result")
        scenario_types = {scenario.scenario_type for scenario in self.scenarios}
        if scenario_types != {"design", "check"}:
            raise ValueError("hydraulic Solver result v0.1 必须同时包含 design 和 check 工况")
        expected_segment_ids: set[str] | None = None
        segment_facts: dict[str, tuple[float, float, float, str, str]] = {}
        for scenario in self.scenarios:
            current_segment_ids = {segment.segment_id for segment in scenario.segments}
            if expected_segment_ids is None:
                expected_segment_ids = current_segment_ids
            elif current_segment_ids != expected_segment_ids:
                raise ValueError("不同 hydraulic scenario 的 segment 集合必须精确一致")
            for segment in scenario.segments:
                facts = (
                    segment.diameter_mm,
                    segment.slope,
                    segment.manning_n,
                    segment.roughness_provenance,
                    segment.roughness_source_reference,
                )
                existing = segment_facts.setdefault(segment.segment_id, facts)
                if existing != facts:
                    raise ValueError("不同 hydraulic scenario 的几何或粗糙系数事实发生漂移")
        all_capacity = all(item.capacity_sufficient for item in self.scenarios)
        expected_status = (
            HydraulicSolveStatus.CALCULATED
            if all_capacity
            else HydraulicSolveStatus.REWORK_REQUIRED
        )
        if self.status is not expected_status:
            raise ValueError("solver status 与容量结果不一致")
        segment_compliances = {
            segment.minimum_velocity_compliance
            for scenario in self.scenarios
            for segment in scenario.segments
        }
        expected_compliance = (
            HydraulicComplianceStatus.FAIL
            if not all_capacity or HydraulicComplianceStatus.FAIL in segment_compliances
            else HydraulicComplianceStatus.UNKNOWN
            if HydraulicComplianceStatus.UNKNOWN in segment_compliances
            else HydraulicComplianceStatus.PASS
        )
        if self.hydraulics_in_spec is not expected_compliance:
            raise ValueError("hydraulics_in_spec 与容量/规则证据不一致")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["scenarios"] = sorted(payload["scenarios"], key=lambda item: item["scenario_id"])
        for scenario in payload["scenarios"]:
            scenario["segments"] = sorted(
                scenario["segments"], key=lambda item: item["segment_id"]
            )
        return payload

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.canonical_dict())

    def rule_evaluation(
        self,
        *,
        compiled_ir: CompiledUtilityIR,
        rule_evidence_bundle: MunicipalRuleEvidenceBundle,
    ) -> RuleEvaluation:
        """聚合全部工况和管段，形成单一、稳定的网络级最小流速规则评估。"""
        if compiled_ir.canonical_sha256() != self.source_ir_sha256:
            raise HydraulicSolverError("hydraulic RuleEvaluation 的 CompiledUtilityIR 身份不匹配")
        if rule_evidence_bundle.canonical_sha256 != self.rule_evidence_bundle_sha256:
            raise HydraulicSolverError("hydraulic RuleEvaluation 的规则证据包身份不匹配")
        known_segment_ids = {segment.segment_id for segment in compiled_ir.segments}
        evaluated_segments = {
            segment.segment_id
            for scenario in self.scenarios
            for segment in scenario.segments
        }
        if evaluated_segments != known_segment_ids:
            raise HydraulicSolverError("hydraulic RuleEvaluation 的 segment 集合与源 IR 不一致")
        rule = rule_evidence_bundle.rule("MU-DRAIN-007")
        segments = tuple(
            segment
            for scenario in sorted(self.scenarios, key=lambda item: item.scenario_id)
            for segment in sorted(scenario.segments, key=lambda item: item.segment_id)
        )
        for segment in segments:
            if (
                segment.rule_set_sha256 != rule_evidence_bundle.canonical_sha256
                or segment.rule_sha256 != rule.canonical_sha256
                or segment.verification_sha256 != rule.verification.canonical_sha256
            ):
                raise HydraulicSolverError("hydraulic RuleEvaluation 的逐段规则身份发生漂移")
        measured_values = [
            segment.velocity_m_s
            for segment in segments
            if segment.velocity_m_s is not None
        ]
        measured_value = min(measured_values) if measured_values else None
        production_verification = rule.verification.production_verification
        velocity_statuses = {
            segment.minimum_velocity_compliance
            for segment in segments
        }
        if production_verification is ProductionVerificationStatus.REVIEW_REQUIRED:
            status = RuleDecisionStatus.UNKNOWN
            limit_value = None
        else:
            status = RuleDecisionStatus(
                HydraulicComplianceStatus.FAIL.value
                if HydraulicComplianceStatus.FAIL in velocity_statuses
                else HydraulicComplianceStatus.UNKNOWN.value
                if HydraulicComplianceStatus.UNKNOWN in velocity_statuses
                else HydraulicComplianceStatus.PASS.value
            )
            limit_value = float(rule.value)
        return build_rule_evaluation(
            evaluation_id=f"{self.request_id}-network-minimum-velocity",
            rule_set_sha256=rule_evidence_bundle.canonical_sha256,
            rule_sha256=rule.canonical_sha256,
            verification_sha256=rule.verification.canonical_sha256,
            production_verification=production_verification,
            rule_id=rule.rule_id,
            subject_type=EvidenceSubjectType.NETWORK,
            subject_id=compiled_ir.ir_id,
            measured_value=measured_value,
            limit_value=limit_value,
            unit="m/s",
            status=status,
            review_reason=None,
            exception_approval_id=None,
            exception_approval_sha256=None,
        )

    def rule_evidence(self, *, compiled_ir: CompiledUtilityIR) -> tuple[RuleEvidence, ...]:
        """导出独立、可散列的水力证据，并校验全部 subject 属于绑定的源 IR。"""
        if compiled_ir.canonical_sha256() != self.source_ir_sha256:
            raise HydraulicSolverError("hydraulic RuleEvidence 的 CompiledUtilityIR 身份不匹配")
        known_segment_ids = {segment.segment_id for segment in compiled_ir.segments}
        result_segment_ids = {
            segment.segment_id for scenario in self.scenarios for segment in scenario.segments
        }
        if result_segment_ids != known_segment_ids:
            raise HydraulicSolverError("hydraulic RuleEvidence 的 segment 集合与源 IR 不一致")
        evidence: list[RuleEvidence] = []
        for scenario in sorted(self.scenarios, key=lambda item: item.scenario_id):
            for segment in sorted(scenario.segments, key=lambda item: item.segment_id):
                evidence.append(
                    RuleEvidence(
                        evidence_id=(
                            f"{self.request_id}-{scenario.scenario_id}-{segment.segment_id}-capacity"
                        ),
                        rule_id="HYDRAULIC-PHYSICS-CAPACITY",
                        check_name="hydraulic_capacity_in_spec",
                        status=(
                            EvidenceStatus.PASS
                            if segment.capacity_sufficient
                            else EvidenceStatus.FAIL
                        ),
                        subject_type=EvidenceSubjectType.SEGMENT,
                        subject_id=segment.segment_id,
                        detail=(
                            f"scenario={scenario.scenario_id}; model={self.calculation_model}; "
                            f"Manning n={segment.manning_n}; provenance="
                            f"{segment.roughness_provenance}; source="
                            f"{segment.roughness_source_reference}"
                        ),
                        measured_value=segment.flow_m3_s,
                        limit_value=segment.full_flow_capacity_m3_s,
                        unit="m3/s",
                        source_clause="Manning uniform open-channel equation (SI)",
                    )
                )
                evidence.append(
                    RuleEvidence(
                        evidence_id=(
                            f"{self.request_id}-{scenario.scenario_id}-{segment.segment_id}-velocity"
                        ),
                        rule_id=segment.minimum_velocity_rule_id,
                        check_name="hydraulics_in_spec",
                        status=EvidenceStatus(segment.minimum_velocity_compliance.value),
                        subject_type=EvidenceSubjectType.SEGMENT,
                        subject_id=segment.segment_id,
                        detail=(
                            f"scenario={scenario.scenario_id}; calculated velocity="
                            f"{segment.velocity_m_s!r}; rule_status="
                            f"{segment.minimum_velocity_rule_status}; rule_set="
                            f"{segment.rule_set_sha256}; rule={segment.rule_sha256}; "
                            f"verification={segment.verification_sha256}"
                        ),
                        measured_value=segment.velocity_m_s,
                        limit_value=segment.minimum_velocity_limit_m_s,
                        unit="m/s",
                        source_clause=None,
                    )
                )
        if self.status is HydraulicSolveStatus.REWORK_REQUIRED:
            evidence.append(
                RuleEvidence(
                    evidence_id=f"{self.request_id}-network-capacity",
                    rule_id="HYDRAULIC-PHYSICS-CAPACITY",
                    check_name="hydraulics_in_spec",
                    status=EvidenceStatus.FAIL,
                    subject_type=EvidenceSubjectType.NETWORK,
                    subject_id=compiled_ir.ir_id,
                    detail="至少一个显式工况超过满流能力，网络水力校核失败且要求返工",
                )
            )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))

    def domain_evidence(self) -> dict[str, dict[str, bool | None | str]]:
        """形成独立水力证据，不改写或重新散列几何 ``CompiledUtilityIR``。"""
        capacity_ok = self.status is HydraulicSolveStatus.CALCULATED
        return {
            "hydraulic_capacity_in_spec": {
                "ok": capacity_ok,
                "detail": (
                    "all explicit hydraulic scenarios are within full-flow capacity"
                    if capacity_ok
                    else "at least one explicit hydraulic scenario exceeds full-flow capacity"
                )
                + f"; hydraulic_result_sha256={self.canonical_sha256()}",
            },
            "hydraulics_in_spec": {
                "ok": (
                    True
                    if self.hydraulics_in_spec is HydraulicComplianceStatus.PASS
                    else False
                    if self.hydraulics_in_spec is HydraulicComplianceStatus.FAIL
                    else None
                ),
                "detail": (
                    "minimum velocity compliance remains unknown because bound rule evidence requires review"
                    if self.hydraulics_in_spec is HydraulicComplianceStatus.UNKNOWN
                    else self.detail
                )
                + f"; hydraulic_result_sha256={self.canonical_sha256()}",
            },
        }


def solve_hydraulic_network(
    compiled_ir: CompiledUtilityIR | dict[str, Any],
    solver_input: HydraulicSolverInput | dict[str, Any],
    *,
    rule_evidence_bundle: MunicipalRuleEvidenceBundle | None = None,
    schema_gate: SchemaGate | None = None,
) -> HydraulicSolverResult:
    """校核不可变重力污水几何；不返回或应用任何几何修改。"""
    gate = schema_gate or SchemaGate()
    try:
        ir = (
            compiled_ir
            if isinstance(compiled_ir, CompiledUtilityIR)
            else CompiledUtilityIR.model_validate(compiled_ir)
        )
        request = (
            solver_input
            if isinstance(solver_input, HydraulicSolverInput)
            else HydraulicSolverInput.model_validate(solver_input)
        )
        gate.gate_or_fix("hydraulic_solver_input", request.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise HydraulicSolverError(f"hydraulic Solver v0.1 输入未通过门禁: {exc}") from exc

    bundle = rule_evidence_bundle or compile_municipal_rule_evidence_bundle()
    if request.rule_evidence_bundle_sha256 != bundle.canonical_sha256:
        raise HydraulicSolverError(
            "hydraulic rule_evidence_bundle_sha256 与规则证据包 canonical SHA-256 不一致: "
            f"input={request.rule_evidence_bundle_sha256}, actual={bundle.canonical_sha256}"
        )
    velocity_rule = bundle.rule("MU-DRAIN-007")
    ir_sha256 = ir.canonical_sha256()
    if request.source_ir_sha256 != ir_sha256:
        raise HydraulicSolverError(
            "hydraulic source_ir_sha256 与 CompiledUtilityIR canonical SHA-256 不一致: "
            f"input={request.source_ir_sha256}, actual={ir_sha256}"
        )
    _validate_supported_ir(ir)
    segments = {item.segment_id: item for item in ir.segments}
    segment_ids = set(segments)
    roughness = {item.segment_id: item for item in request.roughness_inputs}
    if set(roughness) != segment_ids:
        raise HydraulicSolverError(
            "roughness segment 集合必须与 CompiledUtilityIR 精确一致: "
            f"missing={sorted(segment_ids - set(roughness))}, extra={sorted(set(roughness) - segment_ids)}"
        )

    scenario_results: list[HydraulicScenarioResult] = []
    for scenario in sorted(request.scenarios, key=lambda item: item.scenario_id):
        flows = {item.segment_id: item.flow_m3_s for item in scenario.segment_flows}
        if set(flows) != segment_ids:
            raise HydraulicSolverError(
                f"scenario {scenario.scenario_id!r} segment flow 集合必须与 CompiledUtilityIR 精确一致: "
                f"missing={sorted(segment_ids - set(flows))}, extra={sorted(set(flows) - segment_ids)}"
            )
        _validate_flow_conservation(ir, flows, scenario.scenario_id)
        results = tuple(
            _solve_segment(
                segment_id=segment_id,
                diameter_mm=segments[segment_id].diameter_mm,
                slope=segments[segment_id].slope,
                manning_n=roughness[segment_id].manning_n,
                roughness_provenance=roughness[segment_id].provenance,
                roughness_source_reference=roughness[segment_id].source_reference,
                flow_m3_s=flows[segment_id],
                rule_set_sha256=bundle.canonical_sha256,
                velocity_rule=velocity_rule,
            )
            for segment_id in sorted(segment_ids)
        )
        scenario_results.append(
            HydraulicScenarioResult(
                scenario_id=scenario.scenario_id,
                scenario_type=scenario.scenario_type,
                segments=results,
                capacity_sufficient=all(item.capacity_sufficient for item in results),
            )
        )

    all_capacity = all(item.capacity_sufficient for item in scenario_results)
    result = HydraulicSolverResult(
        request_id=request.request_id,
        input_sha256=request.canonical_sha256(),
        source_ir_sha256=ir_sha256,
        rule_evidence_bundle_sha256=bundle.canonical_sha256,
        status=(
            HydraulicSolveStatus.CALCULATED
            if all_capacity
            else HydraulicSolveStatus.REWORK_REQUIRED
        ),
        scenarios=tuple(scenario_results),
        hydraulics_in_spec=(
            HydraulicComplianceStatus.FAIL
            if not all_capacity
            or any(
                segment.minimum_velocity_compliance is HydraulicComplianceStatus.FAIL
                for scenario in scenario_results
                for segment in scenario.segments
            )
            else HydraulicComplianceStatus.UNKNOWN
            if any(
                segment.minimum_velocity_compliance is HydraulicComplianceStatus.UNKNOWN
                for scenario in scenario_results
                for segment in scenario.segments
            )
            else HydraulicComplianceStatus.PASS
        ),
        geometry_mutated=False,
        detail=(
            "满流能力、部分充满度和流速已按显式 Manning n 计算；"
            "MU-DRAIN-007 已绑定版本化规则证据包并确定性计算最小流速合规。"
            if all_capacity
            else "至少一个工况的管段流量超过满流能力；仅返回返工要求，不隐式修改几何。"
        ),
    )
    try:
        gate.gate_or_fix("hydraulic_solver_result", result.model_dump(mode="json"))
    except SchemaGateError as exc:
        raise HydraulicSolverError(f"hydraulic Solver v0.1 结果未通过门禁: {exc}") from exc
    return result


def _validate_supported_ir(ir: CompiledUtilityIR) -> None:
    if len(ir.segments) > MAX_HYDRAULIC_SEGMENTS:
        raise HydraulicSolverError(
            f"hydraulic Solver v0.1 最多支持 {MAX_HYDRAULIC_SEGMENTS} 个 segment"
        )
    systems = {item.system_id: item for item in ir.systems}
    for segment in ir.segments:
        system = systems[segment.system_id]
        if system.system_type.value != "wastewater" or system.flow_regime.value != "gravity":
            raise HydraulicSolverError(
                f"segment {segment.segment_id!r} 所属系统必须是 gravity wastewater"
            )
        if not math.isclose(
            segment.diameter_mm,
            MIN_SEWAGE_DIAMETER_MM,
            abs_tol=HYDRAULIC_TOLERANCE,
        ):
            raise HydraulicSolverError("hydraulic Solver v0.1 仅支持 DN300")
        if segment.material != "concrete":
            raise HydraulicSolverError("hydraulic Solver v0.1 仅支持 concrete")
        if segment.slope <= 0.0:
            raise HydraulicSolverError(
                f"segment {segment.segment_id!r} 必须具有正坡 slope 才能执行重力均匀流计算"
            )


def _validate_flow_conservation(
    ir: CompiledUtilityIR,
    flows: dict[str, float],
    scenario_id: str,
) -> None:
    port_to_node: dict[str, str] = {}
    node_ids = {node.node_id for node in ir.nodes}
    for node in ir.nodes:
        for port in node.ports:
            port_to_node[port.port_id] = node.node_id
    incoming: dict[str, float] = {node_id: 0.0 for node_id in node_ids}
    outgoing: dict[str, float] = {node_id: 0.0 for node_id in node_ids}
    for segment in ir.segments:
        start_node = port_to_node[segment.start_port_id]
        end_node = port_to_node[segment.end_port_id]
        outgoing[start_node] += flows[segment.segment_id]
        incoming[end_node] += flows[segment.segment_id]
    internal_node_ids = {
        node_id
        for node_id in node_ids
        if incoming[node_id] > 0.0 and outgoing[node_id] > 0.0
    }
    for node_id in sorted(internal_node_ids):
        tolerance = max(HYDRAULIC_TOLERANCE, 1e-6 * max(incoming[node_id], outgoing[node_id]))
        if not math.isclose(incoming[node_id], outgoing[node_id], abs_tol=tolerance):
            raise HydraulicSolverError(
                f"scenario {scenario_id!r} internal node {node_id!r} 流量不守恒: "
                f"in={incoming[node_id]:.9f}, out={outgoing[node_id]:.9f} m3/s"
            )


def _solve_segment(
    *,
    segment_id: str,
    diameter_mm: float,
    slope: float,
    manning_n: float,
    roughness_provenance: Literal["designer_input", "approved_catalog", "measured"],
    roughness_source_reference: str,
    flow_m3_s: float,
    rule_set_sha256: str,
    velocity_rule: VerifiedMunicipalRule,
) -> HydraulicSegmentResult:
    diameter_m = diameter_mm / 1000.0
    radius_m = diameter_m / 2.0
    full_area_m2 = math.pi * radius_m * radius_m
    full_hydraulic_radius_m = diameter_m / 4.0
    full_capacity = _manning_discharge(
        area_m2=full_area_m2,
        hydraulic_radius_m=full_hydraulic_radius_m,
        slope=slope,
        manning_n=manning_n,
    )
    margin = full_capacity - flow_m3_s
    sufficient = margin >= -HYDRAULIC_TOLERANCE
    if sufficient:
        if math.isclose(flow_m3_s, full_capacity, abs_tol=HYDRAULIC_TOLERANCE):
            depth_ratio = 1.0
            area_m2 = full_area_m2
            hydraulic_radius_m = full_hydraulic_radius_m
        else:
            depth_ratio = _partial_depth_ratio(
                flow_m3_s=flow_m3_s,
                diameter_m=diameter_m,
                slope=slope,
                manning_n=manning_n,
            )
            area_m2, hydraulic_radius_m = _circular_section(diameter_m, depth_ratio)
        velocity_m_s = flow_m3_s / area_m2
    else:
        depth_ratio = None
        area_m2 = None
        hydraulic_radius_m = None
        velocity_m_s = None
    velocity_compliance = (
        HydraulicComplianceStatus.UNKNOWN
        if velocity_rule.enforcement is RuleEnforcement.REVIEW_REQUIRED or velocity_m_s is None
        else HydraulicComplianceStatus.PASS
        if velocity_m_s + HYDRAULIC_TOLERANCE >= float(velocity_rule.value)
        else HydraulicComplianceStatus.FAIL
    )
    return HydraulicSegmentResult(
        segment_id=segment_id,
        diameter_mm=diameter_mm,
        slope=slope,
        manning_n=manning_n,
        roughness_provenance=roughness_provenance,
        roughness_source_reference=roughness_source_reference,
        flow_m3_s=flow_m3_s,
        full_flow_capacity_m3_s=full_capacity,
        capacity_margin_m3_s=margin,
        capacity_sufficient=sufficient,
        depth_ratio=depth_ratio,
        flow_area_m2=area_m2,
        hydraulic_radius_m=hydraulic_radius_m,
        velocity_m_s=velocity_m_s,
        minimum_velocity_compliance=velocity_compliance,
        minimum_velocity_rule_status=(
            "production"
            if velocity_rule.enforcement is RuleEnforcement.PRODUCTION
            else "review_required"
        ),
        minimum_velocity_limit_m_s=(
            float(velocity_rule.value)
            if velocity_rule.enforcement is RuleEnforcement.PRODUCTION
            else None
        ),
        rule_set_sha256=rule_set_sha256,
        rule_sha256=velocity_rule.canonical_sha256,
        verification_sha256=velocity_rule.verification.canonical_sha256,
    )


def _partial_depth_ratio(
    *,
    flow_m3_s: float,
    diameter_m: float,
    slope: float,
    manning_n: float,
) -> float:
    """在上升支上求最浅正常水深，避免接近满流时选择非唯一深水根。"""
    low = 1e-12
    high = 0.9381812161608764
    high_area, high_radius = _circular_section(diameter_m, high)
    high_flow = _manning_discharge(
        area_m2=high_area,
        hydraulic_radius_m=high_radius,
        slope=slope,
        manning_n=manning_n,
    )
    if flow_m3_s > high_flow + HYDRAULIC_TOLERANCE:
        raise HydraulicSolverError("部分充满圆管流量超过最大均匀流能力")
    for _ in range(100):
        middle = (low + high) / 2.0
        area_m2, hydraulic_radius_m = _circular_section(diameter_m, middle)
        discharge = _manning_discharge(
            area_m2=area_m2,
            hydraulic_radius_m=hydraulic_radius_m,
            slope=slope,
            manning_n=manning_n,
        )
        if discharge < flow_m3_s:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _circular_section(diameter_m: float, depth_ratio: float) -> tuple[float, float]:
    if not 0.0 < depth_ratio <= 1.0:
        raise HydraulicSolverError("depth_ratio 必须位于 (0, 1]")
    radius_m = diameter_m / 2.0
    if math.isclose(depth_ratio, 1.0, abs_tol=1e-15):
        return math.pi * radius_m * radius_m, diameter_m / 4.0
    theta = 2.0 * math.acos(1.0 - 2.0 * depth_ratio)
    area_m2 = 0.5 * radius_m * radius_m * (theta - math.sin(theta))
    wetted_perimeter_m = radius_m * theta
    return area_m2, area_m2 / wetted_perimeter_m


def _manning_discharge(
    *,
    area_m2: float,
    hydraulic_radius_m: float,
    slope: float,
    manning_n: float,
) -> float:
    return (
        area_m2
        * hydraulic_radius_m ** (2.0 / 3.0)
        * math.sqrt(slope)
        / manning_n
    )


def _unique_by(items: tuple[Any, ...], field: str, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        identity = str(getattr(item, field))
        if identity in result:
            raise ValueError(f"{label} {field} 重复: {identity!r}")
        result[identity] = item
    return result


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "HYDRAULIC_CALCULATION_MODEL",
    "HYDRAULIC_SOLVER_INPUT_VERSION",
    "HYDRAULIC_SOLVER_NAME",
    "HYDRAULIC_SOLVER_RESULT_VERSION",
    "HYDRAULIC_SOLVER_VERSION",
    "HydraulicComplianceStatus",
    "HydraulicScenarioInput",
    "HydraulicScenarioResult",
    "HydraulicSegmentResult",
    "HydraulicSolveStatus",
    "HydraulicSolverError",
    "HydraulicSolverInput",
    "HydraulicSolverResult",
    "RoughnessInput",
    "SegmentFlowInput",
    "solve_hydraulic_network",
]
