"""市政管网 Solver v0.4：两井一直管重力污水的确定性竖向与水平净距求解。

该切片只解决已知起终点平面坐标和地面标高的一段直管。正 slope 表示沿
start -> end 流向下降。若未指定 start_invert_m，Solver 选择同时满足两端
最小覆土的最浅起点内底标高；若指定，则保留设计输入并用 RuleEvidence
明确报告合规或失败。

碰撞上下文缺失时 ``clash_free`` 失败关闭为 UNKNOWN；调用方显式声明完整上下文后，
Solver 按 GB 50289-2016 的水平净距定义，在 XY 平面计算设计管与建筑投影或既有管
投影的实体表面最短距离。净距限值只能来自带完整规范核验证据的 MunicipalRuleSet；
未获 production 资格或属性不足生成 UNKNOWN。v0.4 不做路线寻优、自动避让或减距例外，
水力能力仍为 UNKNOWN。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from openbimagent.domain_gate import DomainGateReport, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.compiler import compile_solved_utility_ir
from openbimagent.utility.contracts import (
    CompiledUtilityIR,
    Coordinate3D,
    CoordinateReference,
    StrictFrozenModel,
)
from openbimagent.utility.rules import (
    MunicipalRuleSet,
    RuleSelectionStatus,
    compile_municipal_rule_set,
    select_clearance_rule,
)

UTILITY_SOLVER_INPUT_VERSION = "0.4"
UTILITY_SOLVER_NAME = "municipal-straight-gravity-solver"
UTILITY_SOLVER_VERSION = "0.4.0"
MIN_SEWAGE_DIAMETER_MM = 300.0
MIN_DN300_CONCRETE_SLOPE = 0.003
MAX_DN300_TO_DN600_MANHOLE_SPACING_M = 75.0
MIN_COVER_BY_SURFACE_M = {"driveway": 0.7, "sidewalk": 0.6}
CLASH_TOLERANCE_M = 1e-6


class UtilitySolverError(ValueError):
    """Solver 输入或求解结果无法通过确定性门禁。"""


class SolverEndpoint(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    x_m: float
    y_m: float
    ground_elevation_m: float


class AxisAlignedBoxObstacle(StrictFrozenModel):
    """坐标系内闭合三维轴对齐包围盒。"""

    obstacle_id: str = Field(min_length=1, max_length=256)
    kind: Literal["aabb"] = "aabb"
    category: Literal["building"]
    min_corner: Coordinate3D
    max_corner: Coordinate3D

    @model_validator(mode="after")
    def _validate_box(self) -> "AxisAlignedBoxObstacle":
        if not all(
            low < high
            for low, high in (
                (self.min_corner.x_m, self.max_corner.x_m),
                (self.min_corner.y_m, self.max_corner.y_m),
                (self.min_corner.z_m, self.max_corner.z_m),
            )
        ):
            raise ValueError(f"AABB {self.obstacle_id!r} 每个轴必须满足 min < max")
        return self


class ExistingPipeObstacle(StrictFrozenModel):
    """既有直圆管的三维中心线和外径，几何视为胶囊体。"""

    obstacle_id: str = Field(min_length=1, max_length=256)
    kind: Literal["existing_pipe"] = "existing_pipe"
    category: Literal["water", "gas", "power", "telecom"]
    start_center: Coordinate3D
    end_center: Coordinate3D
    outer_diameter_mm: float = Field(gt=0)
    pressure_class: Literal[
        "low",
        "medium_b",
        "medium_a",
        "sub_high_b",
        "sub_high_a",
    ] | None = None
    burial_method: Literal["direct_buried", "protective_conduit", "duct", "tunnel"] | None = None
    voltage_kv: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_centerline(self) -> "ExistingPipeObstacle":
        if _point_distance(self.start_center, self.end_center) <= CLASH_TOLERANCE_M:
            raise ValueError(f"既有管线 {self.obstacle_id!r} 中心线起终点不能重合")
        return self


CollisionObstacle = Annotated[
    AxisAlignedBoxObstacle | ExistingPipeObstacle,
    Field(discriminator="kind"),
]


class CollisionContext(StrictFrozenModel):
    """本次碰撞检查的环境上下文；complete 表示清单覆盖声明范围。"""

    coverage: Literal["complete"] = "complete"
    obstacles: tuple[CollisionObstacle, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "CollisionContext":
        ids = [item.obstacle_id for item in self.obstacles]
        if len(ids) != len(set(ids)):
            raise ValueError("碰撞上下文 obstacle_id 不能重复")
        return self


class StraightGravitySolverInput(StrictFrozenModel):
    """Solver v0.4 的版本化输入；仅支持单一 DN300 混凝土重力污水直管。"""

    protocol_version: str = Field(default=UTILITY_SOLVER_INPUT_VERSION, pattern=r"^0\.4$")
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
    collision_context: CollisionContext | None = None

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
    municipal_rule_set: MunicipalRuleSet | None = None,
    schema_gate: SchemaGate | None = None,
) -> UtilitySolverResult:
    """求解两井一直管并生成 compiled IR、规则证据和 Domain Gate 报告。"""
    gate = schema_gate or SchemaGate()
    try:
        trusted_rule_set = (
            compile_municipal_rule_set(schema_gate=gate)
            if municipal_rule_set is None
            else MunicipalRuleSet.model_validate(municipal_rule_set.model_dump(mode="json"))
        )
        gate.gate_or_fix("municipal_rule_set", trusted_rule_set.model_dump(mode="json"))
        request = (
            solver_input
            if isinstance(solver_input, StraightGravitySolverInput)
            else StraightGravitySolverInput.model_validate(solver_input)
        )
        gate.gate_or_fix("utility_solver_input", request.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise UtilitySolverError(f"Solver v0 输入或 MunicipalRuleSet 未通过门禁: {exc}") from exc

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
        *_clash_evidence(
            request,
            rule_set=trusted_rule_set,
            ir_id=ir_id,
            segment_id=segment_id,
            start_invert_m=start_invert_m,
            end_invert_m=end_invert_m,
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



def _clash_evidence(
    request: StraightGravitySolverInput,
    *,
    rule_set: MunicipalRuleSet,
    ir_id: str,
    segment_id: str,
    start_invert_m: float,
    end_invert_m: float,
) -> list[dict[str, Any]]:
    """对设计管段实体执行逐障碍物净距检查；上下文缺失时返回 UNKNOWN。"""
    context = request.collision_context
    if context is None:
        return [
            _unknown_evidence(
                evidence_id=f"{request.request_id}-clash-context",
                rule_id="MU-AVOID-001",
                check_name="clash_free",
                subject_id=ir_id,
                detail="未提供完整 collision_context，不能证明检查范围内无碰撞或净距不足",
                source_clause="GB 50289-2016 §3.0.4",
            )
        ]

    if not context.obstacles:
        return [
            _evidence(
                evidence_id=f"{request.request_id}-clash-empty",
                rule_id="MU-AVOID-001",
                check_name="clash_free",
                passed=True,
                subject_type="segment",
                subject_id=segment_id,
                detail="collision_context 声明覆盖完整，检查范围内障碍物清单为空",
                measured_value=0.0,
                limit_value=0.0,
                unit="count",
                source_clause="GB 50289-2016 §3.0.4",
            )
        ]

    pipe_radius_m = request.diameter_mm / 2000.0
    design_start = Coordinate3D(
        x_m=request.start.x_m,
        y_m=request.start.y_m,
        z_m=start_invert_m + pipe_radius_m,
    )
    design_end = Coordinate3D(
        x_m=request.end.x_m,
        y_m=request.end.y_m,
        z_m=end_invert_m + pipe_radius_m,
    )
    evidence: list[dict[str, Any]] = []
    for obstacle in sorted(context.obstacles, key=lambda item: item.obstacle_id):
        attributes: dict[str, Any] = {}
        if isinstance(obstacle, AxisAlignedBoxObstacle):
            axis_distance_m = _segment_aabb_horizontal_distance(
                design_start,
                design_end,
                obstacle.min_corner,
                obstacle.max_corner,
            )
            actual_clearance_m = axis_distance_m - pipe_radius_m
            geometry_label = "AABB horizontal projection"
        else:
            axis_distance_m = _segment_segment_horizontal_distance(
                design_start,
                design_end,
                obstacle.start_center,
                obstacle.end_center,
            )
            actual_clearance_m = (
                axis_distance_m
                - pipe_radius_m
                - obstacle.outer_diameter_mm / 2000.0
            )
            geometry_label = "existing_pipe horizontal projection"
            attributes = {
                "outer_diameter_mm": obstacle.outer_diameter_mm,
                "pressure_class": obstacle.pressure_class,
                "burial_method": obstacle.burial_method,
                "voltage_kv": obstacle.voltage_kv,
            }
        selection = select_clearance_rule(
            rule_set,
            obstacle_kind=obstacle.kind,
            obstacle_category=obstacle.category,
            attributes=attributes,
        )
        if selection.status is not RuleSelectionStatus.SELECTED or selection.rule is None:
            evidence.append(
                _unknown_evidence(
                    evidence_id=f"{request.request_id}-clash-{obstacle.obstacle_id}",
                    rule_id=(
                        selection.rule.source_rule_id
                        if selection.rule is not None
                        else "MU-CLEAR-UNRESOLVED"
                    ),
                    check_name="clash_free",
                    subject_id=segment_id,
                    detail=(
                        f"障碍物 {obstacle.obstacle_id!r} 净距规则未获生产执行资格: "
                        f"status={selection.status.value}; {selection.detail}; "
                        f"rule_set_sha256={rule_set.canonical_sha256}"
                    ),
                    source_clause=(
                        selection.rule.source_clause
                        if selection.rule is not None
                        else "municipal rule selection unresolved"
                    ),
                    subject_type="segment",
                )
            )
            continue
        rule = selection.rule
        verification = rule.verification
        required_m = rule.required_clearance_m
        passed = actual_clearance_m + CLASH_TOLERANCE_M >= required_m
        evidence.append(
            _evidence(
                evidence_id=f"{request.request_id}-clash-{obstacle.obstacle_id}",
                rule_id=rule.source_rule_id,
                check_name="clash_free",
                passed=passed,
                subject_type="segment",
                subject_id=segment_id,
                detail=(
                    f"设计管段与 {geometry_label} 障碍物 {obstacle.obstacle_id!r} 的实体表面水平净距 "
                    f"{actual_clearance_m:.6f} m，要求不少于 {required_m:.6f} m；"
                    f"规则 {rule.rule_key} 来自受信任规则集 {rule_set.rule_set_id}@{rule_set.canonical_sha256}；"
                    f"规范 {verification.standard_id} 表 {verification.table}；"
                    f"规范副本 SHA-256={verification.content_sha256}；"
                    f"原表定位={verification.evidence_locator}；"
                    f"数值容差 {CLASH_TOLERANCE_M:g} m"
                ),
                measured_value=actual_clearance_m,
                limit_value=required_m,
                unit="m",
                source_clause=rule.source_clause,
            )
        )
    return evidence



def _segment_aabb_horizontal_distance(
    start: Coordinate3D,
    end: Coordinate3D,
    minimum: Coordinate3D,
    maximum: Coordinate3D,
) -> float:
    """返回 XY 平面中设计中心线到闭合建筑投影矩形的精确距离。"""
    p0 = (start.x_m, start.y_m)
    p1 = (end.x_m, end.y_m)
    low = (minimum.x_m, minimum.y_m)
    high = (maximum.x_m, maximum.y_m)
    direction = tuple(right - left for left, right in zip(p0, p1, strict=True))
    breaks = {0.0, 1.0}
    for origin, delta, axis_low, axis_high in zip(p0, direction, low, high, strict=True):
        if abs(delta) <= 1e-15:
            continue
        for boundary in (axis_low, axis_high):
            crossing = (boundary - origin) / delta
            if 0.0 < crossing < 1.0:
                breaks.add(crossing)
    ordered = sorted(breaks)

    candidates = set(ordered)
    for left, right in zip(ordered, ordered[1:], strict=False):
        midpoint = (left + right) / 2.0
        numerator = 0.0
        denominator = 0.0
        for origin, delta, axis_low, axis_high in zip(p0, direction, low, high, strict=True):
            coordinate = origin + delta * midpoint
            if coordinate < axis_low:
                boundary = axis_low
            elif coordinate > axis_high:
                boundary = axis_high
            else:
                continue
            numerator += delta * (origin - boundary)
            denominator += delta * delta
        if denominator > 0.0:
            stationary = -numerator / denominator
            if left <= stationary <= right:
                candidates.add(stationary)

    minimum_squared = min(
        _point_aabb_distance_squared(
            tuple(origin + delta * parameter for origin, delta in zip(p0, direction, strict=True)),
            low,
            high,
        )
        for parameter in candidates
    )
    return math.sqrt(max(0.0, minimum_squared))



def _point_aabb_distance_squared(
    point: tuple[float, ...],
    minimum: tuple[float, ...],
    maximum: tuple[float, ...],
) -> float:
    total = 0.0
    for value, low, high in zip(point, minimum, maximum, strict=True):
        if value < low:
            total += (low - value) ** 2
        elif value > high:
            total += (value - high) ** 2
    return total



def _segment_segment_horizontal_distance(
    first_start: Coordinate3D,
    first_end: Coordinate3D,
    second_start: Coordinate3D,
    second_end: Coordinate3D,
) -> float:
    """返回 XY 平面中两条闭线段的精确最短距离。"""
    p1 = (first_start.x_m, first_start.y_m)
    q1 = (first_end.x_m, first_end.y_m)
    p2 = (second_start.x_m, second_start.y_m)
    q2 = (second_end.x_m, second_end.y_m)
    d1 = _subtract(q1, p1)
    d2 = _subtract(q2, p2)
    offset = _subtract(p1, p2)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    if a <= 1e-15 and e <= 1e-15:
        return math.sqrt(max(0.0, _dot(offset, offset)))
    if a <= 1e-15:
        return _point_segment_distance_2d(p1, p2, q2)
    if e <= 1e-15:
        return _point_segment_distance_2d(p2, p1, q1)
    b = _dot(d1, d2)
    c = _dot(d1, offset)
    f = _dot(d2, offset)
    denominator = a * e - b * b
    if denominator > 1e-15:
        first_parameter = _clamp((b * f - c * e) / denominator)
    else:
        first_parameter = 0.0
    second_parameter = (b * first_parameter + f) / e
    if second_parameter < 0.0:
        second_parameter = 0.0
        first_parameter = _clamp(-c / a)
    elif second_parameter > 1.0:
        second_parameter = 1.0
        first_parameter = _clamp((b - c) / a)
    closest_first = _add_scaled(p1, d1, first_parameter)
    closest_second = _add_scaled(p2, d2, second_parameter)
    delta = _subtract(closest_first, closest_second)
    return math.sqrt(max(0.0, _dot(delta, delta)))



def _point_segment_distance_2d(
    point: tuple[float, ...],
    start: tuple[float, ...],
    end: tuple[float, ...],
) -> float:
    direction = _subtract(end, start)
    denominator = _dot(direction, direction)
    if denominator <= 1e-15:
        delta = _subtract(point, start)
        return math.sqrt(max(0.0, _dot(delta, delta)))
    parameter = _clamp(_dot(_subtract(point, start), direction) / denominator)
    delta = _subtract(point, _add_scaled(start, direction, parameter))
    return math.sqrt(max(0.0, _dot(delta, delta)))



def _point_distance(left: Coordinate3D, right: Coordinate3D) -> float:
    delta = _subtract(_point_tuple(left), _point_tuple(right))
    return math.sqrt(_dot(delta, delta))



def _point_tuple(point: Coordinate3D) -> tuple[float, float, float]:
    return point.x_m, point.y_m, point.z_m



def _subtract(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple(a - b for a, b in zip(left, right, strict=True))



def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))



def _add_scaled(
    origin: tuple[float, ...],
    direction: tuple[float, ...],
    parameter: float,
) -> tuple[float, ...]:
    return tuple(
        value + parameter * delta
        for value, delta in zip(origin, direction, strict=True)
    )



def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))



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
    subject_type: str = "network",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "rule_id": rule_id,
        "check_name": check_name,
        "status": "unknown",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "detail": detail,
        "measured_value": None,
        "limit_value": True,
        "unit": None,
        "source_clause": source_clause,
    }


__all__ = [
    "AxisAlignedBoxObstacle",
    "CLASH_TOLERANCE_M",
    "CollisionContext",
    "ExistingPipeObstacle",
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
