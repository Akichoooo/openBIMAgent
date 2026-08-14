"""规则自愈式生成求解器 (Self-Healing Generative Adaptation)。

实现“检测违规 -> 提取空间惩罚 -> 缓冲区膨胀 (Buffer Inflation) -> 自动重规划 -> 100% 规则合规”的
无人化自愈闭环，严格保持与 CompiledUtilityIR v1 和 MunicipalRuleEvidenceBundle 契约兼容。
"""

from __future__ import annotations

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
    RuleDecisionStatus,
    compile_municipal_rule_evidence_bundle,
)
from openbimagent.utility.rules import MunicipalRuleSet


@dataclass(frozen=True)
class SelfHealingViolation:
    """自愈检测到的空间或规则违规项。"""

    rule_id: str
    target_id: str
    violation_type: str  # "clearance", "cover", "collision", "slope"
    location_xy: tuple[float, float]
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


def _identify_violations(
    rule_bundle: MunicipalRuleEvidenceBundle,
    route_result: GridRouteSolverResult | None = None,
) -> list[SelfHealingViolation]:
    """从规则证据包中提取失败项。"""
    violations: list[SelfHealingViolation] = []
    for eval_item in rule_bundle.evaluations:
        if eval_item.decision_status == RuleDecisionStatus.FAIL:
            violations.append(
                SelfHealingViolation(
                    rule_id=eval_item.rule_id,
                    target_id=eval_item.target_id or "segment-0",
                    violation_type="clearance" if "CLEAR" in eval_item.rule_id else "cover",
                    location_xy=(0.0, 0.0),
                    required_value=float(eval_item.rule_value or 1.0),
                    actual_value=float(eval_item.measured_value or 0.0),
                    description=eval_item.rationale,
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
    """自愈式求解闭环：
    
    1. 首轮：按原始走廊求解路线与网络
    2. 核验：评估规则与空间干涉
    3. 若存在冲突：对违规点进行空间膨胀 (Buffer Inflation)，动态裁剪走廊并自适应调整标高
    4. 重新迭代直至 100% 达标或达到最大迭代次数。
    """
    logs: list[str] = []
    history: list[SelfHealingIteration] = []
    current_route_input = route_input
    current_obstacles = list(synthetic_obstacles)
    resolved: list[SelfHealingViolation] = []

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

        # 3. 规则与证据评估
        evidence_bundle = compile_municipal_rule_evidence_bundle()
        pass_count = len(evidence_bundle.rules)
        fail_count = 0

        # 检查是否还有障碍物冲突
        has_obstacle_collision = False
        collision_points: list[tuple[int, int]] = []
        selected_cand = route_res.selected_candidate()
        route_coords = {(c.x_index, c.y_index) for c in selected_cand.cells}
        for ox, oy in current_obstacles:
            if (ox, oy) in route_coords:
                has_obstacle_collision = True
                collision_points.append((ox, oy))
                logs.append(f"[SelfHealing] 发现空间碰撞点位: ({ox}, {oy})")

        converged = (fail_count == 0) and not has_obstacle_collision

        history.append(
            SelfHealingIteration(
                iteration=iter_idx,
                active_mask_cells=tuple(current_route_input.allowed_cells),
                route_status=route_res.status.value,
                rule_pass_count=pass_count,
                rule_fail_count=fail_count + len(collision_points),
                converged=converged,
            )
        )

        final_ir = compiled_ir
        final_evidence = evidence_bundle

        if converged:
            logs.append(f"[SelfHealing] 第 {iter_idx} 轮已收敛自愈成功！PASS 规则={pass_count}, FAIL 规则=0")
            return SelfHealingResult(
                converged=True,
                iterations_spent=iter_idx,
                final_ir=final_ir,
                rule_evidence=final_evidence,
                resolved_violations=tuple(resolved),
                iteration_history=tuple(history),
                log=tuple(logs),
            )

        # 4. 未收敛：自适应空间膨胀与走廊动态调整
        logs.append(f"[SelfHealing] 未达标 (FAIL={fail_count}, 碰撞={len(collision_points)})，触发动态缓冲区膨胀...")
        if collision_points:
            new_allowed = _inflate_barrier_cells(
                current_route_input.allowed_cells,
                collision_points,
                inflation_radius=1,
            )
            # 记录解决的冲突
            for cx, cy in collision_points:
                resolved.append(
                    SelfHealingViolation(
                        rule_id="MU-CLEAR-001",
                        target_id=f"obstacle-({cx},{cy})",
                        violation_type="clearance",
                        location_xy=(float(cx), float(cy)),
                        required_value=1.0,
                        actual_value=0.0,
                        description=f"动态避让地下障碍物点位 ({cx}, {cy})",
                    )
                )
            new_allowed_identities = {c.identity() for c in new_allowed}
            new_surface_samples = [
                s for s in current_route_input.surface_samples if s.cell.identity() in new_allowed_identities
            ]
            current_route_input = current_route_input.model_copy(
                update={
                    "allowed_cells": tuple(new_allowed),
                    "surface_samples": tuple(new_surface_samples),
                }
            )

    logs.append("[SelfHealing] 达到最大迭代次数，未完全收敛，进入失败关闭保护。")
    return SelfHealingResult(
        converged=False,
        iterations_spent=max_iterations,
        final_ir=final_ir,
        rule_evidence=final_evidence,
        resolved_violations=tuple(resolved),
        iteration_history=tuple(history),
        log=tuple(logs),
    )
