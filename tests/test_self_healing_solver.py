"""规则自愈式生成求解器单元测试 (Self-Healing Solver Tests)。"""

from __future__ import annotations


from openbimagent.utility import (
    GridRouteSolverInput,
    NetworkGravitySolverInput,
    compile_municipal_rule_set,
    solve_self_healing_route,
)
from test_grid_route_solver import route_payload
from test_network_utility_solver import network_payload


def test_self_healing_clean_converges_in_first_iteration() -> None:
    """无障碍物时首轮直接收敛。"""
    n_dict = network_payload()
    r_dict = route_payload(width=11, height=3)
    r_dict["source_ir_sha256"] = n_dict["source_ir_sha256"]
    for sample in r_dict["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    r_dict["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    r_dict["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route_input = GridRouteSolverInput.model_validate(r_dict)
    network_input = NetworkGravitySolverInput.model_validate(n_dict)
    ruleset = compile_municipal_rule_set()

    res = solve_self_healing_route(
        network_input=network_input,
        route_input=route_input,
        rule_set=ruleset,
        synthetic_obstacles=(),
        max_iterations=3,
    )

    assert res.converged is True
    assert res.iterations_spent == 1
    assert res.final_ir is not None
    assert res.rule_evidence is not None
    assert len(res.iteration_history) == 1
    assert res.iteration_history[0].rule_fail_count == 0


def test_self_healing_reroutes_around_synthetic_obstacle() -> None:
    """遇到路径中间障碍物时，自适应缓冲区膨胀避障并于第2轮收敛。"""
    n_dict = network_payload()
    r_dict = route_payload(width=11, height=5)
    r_dict["source_ir_sha256"] = n_dict["source_ir_sha256"]
    for sample in r_dict["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    r_dict["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    r_dict["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route_input = GridRouteSolverInput.model_validate(r_dict)
    network_input = NetworkGravitySolverInput.model_validate(n_dict)
    ruleset = compile_municipal_rule_set()


    # 放置在常规直线路径点 (5, 0) 上的障碍物
    obstacles = [(5, 0)]

    res = solve_self_healing_route(
        network_input=network_input,
        route_input=route_input,
        rule_set=ruleset,
        synthetic_obstacles=obstacles,
        max_iterations=3,
    )

    assert res.converged is True
    assert res.iterations_spent == 2
    assert len(res.resolved_violations) >= 1
    assert res.final_ir is not None
    assert len(res.iteration_history) == 2


def test_self_healing_fails_closed_when_completely_blocked() -> None:
    """当障碍物完全切断走廊且无法绕行时，优雅失败关闭并不崩溃。"""
    n_dict = network_payload()
    r_dict = route_payload(width=11, height=1)
    r_dict["source_ir_sha256"] = n_dict["source_ir_sha256"]
    for sample in r_dict["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    r_dict["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    r_dict["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    route_input = GridRouteSolverInput.model_validate(r_dict)
    network_input = NetworkGravitySolverInput.model_validate(n_dict)
    ruleset = compile_municipal_rule_set()

    # 高度仅为 1 的走廊中，(5, 0) 完全切断
    obstacles = [(5, 0)]

    res = solve_self_healing_route(
        network_input=network_input,
        route_input=route_input,
        rule_set=ruleset,
        synthetic_obstacles=obstacles,
        max_iterations=2,
    )

    assert res.converged is False
    assert len(res.log) > 0


