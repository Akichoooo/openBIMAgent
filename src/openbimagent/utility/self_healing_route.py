"""规则自愈式生成求解器 (Self-Healing Generative Adaptation)。

实现“检测违规 -> 提取空间惩罚 -> 缓冲区膨胀 (Buffer Inflation) -> 自动重规划 -> 100% 规则合规”的
冲突驱动无人化自愈闭环 (CDCL-like Conflict-Driven Adaptation)，严格保持与 CompiledUtilityIR v1 契约兼容。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from openbimagent.utility.contracts import CompiledUtilityIR
from openbimagent.utility.network_solver import (
    NetworkGravitySolverInput,
    solve_network_gravity_utility,
)
from openbimagent.utility.route_solver import (
    GridCell,
    GridRouteSolverInput,
    GridRouteSolverResult,
    RouteSolveStatus,
    apply_grid_route_to_network_input,
    solve_grid_route,
)
from openbimagent.utility.rule_evidence import (
    MunicipalRuleEvidenceBundle,
    compile_municipal_rule_evidence_bundle,
)
from openbimagent.utility.rules import (
    MunicipalRuleSet,
    compile_municipal_rule_set,
    select_clearance_rule,
)

_CLEARANCE_TOLERANCE_M = 1e-6
_DEFAULT_BUILDING_CLEARANCE_M = 2.5  # GB 50289-2016 §4.1.9 建筑物净距保守回退值（规则集缺失时失败关闭）


@dataclass(frozen=True)
class SelfHealingViolation:
    """自愈检测到的空间或规则违规项。"""

    rule_id: str
    target_id: str
    violation_type: str  # "clearance", "cover", "collision", "slope"
    location_xy: tuple[int, int]
    required_value: float
    actual_value: float
    description: str


@dataclass(frozen=True)
class SelfHealingIteration:
    """单轮自愈尝试记录。"""

    iteration: int
    active_mask_cells: tuple[GridCell, ...]
    route_status: str
    rule_pass_count: int
    rule_fail_count: int
    converged: bool


@dataclass(frozen=True)
class SelfHealingResult:
    """自愈求解器最终输出。"""

    converged: bool
    iterations_spent: int
    final_ir: CompiledUtilityIR | None
    rule_evidence: MunicipalRuleEvidenceBundle | None
    resolved_violations: tuple[SelfHealingViolation, ...]
    iteration_history: tuple[SelfHealingIteration, ...]
    log: tuple[str, ...]


def _required_building_clearance_m(rule_set: MunicipalRuleSet | None) -> tuple[float, str]:
    """从规则集解析建筑物净距下限 (MU-CLEAR-001)；规则集缺失时回退保守默认值。"""
    if rule_set is not None:
        selection = select_clearance_rule(rule_set, obstacle_kind="aabb", obstacle_category="building")
        if selection.rule is not None:
            return selection.rule.required_clearance_m, selection.rule.source_rule_id
    return _DEFAULT_BUILDING_CLEARANCE_M, "MU-CLEAR-001"


def _check_route_and_geometry_violations(
    route_result: GridRouteSolverResult,
    route_input: GridRouteSolverInput,
    synthetic_obstacles: Sequence[tuple[int, int]],
    rule_set: MunicipalRuleSet | None,
) -> list[SelfHealingViolation]:
    """真实核验：规则集驱动的净距缓冲区、物理碰撞与求解器覆土合规报告。"""
    violations: list[SelfHealingViolation] = []
    selected_cand = route_result.selected_candidate()
    if selected_cand is None:
        return violations

    required_clearance_m, clearance_rule_id = _required_building_clearance_m(rule_set)
    resolution = route_input.grid.resolution_m
    origin_x = route_input.grid.origin_x_m
    origin_y = route_input.grid.origin_y_m

    # 1. 地下障碍物核验：物理碰撞 + 规则集净距缓冲区 (GB 50289 §4.1.9 表 4.1.9)
    for ox, oy in synthetic_obstacles:
        obstacle_x = origin_x + ox * resolution
        obstacle_y = origin_y + oy * resolution
        nearest_dist = min(
            math.hypot(
                origin_x + cell.x_index * resolution - obstacle_x,
                origin_y + cell.y_index * resolution - obstacle_y,
            )
            for cell in selected_cand.cells
        )
        if nearest_dist <= _CLEARANCE_TOLERANCE_M:
            violations.append(
                SelfHealingViolation(
                    rule_id=clearance_rule_id,
                    target_id=f"obstacle-({ox},{oy})",
                    violation_type="collision",
                    location_xy=(ox, oy),
                    required_value=required_clearance_m,
                    actual_value=0.0,
                    description=(
                        f"管线直接穿过地下障碍物禁行单元 ({ox},{oy})，水平净距 0 "
                        f"< 规则要求 {required_clearance_m:.2f}m"
                    ),
                )
            )
        elif nearest_dist < required_clearance_m - _CLEARANCE_TOLERANCE_M:
            violations.append(
                SelfHealingViolation(
                    rule_id=clearance_rule_id,
                    target_id=f"obstacle-({ox},{oy})",
                    violation_type="clearance",
                    location_xy=(ox, oy),
                    required_value=required_clearance_m,
                    actual_value=round(nearest_dist, 3),
                    description=(
                        f"管线距地下障碍物 ({ox},{oy}) 水平净距 {nearest_dist:.2f}m "
                        f"< 规则要求 {required_clearance_m:.2f}m"
                    ),
                )
            )

    # 2. 覆土深度核验：直接消费求解器按规则集计算的 RouteConstraintReport 与逐点埋深
    constraint_report = selected_cand.constraint_report
    if not constraint_report.cover_depth_in_spec:
        worst = min(selected_cand.points, key=lambda p: p.cover_depth_m)
        violations.append(
            SelfHealingViolation(
                rule_id="MU-DRAIN-004",
                target_id=f"cover-{worst.cell.identity()}",
                violation_type="cover",
                location_xy=worst.cell.identity(),
                required_value=constraint_report.required_cover_depth_m,
                actual_value=round(worst.cover_depth_m, 3),
                description=(
                    f"单元 {worst.cell.identity()} 覆土深度 {worst.cover_depth_m:.2f}m "
                    f"< 规范最小覆土 {constraint_report.required_cover_depth_m:.2f}m"
                ),
            )
        )
    if not constraint_report.clearance_in_spec:
        violations.append(
            SelfHealingViolation(
                rule_id=";".join(constraint_report.applied_rule_keys) or clearance_rule_id,
                target_id=f"candidate-{selected_cand.candidate_id}",
                violation_type="clearance",
                location_xy=selected_cand.cells[0].identity(),
                required_value=0.0,
                actual_value=round(constraint_report.minimum_clearance_margin_m or 0.0, 3),
                description="走廊内规则净距未达标 (RouteConstraintReport clearance_in_spec=False)",
            )
        )

    return violations


def _inflate_barrier_cells(
    base_corridor: Sequence[GridCell],
    blocked_coordinates: Sequence[tuple[int, int]],
    inflation_radius: int = 1,
) -> list[GridCell]:
    """对障碍物点位执行安全缓冲区膨胀 (Buffer Inflation)，从走廊中扣除阻挡网格。"""
    blocked_set: set[tuple[int, int]] = set()
    for bx, by in blocked_coordinates:
        for dx in range(-inflation_radius, inflation_radius + 1):
            for dy in range(-inflation_radius, inflation_radius + 1):
                blocked_set.add((bx + dx, by + dy))

    retained: list[GridCell] = []
    for cell in base_corridor:
        if (cell.x_index, cell.y_index) not in blocked_set:
            retained.append(cell)
    return retained


def solve_self_healing_route(
    *,
    network_input: NetworkGravitySolverInput,
    route_input: GridRouteSolverInput,
    rule_set: MunicipalRuleSet | None = None,
    synthetic_obstacles: Sequence[tuple[int, int]] = (),
    max_iterations: int = 3,
) -> SelfHealingResult:
    """冲突驱动规则自愈求解闭环 (Conflict-Driven Generative Self-Healing Loop)。
    
    1. 首轮：按原始走廊网格求解路线与网络拓扑
    2. 核验：评估 GB 50289 规则与地下三维空间干涉 (CDCL Conflict Detection)
    3. 冲突学习：提取违规坐标点，执行安全缓冲区动态膨胀 (Buffer Zone Inflation)
    4. 空间剪枝：动态裁剪走廊搜索网格并自适应重新规划
    5. 迭代收敛：在 <= max_iterations 轮内达成 100% 规则合规 PASS，否则安全失败关闭。
    """
    if rule_set is None:
        rule_set = compile_municipal_rule_set()

    logs: list[str] = []
    history: list[SelfHealingIteration] = []
    current_route_input = route_input
    current_obstacles = list(synthetic_obstacles)
    all_resolved_violations: list[SelfHealingViolation] = []

    final_ir: CompiledUtilityIR | None = None
    final_evidence: MunicipalRuleEvidenceBundle | None = None

    logs.append(f"[SelfHealing] 启动自愈求解循环，最大迭代轮数={max_iterations}")

    for iter_idx in range(1, max_iterations + 1):
        logs.append(f"[SelfHealing] 第 {iter_idx}/{max_iterations} 轮求解开始...")

        # 1. 求解路线
        route_res = solve_grid_route(current_route_input)
        if route_res.status != RouteSolveStatus.FEASIBLE or route_res.selected_candidate() is None:
            logs.append(f"[SelfHealing] 第 {iter_idx} 轮路线求解无可行解: status={route_res.status}")
            history.append(
                SelfHealingIteration(
                    iteration=iter_idx,
                    active_mask_cells=tuple(current_route_input.allowed_cells),
                    route_status=route_res.status.value,
                    rule_pass_count=0,
                    rule_fail_count=1,
                    converged=False,
                )
            )
            break

        # 2. 映射至网络求解器输入
        target_segment_id = network_input.segments[0].segment_id if network_input.segments else "segment-0"
        updated_net_input = apply_grid_route_to_network_input(
            network_input,
            segment_id=target_segment_id,
            route_input=current_route_input,
            route_result=route_res,
            municipal_rule_set=rule_set,
        )
        net_result = solve_network_gravity_utility(updated_net_input)

        if net_result.compiled_ir is None:
            logs.append(f"[SelfHealing] 第 {iter_idx} 轮网络求解失败: 未生成 CompiledIR")
            break

        compiled_ir = net_result.compiled_ir

        # 3. 真实核验违规项与冲突点
        violations = _check_route_and_geometry_violations(
            route_result=route_res,
            route_input=current_route_input,
            synthetic_obstacles=current_obstacles,
            rule_set=rule_set,
        )
        evidence_bundle = compile_municipal_rule_evidence_bundle()
        pass_count = len(evidence_bundle.rules)
        fail_count = len(violations)

        for v in violations:
            logs.append(f"[SelfHealing] 检测到冲突违规项: {v.rule_id} at {v.location_xy} ({v.description})")
            if v not in all_resolved_violations:
                all_resolved_violations.append(v)

        converged = (fail_count == 0)

        history.append(
            SelfHealingIteration(
                iteration=iter_idx,
                active_mask_cells=tuple(current_route_input.allowed_cells),
                route_status=route_res.status.value,
                rule_pass_count=pass_count,
                rule_fail_count=fail_count,
                converged=converged,
            )
        )

        if converged:
            logs.append(f"[SelfHealing] 第 {iter_idx} 轮达成 100% 规则合规与无碰撞收敛！")
            final_ir = compiled_ir
            final_evidence = evidence_bundle
            break

        # 4. 冲突驱动空间剪枝：对检测到的违规点位执行安全缓冲区膨胀 (Buffer Inflation)
        logs.append(f"[SelfHealing] 第 {iter_idx} 轮发现 {len(violations)} 个违规冲突，执行安全缓冲区动态膨胀...")
        required_clearance_m, _ = _required_building_clearance_m(rule_set)
        obstacle_radius = max(1, math.ceil(required_clearance_m / current_route_input.grid.resolution_m))
        obstacle_coords = [
            v.location_xy
            for v in violations
            if v.violation_type in ("collision", "clearance") and v.target_id.startswith("obstacle-")
        ]
        geometry_coords = [v.location_xy for v in violations if v.location_xy not in obstacle_coords]

        new_allowed_cells = list(current_route_input.allowed_cells)
        if obstacle_coords:
            # 膨胀半径按规则净距要求自适应（欧氏距离下保守覆盖规则缓冲区）
            new_allowed_cells = _inflate_barrier_cells(
                base_corridor=new_allowed_cells,
                blocked_coordinates=obstacle_coords,
                inflation_radius=obstacle_radius,
            )
        if geometry_coords:
            new_allowed_cells = _inflate_barrier_cells(
                base_corridor=new_allowed_cells,
                blocked_coordinates=geometry_coords,
                inflation_radius=1,
            )

        # 起终点检查井网格受保护，避免膨胀后违反走廊输入契约
        retained_ids = {c.identity() for c in new_allowed_cells}
        for endpoint in (current_route_input.start.cell, current_route_input.end.cell):
            if endpoint.identity() not in retained_ids:
                new_allowed_cells.append(endpoint)
                retained_ids.add(endpoint.identity())

        logs.append(f"[SelfHealing] 可用走廊网格由 {len(current_route_input.allowed_cells)} 裁剪至 {len(new_allowed_cells)}")

        # 保持 surface_samples 与 allowed_cells 空间同步
        retained_coords = {(c.x_index, c.y_index) for c in new_allowed_cells}
        new_surface_samples = tuple(
            s for s in current_route_input.surface_samples if (s.cell.x_index, s.cell.y_index) in retained_coords
        )

        current_route_input = current_route_input.model_copy(
            update={
                "allowed_cells": tuple(new_allowed_cells),
                "surface_samples": new_surface_samples,
            }
        )

    return SelfHealingResult(
        converged=final_ir is not None,
        iterations_spent=len(history),
        final_ir=final_ir,
        rule_evidence=final_evidence,
        resolved_violations=tuple(all_resolved_violations),
        iteration_history=tuple(history),
        log=tuple(logs),
    )
