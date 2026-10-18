"""三维空间拓扑图谱引擎 (Spatial BIM Graph Engine)。

将 CompiledUtilityIR 与空间几何实体转化为 3D 空间拓扑图谱，提供：
  - 空间邻域范围快速检索 (find_nodes_in_radius，基于 Bounding-Box 预剪枝与欧氏精确距离)
  - 空间三维管线净距与交叉分析 (find_crossings)
  - 重力流水力 DAG 有向无环性与连通子图核验 (Kahn 拓扑排序算法)
  - Web UI / GraphRAG 拓扑序列化导出
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from openbimagent.utility.contracts import CompiledUtilityIR
from openbimagent.utility.geometry import axis_centerline, closest_segment_points, resolve_dimensions


@dataclass(frozen=True)
class SpatialNode:
    """空间图谱中的三维节点（检查井/接驳口）。"""

    node_id: str
    node_type: str
    x: float
    y: float
    rim_z: float | None
    invert_z: float
    depth_m: float | None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpatialSegment:
    """空间图谱中的三维管段（边）。"""

    segment_id: str
    from_node: str
    to_node: str
    dn_mm: int
    slope: float
    length_m: float
    start_pos: tuple[float, float, float]
    end_pos: tuple[float, float, float]
    outer_diameter_mm: float | None = None
    axis_known: bool = True
    path: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class SpatialCrossing:
    """两条空间管线的三维交叉几何关系。"""

    segment_a: str
    segment_b: str
    vertical_clearance_m: float | None
    horizontal_distance_m: float
    euclidean_min_distance_m: float | None
    crossing_angle_deg: float
    is_clash: bool | None
    surface_clearance_m: float | None = None
    status: str = "unknown"
    detail: str = ""


class SpatialBIMGraph:
    """地下管网与构筑物三维空间拓扑图。"""

    def __init__(self) -> None:
        self.nodes: dict[str, SpatialNode] = {}
        self.segments: dict[str, SpatialSegment] = {}
        self.adjacency: dict[str, list[str]] = {}
        self.in_degree: dict[str, int] = {}
        self.out_degree: dict[str, int] = {}

    @classmethod
    def build_from_ir(cls, ir: CompiledUtilityIR) -> SpatialBIMGraph:
        """从 CompiledUtilityIR v1 构建空间图谱。"""
        graph = cls()
        port_to_node: dict[str, str] = {}

        for n in ir.nodes:
            rim_z = n.ground_elevation_m
            invert_z = min(port.position.z_m for port in n.ports)
            depth = round(rim_z - invert_z, 4) if rim_z is not None else None
            s_node = SpatialNode(
                node_id=n.node_id,
                node_type=n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
                x=float(n.position.x_m),
                y=float(n.position.y_m),
                rim_z=float(rim_z) if rim_z is not None else None,
                invert_z=float(invert_z),
                depth_m=depth,
            )
            graph.nodes[n.node_id] = s_node
            graph.adjacency[n.node_id] = []
            graph.in_degree[n.node_id] = 0
            graph.out_degree[n.node_id] = 0

            for port in n.ports:
                port_to_node[port.port_id] = n.node_id

        for seg in ir.segments:
            axis = axis_centerline(seg)
            _, outer = resolve_dimensions(seg.inner_diameter_mm, seg.outer_diameter_mm, seg.wall_thickness_mm)
            line = axis or seg.centerline
            from_nid = port_to_node.get(seg.start_port_id)
            to_nid = port_to_node.get(seg.end_port_id)
            if from_nid is None or to_nid is None:
                continue

            start_pos = (
                float(line[0].x_m),
                float(line[0].y_m),
                float(line[0].z_m),
            )
            end_pos = (
                float(line[-1].x_m),
                float(line[-1].y_m),
                float(line[-1].z_m),
            )

            s_seg = SpatialSegment(
                segment_id=seg.segment_id,
                from_node=from_nid,
                to_node=to_nid,
                dn_mm=int(seg.diameter_mm),
                slope=float(seg.slope),
                length_m=float(seg.horizontal_length_m),
                start_pos=start_pos,
                end_pos=end_pos,
                outer_diameter_mm=outer,
                axis_known=axis is not None,
                path=tuple((point.x_m, point.y_m, point.z_m) for point in line),
            )
            graph.segments[seg.segment_id] = s_seg
            graph.adjacency[from_nid].append(to_nid)
            graph.out_degree[from_nid] = graph.out_degree.get(from_nid, 0) + 1
            graph.in_degree[to_nid] = graph.in_degree.get(to_nid, 0) + 1

        return graph

    def find_nodes_in_radius(self, center_xy: tuple[float, float], radius_m: float) -> list[SpatialNode]:
        """查询指定二维范围内的所有节点（先通过 Bounding-Box 预剪枝，再计算欧氏距离）。"""
        cx, cy = center_xy
        results: list[SpatialNode] = []
        for node in self.nodes.values():
            # Bounding-box 快速初筛
            if abs(node.x - cx) > radius_m or abs(node.y - cy) > radius_m:
                continue
            dist = math.hypot(node.x - cx, node.y - cy)
            if dist <= radius_m:
                results.append(node)
        return sorted(results, key=lambda n: math.hypot(n.x - cx, n.y - cy))

    def check_hydraulic_dag(self) -> tuple[bool, list[str]]:
        """检查管网拓扑是否有向无环 (DAG)，并返回拓扑排序序列。"""
        in_deg = dict(self.in_degree)
        queue = [nid for nid, d in in_deg.items() if d == 0]
        topo_order: list[str] = []

        while queue:
            curr = queue.pop(0)
            topo_order.append(curr)
            for neighbor in self.adjacency.get(curr, []):
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)

        is_dag = len(topo_order) == len(self.nodes)
        return is_dag, topo_order

    def calculate_total_network_length(self) -> float:
        """计算管网总长度（米）。"""
        return round(sum(s.length_m for s in self.segments.values()), 3)

    def find_crossings(self, *, required_clearance_m: float = 0.0, connection_exemption_m: float = 0.0, connection_exemptions_m: dict[str, float] | None = None) -> list[SpatialCrossing]:
        """Finite polyline capsule checks; zero clearance checks physical overlap only.

        A supplied connection_exemption_m excludes ONLY a ball around a shared
        endpoint. Its size is caller owned, not a fabricated normative clearance
        or chamber dimension. Other legs of connected polylines remain checked.
        Without a supplied connection boundary, shared endpoint contact is UNKNOWN.
        Values are kept unrounded so the verdict uses the actual threshold.
        """
        if not math.isfinite(required_clearance_m) or required_clearance_m < 0 or not math.isfinite(connection_exemption_m) or connection_exemption_m < 0:
            raise ValueError("clearance and connection exemption must be finite and nonnegative")
        crossings = []
        segments = sorted(self.segments.values(), key=lambda segment: segment.segment_id)
        for index, first in enumerate(segments):
            for second in segments[index + 1:]:
                path_a = first.path or (first.start_pos, first.end_pos)
                path_b = second.path or (second.start_pos, second.end_pos)
                shared = {first.from_node, first.to_node} & {second.from_node, second.to_node}
                shared_points = [first.start_pos if node == first.from_node else first.end_pos for node in shared]
                legs_a = list(zip(path_a, path_a[1:], strict=False))
                legs_b = list(zip(path_b, path_b[1:], strict=False))
                missing_connection = False
                oversized_connection = False
                for node in shared:
                    point = first.start_pos if node == first.from_node else first.end_pos
                    radius = (connection_exemptions_m or {}).get(node, connection_exemption_m)
                    if not math.isfinite(radius) or radius < 0:
                        raise ValueError("connection radii must be finite and nonnegative")
                    missing_connection |= radius == 0
                    if radius > 0 and (any(math.dist(vertex, point) <= radius for vertex in path_a if vertex != point) or any(math.dist(vertex, point) <= radius for vertex in path_b if vertex != point)):
                        oversized_connection = True
                    # Exempt only envelopes wholly inside the declared region.
                    trim_a = max(0.0, radius - first.outer_diameter_mm / 2000.0) if first.outer_diameter_mm is not None else 0.0
                    trim_b = max(0.0, radius - second.outer_diameter_mm / 2000.0) if second.outer_diameter_mm is not None else 0.0
                    legs_a = [clipped for leg in legs_a for clipped in _outside_connection_legs(leg, [point], trim_a)]
                    legs_b = [clipped for leg in legs_b for clipped in _outside_connection_legs(leg, [point], trim_b)]
                if not legs_a or not legs_b:
                    crossings.append(SpatialCrossing(first.segment_id, second.segment_id, None, 0.0, None, 0.0, None, None, "unknown", "connection region covers an entire pipe leg; unexamined geometry cannot establish clearance"))
                    continue
                pairs = [(a, b, c, d) for a, b in legs_a for c, d in legs_b]
                nearest = min((closest_segment_points(*pair) for pair in pairs), key=lambda item: item.distance_m)
                horizontal = min(closest_segment_points(*tuple((p[0], p[1], 0.0) for p in pair)).distance_m for pair in pairs)
                known = first.axis_known and second.axis_known and first.outer_diameter_mm is not None and second.outer_diameter_mm is not None
                radius_sum = (first.outer_diameter_mm + second.outer_diameter_mm) / 2000.0 if known else None
                surface = nearest.distance_m - radius_sum if radius_sum is not None else None
                local_contact = missing_connection and any(math.dist(nearest.first_point, p) <= 1e-6 and math.dist(nearest.second_point, p) <= 1e-6 for p in shared_points)
                clash = surface + 1e-6 < required_clearance_m if surface is not None else None
                if local_contact:
                    # Recheck all other polyline legs. Never suppress a whole connected pair.
                    remote = [closest_segment_points(*pair) for pair in pairs if not any(p in (pair[0], pair[1]) and p in (pair[2], pair[3]) for p in shared_points)]
                    if radius_sum is not None and any(item.distance_m - radius_sum + 1e-6 < required_clearance_m for item in remote):
                        clash = True
                    else:
                        clash = None
                endpoint_approximation = False
                if clash is True:
                    witnesses = [closest_segment_points(*pair) for pair in pairs]
                    interior_overlap = any(1e-8 < item.first_parameter < 1 - 1e-8 and 1e-8 < item.second_parameter < 1 - 1e-8 and item.distance_m - radius_sum + 1e-6 < required_clearance_m for item in witnesses) if radius_sum is not None else False
                    if not interior_overlap:
                        clash = None
                        endpoint_approximation = True
                if oversized_connection and clash is False:
                    clash = None
                va = tuple(b - a for a, b in zip(first.start_pos, first.end_pos, strict=True))
                vb = tuple(b - a for a, b in zip(second.start_pos, second.end_pos, strict=True))
                magnitude = math.hypot(*va[:2]) * math.hypot(*vb[:2])
                cosine = sum(a * b for a, b in zip(va[:2], vb[:2], strict=True)) / magnitude if magnitude else 1.0
                crossings.append(SpatialCrossing(
                    first.segment_id, second.segment_id,
                    abs(nearest.first_point[2] - nearest.second_point[2]) - radius_sum if radius_sum is not None else None,
                    horizontal, nearest.distance_m if first.axis_known and second.axis_known else None,
                    math.degrees(math.acos(max(-1.0, min(1.0, cosine)))), clash, surface,
                    "unknown" if clash is None else "fail" if clash else "pass",
                    "connection region reaches another endpoint or polyline vertex; local exemption is not established" if oversized_connection else "capsule end cap overlap requires exact end geometry" if endpoint_approximation else "capsule with hemispherical end caps; shared endpoint boundary missing" if local_contact else "capsule with hemispherical end caps; 3D surface clearance, horizontal axis distance reported separately",
                ))
        return crossings

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 兼容字典，供前端三维网络拓扑与 GraphRAG 视图使用。"""
        is_dag, topo_order = self.check_hydraulic_dag()
        return {
            "node_count": len(self.nodes),
            "segment_count": len(self.segments),
            "total_length_m": self.calculate_total_network_length(),
            "is_hydraulic_dag": is_dag,
            "topological_sequence": topo_order,
            "nodes": [
                {
                    "id": n.node_id,
                    "type": n.node_type,
                    "x": n.x,
                    "y": n.y,
                    "rim_z": n.rim_z,
                    "invert_z": n.invert_z,
                    "depth_m": n.depth_m,
                }
                for n in self.nodes.values()
            ],
            "segments": [
                {
                    "id": s.segment_id,
                    "from": s.from_node,
                    "to": s.to_node,
                    "dn": s.dn_mm,
                    "slope": s.slope,
                    "length_m": s.length_m,
                }
                for s in self.segments.values()
            ],
        }


def _outside_connection_legs(path: tuple[tuple[float, float, float], ...], centers: list[tuple[float, float, float]], radius: float) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Clip each straight leg against local spherical connection boundaries."""
    result = []
    for start, end in zip(path, path[1:], strict=False):
        spans = [(0.0, 1.0)]
        direction = tuple(b - a for a, b in zip(start, end, strict=True))
        length_squared = sum(value * value for value in direction)
        for center in centers if radius > 0 else []:
            offset = tuple(a - b for a, b in zip(start, center, strict=True))
            if length_squared <= 1e-24:
                if math.dist(start, center) < radius:
                    spans = []
                continue
            linear = sum(a * b for a, b in zip(offset, direction, strict=True))
            constant = sum(value * value for value in offset) - radius * radius
            discriminant = linear * linear - length_squared * constant
            if discriminant <= 0:
                continue
            low = (-linear - math.sqrt(discriminant)) / length_squared
            high = (-linear + math.sqrt(discriminant)) / length_squared
            spans = [(a, min(b, low)) for a, b in spans if a < min(b, low)] + [(max(a, high), b) for a, b in spans if max(a, high) < b]
        for low, high in spans:
            result.append((tuple(a + low * d for a, d in zip(start, direction, strict=True)), tuple(a + high * d for a, d in zip(start, direction, strict=True))))
    return result
