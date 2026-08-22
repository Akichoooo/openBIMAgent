"""规则自愈消融电池 (Self-Healing Ablation Battery)。

通过微内核能力调度 + Profile 补丁层真实测量自愈闭环的贡献度：
  - ON  行: 默认绑定 ``solver:self_healing`` (冲突驱动自愈，缓冲区膨胀 + 重规划)
  - OFF 行: 激活 ``profile.ablation.no_self_healing`` 补丁 (max_iterations=1 单轮直连基线)

实验变量 = Profile 补丁重定向，而非代码分支——对标 DSH patch layer 的
"任务特化运行时 = 不同能力绑定" 语义，为论文消融实验提供机制化、可复现的数据来源。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from openbimagent.utility import (
    GridRouteSolverInput,
    NetworkGravitySolverInput,
    compile_municipal_rule_set,
)

ABLATION_PROFILE_ID = "profile.ablation.no_self_healing"
_CAPABILITY = "solver:self_healing"
_DEFAULT_PROVIDER = "plugin.core.municipal_utility"
_GRID_WIDTH = 11
_END_X = _GRID_WIDTH - 1


@dataclass(frozen=True)
class SelfHealingCase:
    """单个自愈消融场景：固定 11 宽走廊，变化高度与地下障碍物布局。"""

    case_id: str
    description: str
    height: int
    obstacles: tuple[tuple[int, int], ...]


def build_default_ablation_cases() -> tuple[SelfHealingCase, ...]:
    """默认 6 场景电池：2 个无障碍对照 + 3 个可绕行障碍 + 1 个封死走廊。

    障碍布局均满足：与起终点 (0,0)/(10,0) 保持欧氏净距 >= 2.5m 规则缓冲区，
    保证违规只出现在路径中段（自愈可通过走廊剪枝修复）。
    """
    return (
        SelfHealingCase("SH-1", "无障碍三行走廊 (对照组)", 3, ()),
        SelfHealingCase("SH-2", "路径中段单障碍 (5,0)", 5, ((5, 0),)),
        SelfHealingCase("SH-3", "双障碍 (4,2)+(6,2)", 7, ((4, 2), (6, 2))),
        SelfHealingCase("SH-4", "单行封死走廊 (不可绕行)", 1, ((5, 0),)),
        SelfHealingCase("SH-5", "无障碍七行走廊 (对照组)", 7, ()),
        SelfHealingCase("SH-6", "高位单障碍 (6,1) 六行走廊", 6, ((6, 1),)),
    )


@dataclass(frozen=True)
class AblationMethodStats:
    """单方法 (ON/OFF) 的电池统计。"""

    label: str
    total_cases: int
    converged_count: int
    route_feasible_count: int
    avg_iterations: float
    avg_latency_ms: float
    case_results: tuple[tuple[str, bool, bool, int], ...]  # (case_id, route_feasible, converged, iterations)


def _network_input_dict() -> dict:
    """最小三节点重力网络（source -> junction -> out-a/out-b），与单测夹具同构。"""
    return {
        "protocol_version": "0.1",
        "request_id": "self-healing-ablation-network",
        "source_ir_sha256": "c" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "system_id": "sys-wastewater",
        "system_name": "污水重力系统",
        "nodes": [
            {
                "node_id": "source",
                "node_type": "manhole",
                "x_m": 0.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": 10.0,
            },
            {
                "node_id": "junction",
                "node_type": "junction",
                "x_m": 10.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
            {
                "node_id": "out-a",
                "node_type": "manhole",
                "x_m": 20.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
            {
                "node_id": "out-b",
                "node_type": "manhole",
                "x_m": 10.0,
                "y_m": 10.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
            },
        ],
        "segments": [
            {
                "segment_id": "pipe-001",
                "start_node_id": "source",
                "end_node_id": "junction",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
            {
                "segment_id": "pipe-002",
                "start_node_id": "junction",
                "end_node_id": "out-a",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
            {
                "segment_id": "pipe-003",
                "start_node_id": "junction",
                "end_node_id": "out-b",
                "diameter_mm": 300.0,
                "material": "concrete",
                "design_slope": 0.003,
                "surface_context": "driveway",
            },
        ],
    }


def _route_input_dict(case: SelfHealingCase, rule_set_sha256: str) -> dict:
    """与单测夹具同构的走廊网格输入：全网格 allowed + 均匀地表高程 11.0m。"""
    allowed = [
        {"x_index": x_index, "y_index": y_index}
        for x_index in range(_GRID_WIDTH)
        for y_index in range(case.height)
    ]
    return {
        "protocol_version": "0.1",
        "request_id": f"self-healing-ablation-{case.case_id}",
        # 必须与 network 输入的 source_ir_sha256 一致 (apply_grid_route_to_network_input 契约)
        "source_ir_sha256": "c" * 64,
        "municipal_rule_set_sha256": rule_set_sha256,
        "coordinate_reference": {
            "crs_id": "LOCAL:PROJECT-M",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "grid": {
            "origin_x_m": 0.0,
            "origin_y_m": 0.0,
            "resolution_m": 1.0,
            "width": _GRID_WIDTH,
            "height": case.height,
        },
        "start": {
            "node_id": "source",
            "cell": {"x_index": 0, "y_index": 0},
            "invert_anchor_m": 10.0,
        },
        "end": {
            "node_id": "junction",
            "cell": {"x_index": _END_X, "y_index": 0},
        },
        "allowed_cells": allowed,
        "surface_samples": [
            {"cell": cell, "ground_elevation_m": 11.0} for cell in allowed
        ],
        "obstacles": [],
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
        "max_candidates": 3,
        "max_search_expansions": 100000,
    }


def _invoke_battery(registry, cases: Sequence[SelfHealingCase], *, patched: bool) -> AblationMethodStats:
    """对一个方法 (patched=False 自愈 ON / patched=True 补丁直连 OFF) 跑完整电池。"""
    rule_set = compile_municipal_rule_set()
    network_input = NetworkGravitySolverInput.model_validate(_network_input_dict())
    results: list[tuple[str, bool, bool, int]] = []
    latencies: list[float] = []
    iterations_total = 0

    for case in cases:
        route_input = GridRouteSolverInput.model_validate(
            _route_input_dict(case, rule_set.canonical_sha256)
        )
        kwargs = dict(
            network_input=network_input,
            route_input=route_input,
            rule_set=rule_set,
            synthetic_obstacles=list(case.obstacles),
            max_iterations=3,
        )
        t0 = time.perf_counter()
        res = registry.invoke(_CAPABILITY, **kwargs)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        route_feasible = any(it.route_status == "feasible" for it in res.iteration_history)
        results.append((case.case_id, route_feasible, res.converged, res.iterations_spent))
        iterations_total += res.iterations_spent

    total = len(results)
    return AblationMethodStats(
        label="OFF (Profile 补丁直连)" if patched else "ON (冲突驱动自愈)",
        total_cases=total,
        converged_count=sum(1 for _, _, converged, _ in results if converged),
        route_feasible_count=sum(1 for _, feasible, _, _ in results if feasible),
        avg_iterations=round(iterations_total / max(1, total), 2),
        avg_latency_ms=round(sum(latencies) / max(1, len(latencies)), 1),
        case_results=tuple(results),
    )


def build_demo_invocation(case_id: str = "SH-2") -> dict:
    """构造演示场景的 ``solver:self_healing`` 调用参数（公开 API，供服务端演示用）。"""
    case = next(c for c in build_default_ablation_cases() if c.case_id == case_id)
    rule_set = compile_municipal_rule_set()
    network_input = NetworkGravitySolverInput.model_validate(_network_input_dict())
    route_input = GridRouteSolverInput.model_validate(
        _route_input_dict(case, rule_set.canonical_sha256)
    )
    return dict(
        network_input=network_input,
        route_input=route_input,
        rule_set=rule_set,
        synthetic_obstacles=list(case.obstacles),
        max_iterations=3,
    )


def run_self_healing_ablation(
    cases: Sequence[SelfHealingCase] | None = None,
    *,
    registry=None,
) -> tuple[AblationMethodStats, AblationMethodStats]:
    """执行自愈 ON/OFF 对照电池，返回 (on_stats, off_stats)。

    OFF 行通过激活 ``profile.ablation.no_self_healing`` 补丁层实现——同一
    ``registry.invoke("solver:self_healing")`` 切入点，仅能力绑定不同；
    运行结束后自动停用补丁并还原默认绑定。
    """
    # 延迟导入避免与 academic_bench/plugin 注册循环
    from openbimagent.core.plugin import create_default_plugin_registry

    reg = registry if registry is not None else create_default_plugin_registry()
    battery = tuple(cases) if cases is not None else build_default_ablation_cases()

    on_stats = _invoke_battery(reg, battery, patched=False)

    reg.activate_profile(ABLATION_PROFILE_ID)
    try:
        off_stats = _invoke_battery(reg, battery, patched=True)
    finally:
        reg.deactivate_profile(ABLATION_PROFILE_ID)

    return on_stats, off_stats
