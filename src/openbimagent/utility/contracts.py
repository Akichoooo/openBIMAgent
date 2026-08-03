"""市政管网 compiled utility IR v1 的版本化确定性契约。

Planner 语义 IR 不携带绝对坐标；本模块只接收 Solver 已经求解出的坐标、拓扑和工程属性。
Pydantic 负责跨引用和数值一致性校验，JSON Schema 负责工件协议漂移门禁。
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

COMPILED_UTILITY_IR_VERSION = "1.0"
GEOMETRY_TOLERANCE_M = 1e-6
SLOPE_TOLERANCE = 1e-9


class UtilitySystemType(StrEnum):
    STORMWATER = "stormwater"
    WASTEWATER = "wastewater"
    COMBINED = "combined"
    WATER = "water"
    GAS = "gas"
    POWER = "power"
    TELECOM = "telecom"


class FlowRegime(StrEnum):
    GRAVITY = "gravity"
    PRESSURE = "pressure"
    PASSIVE = "passive"


class NodeType(StrEnum):
    MANHOLE = "manhole"
    INLET = "inlet"
    OUTLET = "outlet"
    JUNCTION = "junction"
    VALVE = "valve"
    EQUIPMENT = "equipment"
    TERMINAL = "terminal"


class PortDirection(StrEnum):
    INLET = "inlet"
    OUTLET = "outlet"
    BIDIRECTIONAL = "bidirectional"


class EvidenceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class EvidenceSubjectType(StrEnum):
    NETWORK = "network"
    SYSTEM = "system"
    NODE = "node"
    PORT = "port"
    SEGMENT = "segment"


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Coordinate3D(StrictFrozenModel):
    x_m: float
    y_m: float
    z_m: float


class CoordinateReference(StrictFrozenModel):
    crs_id: str = Field(min_length=1, max_length=256)
    origin: Coordinate3D
    horizontal_unit: str = Field(default="m", pattern=r"^m$")
    vertical_unit: str = Field(default="m", pattern=r"^m$")
    vertical_datum: str | None = Field(default=None, max_length=256)


class UtilitySystem(StrictFrozenModel):
    system_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    system_type: UtilitySystemType
    flow_regime: FlowRegime
    ifc_class: str = Field(default="IfcDistributionSystem", pattern=r"^Ifc[A-Za-z0-9]+$")
    ifc_predefined_type: str | None = Field(default=None, max_length=128)


class UtilityPort(StrictFrozenModel):
    port_id: str = Field(min_length=1, max_length=256)
    direction: PortDirection
    position: Coordinate3D
    ifc_class: str = Field(default="IfcDistributionPort", pattern=r"^Ifc[A-Za-z0-9]+$")


class UtilityNode(StrictFrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    system_id: str = Field(min_length=1, max_length=256)
    node_type: NodeType
    position: Coordinate3D
    ports: tuple[UtilityPort, ...] = Field(min_length=1)
    ground_elevation_m: float | None = None
    ifc_class: str = Field(default="IfcDistributionChamberElement", pattern=r"^Ifc[A-Za-z0-9]+$")
    ifc_predefined_type: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _ports_match_node_position(self) -> "UtilityNode":
        for port in self.ports:
            if not _same_xy(port.position, self.position):
                raise ValueError(f"port {port.port_id!r} 的平面位置必须与 node {self.node_id!r} 一致")
        return self


class PipeSegment(StrictFrozenModel):
    segment_id: str = Field(min_length=1, max_length=256)
    system_id: str = Field(min_length=1, max_length=256)
    start_port_id: str = Field(min_length=1, max_length=256)
    end_port_id: str = Field(min_length=1, max_length=256)
    centerline: tuple[Coordinate3D, ...] = Field(min_length=2)
    horizontal_length_m: float = Field(gt=0)
    start_invert_m: float
    end_invert_m: float
    slope: float
    diameter_mm: float = Field(gt=0)
    material: str = Field(min_length=1, max_length=256)
    min_cover_depth_m: float | None = Field(default=None, ge=0)
    ifc_class: str = Field(default="IfcPipeSegment", pattern=r"^Ifc[A-Za-z0-9]+$")
    ifc_predefined_type: str = Field(default="RIGIDSEGMENT", min_length=1, max_length=128)


class RuleEvidence(StrictFrozenModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    rule_id: str = Field(min_length=1, max_length=256)
    check_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    status: EvidenceStatus
    subject_type: EvidenceSubjectType
    subject_id: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=10_000)
    measured_value: float | str | bool | None = None
    limit_value: float | str | bool | None = None
    unit: str | None = Field(default=None, max_length=64)
    source_clause: str | None = Field(default=None, max_length=1024)


class CompiledUtilityIR(StrictFrozenModel):
    """Solver 输出的可审计市政管网编译 IR；所有跨引用和数值关系失败关闭。"""

    protocol_version: str = Field(default=COMPILED_UTILITY_IR_VERSION, pattern=r"^1(?:\.\d+)?$")
    ir_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    solver_name: str = Field(min_length=1, max_length=256)
    solver_version: str = Field(min_length=1, max_length=128)
    coordinate_reference: CoordinateReference
    systems: tuple[UtilitySystem, ...] = Field(min_length=1)
    nodes: tuple[UtilityNode, ...] = Field(min_length=1)
    segments: tuple[PipeSegment, ...] = Field(min_length=1)
    evidence: tuple[RuleEvidence, ...] = ()

    @model_validator(mode="after")
    def _validate_compiled_network(self) -> "CompiledUtilityIR":
        systems = _unique_by(self.systems, "system_id", "system")
        nodes = _unique_by(self.nodes, "node_id", "node")
        segments = _unique_by(self.segments, "segment_id", "segment")
        evidence = _unique_by(self.evidence, "evidence_id", "evidence")

        ports: dict[str, tuple[UtilityNode, UtilityPort]] = {}
        for node in self.nodes:
            if node.system_id not in systems:
                raise ValueError(f"node {node.node_id!r} 引用未知 system {node.system_id!r}")
            for port in node.ports:
                if port.port_id in ports:
                    raise ValueError(f"port id 重复: {port.port_id!r}")
                ports[port.port_id] = (node, port)

        connected_ports: set[str] = set()
        system_node_ids: dict[str, set[str]] = {system_id: set() for system_id in systems}
        system_segment_ids: dict[str, set[str]] = {system_id: set() for system_id in systems}
        undirected_adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        directed_adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        incident_segment_ids: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        incoming_segment_ids: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        outgoing_segment_ids: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for node in self.nodes:
            system_node_ids[node.system_id].add(node.node_id)

        for segment in self.segments:
            if segment.system_id not in systems:
                raise ValueError(f"segment {segment.segment_id!r} 引用未知 system {segment.system_id!r}")
            if segment.start_port_id == segment.end_port_id:
                raise ValueError(f"segment {segment.segment_id!r} 起终 port 不能相同")
            try:
                start_node, start_port = ports[segment.start_port_id]
                end_node, end_port = ports[segment.end_port_id]
            except KeyError as exc:
                raise ValueError(f"segment {segment.segment_id!r} 引用未知 port {exc.args[0]!r}") from exc
            if start_node.system_id != segment.system_id or end_node.system_id != segment.system_id:
                raise ValueError(f"segment {segment.segment_id!r} 与端口所属 system 不一致")
            if start_node.node_id == end_node.node_id:
                raise ValueError(f"segment {segment.segment_id!r} 起终 port 不能属于同一 node")
            if start_port.direction is PortDirection.INLET:
                raise ValueError(f"segment {segment.segment_id!r} 的 start port 不能声明为 inlet")
            if end_port.direction is PortDirection.OUTLET:
                raise ValueError(f"segment {segment.segment_id!r} 的 end port 不能声明为 outlet")
            for port_id in (segment.start_port_id, segment.end_port_id):
                if port_id in connected_ports:
                    raise ValueError(f"port {port_id!r} 被多个 segment 重复占用")
                connected_ports.add(port_id)
            _validate_segment_geometry(segment, start_port, end_port)
            if systems[segment.system_id].flow_regime is FlowRegime.GRAVITY and segment.slope < -SLOPE_TOLERANCE:
                raise ValueError(f"gravity segment {segment.segment_id!r} 不允许逆坡")
            system_segment_ids[segment.system_id].add(segment.segment_id)
            undirected_adjacency[start_node.node_id].add(end_node.node_id)
            undirected_adjacency[end_node.node_id].add(start_node.node_id)
            directed_adjacency[start_node.node_id].add(end_node.node_id)
            incident_segment_ids[start_node.node_id].add(segment.segment_id)
            incident_segment_ids[end_node.node_id].add(segment.segment_id)
            outgoing_segment_ids[start_node.node_id].add(segment.segment_id)
            incoming_segment_ids[end_node.node_id].add(segment.segment_id)

        _validate_network_topology(
            systems=systems,
            nodes=nodes,
            system_node_ids=system_node_ids,
            system_segment_ids=system_segment_ids,
            undirected_adjacency=undirected_adjacency,
            directed_adjacency=directed_adjacency,
            incident_segment_ids=incident_segment_ids,
            incoming_segment_ids=incoming_segment_ids,
            outgoing_segment_ids=outgoing_segment_ids,
        )

        known_subjects: dict[EvidenceSubjectType, set[str]] = {
            EvidenceSubjectType.NETWORK: {self.ir_id},
            EvidenceSubjectType.SYSTEM: set(systems),
            EvidenceSubjectType.NODE: set(nodes),
            EvidenceSubjectType.PORT: set(ports),
            EvidenceSubjectType.SEGMENT: set(segments),
        }
        for item in evidence.values():
            if item.subject_id not in known_subjects[item.subject_type]:
                raise ValueError(
                    f"evidence {item.evidence_id!r} 引用未知 {item.subject_type.value} {item.subject_id!r}"
                )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """返回按稳定身份排序的 JSON 兼容结构，与调用方输入顺序无关。"""
        payload = self.model_dump(mode="json")
        payload["systems"] = sorted(payload["systems"], key=lambda item: item["system_id"])
        payload["nodes"] = sorted(payload["nodes"], key=lambda item: item["node_id"])
        for node in payload["nodes"]:
            node["ports"] = sorted(node["ports"], key=lambda item: item["port_id"])
        payload["segments"] = sorted(payload["segments"], key=lambda item: item["segment_id"])
        payload["evidence"] = sorted(payload["evidence"], key=lambda item: item["evidence_id"])
        return payload

    def canonical_json(self) -> bytes:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def domain_evidence(self) -> dict[str, dict[str, bool | None | str]]:
        """把逐对象规则证据确定性聚合为现有 domain_gate 可消费的 check_name 映射。"""
        grouped: dict[str, list[RuleEvidence]] = {}
        for item in sorted(self.evidence, key=lambda entry: entry.evidence_id):
            grouped.setdefault(item.check_name, []).append(item)

        result: dict[str, dict[str, bool | None | str]] = {}
        for check_name, items in sorted(grouped.items()):
            statuses = {item.status for item in items}
            if EvidenceStatus.FAIL in statuses:
                ok: bool | None = False
            elif EvidenceStatus.UNKNOWN in statuses:
                ok = None
            else:
                ok = True
            result[check_name] = {
                "ok": ok,
                "detail": f"compiled_utility_ir:{len(items)} evidence item(s); sha256={self.canonical_sha256()}",
            }
        return result


def _unique_by(items: tuple[Any, ...], field: str, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        identity = str(getattr(item, field))
        if identity in result:
            raise ValueError(f"{label} id 重复: {identity!r}")
        result[identity] = item
    return result


def _validate_network_topology(
    *,
    systems: dict[str, UtilitySystem],
    nodes: dict[str, UtilityNode],
    system_node_ids: dict[str, set[str]],
    system_segment_ids: dict[str, set[str]],
    undirected_adjacency: dict[str, set[str]],
    directed_adjacency: dict[str, set[str]],
    incident_segment_ids: dict[str, set[str]],
    incoming_segment_ids: dict[str, set[str]],
    outgoing_segment_ids: dict[str, set[str]],
) -> None:
    for system_id, system in systems.items():
        node_ids = system_node_ids[system_id]
        if not node_ids:
            raise ValueError(f"system {system_id!r} 没有 node")
        if not system_segment_ids[system_id]:
            raise ValueError(f"system {system_id!r} 没有 segment")

        for node_id in sorted(node_ids):
            degree = len(incident_segment_ids[node_id])
            if degree == 0:
                raise ValueError(f"system {system_id!r} 存在孤立 node {node_id!r}")
            node = nodes[node_id]
            if degree >= 3 and node.node_type is not NodeType.JUNCTION:
                raise ValueError(
                    f"node {node_id!r} 连接 {degree} 个 segment，分支或汇流节点必须声明为 junction"
                )
            if node.node_type is NodeType.JUNCTION and degree < 3:
                raise ValueError(f"junction node {node_id!r} 至少连接 3 个 segment，实际 {degree}")
            if node.node_type is NodeType.JUNCTION and (
                not incoming_segment_ids[node_id] or not outgoing_segment_ids[node_id]
            ):
                raise ValueError(f"junction node {node_id!r} 必须同时具有入流和出流 segment")

        visited = _reachable_nodes(min(node_ids), undirected_adjacency, allowed=node_ids)
        if visited != node_ids:
            missing = sorted(node_ids - visited)
            raise ValueError(f"system {system_id!r} 存在不连通子图，未连通 nodes={missing}")

        if system.flow_regime is FlowRegime.GRAVITY and _has_directed_cycle(node_ids, directed_adjacency):
            raise ValueError(f"gravity system {system_id!r} 不允许有向环路")


def _reachable_nodes(
    start: str,
    adjacency: dict[str, set[str]],
    *,
    allowed: set[str],
) -> set[str]:
    visited: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(adjacency[current] & allowed - visited, reverse=True))
    return visited


def _has_directed_cycle(node_ids: set[str], adjacency: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for neighbor in sorted(adjacency[node_id] & node_ids):
            if visit(neighbor):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in sorted(node_ids) if node_id not in visited)


def _validate_segment_geometry(segment: PipeSegment, start_port: UtilityPort, end_port: UtilityPort) -> None:
    first = segment.centerline[0]
    last = segment.centerline[-1]
    if not _same_point(first, start_port.position):
        raise ValueError(f"segment {segment.segment_id!r} centerline 起点与 start port 不一致")
    if not _same_point(last, end_port.position):
        raise ValueError(f"segment {segment.segment_id!r} centerline 终点与 end port 不一致")
    if not math.isclose(segment.start_invert_m, first.z_m, abs_tol=GEOMETRY_TOLERANCE_M):
        raise ValueError(f"segment {segment.segment_id!r} start_invert_m 与起点标高不一致")
    if not math.isclose(segment.end_invert_m, last.z_m, abs_tol=GEOMETRY_TOLERANCE_M):
        raise ValueError(f"segment {segment.segment_id!r} end_invert_m 与终点标高不一致")

    horizontal_length = sum(
        math.hypot(right.x_m - left.x_m, right.y_m - left.y_m)
        for left, right in zip(segment.centerline, segment.centerline[1:], strict=False)
    )
    if not math.isclose(
        segment.horizontal_length_m,
        horizontal_length,
        rel_tol=GEOMETRY_TOLERANCE_M,
        abs_tol=GEOMETRY_TOLERANCE_M,
    ):
        raise ValueError(f"segment {segment.segment_id!r} horizontal_length_m 与 centerline 不一致")
    expected_slope = (segment.start_invert_m - segment.end_invert_m) / segment.horizontal_length_m
    if not math.isclose(segment.slope, expected_slope, rel_tol=SLOPE_TOLERANCE, abs_tol=SLOPE_TOLERANCE):
        raise ValueError(f"segment {segment.segment_id!r} slope 与标高差/水平长度不一致")


def _same_xy(left: Coordinate3D, right: Coordinate3D) -> bool:
    return all(
        math.isclose(a, b, abs_tol=GEOMETRY_TOLERANCE_M)
        for a, b in ((left.x_m, right.x_m), (left.y_m, right.y_m))
    )


def _same_point(left: Coordinate3D, right: Coordinate3D) -> bool:
    return _same_xy(left, right) and math.isclose(left.z_m, right.z_m, abs_tol=GEOMETRY_TOLERANCE_M)


__all__ = [
    "COMPILED_UTILITY_IR_VERSION",
    "CompiledUtilityIR",
    "Coordinate3D",
    "CoordinateReference",
    "EvidenceStatus",
    "EvidenceSubjectType",
    "FlowRegime",
    "NodeType",
    "PipeSegment",
    "PortDirection",
    "RuleEvidence",
    "UtilityNode",
    "UtilityPort",
    "UtilitySystem",
    "UtilitySystemType",
]
