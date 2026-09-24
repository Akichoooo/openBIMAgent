"""几何数值误差指标：候选解（如 LLM-Direct 直出）vs 求解器参考解。

现有评测口径（llm_direct_baseline / academic_bench）是布尔通过率——"LLM 输出的标高
合不合规"。本模块补上数值级：候选逐节点标高与确定性求解器参考解的偏差（米）、
逐管段坡度偏差、覆土深度偏差。论文表格从"合规率"提升到"误差毫米/米级"。

参考解来源：solve_network_gravity_utility（确定性内核,同输入同输出）,逐节点 invert
取自 compiled IR 管段的 start/end_invert_m(经 ports 映射到节点)。

纯函数无 IO/无网络：候选 inverts 由调用方传入（LLM 输出解析后,或任何对照方法）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from openbimagent.utility import solve_network_gravity_utility


@dataclass(frozen=True)
class GeometricDiffReport:
    """候选 vs 参考的几何数值误差汇总（均为绝对值）。"""

    node_count: int
    segment_count: int
    invert_error_mean_m: float
    invert_error_max_m: float
    slope_error_mean: float
    slope_error_max: float
    cover_error_mean_m: float

    def summary(self) -> str:
        return (
            f"nodes={self.node_count} segments={self.segment_count} "
            f"invert_err mean={self.invert_error_mean_m:.3f}m max={self.invert_error_max_m:.3f}m "
            f"slope_err mean={self.slope_error_mean:.4f} max={self.slope_error_max:.4f} "
            f"cover_err mean={self.cover_error_mean_m:.3f}m"
        )


def reference_inverts(payload: dict[str, Any]) -> dict[str, float]:
    """求解器参考解:node_id → 管底标高(m)。求解失败抛原生异常(调用方决定计不计)。"""
    solved = solve_network_gravity_utility(payload)
    compiled = solved.compiled_ir
    ports = {p.port_id: n.node_id for n in compiled.nodes for p in n.ports}
    inverts: dict[str, float] = {}
    for seg in compiled.segments:
        inverts.setdefault(ports[seg.start_port_id], seg.start_invert_m)
        inverts.setdefault(ports[seg.end_port_id], seg.end_invert_m)
    return inverts


def compare_inverts(
    payload: dict[str, Any],
    reference: dict[str, float],
    candidate: dict[str, float],
) -> GeometricDiffReport:
    """候选 vs 参考的数值 diff;候选缺节点按该节点误差 = NaN 不计入统计(计数如实)。"""
    nodes = {n["node_id"]: n for n in payload["nodes"]}
    invert_errors: list[float] = []
    cover_errors: list[float] = []
    for node_id, ref in reference.items():
        cand = candidate.get(node_id)
        if cand is None or not isinstance(cand, (int, float)):
            continue
        invert_errors.append(abs(float(cand) - ref))
        ground = nodes[node_id]["ground_elevation_m"] if node_id in nodes else None
        if ground is not None:
            cover_errors.append(abs((ground - float(cand)) - (ground - ref)))

    slope_errors: list[float] = []
    for seg in payload["segments"]:
        s_id, e_id = seg["start_node_id"], seg["end_node_id"]
        if s_id not in reference or e_id not in reference:
            continue
        s_node, e_node = nodes[s_id], nodes[e_id]
        length = math.hypot(e_node["x_m"] - s_node["x_m"], e_node["y_m"] - s_node["y_m"])
        if length <= 0:
            continue
        ref_slope = (reference[s_id] - reference[e_id]) / length
        c_s, c_e = candidate.get(s_id), candidate.get(e_id)
        if c_s is None or c_e is None or not isinstance(c_s, (int, float)) or not isinstance(c_e, (int, float)):
            continue
        cand_slope = (float(c_s) - float(c_e)) / length
        slope_errors.append(abs(cand_slope - ref_slope))

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return GeometricDiffReport(
        node_count=len(reference),
        segment_count=len(payload["segments"]),
        invert_error_mean_m=round(_mean(invert_errors), 4),
        invert_error_max_m=round(max(invert_errors), 4) if invert_errors else 0.0,
        slope_error_mean=round(_mean(slope_errors), 4),
        slope_error_max=round(max(slope_errors), 4) if slope_errors else 0.0,
        cover_error_mean_m=round(_mean(cover_errors), 4),
    )


__all__ = ["GeometricDiffReport", "compare_inverts", "reference_inverts"]
