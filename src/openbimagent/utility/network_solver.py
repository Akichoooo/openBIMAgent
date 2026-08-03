"""M1.5 多节点重力污水管网的确定性 Solver v0.1。

输入显式给出节点 XY、地面标高、DAG 拓扑、逐段坡度和源节点管底锚点。
Solver 按稳定拓扑序传播标高：普通节点沿用来管最低管底，汇流节点也以最低来管
为出流基准；显式更低锚点表示跌水，任何高于最低来管的锚点均失败关闭。

首版只支持 DN300 混凝土重力污水管，不做路线寻优、水力计算或管径优化。
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
from openbimagent.utility.contracts import CoordinateReference, StrictFrozenModel
from openbimagent.utility.rules import MunicipalRuleSet, compile_municipal_rule_set
from openbimagent.utility.solver import (
    MAX_DN300_TO_DN600_MANHOLE_SPACING_M,
    MIN_COVER_BY_SURFACE_M,
    MIN_DN300_CONCRETE_SLOPE,
    MIN_SEWAGE_DIAMETER_MM,
    CollisionContext,
    SolverEndpoint,
    StraightGravitySolverInput,
    UtilitySolverError,
    UtilitySolverResult,
    _clash_evidence,
    _evidence,
    _unknown_evidence,
)

NETWORK_UTILITY_SOLVER_INPUT_VERSION = "0.1"
NETWORK_UTILITY_SOLVER_NAME = "municipal-network-gravity-solver"
NETWORK_UTILITY_SOLVER_VERSION = "0.1.0"
INVERT_TOLERANCE_M = 1e-9


class NetworkSolverNode(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    node_type: Literal["manhole", "junction", "inlet", "outlet", "terminal"]
    x_m: float
    y_m: float
    ground_elevation_m: float
    invert_anchor_m: float | None = None


class NetworkSolverSegment(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    start_node_id: str = Field(min_length=1, max_length=256)
    end_node_id: str = Field(min_length=1, max_length=256)
    diameter_mm: float = Field(default=MIN_SEWAGE_DIAMETER_MM, gt=0)
    material: Literal["concrete"] = "concrete"
    design_slope: float = Field(default=MIN_DN300_CONCRETE_SLOPE, ge=0)
    surface_context: Literal["driveway", "sidewalk"] = "driveway"


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
            if not math.isclose(segment.diameter_mm, MIN_SEWAGE_DIAMETER_MM, abs_tol=1e-9):
                raise ValueError(f"network Solver v0.1 仅支持 DN{MIN_SEWAGE_DIAMETER_MM:g}")
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
        diameter_m = segment.diameter_mm / 1000.0
        required_cover_m = MIN_COVER_BY_SURFACE_M[segment.surface_context]
        start_cover_m = start.ground_elevation_m - (solved["start_invert_m"] + diameter_m)
        end_cover_m = end.ground_elevation_m - (solved["end_invert_m"] + diameter_m)
        minimum_cover_m = min(start_cover_m, end_cover_m)
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
                required_cover_m=required_cover_m,
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
        "protocol_version": "1.0",
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
    minimum_cover_m: float,
    required_cover_m: float,
    trusted_rule_set: MunicipalRuleSet,
) -> list[dict[str, Any]]:
    evidence_prefix = f"{request.request_id}-{segment.segment_id}"
    start = next(node for node in request.nodes if node.node_id == segment.start_node_id)
    end = next(node for node in request.nodes if node.node_id == segment.end_node_id)
    straight_input = StraightGravitySolverInput(
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
        material=segment.material,
        design_slope=segment.design_slope,
        surface_context=segment.surface_context,
        start_invert_m=solved["start_invert_m"],
        collision_context=request.collision_context,
    )
    return [
        _evidence(
            evidence_id=f"{evidence_prefix}-diameter",
            rule_id="MU-DRAIN-001",
            check_name="diameter_in_spec",
            passed=segment.diameter_mm >= MIN_SEWAGE_DIAMETER_MM,
            subject_type="segment",
            subject_id=segment.segment_id,
            detail=f"污水管径 DN{segment.diameter_mm:g} mm，最小允许 DN{MIN_SEWAGE_DIAMETER_MM:g} mm",
            measured_value=segment.diameter_mm,
            limit_value=MIN_SEWAGE_DIAMETER_MM,
            unit="mm",
            source_clause="GB 50014-2021 §5.2.10 表 5.2.10",
        ),
        _evidence(
            evidence_id=f"{evidence_prefix}-slope",
            rule_id="MU-DRAIN-004",
            check_name="slope_in_spec",
            passed=segment.design_slope >= MIN_DN300_CONCRETE_SLOPE,
            subject_type="segment",
            subject_id=segment.segment_id,
            detail=(
                f"DN300 混凝土污水管设计坡度 {segment.design_slope:.6f}，"
                f"最小允许 {MIN_DN300_CONCRETE_SLOPE:.6f}"
            ),
            measured_value=segment.design_slope,
            limit_value=MIN_DN300_CONCRETE_SLOPE,
            unit="ratio",
            source_clause="GB 50014-2021 §5.2.10 表 5.2.10",
        ),
        _evidence(
            evidence_id=f"{evidence_prefix}-cover",
            rule_id="MU-ELEV-001" if segment.surface_context == "driveway" else "MU-ELEV-002",
            check_name="cover_depth_in_spec",
            passed=minimum_cover_m >= required_cover_m - INVERT_TOLERANCE_M,
            subject_type="segment",
            subject_id=segment.segment_id,
            detail=(
                f"管段两端最小实际覆土 {minimum_cover_m:.6f} m，"
                f"{segment.surface_context} 要求不少于 {required_cover_m:.3f} m"
            ),
            measured_value=minimum_cover_m,
            limit_value=required_cover_m,
            unit="m",
            source_clause="GB 50014-2021 §5.3.7",
        ),
        _evidence(
            evidence_id=f"{evidence_prefix}-spacing",
            rule_id="MU-WELL-001",
            check_name="manhole_spacing_in_spec",
            passed=solved["horizontal_length_m"] <= MAX_DN300_TO_DN600_MANHOLE_SPACING_M,
            subject_type="segment",
            subject_id=segment.segment_id,
            detail=(
                f"DN300 管段井距 {solved['horizontal_length_m']:.6f} m，"
                f"最大允许 {MAX_DN300_TO_DN600_MANHOLE_SPACING_M:.1f} m"
            ),
            measured_value=solved["horizontal_length_m"],
            limit_value=MAX_DN300_TO_DN600_MANHOLE_SPACING_M,
            unit="m",
            source_clause="GB 50014-2021 §5.4.4 表 5.4.4",
        ),
        *_clash_evidence(
            straight_input,
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
    "INVERT_TOLERANCE_M",
    "NETWORK_UTILITY_SOLVER_INPUT_VERSION",
    "NETWORK_UTILITY_SOLVER_NAME",
    "NETWORK_UTILITY_SOLVER_VERSION",
    "NetworkGravitySolverInput",
    "NetworkSolverNode",
    "NetworkSolverSegment",
    "solve_network_gravity_utility",
]
