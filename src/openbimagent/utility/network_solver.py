"""M1.5 多节点重力污水管网的确定性 Solver v0.1。

输入显式给出节点 XY、地面标高、DAG 拓扑、逐段坡度和源节点管底锚点。
Solver 按稳定拓扑序传播标高：普通节点沿用来管最低管底，汇流节点也以最低来管
为出流基准；显式更低锚点表示跌水，任何高于最低来管的锚点均失败关闭。

管径采用动态查表（Table-Driven Lookup）：DN[150, 2000] 内的管径按
``lookup_pipe_normative_limits`` 分档取用规范限值（DN300/DN400/DN500/DN600
全覆盖）；不做路线寻优、水力计算或管径优化。
"""

from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from openbimagent.domain_gate import DomainGateReport, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.compiler import compile_solved_utility_ir
from openbimagent.utility.contracts import CoordinateReference, StrictFrozenModel
from openbimagent.utility.rules import MunicipalRuleSet, compile_municipal_rule_set
from openbimagent.utility.design_limits import (
    DesignLimit,
    MunicipalDesignLimits,
    cached_municipal_design_limits,
    compile_municipal_design_limits,
)
from openbimagent.utility.geometry import resolve_dimensions
from openbimagent.utility.solver import (
    MIN_DN300_CONCRETE_SLOPE,
    CollisionContext,
    SolverEndpoint,
    StraightGravitySolverInput,
    UtilitySolverError,
    UtilitySolverResult,
    _clash_evidence,
    _unknown_evidence,
    _spec_evidence,
    SUPPORTED_DIAMETER_MM,
)

NETWORK_UTILITY_SOLVER_INPUT_VERSION = "0.1"
NETWORK_UTILITY_SOLVER_NAME = "municipal-network-gravity-solver"
NETWORK_UTILITY_SOLVER_VERSION = "0.1.0"
INVERT_TOLERANCE_M = 1e-9

# ============ 动态查表（Table-Driven Lookup）============
# 旧版只允许 SUPPORTED_DIAMETER_MM = 300.0 一种管径（能力面与规范限值混写）。
# 现在能力面放宽为区间：[150, 2000] mm 之外的管径才报 unsupported_diameter；
# 每个管径的具体规范限值由 lookup_pipe_normative_limits 按 constraints.yaml /
# design_limits.py 分档返回，代码里不再出现"只认 DN300"的分支判断。

SUPPORTED_DIAMETER_RANGE_MM: tuple[float, float] = (150.0, 2000.0)
"""动态查表能力面：区间外的管径报 unsupported_diameter（fail-closed）。"""

#: 最小设计坡度分档（m/m）。DN300 档数值与 MU-DRAIN-004（GB 50014-2021 §5.2.10
#: 表 5.2.10，规范明文）一致；DN400+ 档为 MU-DRAIN-012 管径序列的工程分档
#: （规范未列固定表，由水力计算确定，此处取工程下限），出处随查询结果一并返回。
MIN_SLOPE_BY_DIAMETER_BANDS: tuple[tuple[float, float], ...] = (
    (300.0, 0.003),
    (400.0, 0.002),
    (500.0, 0.0015),
    (600.0, 0.0010),
)

#: 检查井最大间距设计分档（m）。MU-WELL-001（GB 50014-2021 §5.4.4 表 5.4.4）的
#: 规范上限按管径为 300-600→75m、700-1000→100m 等；本表是维修沙箱使用的**设计
#: 保守分档**（养护条件受限路段不宜大于 40m 的同一表内建议），用于自动插井算子
#: 的拆分目标，规范判定仍以 MU-WELL-001 编译限值为准。
MAX_WELL_SPACING_DESIGN_BANDS: tuple[tuple[float, float], ...] = (
    (600.0, 40.0),
    (2000.0, 50.0),
)

#: 最小覆土（m）按地表类型：车行道 0.7（MU-ELEV-001，GB 50014-2021 §5.3.7）；
#: 人行道/绿地 0.6（MU-ELEV-002）。
MIN_COVER_BY_SURFACE_M: dict[str, float] = {"driveway": 0.7, "sidewalk": 0.6, "green": 0.6}

#: 污水/雨水管线与其他管线的最小净距（m）：MU-VERTICAL-001（GB 50289-2016
#: §4.1.14 表 4.1.14，wastewater_rainwater 列 0.15m）。
MIN_OBSTACLE_CLEARANCE_M = 0.15

#: Tier-2 国标受控特批（满包混凝土加固）覆土下限（m）。
EXCEPTION_MIN_COVER_M = 0.45

_SURFACE_ALIASES: dict[str, str] = {
    "driveway": "driveway",
    "roadway": "driveway",
    "carriageway": "driveway",
    "sidewalk": "sidewalk",
    "footway": "sidewalk",
    "green": "green",
    "greenbelt": "green",
    "lawn": "green",
}


def _band_value(diameter_mm: float, bands: tuple[tuple[float, float], ...]) -> float:
    """按管径取分档值：返回第一个上限 ≥ diameter_mm 的档位值。"""
    for upper, value in bands:
        if diameter_mm <= upper + 1e-9:
            return value
    return bands[-1][1]


def lookup_pipe_normative_limits(diameter_mm: float, surface_type: str = "driveway") -> dict[str, Any]:
    """按管径与地表类型动态返回规范限值分档（Table-Driven Lookup）。

    结合 ``design_limits.py``（constraints.yaml 编译产物）与本模块的分档表，
    返回该管径的最小坡度、检查井最大间距（设计保守档 + 规范档）、最小覆土与
    最小障碍物净距。仅当 ``diameter_mm <= 0`` 或超出 ``[150, 2000]`` 时抛出
    ``ValueError``（unsupported_diameter）；不做任何静默回退。
    """
    try:
        diameter = float(diameter_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported_diameter: 管径不可解析 {diameter_mm!r}") from exc
    low, high = SUPPORTED_DIAMETER_RANGE_MM
    if not math.isfinite(diameter) or diameter <= 0.0 or diameter < low - 1e-9 or diameter > high + 1e-9:
        raise ValueError(
            f"unsupported_diameter: {diameter_mm!r} mm 超出动态查表能力面 "
            f"[{low:g}, {high:g}]（constraints.yaml / design_limits.py 分档）"
        )
    surface = _SURFACE_ALIASES.get(str(surface_type).strip().lower(), "driveway")
    if diameter <= 300.0 + 1e-9:
        min_slope, slope_rule = 0.003, "MU-DRAIN-004"
    elif diameter <= 400.0 + 1e-9:
        min_slope, slope_rule = 0.002, "MU-DRAIN-012"
    elif diameter <= 500.0 + 1e-9:
        min_slope, slope_rule = 0.0015, "MU-DRAIN-012"
    else:
        min_slope, slope_rule = 0.0010, "MU-DRAIN-012"
    code_spacing: float | None = None
    code_spacing_rule: str | None = None
    try:
        limits = cached_municipal_design_limits(diameter_mm=diameter)
        if limits.max_manhole_spacing is not None:
            code_spacing = float(limits.max_manhole_spacing.limit_value)
            code_spacing_rule = limits.max_manhole_spacing.rule_id
    except Exception:  # noqa: BLE001 — 规范档缺失时如实返回 None，不猜测
        code_spacing = None
    return {
        "diameter_mm": diameter,
        "min_slope": min_slope,
        "min_slope_source_rule": slope_rule,
        "max_well_spacing_m": _band_value(diameter, MAX_WELL_SPACING_DESIGN_BANDS),
        "max_well_spacing_source_rule": "MU-WELL-001（设计保守分档：DN300-600→40m，>600→50m）",
        "code_max_manhole_spacing_m": code_spacing,
        "code_max_manhole_spacing_rule": code_spacing_rule,
        "min_cover_m": MIN_COVER_BY_SURFACE_M[surface],
        "min_cover_source_rule": "MU-ELEV-001" if surface == "driveway" else "MU-ELEV-002",
        "min_obstacle_clearance_m": MIN_OBSTACLE_CLEARANCE_M,
        "min_obstacle_clearance_source_rule": "MU-VERTICAL-001",
        "exception_min_cover_m": EXCEPTION_MIN_COVER_M,
        "surface_type": surface,
    }


def _per_diameter_slope_limit(diameter_mm: float, context_limits: MunicipalDesignLimits) -> DesignLimit | None:
    """按管径返回最小坡度限值：DN300 沿用冻结上下文里的 MU-DRAIN-004 条目
    （保留调用方对冻结规则上下文的覆写能力），其余管径由 MU-DRAIN-012 分档合成。"""
    if math.isclose(diameter_mm, SUPPORTED_DIAMETER_MM, abs_tol=1e-9):
        return context_limits.min_slope_sewage_d300_concrete
    norm = lookup_pipe_normative_limits(diameter_mm)
    return DesignLimit(
        rule_id="MU-DRAIN-012",
        parameter="min_slope_sewage_by_diameter",
        limit_value=float(norm["min_slope"]),
        comparison="min",
        unit="ratio",
        source_clause="GB 50014-2021 §5.2.10 表 5.2.10（DN400 及以上由水力计算确定，此处为工程分档下限）",
        confidence="high",
        enforcement="production",
        source_sha256=context_limits.source_sha256,
        locator=f"constraints[rule_id=MU-DRAIN-012].value(diameter_mm={diameter_mm:g}).min_slope",
    )


class NetworkSolverNode(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    node_type: Literal["manhole", "junction", "inlet", "outlet", "terminal"]
    x_m: float
    y_m: float
    ground_elevation_m: float
    invert_anchor_m: float | None = None
    connection_radius_m: float | None = Field(default=None, gt=0)
    manhole_type: Literal["standard", "drop_manhole"] | None = None
    drop_height_m: float | None = Field(default=None, ge=0)


class NetworkSolverSegment(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    start_node_id: str = Field(min_length=1, max_length=256)
    end_node_id: str = Field(min_length=1, max_length=256)
    diameter_mm: float = Field(default=SUPPORTED_DIAMETER_MM, gt=0)
    inner_diameter_mm: float | None = Field(default=None, gt=0)
    outer_diameter_mm: float | None = Field(default=None, gt=0)
    wall_thickness_mm: float | None = Field(default=None, gt=0)
    material: Literal["concrete"] = "concrete"
    design_slope: float = Field(default=MIN_DN300_CONCRETE_SLOPE, ge=0)
    surface_context: Literal["driveway", "sidewalk"] = "driveway"
    protection_measure: Literal["none", "concrete_encasement"] | None = None
    exception_approval_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _dimensions(self) -> "NetworkSolverSegment":
        resolve_dimensions(self.inner_diameter_mm, self.outer_diameter_mm, self.wall_thickness_mm)
        return self


class NetworkConstraintFlags(StrictFrozenModel):
    """候选级三级刚柔约束开关（Tier-2 受控特批等），默认全部关闭。"""

    allow_exception_approval: bool = False


class NetworkGravitySolverInput(StrictFrozenModel):
    """已知平面坐标的单系统重力 DAG 输入；不包含任何可执行表达式。"""

    protocol_version: str = Field(default=NETWORK_UTILITY_SOLVER_INPUT_VERSION, pattern=r"^0\.1$")
    request_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_reference: CoordinateReference
    system_id: str = Field(min_length=1, max_length=256)
    system_name: str = Field(min_length=1, max_length=512)
    nodes: tuple[NetworkSolverNode, ...] = Field(min_length=2)
    segments: tuple[NetworkSolverSegment, ...] = Field(min_length=1)
    collision_context: CollisionContext | None = None
    constraints: NetworkConstraintFlags | None = None

    @model_validator(mode="after")
    def _validate_network(self) -> "NetworkGravitySolverInput":
        nodes = _unique_models(self.nodes, "node_id", "node")
        segments = _unique_models(self.segments, "segment_id", "segment")
        incoming: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        outgoing: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        undirected: dict[str, set[str]] = {node_id: set() for node_id in nodes}

        for segment in segments.values():
            if segment.start_node_id == segment.end_node_id:
                raise ValueError(f"segment {segment.segment_id!r} 起终 node 不能相同")
            try:
                start = nodes[segment.start_node_id]
                end = nodes[segment.end_node_id]
            except KeyError as exc:
                raise ValueError(f"segment {segment.segment_id!r} 引用未知 node {exc.args[0]!r}") from exc
            if math.hypot(end.x_m - start.x_m, end.y_m - start.y_m) <= 0.0:
                raise ValueError(f"segment {segment.segment_id!r} 起终 node 平面位置不能相同")
            low, high = SUPPORTED_DIAMETER_RANGE_MM
            if not (low - 1e-9 <= segment.diameter_mm <= high + 1e-9):
                raise ValueError(
                    f"segment {segment.segment_id!r} unsupported_diameter: DN{segment.diameter_mm:g} "
                    f"超出动态查表能力面 [{low:g}, {high:g}] mm（constraints.yaml / design_limits.py 分档）"
                )
            outgoing[start.node_id].add(segment.segment_id)
            incoming[end.node_id].add(segment.segment_id)
            adjacency[start.node_id].add(end.node_id)
            undirected[start.node_id].add(end.node_id)
            undirected[end.node_id].add(start.node_id)

        for node_id, node in nodes.items():
            degree = len(incoming[node_id]) + len(outgoing[node_id])
            if degree == 0:
                raise ValueError(f"存在孤立 node {node_id!r}")
            if degree >= 3 and node.node_type != "junction":
                raise ValueError(f"node {node_id!r} 的分支或汇流必须声明为 junction")
            if node.node_type == "junction" and degree < 3:
                raise ValueError(f"junction node {node_id!r} 至少连接 3 个 segment")
            if node.node_type == "junction" and (not incoming[node_id] or not outgoing[node_id]):
                raise ValueError(f"junction node {node_id!r} 必须同时具有入流和出流 segment")
            if not incoming[node_id] and node.invert_anchor_m is None:
                raise ValueError(f"源节点 {node_id!r} 必须提供 invert_anchor_m")

        visited = _reachable(min(nodes), undirected)
        if visited != set(nodes):
            raise ValueError(f"network 存在不连通子图，未连通 nodes={sorted(set(nodes) - visited)}")
        _stable_topological_order(nodes=set(nodes), adjacency=adjacency, incoming=incoming)
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["nodes"] = sorted(payload["nodes"], key=lambda item: item["node_id"])
        payload["segments"] = sorted(payload["segments"], key=lambda item: item["segment_id"])
        if payload["collision_context"] is not None:
            payload["collision_context"]["obstacles"] = sorted(
                payload["collision_context"]["obstacles"],
                key=lambda item: item["obstacle_id"],
            )
        return payload

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def solve_network_gravity_utility(
    solver_input: NetworkGravitySolverInput | dict[str, Any],
    *,
    domain_requirements: dict[str, Any] | None = None,
    municipal_rule_set: MunicipalRuleSet | None = None,
    design_limits: MunicipalDesignLimits | None = None,
    schema_gate: SchemaGate | None = None,
) -> UtilitySolverResult:
    """确定性求解单系统多节点重力 DAG，并编译为现有 ``CompiledUtilityIR v1``。"""
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
            if isinstance(solver_input, NetworkGravitySolverInput)
            else NetworkGravitySolverInput.model_validate(solver_input)
        )
        gate.gate_or_fix("network_utility_solver_input", request.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise UtilitySolverError(f"network Solver v0.1 输入或 MunicipalRuleSet 未通过门禁: {exc}") from exc

    limits = design_limits or compile_municipal_design_limits(diameter_mm=SUPPORTED_DIAMETER_MM)
    nodes = {item.node_id: item for item in request.nodes}
    segments = {item.segment_id: item for item in request.segments}
    incoming_by_node: dict[str, list[NetworkSolverSegment]] = {node_id: [] for node_id in nodes}
    outgoing_by_node: dict[str, list[NetworkSolverSegment]] = {node_id: [] for node_id in nodes}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    incoming_ids: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for segment in segments.values():
        incoming_by_node[segment.end_node_id].append(segment)
        outgoing_by_node[segment.start_node_id].append(segment)
        adjacency[segment.start_node_id].add(segment.end_node_id)
        incoming_ids[segment.end_node_id].add(segment.segment_id)
    order = _stable_topological_order(nodes=set(nodes), adjacency=adjacency, incoming=incoming_ids)

    solved_segments: dict[str, dict[str, float]] = {}
    node_outgoing_invert: dict[str, float] = {}
    for node_id in order:
        node = nodes[node_id]
        incoming_inverts = [solved_segments[item.segment_id]["end_invert_m"] for item in incoming_by_node[node_id]]
        natural_invert = min(incoming_inverts) if incoming_inverts else None
        if node.invert_anchor_m is not None:
            if natural_invert is not None and node.invert_anchor_m > natural_invert + INVERT_TOLERANCE_M:
                raise UtilitySolverError(
                    f"node {node_id!r} invert_anchor_m={node.invert_anchor_m:.6f} 高于来流管底 "
                    f"{natural_invert:.6f}，形成抬升冲突"
                )
            outgoing_invert = node.invert_anchor_m
        elif natural_invert is not None:
            outgoing_invert = natural_invert
        else:  # Pydantic 已要求所有源节点提供锚点；防御性失败关闭。
            raise UtilitySolverError(f"源节点 {node_id!r} 缺少 invert_anchor_m")
        node_outgoing_invert[node_id] = outgoing_invert

        for segment in sorted(outgoing_by_node[node_id], key=lambda item: item.segment_id):
            start = nodes[segment.start_node_id]
            end = nodes[segment.end_node_id]
            length_m = math.hypot(end.x_m - start.x_m, end.y_m - start.y_m)
            solved_segments[segment.segment_id] = {
                "horizontal_length_m": length_m,
                "start_invert_m": outgoing_invert,
                "end_invert_m": outgoing_invert - segment.design_slope * length_m,
            }

    ir_id = f"utility-{request.request_id}"
    evidence: list[dict[str, Any]] = []
    compiled_segments: list[dict[str, Any]] = []
    node_ports: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    for segment in sorted(segments.values(), key=lambda item: item.segment_id):
        solved = solved_segments[segment.segment_id]
        start = nodes[segment.start_node_id]
        end = nodes[segment.end_node_id]
        start_port_id = f"{segment.segment_id}-start"
        end_port_id = f"{segment.segment_id}-end"
        inner, outer = resolve_dimensions(segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm)
        crown_offset = (inner + outer) / 2000.0 if inner is not None and outer is not None else None
        minimum_cover_m = (
            min(start.ground_elevation_m - solved["start_invert_m"], end.ground_elevation_m - solved["end_invert_m"]) - crown_offset
            if crown_offset is not None else None
        )
        node_ports[start.node_id].append(
            _port_payload(start_port_id, "outlet", start.x_m, start.y_m, solved["start_invert_m"])
        )
        node_ports[end.node_id].append(
            _port_payload(end_port_id, "inlet", end.x_m, end.y_m, solved["end_invert_m"])
        )
        compiled_segments.append(
            {
                "segment_id": segment.segment_id,
                "system_id": request.system_id,
                "start_port_id": start_port_id,
                "end_port_id": end_port_id,
                "centerline": [
                    {"x_m": start.x_m, "y_m": start.y_m, "z_m": solved["start_invert_m"]},
                    {"x_m": end.x_m, "y_m": end.y_m, "z_m": solved["end_invert_m"]},
                ],
                "horizontal_length_m": solved["horizontal_length_m"],
                "start_invert_m": solved["start_invert_m"],
                "end_invert_m": solved["end_invert_m"],
                "slope": segment.design_slope,
                "diameter_mm": segment.diameter_mm,
                "centerline_reference": "invert",
                "inner_diameter_mm": segment.inner_diameter_mm,
                "outer_diameter_mm": segment.outer_diameter_mm,
                "wall_thickness_mm": segment.wall_thickness_mm,
                "material": segment.material,
                "min_cover_depth_m": minimum_cover_m,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            }
        )
        evidence.extend(
            _segment_evidence(
                request=request,
                segment=segment,
                ir_id=ir_id,
                solved=solved,
                minimum_cover_m=minimum_cover_m,
                design_limits=limits,
                trusted_rule_set=trusted_rule_set,
            )
        )

    evidence.append(
        _unknown_evidence(
            evidence_id=f"{request.request_id}-hydraulics",
            rule_id="MU-DRAIN-007",
            check_name="hydraulics_in_spec",
            subject_id=ir_id,
            detail="network Solver v0.1 未接收流量、粗糙系数或充满度，不能执行水力校核",
            source_clause="GB 50014-2021 §5.2.7",
        )
    )
    payload = {
        "protocol_version": "1.1",
        "ir_id": ir_id,
        "source_ir_sha256": request.source_ir_sha256,
        "solver_name": NETWORK_UTILITY_SOLVER_NAME,
        "solver_version": NETWORK_UTILITY_SOLVER_VERSION,
        "coordinate_reference": request.coordinate_reference.model_dump(mode="json"),
        "systems": [
            {
                "system_id": request.system_id,
                "name": request.system_name,
                "system_type": "wastewater",
                "flow_regime": "gravity",
                "ifc_class": "IfcDistributionSystem",
                "ifc_predefined_type": "WASTEWATER",
            }
        ],
        "nodes": [
            {
                "node_id": node.node_id,
                "system_id": request.system_id,
                "node_type": node.node_type,
                "position": {"x_m": node.x_m, "y_m": node.y_m, "z_m": node.ground_elevation_m},
                "ports": sorted(node_ports[node.node_id], key=lambda item: item["port_id"]),
                "ground_elevation_m": node.ground_elevation_m,
                "connection_radius_m": node.connection_radius_m,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE" if node.node_type != "junction" else "INSPECTIONCHAMBER",
            }
            for node in sorted(nodes.values(), key=lambda item: item.node_id)
        ],
        "segments": compiled_segments,
        "evidence": sorted(evidence, key=lambda item: item["evidence_id"]),
    }
    compiled = compile_solved_utility_ir(payload, schema_gate=gate)
    requirements = domain_requirements or {
        "diameter_in_spec": True,
        "slope_in_spec": True,
        "cover_depth_in_spec": True,
        "manhole_spacing_in_spec": True,
        "clash_free": True,
    }
    report: DomainGateReport = evaluate_domain_gate(requirements, compiled.domain_evidence())
    return UtilitySolverResult(compiled_ir=compiled, domain_gate=report)


def _segment_evidence(
    *,
    request: NetworkGravitySolverInput,
    segment: NetworkSolverSegment,
    ir_id: str,
    solved: dict[str, float],
    minimum_cover_m: float | None,
    design_limits: MunicipalDesignLimits,
    trusted_rule_set: MunicipalRuleSet,
) -> list[dict[str, Any]]:
    evidence_prefix = f"{request.request_id}-{segment.segment_id}"
    start = next(node for node in request.nodes if node.node_id == segment.start_node_id)
    end = next(node for node in request.nodes if node.node_id == segment.end_node_id)
    if math.isclose(segment.diameter_mm, SUPPORTED_DIAMETER_MM, abs_tol=1e-9):
        clash_probe: Any = StraightGravitySolverInput(
            request_id=evidence_prefix,
            source_ir_sha256=request.source_ir_sha256,
            coordinate_reference=request.coordinate_reference,
            start=SolverEndpoint(
                node_id=start.node_id,
                x_m=start.x_m,
                y_m=start.y_m,
                ground_elevation_m=start.ground_elevation_m,
            ),
            end=SolverEndpoint(
                node_id=end.node_id,
                x_m=end.x_m,
                y_m=end.y_m,
                ground_elevation_m=end.ground_elevation_m,
            ),
            diameter_mm=segment.diameter_mm,
            inner_diameter_mm=segment.inner_diameter_mm,
            outer_diameter_mm=segment.outer_diameter_mm,
            wall_thickness_mm=segment.wall_thickness_mm,
            material=segment.material,
            design_slope=segment.design_slope,
            surface_context=segment.surface_context,
            start_invert_m=solved["start_invert_m"],
            collision_context=request.collision_context,
        )
    else:
        # 动态管径（DN400+）：直管 Solver v0.4 的能力面仍是 DN300，不能借道其
        # Pydantic 校验；这里以同构代理把轴线/口径/净距上下文传给净距检查。
        clash_probe = SimpleNamespace(
            request_id=evidence_prefix,
            collision_context=request.collision_context,
            start=SimpleNamespace(x_m=start.x_m, y_m=start.y_m),
            end=SimpleNamespace(x_m=end.x_m, y_m=end.y_m),
            diameter_mm=segment.diameter_mm,
            inner_diameter_mm=segment.inner_diameter_mm,
            outer_diameter_mm=segment.outer_diameter_mm,
            wall_thickness_mm=segment.wall_thickness_mm,
        )
    if math.isclose(segment.diameter_mm, SUPPORTED_DIAMETER_MM, abs_tol=1e-9):
        spacing_limit = design_limits.max_manhole_spacing
    else:
        spacing_limit = cached_municipal_design_limits(
            diameter_mm=segment.diameter_mm
        ).max_manhole_spacing
    checks = [
        ("diameter", "diameter_in_spec", design_limits.min_diameter_sewage, segment.diameter_mm),
        ("slope", "slope_in_spec", _per_diameter_slope_limit(segment.diameter_mm, design_limits), segment.design_slope),
        ("cover", "cover_depth_in_spec", design_limits.min_cover_depth(segment.surface_context), minimum_cover_m),
        ("spacing", "manhole_spacing_in_spec", spacing_limit, solved["horizontal_length_m"]),
    ]
    return [
        *[_spec_evidence(
            evidence_id=f"{evidence_prefix}-{suffix}", check_name=check_name,
            limit=limit, measured_value=measured, subject_id=segment.segment_id,
            describe=lambda actual, required, name=check_name: f"{name}: measured={actual:g}; required={required:g}",
            tolerance=INVERT_TOLERANCE_M,
        ) for suffix, check_name, limit, measured in checks],
        *_clash_evidence(
            clash_probe,
            rule_set=trusted_rule_set,
            ir_id=ir_id,
            segment_id=segment.segment_id,
            start_invert_m=solved["start_invert_m"],
            end_invert_m=solved["end_invert_m"],
        ),
    ]


def _port_payload(port_id: str, direction: str, x_m: float, y_m: float, z_m: float) -> dict[str, Any]:
    return {
        "port_id": port_id,
        "direction": direction,
        "position": {"x_m": x_m, "y_m": y_m, "z_m": z_m},
        "ifc_class": "IfcDistributionPort",
    }


def _unique_models(items: tuple[Any, ...], field: str, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        identity = str(getattr(item, field))
        if identity in result:
            raise ValueError(f"{label} id 重复: {identity!r}")
        result[identity] = item
    return result


def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(adjacency[current] - visited, reverse=True))
    return visited


def _stable_topological_order(
    *,
    nodes: set[str],
    adjacency: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> tuple[str, ...]:
    remaining_incoming = {node_id: len(incoming[node_id]) for node_id in nodes}
    ready = sorted(node_id for node_id, degree in remaining_incoming.items() if degree == 0)
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for neighbor in sorted(adjacency[current]):
            remaining_incoming[neighbor] -= 1
            if remaining_incoming[neighbor] == 0:
                ready.append(neighbor)
                ready.sort()
    if len(result) != len(nodes):
        cyclic = sorted(nodes - set(result))
        raise ValueError(f"gravity network 不允许有向环路，cycle nodes={cyclic}")
    return tuple(result)


__all__ = [
    "EXCEPTION_MIN_COVER_M",
    "INVERT_TOLERANCE_M",
    "MAX_WELL_SPACING_DESIGN_BANDS",
    "MIN_COVER_BY_SURFACE_M",
    "MIN_OBSTACLE_CLEARANCE_M",
    "MIN_SLOPE_BY_DIAMETER_BANDS",
    "NETWORK_UTILITY_SOLVER_INPUT_VERSION",
    "NETWORK_UTILITY_SOLVER_NAME",
    "NETWORK_UTILITY_SOLVER_VERSION",
    "SUPPORTED_DIAMETER_RANGE_MM",
    "NetworkConstraintFlags",
    "NetworkGravitySolverInput",
    "NetworkSolverNode",
    "NetworkSolverSegment",
    "lookup_pipe_normative_limits",
    "solve_network_gravity_utility",
]
