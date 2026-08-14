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


@dataclass(frozen=True)
class SpatialNode:
    """空间图谱中的三维节点（检查井/接驳口）。"""

    node_id: str
    node_type: str
    x: float
    y: float
    rim_z: float
    invert_z: float
    depth_m: float
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


@dataclass(frozen=True)
class SpatialCrossing:
    """两条空间管线的三维交叉几何关系。"""

    segment_a: str
    segment_b: str
    vertical_clearance_m: float
    horizontal_distance_m: float
    euclidean_min_distance_m: float
    crossing_angle_deg: float
    is_clash: bool


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
            rim_z = n.ground_elevation_m if n.ground_elevation_m is not None else n.position.z_m + 1.5
            invert_z = n.position.z_m
            depth = round(abs(rim_z - invert_z), 4)
            s_node = SpatialNode(
                node_id=n.node_id,
                node_type=n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
                x=float(n.position.x_m),
                y=float(n.position.y_m),
                rim_z=float(rim_z),
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
            from_nid = port_to_node.get(seg.start_port_id)
            to_nid = port_to_node.get(seg.end_port_id)
            if from_nid is None or to_nid is None:
                continue

            start_pos = (
                float(seg.centerline[0].x_m),
                float(seg.centerline[0].y_m),
                float(seg.centerline[0].z_m),
            )
            end_pos = (
                float(seg.centerline[-1].x_m),
                float(seg.centerline[-1].y_m),
                float(seg.centerline[-1].z_m),
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

    def find_crossings(self) -> list[SpatialCrossing]:
        """计算图谱中管段之间的三维空间交叉与净距关系。"""
        crossings: list[SpatialCrossing] = []
        seg_list = list(self.segments.values())

        for i in range(len(seg_list)):
            for j in range(i + 1, len(seg_list)):
                s1 = seg_list[i]
                s2 = seg_list[j]
                if s1.from_node == s2.from_node or s1.to_node == s2.to_node or s1.from_node == s2.to_node or s1.to_node == s2.from_node:
                    continue

                # 垂直标高差估算
                avg_z1 = (s1.start_pos[2] + s1.end_pos[2]) / 2.0
                avg_z2 = (s2.start_pos[2] + s2.end_pos[2]) / 2.0
                vert_clearance = abs(avg_z1 - avg_z2) - ((s1.dn_mm + s2.dn_mm) / 2000.0)

                # 水平中点距离
                mid1 = ((s1.start_pos[0] + s1.end_pos[0]) / 2.0, (s1.start_pos[1] + s1.end_pos[1]) / 2.0)
                mid2 = ((s2.start_pos[0] + s2.end_pos[0]) / 2.0, (s2.start_pos[1] + s2.end_pos[1]) / 2.0)
                horiz_dist = math.hypot(mid1[0] - mid2[0], mid1[1] - mid2[1])

                # 夹角
                dx1 = s1.end_pos[0] - s1.start_pos[0]
                dy1 = s1.end_pos[1] - s1.start_pos[1]
                dx2 = s2.end_pos[0] - s2.start_pos[0]
                dy2 = s2.end_pos[1] - s2.start_pos[1]
                dot = dx1 * dx2 + dy1 * dy2
                mag1 = math.hypot(dx1, dy1)
                mag2 = math.hypot(dx2, dy2)
                cos_theta = dot / (mag1 * mag2) if (mag1 * mag2) > 0 else 1.0
                cos_theta = max(-1.0, min(1.0, cos_theta))
                angle_deg = math.degrees(math.acos(cos_theta))

                is_clash = (vert_clearance < 0.15) and (horiz_dist < 1.0)
                crossings.append(
                    SpatialCrossing(
                        segment_a=s1.segment_id,
                        segment_b=s2.segment_id,
                        vertical_clearance_m=round(vert_clearance, 3),
                        horizontal_distance_m=round(horiz_dist, 3),
                        euclidean_min_distance_m=round(math.hypot(horiz_dist, vert_clearance), 3),
                        crossing_angle_deg=round(angle_deg, 2),
                        is_clash=is_clash,
                    )
                )

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
