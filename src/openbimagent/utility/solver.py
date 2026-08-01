"""市政管网 Solver v0：两井一直管重力污水的确定性竖向求解。

该切片只解决已知起终点平面坐标和地面标高的一段直管。正 slope 表示沿
start -> end 流向下降。若未指定 start_invert_m，Solver 选择同时满足两端
最小覆土的最浅起点内底标高；若指定，则保留设计输入并用 RuleEvidence
明确报告合规或失败。碰撞和水力能力不在 v0 内，始终产生 UNKNOWN 证据。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from openbimagent.domain_gate import DomainGateReport, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.compiler import compile_solved_utility_ir
from openbimagent.utility.contracts import (
    CompiledUtilityIR,
    CoordinateReference,
    StrictFrozenModel,
)

UTILITY_SOLVER_INPUT_VERSION = "0.1"
UTILITY_SOLVER_NAME = "municipal-straight-gravity-solver"
UTILITY_SOLVER_VERSION = "0.1.0"
MIN_SEWAGE_DIAMETER_MM = 300.0
MIN_DN300_CONCRETE_SLOPE = 0.003
MAX_DN300_TO_DN600_MANHOLE_SPACING_M = 75.0
MIN_COVER_BY_SURFACE_M = {"driveway": 0.7, "sidewalk": 0.6}


class UtilitySolverError(ValueError):
    """Solver 输入或求解结果无法通过确定性门禁。"""


class SolverEndpoint(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    x_m: float
    y_m: float
    ground_elevation_m: float


class StraightGravitySolverInput(StrictFrozenModel):
    """Solver v0 的版本化输入；仅支持单一 DN300 混凝土重力污水直管。"""

    protocol_version: str = Field(default=UTILITY_SOLVER_INPUT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_reference: CoordinateReference
    start: SolverEndpoint
    end: SolverEndpoint
    diameter_mm: float = Field(default=MIN_SEWAGE_DIAMETER_MM, gt=0)
    material: Literal["concrete"] = "concrete"
    design_slope: float = Field(default=MIN_DN300_CONCRETE_SLOPE, ge=0)
    surface_context: Literal["driveway", "sidewalk"] = "driveway"
    start_invert_m: float | None = None

    @model_validator(mode="after")
    def _validate_supported_slice(self) -> "StraightGravitySolverInput":
        if self.start.node_id == self.end.node_id:
            raise ValueError("Solver v0 起终 node_id 不能相同")
        length = math.hypot(self.end.x_m - self.start.x_m, self.end.y_m - self.start.y_m)
        if length <= 0:
            raise ValueError("Solver v0 起终点平面位置不能相同")
        if not math.isclose(self.diameter_mm, MIN_SEWAGE_DIAMETER_MM, abs_tol=1e-9):
            raise ValueError("Solver v0 仅支持 DN300 污水管")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class UtilitySolverResult(StrictFrozenModel):
    compiled_ir: CompiledUtilityIR
    domain_gate: DomainGateReport



def solve_straight_gravity_utility(
    solver_input: StraightGravitySolverInput | dict[str, Any],
    *,
    domain_requirements: dict[str, Any] | None = None,
    schema_gate: SchemaGate | None = None,
) -> UtilitySolverResult:
    """求解两井一直管并生成 compiled IR、规则证据和 Domain Gate 报告。"""
    gate = schema_gate or SchemaGate()
    try:
        request = (
            solver_input
            if isinstance(solver_input, StraightGravitySolverInput)
            else StraightGravitySolverInput.model_validate(solver_input)
        )
        gate.gate_or_fix("utility_solver_input", request.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise UtilitySolverError(f"Solver v0 输入未通过门禁: {exc}") from exc

    length_m = math.hypot(request.end.x_m - request.start.x_m, request.end.y_m - request.start.y_m)
    diameter_m = request.diameter_mm / 1000.0
    required_cover_m = MIN_COVER_BY_SURFACE_M[request.surface_context]
    start_limit_m = request.start.ground_elevation_m - required_cover_m - diameter_m
    end_limit_as_start_m = (
        request.end.ground_elevation_m - required_cover_m - diameter_m + request.design_slope * length_m
    )
    start_invert_m = (
        min(start_limit_m, end_limit_as_start_m)
        if request.start_invert_m is None
        else request.start_invert_m
    )
    end_invert_m = start_invert_m - request.design_slope * length_m
    start_cover_m = request.start.ground_elevation_m - (start_invert_m + diameter_m)
    end_cover_m = request.end.ground_elevation_m - (end_invert_m + diameter_m)
    minimum_actual_cover_m = min(start_cover_m, end_cover_m)

    ir_id = f"utility-{request.request_id}"
    system_id = f"{request.request_id}-wastewater"
    segment_id = f"{request.request_id}-pipe-001"
    start_port_id = f"{request.start.node_id}-out"
    end_port_id = f"{request.end.node_id}-in"

    evidence = [
        _evidence(
            evidence_id=f"{request.request_id}-diameter",
            rule_id="MU-DRAIN-001",
            check_name="diameter_in_spec",
            passed=request.diameter_mm >= MIN_SEWAGE_DIAMETER_MM,
            subject_type="segment",
            subject_id=segment_id,
            detail=(
                f"污水管径 DN{request.diameter_mm:g} mm，最小允许 DN{MIN_SEWAGE_DIAMETER_MM:g} mm"
            ),
            measured_value=request.diameter_mm,
            limit_value=MIN_SEWAGE_DIAMETER_MM,
            unit="mm",
            source_clause="GB 50014-2021 §5.2.10 表 5.2.10",
        ),
        _evidence(
            evidence_id=f"{request.request_id}-slope",
            rule_id="MU-DRAIN-004",
            check_name="slope_in_spec",
            passed=request.design_slope >= MIN_DN300_CONCRETE_SLOPE,
            subject_type="segment",
            subject_id=segment_id,
            detail=(
                f"DN300 混凝土污水管设计坡度 {request.design_slope:.6f}，"
                f"最小允许 {MIN_DN300_CONCRETE_SLOPE:.6f}"
            ),
            measured_value=request.design_slope,
            limit_value=MIN_DN300_CONCRETE_SLOPE,
            unit="ratio",
            source_clause="GB 50014-2021 §5.2.10 表 5.2.10",
        ),
        _evidence(
            evidence_id=f"{request.request_id}-cover",
            rule_id="MU-ELEV-001" if request.surface_context == "driveway" else "MU-ELEV-002",
            check_name="cover_depth_in_spec",
            passed=minimum_actual_cover_m >= required_cover_m - 1e-9,
            subject_type="segment",
            subject_id=segment_id,
            detail=(
                f"两端最小实际覆土 {minimum_actual_cover_m:.6f} m，"
                f"{request.surface_context} 要求不少于 {required_cover_m:.3f} m"
            ),
            measured_value=minimum_actual_cover_m,
            limit_value=required_cover_m,
            unit="m",
            source_clause="GB 50014-2021 §5.3.7",
        ),
        _evidence(
            evidence_id=f"{request.request_id}-spacing",
            rule_id="MU-WELL-001",
            check_name="manhole_spacing_in_spec",
            passed=length_m <= MAX_DN300_TO_DN600_MANHOLE_SPACING_M,
            subject_type="segment",
            subject_id=segment_id,
            detail=(
                f"DN300 管段井距 {length_m:.6f} m，最大允许 "
                f"{MAX_DN300_TO_DN600_MANHOLE_SPACING_M:.1f} m"
            ),
            measured_value=length_m,
            limit_value=MAX_DN300_TO_DN600_MANHOLE_SPACING_M,
            unit="m",
            source_clause="GB 50014-2021 §5.4.4 表 5.4.4",
        ),
        _unknown_evidence(
            evidence_id=f"{request.request_id}-clash",
            rule_id="MU-AVOID-001",
            check_name="clash_free",
            subject_id=ir_id,
            detail="Solver v0 未接收其他管线、基础或障碍物几何，不能执行碰撞检查",
            source_clause="GB 50289-2016 §3.0.4",
        ),
        _unknown_evidence(
            evidence_id=f"{request.request_id}-hydraulics",
            rule_id="MU-DRAIN-007",
            check_name="hydraulics_in_spec",
            subject_id=ir_id,
            detail="Solver v0 未接收设计流量、粗糙系数或充满度，不能执行水力校核",
            source_clause="GB 50014-2021 §5.2.7",
        ),
    ]

    payload = {
        "protocol_version": "1.0",
        "ir_id": ir_id,
        "source_ir_sha256": request.source_ir_sha256,
        "solver_name": UTILITY_SOLVER_NAME,
        "solver_version": UTILITY_SOLVER_VERSION,
        "coordinate_reference": request.coordinate_reference.model_dump(mode="json"),
        "systems": [
            {
                "system_id": system_id,
                "name": "污水重力系统",
                "system_type": "wastewater",
                "flow_regime": "gravity",
                "ifc_class": "IfcDistributionSystem",
                "ifc_predefined_type": "WASTEWATER",
            }
        ],
        "nodes": [
            _node_payload(
                request.start,
                system_id=system_id,
                port_id=start_port_id,
                direction="outlet",
                invert_m=start_invert_m,
            ),
            _node_payload(
                request.end,
                system_id=system_id,
                port_id=end_port_id,
                direction="inlet",
                invert_m=end_invert_m,
            ),
        ],
        "segments": [
            {
                "segment_id": segment_id,
                "system_id": system_id,
                "start_port_id": start_port_id,
                "end_port_id": end_port_id,
                "centerline": [
                    {"x_m": request.start.x_m, "y_m": request.start.y_m, "z_m": start_invert_m},
                    {"x_m": request.end.x_m, "y_m": request.end.y_m, "z_m": end_invert_m},
                ],
                "horizontal_length_m": length_m,
                "start_invert_m": start_invert_m,
                "end_invert_m": end_invert_m,
                "slope": request.design_slope,
                "diameter_mm": request.diameter_mm,
                "material": request.material,
                "min_cover_depth_m": minimum_actual_cover_m,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            }
        ],
        "evidence": evidence,
    }
    compiled = compile_solved_utility_ir(payload, schema_gate=gate)
    requirements = domain_requirements or {
        "diameter_in_spec": True,
        "slope_in_spec": True,
        "cover_depth_in_spec": True,
        "manhole_spacing_in_spec": True,
        "clash_free": True,
    }
    report = evaluate_domain_gate(requirements, compiled.domain_evidence())
    return UtilitySolverResult(compiled_ir=compiled, domain_gate=report)



def _node_payload(
    endpoint: SolverEndpoint,
    *,
    system_id: str,
    port_id: str,
    direction: str,
    invert_m: float,
) -> dict[str, Any]:
    return {
        "node_id": endpoint.node_id,
        "system_id": system_id,
        "node_type": "manhole",
        "position": {"x_m": endpoint.x_m, "y_m": endpoint.y_m, "z_m": endpoint.ground_elevation_m},
        "ports": [
            {
                "port_id": port_id,
                "direction": direction,
                "position": {"x_m": endpoint.x_m, "y_m": endpoint.y_m, "z_m": invert_m},
                "ifc_class": "IfcDistributionPort",
            }
        ],
        "ground_elevation_m": endpoint.ground_elevation_m,
        "ifc_class": "IfcDistributionChamberElement",
        "ifc_predefined_type": "MANHOLE",
    }



def _evidence(
    *,
    evidence_id: str,
    rule_id: str,
    check_name: str,
    passed: bool,
    subject_type: str,
    subject_id: str,
    detail: str,
    measured_value: float,
    limit_value: float,
    unit: str,
    source_clause: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "rule_id": rule_id,
        "check_name": check_name,
        "status": "pass" if passed else "fail",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "detail": detail,
        "measured_value": measured_value,
        "limit_value": limit_value,
        "unit": unit,
        "source_clause": source_clause,
    }



def _unknown_evidence(
    *,
    evidence_id: str,
    rule_id: str,
    check_name: str,
    subject_id: str,
    detail: str,
    source_clause: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "rule_id": rule_id,
        "check_name": check_name,
        "status": "unknown",
        "subject_type": "network",
        "subject_id": subject_id,
        "detail": detail,
        "measured_value": None,
        "limit_value": True,
        "unit": None,
        "source_clause": source_clause,
    }


__all__ = [
    "MIN_COVER_BY_SURFACE_M",
    "MIN_DN300_CONCRETE_SLOPE",
    "MIN_SEWAGE_DIAMETER_MM",
    "StraightGravitySolverInput",
    "SolverEndpoint",
    "UTILITY_SOLVER_INPUT_VERSION",
    "UTILITY_SOLVER_NAME",
    "UTILITY_SOLVER_VERSION",
    "UtilitySolverError",
    "UtilitySolverResult",
    "solve_straight_gravity_utility",
]
