"""M1.5 T4 受约束规则网格路线与纵断面 Solver v0.1。

路线只在调用方显式批准的网格走廊内搜索。障碍物净距只能来自与输入 SHA-256
绑定且具备 production 资格的 MunicipalRuleSet。搜索使用四邻接、固定代价和稳定
字典序 tie-break；地表标高逐 cell 显式给出，管底按累计路线长度和设计坡度传播。
无可行路线返回结构化结果；协议漂移、规则未晋级或身份不匹配则失败关闭。
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import Field, ValidationError, model_validator

from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.contracts import CoordinateReference, StrictFrozenModel
from openbimagent.utility.network_solver import NetworkGravitySolverInput
from openbimagent.utility.rule_evidence import (
    ClearanceExceptionApproval,
    EvidenceRuleSelectionStatus,
    MunicipalRuleEvidenceBundle,
    RuleDecisionStatus,
    RuleType,
    evaluate_municipal_rule,
    select_municipal_rule,
)
from openbimagent.utility.rules import (
    MunicipalRuleSet,
    RuleSelectionStatus,
    compile_municipal_rule_set,
    select_clearance_rule,
)
from openbimagent.utility.solver import (
    CLASH_TOLERANCE_M,
    MIN_COVER_BY_SURFACE_M,
    MIN_DN300_CONCRETE_SLOPE,
    MIN_SEWAGE_DIAMETER_MM,
    AxisAlignedBoxObstacle,
    ExistingPipeObstacle,
    UtilitySolverError,
)

GRID_ROUTE_SOLVER_INPUT_VERSION = "0.1"
GRID_ROUTE_SOLVER_RESULT_VERSION = "0.1"
GRID_ROUTE_SOLVER_NAME = "municipal-grid-route-solver"
GRID_ROUTE_SOLVER_VERSION = "0.1.0"
_GRID_TOLERANCE_M = 1e-9


class RouteSolverError(UtilitySolverError):
    """路线协议、可信规则或调用关系未通过失败关闭门禁。"""


class RouteSolveStatus(StrEnum):
    FEASIBLE = "feasible"
    NO_FEASIBLE_ROUTE = "no_feasible_route"
    UNKNOWN = "unknown"


class NoFeasibleRouteReason(StrEnum):
    CORRIDOR_DISCONNECTED = "corridor_disconnected"
    OBSTACLE_BLOCKED = "obstacle_blocked"
    COVER_CONFLICT = "cover_conflict"
    SEARCH_LIMIT_EXCEEDED = "search_limit_exceeded"


class GridCell(StrictFrozenModel):
    x_index: int = Field(ge=0)
    y_index: int = Field(ge=0)

    def identity(self) -> tuple[int, int]:
        return self.x_index, self.y_index


class RouteGrid(StrictFrozenModel):
    origin_x_m: float
    origin_y_m: float
    resolution_m: float = Field(gt=0)
    width: int = Field(ge=1, le=1000)
    height: int = Field(ge=1, le=1000)

    def contains(self, cell: GridCell) -> bool:
        return cell.x_index < self.width and cell.y_index < self.height

    def xy(self, cell: GridCell) -> tuple[float, float]:
        return (
            self.origin_x_m + cell.x_index * self.resolution_m,
            self.origin_y_m + cell.y_index * self.resolution_m,
        )


class RouteEndpoint(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    cell: GridCell
    invert_anchor_m: float | None = None


class SurfaceSample(StrictFrozenModel):
    cell: GridCell
    ground_elevation_m: float


class GridRouteSolverInput(StrictFrozenModel):
    protocol_version: str = Field(default=GRID_ROUTE_SOLVER_INPUT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    municipal_rule_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_reference: CoordinateReference
    grid: RouteGrid
    start: RouteEndpoint
    end: RouteEndpoint
    allowed_cells: tuple[GridCell, ...] = Field(min_length=2, max_length=10000)
    surface_samples: tuple[SurfaceSample, ...] = Field(min_length=2, max_length=10000)
    obstacles: tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, ...] = ()
    diameter_mm: float = Field(default=MIN_SEWAGE_DIAMETER_MM, gt=0)
    material: Literal["concrete"] = "concrete"
    design_slope: float = Field(default=MIN_DN300_CONCRETE_SLOPE, ge=0)
    surface_context: Literal["driveway", "sidewalk"] = "driveway"
    max_candidates: int = Field(default=3, ge=1, le=10)
    max_search_expansions: int = Field(default=100000, ge=1, le=250000)

    @model_validator(mode="after")
    def _validate_route_input(self) -> "GridRouteSolverInput":
        if self.start.node_id == self.end.node_id:
            raise ValueError("路线起终 node_id 不能相同")
        if self.start.cell == self.end.cell:
            raise ValueError("路线起终 cell 不能相同")
        if self.start.invert_anchor_m is None:
            raise ValueError("路线起点必须提供 invert_anchor_m")
        if self.end.invert_anchor_m is not None:
            raise ValueError("grid route Solver v0.1 不接受终点 invert_anchor_m，避免静默忽略纵向约束")
        if not math.isclose(self.diameter_mm, MIN_SEWAGE_DIAMETER_MM, abs_tol=_GRID_TOLERANCE_M):
            raise ValueError("grid route Solver v0.1 仅支持 DN300")

        for label, endpoint in (("start", self.start), ("end", self.end)):
            if not self.grid.contains(endpoint.cell):
                raise ValueError(f"{label} cell {endpoint.cell.identity()} 超出网格范围")

        allowed = _unique_cells(self.allowed_cells, "allowed_cells")
        for identity, cell in allowed.items():
            if not self.grid.contains(cell):
                raise ValueError(f"allowed cell {identity} 超出网格范围")
        endpoint_cells = {self.start.cell.identity(), self.end.cell.identity()}
        if not endpoint_cells.issubset(allowed):
            raise ValueError("路线起终点必须都位于批准走廊 allowed_cells")

        samples: dict[tuple[int, int], SurfaceSample] = {}
        for sample in self.surface_samples:
            identity = sample.cell.identity()
            if identity in samples:
                raise ValueError(f"surface_samples cell 重复: {identity}")
            if not self.grid.contains(sample.cell):
                raise ValueError(f"surface sample {identity} 超出网格范围")
            samples[identity] = sample
        missing = sorted(set(allowed) - set(samples))
        extra = sorted(set(samples) - set(allowed))
        if missing:
            raise ValueError(f"批准走廊地表高程 sample 缺失: {missing}")
        if extra:
            raise ValueError(f"surface_samples 包含走廊外 cell: {extra}")

        obstacle_ids = [item.obstacle_id for item in self.obstacles]
        if len(obstacle_ids) != len(set(obstacle_ids)):
            raise ValueError("route obstacles obstacle_id 不能重复")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["allowed_cells"] = sorted(
            payload["allowed_cells"], key=lambda item: (item["x_index"], item["y_index"])
        )
        payload["surface_samples"] = sorted(
            payload["surface_samples"],
            key=lambda item: (item["cell"]["x_index"], item["cell"]["y_index"]),
        )
        payload["obstacles"] = sorted(payload["obstacles"], key=lambda item: item["obstacle_id"])
        return payload

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.canonical_dict())


class RoutePoint(StrictFrozenModel):
    cell: GridCell
    x_m: float
    y_m: float
    ground_elevation_m: float
    invert_m: float
    cover_depth_m: float


class T6RouteObstacleConstraint(StrictFrozenModel):
    obstacle_id: str = Field(min_length=1, max_length=256)
    rule_id: str = Field(min_length=1, max_length=256)
    rule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_clearance_m: float = Field(gt=0)
    effective_clearance_m: float = Field(gt=0)
    exception_approval_id: str | None = Field(default=None, min_length=1, max_length=256)
    exception_approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_approval_pair(self) -> "T6RouteObstacleConstraint":
        if (self.exception_approval_id is None) is not (self.exception_approval_sha256 is None):
            raise ValueError("T6 route 例外审批 ID/SHA 必须同时存在或同时缺失")
        reduced = self.effective_clearance_m < self.original_clearance_m - _GRID_TOLERANCE_M
        unchanged = math.isclose(
            self.effective_clearance_m,
            self.original_clearance_m,
            rel_tol=0.0,
            abs_tol=_GRID_TOLERANCE_M,
        )
        if not reduced and not unchanged:
            raise ValueError("T6 route effective_clearance_m 不得高于原规则净距")
        if reduced and self.exception_approval_id is None:
            raise ValueError("T6 route 减距结果必须绑定例外审批 ID/SHA")
        if unchanged and self.exception_approval_id is not None:
            raise ValueError("T6 route 未发生减距时不得携带例外审批身份")
        return self


class RouteConstraintReport(StrictFrozenModel):
    cover_depth_in_spec: bool
    clearance_in_spec: bool
    minimum_cover_depth_m: float
    required_cover_depth_m: float
    minimum_clearance_margin_m: float | None = None
    applied_rule_keys: tuple[str, ...] = ()
    municipal_rule_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RouteCandidate(StrictFrozenModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    rank: int = Field(ge=1)
    cells: tuple[GridCell, ...] = Field(min_length=2)
    points: tuple[RoutePoint, ...] = Field(min_length=2)
    horizontal_length_m: float = Field(gt=0)
    turn_count: int = Field(ge=0)
    constraint_report: RouteConstraintReport

    @model_validator(mode="after")
    def _validate_points(self) -> "RouteCandidate":
        if len(self.cells) != len(self.points):
            raise ValueError("RouteCandidate cells 与 points 数量必须一致")
        if any(cell != point.cell for cell, point in zip(self.cells, self.points, strict=True)):
            raise ValueError("RouteCandidate cells 与 points cell 必须逐项一致")
        return self


class GridRouteSolverResult(StrictFrozenModel):
    protocol_version: str = Field(default=GRID_ROUTE_SOLVER_RESULT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    solver_name: str = Field(default=GRID_ROUTE_SOLVER_NAME, pattern=r"^municipal-grid-route-solver$")
    solver_version: str = Field(default=GRID_ROUTE_SOLVER_VERSION, pattern=r"^0\.1\.0$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    municipal_rule_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RouteSolveStatus
    candidates: tuple[RouteCandidate, ...] = ()
    selected_candidate_id: str | None = None
    failure_reason: NoFeasibleRouteReason | None = None
    detail: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _validate_result(self) -> "GridRouteSolverResult":
        if self.status is RouteSolveStatus.FEASIBLE:
            if not self.candidates or self.selected_candidate_id is None or self.failure_reason is not None:
                raise ValueError("feasible 路线结果必须包含候选和 selected_candidate_id，且不得有 failure_reason")
            if self.selected_candidate_id not in {item.candidate_id for item in self.candidates}:
                raise ValueError("selected_candidate_id 未引用 candidates")
        elif self.candidates or self.selected_candidate_id is not None or self.failure_reason is None:
            raise ValueError("非 feasible 路线结果不得携带候选，且必须有 failure_reason")
        elif (
            self.status is RouteSolveStatus.UNKNOWN
            and self.failure_reason is not NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
        ):
            raise ValueError("unknown 当前仅用于 search_limit_exceeded")
        elif (
            self.status is RouteSolveStatus.NO_FEASIBLE_ROUTE
            and self.failure_reason is NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
        ):
            raise ValueError("search_limit_exceeded 不得包装为 no_feasible_route")
        return self

    def selected_candidate(self) -> RouteCandidate:
        if self.status is not RouteSolveStatus.FEASIBLE or self.selected_candidate_id is None:
            raise RouteSolverError(f"路线结果不可选择候选: status={self.status.value}")
        return next(item for item in self.candidates if item.candidate_id == self.selected_candidate_id)

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["candidates"] = sorted(payload["candidates"], key=lambda item: item["rank"])
        return payload

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.canonical_dict())


class T6GridRouteSolverResult(StrictFrozenModel):
    route_result: GridRouteSolverResult
    rule_evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    obstacle_constraints: tuple[T6RouteObstacleConstraint, ...]

    @model_validator(mode="after")
    def _validate_t6_route_result(self) -> "T6GridRouteSolverResult":
        obstacle_ids = [item.obstacle_id for item in self.obstacle_constraints]
        if obstacle_ids != sorted(obstacle_ids) or len(obstacle_ids) != len(set(obstacle_ids)):
            raise ValueError("T6 route obstacle_constraints 必须按 obstacle_id 排序且不得重复")
        if any(
            item.rule_evidence_bundle_sha256 != self.rule_evidence_bundle_sha256
            for item in self.obstacle_constraints
        ):
            raise ValueError("T6 route 规则决策与规则包 SHA-256 不一致")
        return self


def solve_grid_route(
    solver_input: GridRouteSolverInput | dict[str, Any],
    *,
    municipal_rule_set: MunicipalRuleSet | None = None,
    schema_gate: SchemaGate | None = None,
    _t6_obstacle_constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...] | None = None,
) -> GridRouteSolverResult:
    """求解批准走廊中的稳定四邻接路线与逐 cell 纵断面。"""
    gate = schema_gate or SchemaGate()
    try:
        rule_set = (
            compile_municipal_rule_set(schema_gate=gate)
            if municipal_rule_set is None
            else MunicipalRuleSet.model_validate(municipal_rule_set.model_dump(mode="json"))
        )
        gate.gate_or_fix("municipal_rule_set", rule_set.model_dump(mode="json"))
        request = (
            solver_input
            if isinstance(solver_input, GridRouteSolverInput)
            else GridRouteSolverInput.model_validate(solver_input)
        )
        gate.gate_or_fix("grid_route_solver_input", request.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise RouteSolverError(f"grid route Solver v0.1 输入或 MunicipalRuleSet 未通过门禁: {exc}") from exc

    if request.municipal_rule_set_sha256 != rule_set.canonical_sha256:
        raise RouteSolverError(
            "输入绑定的 MunicipalRuleSet SHA-256 与实际规则集不一致: "
            f"input={request.municipal_rule_set_sha256}, actual={rule_set.canonical_sha256}"
        )

    obstacle_constraints = (
        _compile_obstacle_constraints(request, rule_set)
        if _t6_obstacle_constraints is None
        else _t6_obstacle_constraints
    )
    allowed = {cell.identity() for cell in request.allowed_cells}
    start = request.start.cell.identity()
    end = request.end.cell.identity()
    planar_without_obstacles, limit_without = _search_route(
        request,
        allowed=allowed,
        start=start,
        end=end,
        obstacle_constraints=(),
        enforce_cover=False,
    )
    if planar_without_obstacles is None:
        reason = (
            NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
            if limit_without
            else NoFeasibleRouteReason.CORRIDOR_DISCONNECTED
        )
        return _gated_result(
            gate,
            _failed_result(request, rule_set, reason, "批准走廊内起终点不连通"),
        )

    planar_with_obstacles, limit_obstacle = _search_route(
        request,
        allowed=allowed,
        start=start,
        end=end,
        obstacle_constraints=obstacle_constraints,
        enforce_cover=False,
    )
    if planar_with_obstacles is None:
        reason = (
            NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
            if limit_obstacle
            else NoFeasibleRouteReason.OBSTACLE_BLOCKED
        )
        return _gated_result(
            gate,
            _failed_result(request, rule_set, reason, "可信净距规则膨胀后的障碍物封堵批准走廊"),
        )

    paths, limit_cover = _search_routes(
        request,
        allowed=allowed,
        start=start,
        end=end,
        obstacle_constraints=obstacle_constraints,
        enforce_cover=True,
        max_results=request.max_candidates,
    )
    if not paths:
        reason = (
            NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
            if limit_cover
            else NoFeasibleRouteReason.COVER_CONFLICT
        )
        return _gated_result(
            gate,
            _failed_result(request, rule_set, reason, "平面路线存在，但显式地表标高与连续坡度无法满足覆土"),
        )

    candidates = tuple(
        _build_candidate(
            request,
            rule_set,
            path,
            obstacle_constraints,
            rank=rank,
        )
        for rank, path in enumerate(paths, start=1)
    )
    result = GridRouteSolverResult(
        request_id=request.request_id,
        input_sha256=request.canonical_sha256(),
        municipal_rule_set_sha256=rule_set.canonical_sha256,
        status=RouteSolveStatus.FEASIBLE,
        candidates=candidates,
        selected_candidate_id=candidates[0].candidate_id,
        detail=(
            "在批准网格走廊内找到确定性路线；候选按水平长度、转折数和完整 cell 序列稳定排序。"
        ),
    )
    return _gated_result(gate, result)


def solve_grid_route_t6(
    solver_input: GridRouteSolverInput | dict[str, Any],
    *,
    rule_evidence_bundle: MunicipalRuleEvidenceBundle,
    project_id: str,
    evaluated_at: datetime,
    exception_approvals: Mapping[str, ClearanceExceptionApproval] | None = None,
    municipal_rule_set: MunicipalRuleSet | None = None,
    schema_gate: SchemaGate | None = None,
) -> T6GridRouteSolverResult:
    """使用 T6 规则包和按 obstacle_id 显式给出的减距审批求解路线。"""
    try:
        request = (
            solver_input
            if isinstance(solver_input, GridRouteSolverInput)
            else GridRouteSolverInput.model_validate(solver_input)
        )
        bundle = MunicipalRuleEvidenceBundle.model_validate(
            rule_evidence_bundle.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise RouteSolverError(f"T6 route 输入或规则包未通过门禁: {exc}") from exc
    approvals = dict(exception_approvals or {})
    obstacle_ids = {item.obstacle_id for item in request.obstacles}
    unknown_approvals = sorted(set(approvals) - obstacle_ids)
    if unknown_approvals:
        raise RouteSolverError(f"T6 route 审批引用未知 obstacle_id: {unknown_approvals}")

    compiled: list[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str]] = []
    decisions: list[T6RouteObstacleConstraint] = []
    pipe_radius_m = request.diameter_mm / 2000.0
    for obstacle in sorted(request.obstacles, key=lambda item: item.obstacle_id):
        rule_type, facts = _t6_obstacle_facts(obstacle)
        approval = approvals.get(obstacle.obstacle_id)
        selection, evaluation = evaluate_municipal_rule(
            bundle,
            evaluation_id=f"{request.request_id}:{obstacle.obstacle_id}:clearance",
            rule_type=rule_type,
            facts=facts,
            subject_type="segment",
            subject_id=obstacle.obstacle_id,
            measured_value=(
                approval.approved_clearance_m if approval is not None else _selected_rule_value(
                    bundle,
                    rule_type=rule_type,
                    facts=facts,
                    obstacle_id=obstacle.obstacle_id,
                )
            ),
            project_id=project_id,
            evaluated_at=evaluated_at,
            exception_approval=approval,
        )
        if (
            selection.status is not EvidenceRuleSelectionStatus.SELECTED
            or selection.rule is None
            or evaluation is None
            or evaluation.status is not RuleDecisionStatus.PASS
        ):
            raise RouteSolverError(
                f"障碍物 {obstacle.obstacle_id!r} T6 净距规则未获生产执行资格: "
                f"selection={selection.status.value}; {selection.detail}"
            )
        if isinstance(selection.rule.value, bool) or not isinstance(selection.rule.value, int | float):
            raise RouteSolverError(f"障碍物 {obstacle.obstacle_id!r} T6 净距规则不是数值")
        effective_clearance_m = float(evaluation.limit_value)
        obstacle_radius_m = (
            obstacle.outer_diameter_mm / 2000.0
            if isinstance(obstacle, ExistingPipeObstacle)
            else 0.0
        )
        compiled.append(
            (
                obstacle,
                effective_clearance_m + pipe_radius_m + obstacle_radius_m,
                selection.rule.rule_id,
            )
        )
        decisions.append(
            T6RouteObstacleConstraint(
                obstacle_id=obstacle.obstacle_id,
                rule_id=selection.rule.rule_id,
                rule_sha256=selection.rule.canonical_sha256,
                rule_evidence_bundle_sha256=bundle.canonical_sha256,
                original_clearance_m=float(selection.rule.value),
                effective_clearance_m=effective_clearance_m,
                exception_approval_id=evaluation.exception_approval_id,
                exception_approval_sha256=evaluation.exception_approval_sha256,
            )
        )
    route_result = solve_grid_route(
        request,
        municipal_rule_set=municipal_rule_set,
        schema_gate=schema_gate,
        _t6_obstacle_constraints=tuple(compiled),
    )
    result = T6GridRouteSolverResult(
        route_result=route_result,
        rule_evidence_bundle_sha256=bundle.canonical_sha256,
        obstacle_constraints=tuple(decisions),
    )
    try:
        (schema_gate or SchemaGate()).gate_or_fix(
            "t6_grid_route_solver_result", result.model_dump(mode="json")
        )
    except SchemaGateError as exc:
        raise RouteSolverError(f"T6 route 结果未通过 Schema Gate: {exc}") from exc
    return result


def apply_grid_route_to_network_input(
    network_input: NetworkGravitySolverInput | dict[str, Any],
    *,
    segment_id: str,
    route_input: GridRouteSolverInput | dict[str, Any],
    route_result: GridRouteSolverResult,
    municipal_rule_set: MunicipalRuleSet | None = None,
    _expected_route_result: GridRouteSolverResult | None = None,
) -> NetworkGravitySolverInput:
    """将已选择折线路线展开为网络节点/管段；网络 Solver 仍负责最终标高传播。"""
    try:
        network = (
            network_input
            if isinstance(network_input, NetworkGravitySolverInput)
            else NetworkGravitySolverInput.model_validate(network_input)
        )
        route = (
            route_input
            if isinstance(route_input, GridRouteSolverInput)
            else GridRouteSolverInput.model_validate(route_input)
        )
    except ValidationError as exc:
        raise RouteSolverError(f"路线接入前输入未通过模型门禁: {exc}") from exc
    if route_result.input_sha256 != route.canonical_sha256():
        raise RouteSolverError("route_result input_sha256 与 route_input 不一致")
    if route.source_ir_sha256 != network.source_ir_sha256:
        raise RouteSolverError("route source_ir_sha256 与 network source_ir_sha256 不一致")
    if route.coordinate_reference != network.coordinate_reference:
        raise RouteSolverError("route coordinate_reference 与 network coordinate_reference 不一致")
    candidate = route_result.selected_candidate()
    expected_result = _expected_route_result or solve_grid_route(
        route,
        municipal_rule_set=municipal_rule_set,
    )
    expected_candidates = {item.candidate_id: item for item in expected_result.candidates}
    if candidate.candidate_id not in expected_candidates or candidate != expected_candidates[candidate.candidate_id]:
        raise RouteSolverError("route_result selected candidate 与确定性重算候选集不一致")
    segments = {item.segment_id: item for item in network.segments}
    if segment_id not in segments:
        raise RouteSolverError(f"网络中不存在待替换 segment {segment_id!r}")
    original = segments[segment_id]
    if not math.isclose(route.design_slope, original.design_slope, abs_tol=_GRID_TOLERANCE_M):
        raise RouteSolverError("route design_slope 与待替换 segment 不一致")
    if not math.isclose(route.diameter_mm, original.diameter_mm, abs_tol=_GRID_TOLERANCE_M):
        raise RouteSolverError("route diameter_mm 与待替换 segment 不一致")
    if route.material != original.material or route.surface_context != original.surface_context:
        raise RouteSolverError("route material/surface_context 与待替换 segment 不一致")
    if route.start.node_id != original.start_node_id or route.end.node_id != original.end_node_id:
        raise RouteSolverError("route 起终 node_id 与待替换 segment 方向不一致")
    network_start_anchor = next(
        item.invert_anchor_m for item in network.nodes if item.node_id == original.start_node_id
    )
    if network_start_anchor is None or not math.isclose(
        route.start.invert_anchor_m or 0.0,
        network_start_anchor,
        abs_tol=_GRID_TOLERANCE_M,
    ):
        raise RouteSolverError("route 起点 invert_anchor_m 与 network node 不一致")
    network_nodes = {item.node_id: item for item in network.nodes}
    start_xy = route.grid.xy(route.start.cell)
    end_xy = route.grid.xy(route.end.cell)
    endpoint_points = (candidate.points[0], candidate.points[-1])
    for node_id, expected, route_point in zip(
        (original.start_node_id, original.end_node_id),
        (start_xy, end_xy),
        endpoint_points,
        strict=True,
    ):
        node = network_nodes[node_id]
        if not (
            math.isclose(node.x_m, expected[0], abs_tol=_GRID_TOLERANCE_M)
            and math.isclose(node.y_m, expected[1], abs_tol=_GRID_TOLERANCE_M)
        ):
            raise RouteSolverError(f"route endpoint {node_id!r} XY 与 network node 不一致")
        if not math.isclose(
            node.ground_elevation_m,
            route_point.ground_elevation_m,
            abs_tol=_GRID_TOLERANCE_M,
        ):
            raise RouteSolverError(f"route endpoint {node_id!r} ground_elevation_m 与 network node 不一致")

    bend_indexes = _bend_indexes(candidate.cells)
    path_indexes = (0, *bend_indexes, len(candidate.cells) - 1)
    expanded_nodes = [item.model_dump(mode="json") for item in network.nodes]
    route_node_ids = [original.start_node_id]
    for ordinal, point_index in enumerate(path_indexes[1:-1], start=1):
        point = candidate.points[point_index]
        node_id = f"{segment_id}-route-node-{ordinal:03d}"
        if node_id in network_nodes:
            raise RouteSolverError(f"路线展开 node id 冲突: {node_id}")
        expanded_nodes.append(
            {
                "node_id": node_id,
                "node_type": "manhole",
                "x_m": point.x_m,
                "y_m": point.y_m,
                "ground_elevation_m": point.ground_elevation_m,
                "invert_anchor_m": None,
            }
        )
        route_node_ids.append(node_id)
    route_node_ids.append(original.end_node_id)

    expanded_segments = [
        item.model_dump(mode="json") for item in network.segments if item.segment_id != segment_id
    ]
    for ordinal, (start_node_id, end_node_id) in enumerate(
            zip(route_node_ids, route_node_ids[1:], strict=False), start=1
    ):
        expanded_segments.append(
            {
                **original.model_dump(mode="json"),
                "segment_id": f"{segment_id}-route-{ordinal:03d}",
                "start_node_id": start_node_id,
                "end_node_id": end_node_id,
            }
        )
    payload = network.model_dump(mode="json")
    payload["nodes"] = expanded_nodes
    payload["segments"] = expanded_segments
    try:
        return NetworkGravitySolverInput.model_validate(payload)
    except ValidationError as exc:
        raise RouteSolverError(f"路线展开后的 network input 未通过门禁: {exc}") from exc


def apply_grid_route_t6_to_network_input(
    network_input: NetworkGravitySolverInput | dict[str, Any],
    *,
    segment_id: str,
    route_input: GridRouteSolverInput | dict[str, Any],
    route_result: T6GridRouteSolverResult,
    rule_evidence_bundle: MunicipalRuleEvidenceBundle,
    project_id: str,
    evaluated_at: datetime,
    exception_approvals: Mapping[str, ClearanceExceptionApproval] | None = None,
    municipal_rule_set: MunicipalRuleSet | None = None,
) -> NetworkGravitySolverInput:
    """按同一 T6 规则与审批上下文重算路线，再安全展开到网络输入。"""
    expected = solve_grid_route_t6(
        route_input,
        rule_evidence_bundle=rule_evidence_bundle,
        project_id=project_id,
        evaluated_at=evaluated_at,
        exception_approvals=exception_approvals,
        municipal_rule_set=municipal_rule_set,
    )
    if route_result.rule_evidence_bundle_sha256 != rule_evidence_bundle.canonical_sha256:
        raise RouteSolverError("T6 route_result 与 rule_evidence_bundle SHA-256 不一致")
    if route_result.obstacle_constraints != expected.obstacle_constraints:
        raise RouteSolverError("T6 route obstacle 规则或审批决策与确定性重算不一致")
    return apply_grid_route_to_network_input(
        network_input,
        segment_id=segment_id,
        route_input=route_input,
        route_result=route_result.route_result,
        municipal_rule_set=municipal_rule_set,
        _expected_route_result=expected.route_result,
    )


def _t6_obstacle_facts(
    obstacle: AxisAlignedBoxObstacle | ExistingPipeObstacle,
) -> tuple[RuleType, dict[str, Any]]:
    if isinstance(obstacle, AxisAlignedBoxObstacle):
        return RuleType.STRUCTURE_CLEARANCE, {"obstacle_category": obstacle.category}
    facts: dict[str, Any] = {"obstacle_category": obstacle.category}
    if obstacle.category == "water":
        facts["outer_diameter_class"] = (
            "d_le_200" if obstacle.outer_diameter_mm <= 200.0 else "d_gt_200"
        )
    elif obstacle.category == "gas":
        facts["pressure_class"] = obstacle.pressure_class
    elif obstacle.category == "telecom":
        facts["burial_method"] = obstacle.burial_method
    elif obstacle.category == "power":
        facts["burial_method"] = obstacle.burial_method
    else:
        raise RouteSolverError(f"T6 route 不支持 obstacle category={obstacle.category!r}")
    return RuleType.HORIZONTAL_CLEARANCE, facts


def _selected_rule_value(
    bundle: MunicipalRuleEvidenceBundle,
    *,
    rule_type: RuleType,
    facts: Mapping[str, Any],
    obstacle_id: str,
) -> float:
    selection = select_municipal_rule(bundle, rule_type=rule_type, facts=facts)
    if selection.status is not EvidenceRuleSelectionStatus.SELECTED or selection.rule is None:
        raise RouteSolverError(
            f"障碍物 {obstacle_id!r} T6 净距规则无法唯一选择: "
            f"status={selection.status.value}; {selection.detail}"
        )
    if isinstance(selection.rule.value, bool) or not isinstance(selection.rule.value, int | float):
        raise RouteSolverError(f"障碍物 {obstacle_id!r} T6 净距规则不是数值")
    return float(selection.rule.value)


def _compile_obstacle_constraints(
    request: GridRouteSolverInput,
    rule_set: MunicipalRuleSet,
) -> tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...]:
    constraints: list[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str]] = []
    pipe_radius_m = request.diameter_mm / 2000.0
    for obstacle in sorted(request.obstacles, key=lambda item: item.obstacle_id):
        attributes: dict[str, Any] = {}
        obstacle_radius_m = 0.0
        if isinstance(obstacle, ExistingPipeObstacle):
            obstacle_radius_m = obstacle.outer_diameter_mm / 2000.0
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
            raise RouteSolverError(
                f"障碍物 {obstacle.obstacle_id!r} 净距规则未获生产执行资格: "
                f"status={selection.status.value}; {selection.detail}"
            )
        expanded_distance_m = (
            selection.rule.required_clearance_m + pipe_radius_m + obstacle_radius_m
        )
        constraints.append((obstacle, expanded_distance_m, selection.rule.rule_key))
    return tuple(constraints)


def _search_route(
    request: GridRouteSolverInput,
    *,
    allowed: set[tuple[int, int]],
    start: tuple[int, int],
    end: tuple[int, int],
    obstacle_constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...],
    enforce_cover: bool,
) -> tuple[tuple[tuple[int, int], ...] | None, bool]:
    paths, limit_exceeded = _search_routes(
        request,
        allowed=allowed,
        start=start,
        end=end,
        obstacle_constraints=obstacle_constraints,
        enforce_cover=enforce_cover,
        max_results=1,
    )
    return (paths[0] if paths else None), limit_exceeded


def _search_routes(
    request: GridRouteSolverInput,
    *,
    allowed: set[tuple[int, int]],
    start: tuple[int, int],
    end: tuple[int, int],
    obstacle_constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...],
    enforce_cover: bool,
    max_results: int,
) -> tuple[tuple[tuple[tuple[int, int], ...], ...], bool]:
    samples = {item.cell.identity(): item.ground_elevation_m for item in request.surface_samples}
    required_cover_m = MIN_COVER_BY_SURFACE_M[request.surface_context]
    diameter_m = request.diameter_mm / 1000.0
    resolution = request.grid.resolution_m
    start_invert = request.start.invert_anchor_m
    if start_invert is None:
        raise AssertionError("validated start invert cannot be None")

    if enforce_cover:
        start_cover_m = samples[start] - (start_invert + diameter_m)
        if start_cover_m + _GRID_TOLERANCE_M < required_cover_m:
            return (), False

    start_path = (start,)
    pending: list[
        tuple[int, int, tuple[tuple[int, int], ...], tuple[int, int], tuple[int, int] | None]
    ] = []
    heapq.heappush(
        pending,
        (_manhattan(start, end), 0, start_path, start, None),
    )
    expansions = 0
    results: list[tuple[tuple[int, int], ...]] = []
    while pending:
        estimated_steps, turns, path, current, previous_direction = heapq.heappop(pending)
        del estimated_steps
        expansions += 1
        if expansions > request.max_search_expansions:
            return tuple(results), True
        if current == end:
            results.append(path)
            if len(results) >= max_results:
                return tuple(results), False
            continue
        for neighbor in sorted(_neighbors(current)):
            if neighbor not in allowed or neighbor in path:
                continue
            if not _edge_allowed(request.grid, current, neighbor, obstacle_constraints):
                continue
            next_steps = len(path)
            if enforce_cover:
                invert_m = start_invert - request.design_slope * next_steps * resolution
                cover_m = samples[neighbor] - (invert_m + diameter_m)
                if cover_m + _GRID_TOLERANCE_M < required_cover_m:
                    continue
            direction = (neighbor[0] - current[0], neighbor[1] - current[1])
            next_turns = turns + int(previous_direction is not None and direction != previous_direction)
            next_path = (*path, neighbor)
            priority = next_steps + _manhattan(neighbor, end)
            heapq.heappush(
                pending,
                (priority, next_turns, next_path, neighbor, direction),
            )
    return tuple(results), False


def _build_candidate(
    request: GridRouteSolverInput,
    rule_set: MunicipalRuleSet,
    path: tuple[tuple[int, int], ...],
    obstacle_constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...],
    *,
    rank: int,
) -> RouteCandidate:
    samples = {item.cell.identity(): item.ground_elevation_m for item in request.surface_samples}
    start_invert = request.start.invert_anchor_m
    if start_invert is None:
        raise AssertionError("validated start invert cannot be None")
    diameter_m = request.diameter_mm / 1000.0
    points: list[RoutePoint] = []
    for index, identity in enumerate(path):
        cell = GridCell(x_index=identity[0], y_index=identity[1])
        x_m, y_m = request.grid.xy(cell)
        invert_m = start_invert - request.design_slope * index * request.grid.resolution_m
        ground = samples[identity]
        points.append(
            RoutePoint(
                cell=cell,
                x_m=x_m,
                y_m=y_m,
                ground_elevation_m=ground,
                invert_m=invert_m,
                cover_depth_m=ground - (invert_m + diameter_m),
            )
        )
    margins = [
        _edge_clearance_margin(request.grid, left, right, obstacle_constraints)
        for left, right in zip(path, path[1:], strict=False)
    ]
    finite_margins = [margin for margin in margins if margin is not None]
    required_cover = MIN_COVER_BY_SURFACE_M[request.surface_context]
    report = RouteConstraintReport(
        cover_depth_in_spec=all(point.cover_depth_m + _GRID_TOLERANCE_M >= required_cover for point in points),
        clearance_in_spec=all(margin + CLASH_TOLERANCE_M >= 0.0 for margin in finite_margins),
        minimum_cover_depth_m=min(point.cover_depth_m for point in points),
        required_cover_depth_m=required_cover,
        minimum_clearance_margin_m=min(finite_margins) if finite_margins else None,
        applied_rule_keys=tuple(sorted({item[2] for item in obstacle_constraints})),
        municipal_rule_set_sha256=rule_set.canonical_sha256,
    )
    turns = len(_bend_indexes(tuple(GridCell(x_index=x, y_index=y) for x, y in path)))
    return RouteCandidate(
        candidate_id=f"{request.request_id}-candidate-{rank:03d}",
        rank=rank,
        cells=tuple(GridCell(x_index=x, y_index=y) for x, y in path),
        points=tuple(points),
        horizontal_length_m=(len(path) - 1) * request.grid.resolution_m,
        turn_count=turns,
        constraint_report=report,
    )


def _failed_result(
    request: GridRouteSolverInput,
    rule_set: MunicipalRuleSet,
    reason: NoFeasibleRouteReason,
    detail: str,
) -> GridRouteSolverResult:
    status = (
        RouteSolveStatus.UNKNOWN
        if reason is NoFeasibleRouteReason.SEARCH_LIMIT_EXCEEDED
        else RouteSolveStatus.NO_FEASIBLE_ROUTE
    )
    return GridRouteSolverResult(
        request_id=request.request_id,
        input_sha256=request.canonical_sha256(),
        municipal_rule_set_sha256=rule_set.canonical_sha256,
        status=status,
        failure_reason=reason,
        detail=detail,
    )


def _gated_result(gate: SchemaGate, result: GridRouteSolverResult) -> GridRouteSolverResult:
    try:
        gate.gate_or_fix("grid_route_solver_result", result.model_dump(mode="json"))
    except SchemaGateError as exc:
        raise RouteSolverError(f"grid route Solver v0.1 结果未通过门禁: {exc}") from exc
    return result


def _unique_cells(items: tuple[GridCell, ...], label: str) -> dict[tuple[int, int], GridCell]:
    result: dict[tuple[int, int], GridCell] = {}
    for cell in items:
        identity = cell.identity()
        if identity in result:
            raise ValueError(f"{label} cell 重复: {identity}")
        result[identity] = cell
    return result


def _neighbors(cell: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    x_index, y_index = cell
    candidates = (
        (x_index - 1, y_index),
        (x_index, y_index - 1),
        (x_index, y_index + 1),
        (x_index + 1, y_index),
    )
    return tuple(item for item in candidates if item[0] >= 0 and item[1] >= 0)


def _manhattan(left: tuple[int, int], right: tuple[int, int]) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _edge_allowed(
    grid: RouteGrid,
    left: tuple[int, int],
    right: tuple[int, int],
    constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...],
) -> bool:
    margin = _edge_clearance_margin(grid, left, right, constraints)
    return margin is None or margin + CLASH_TOLERANCE_M >= 0.0


def _edge_clearance_margin(
    grid: RouteGrid,
    left: tuple[int, int],
    right: tuple[int, int],
    constraints: tuple[tuple[AxisAlignedBoxObstacle | ExistingPipeObstacle, float, str], ...],
) -> float | None:
    if not constraints:
        return None
    start = grid.xy(GridCell(x_index=left[0], y_index=left[1]))
    end = grid.xy(GridCell(x_index=right[0], y_index=right[1]))
    margins: list[float] = []
    for obstacle, expanded_distance_m, _rule_key in constraints:
        if isinstance(obstacle, AxisAlignedBoxObstacle):
            distance = _segment_rectangle_distance(
                start,
                end,
                (obstacle.min_corner.x_m, obstacle.min_corner.y_m),
                (obstacle.max_corner.x_m, obstacle.max_corner.y_m),
            )
        else:
            distance = _segment_segment_distance(
                start,
                end,
                (obstacle.start_center.x_m, obstacle.start_center.y_m),
                (obstacle.end_center.x_m, obstacle.end_center.y_m),
            )
        margins.append(distance - expanded_distance_m)
    return min(margins)


def _segment_rectangle_distance(
    start: tuple[float, float],
    end: tuple[float, float],
    minimum: tuple[float, float],
    maximum: tuple[float, float],
) -> float:
    if _point_in_rectangle(start, minimum, maximum) or _point_in_rectangle(end, minimum, maximum):
        return 0.0
    corners = (
        minimum,
        (minimum[0], maximum[1]),
        maximum,
        (maximum[0], minimum[1]),
    )
    edges = tuple(zip(corners, (*corners[1:], corners[0]), strict=True))
    return min(_segment_segment_distance(start, end, edge_start, edge_end) for edge_start, edge_end in edges)


def _point_in_rectangle(
    point: tuple[float, float],
    minimum: tuple[float, float],
    maximum: tuple[float, float],
) -> bool:
    return minimum[0] <= point[0] <= maximum[0] and minimum[1] <= point[1] <= maximum[1]


def _segment_segment_distance(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> float:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return 0.0
    return min(
        _point_segment_distance(first_start, second_start, second_end),
        _point_segment_distance(first_end, second_start, second_end),
        _point_segment_distance(second_start, first_start, first_end),
        _point_segment_distance(second_end, first_start, first_end),
    )


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    values = (orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) <= _GRID_TOLERANCE_M and _on_segment(point, left, right)
        for value, point, left, right in (
            (values[0], c, a, b),
            (values[1], d, a, b),
            (values[2], a, c, d),
            (values[3], b, c, d),
        )
    )


def _on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    return (
        min(start[0], end[0]) - _GRID_TOLERANCE_M <= point[0] <= max(start[0], end[0]) + _GRID_TOLERANCE_M
        and min(start[1], end[1]) - _GRID_TOLERANCE_M <= point[1] <= max(start[1], end[1]) + _GRID_TOLERANCE_M
    )


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= _GRID_TOLERANCE_M:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    parameter = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator),
    )
    closest = (start[0] + parameter * dx, start[1] + parameter * dy)
    return math.hypot(point[0] - closest[0], point[1] - closest[1])


def _bend_indexes(cells: tuple[GridCell, ...]) -> tuple[int, ...]:
    if len(cells) < 3:
        return ()
    bends: list[int] = []
    for index in range(1, len(cells) - 1):
        before = (
            cells[index].x_index - cells[index - 1].x_index,
            cells[index].y_index - cells[index - 1].y_index,
        )
        after = (
            cells[index + 1].x_index - cells[index].x_index,
            cells[index + 1].y_index - cells[index].y_index,
        )
        if before != after:
            bends.append(index)
    return tuple(bends)


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
    "GRID_ROUTE_SOLVER_INPUT_VERSION",
    "GRID_ROUTE_SOLVER_NAME",
    "GRID_ROUTE_SOLVER_RESULT_VERSION",
    "GRID_ROUTE_SOLVER_VERSION",
    "GridCell",
    "GridRouteSolverInput",
    "GridRouteSolverResult",
    "NoFeasibleRouteReason",
    "RouteCandidate",
    "RouteConstraintReport",
    "RouteEndpoint",
    "RouteGrid",
    "RoutePoint",
    "RouteSolveStatus",
    "RouteSolverError",
    "SurfaceSample",
    "T6GridRouteSolverResult",
    "T6RouteObstacleConstraint",
    "apply_grid_route_t6_to_network_input",
    "apply_grid_route_to_network_input",
    "solve_grid_route",
    "solve_grid_route_t6",
]
