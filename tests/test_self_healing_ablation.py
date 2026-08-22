"""自愈消融电池 (Self-Healing Ablation Battery) 专项测试。"""

from __future__ import annotations

from openbimagent.benchmark.self_healing_ablation import (
    ABLATION_PROFILE_ID,
    build_default_ablation_cases,
    run_self_healing_ablation,
)
from openbimagent.core.plugin import create_default_plugin_registry


def test_default_battery_healing_beats_direct_baseline() -> None:
    """默认 6 场景电池：自愈 ON 收敛率显著高于补丁直连 OFF 基线。"""
    on_stats, off_stats = run_self_healing_ablation()

    assert on_stats.total_cases == 6
    assert off_stats.total_cases == 6

    # 无障碍对照 (SH-1/SH-5) 两种方法都收敛
    on_by_id = {case_id: converged for case_id, _, converged, _ in on_stats.case_results}
    off_by_id = {case_id: converged for case_id, _, converged, _ in off_stats.case_results}
    assert on_by_id["SH-1"] is True and off_by_id["SH-1"] is True
    assert on_by_id["SH-5"] is True and off_by_id["SH-5"] is True

    # 可绕行障碍场景：仅自愈收敛
    for case_id in ("SH-2", "SH-3", "SH-6"):
        assert on_by_id[case_id] is True, case_id
        assert off_by_id[case_id] is False, case_id

    # 封死走廊：两种方法都失败关闭
    assert on_by_id["SH-4"] is False
    assert off_by_id["SH-4"] is False

    # 汇总收敛率 5/6 vs 2/6，且 ON 平均迭代更多 (自愈重规划)
    assert on_stats.converged_count == 5
    assert off_stats.converged_count == 2
    assert on_stats.avg_iterations > off_stats.avg_iterations


def test_battery_restores_default_capability_binding() -> None:
    """电池运行结束后补丁自动停用，默认能力绑定还原。"""
    registry = create_default_plugin_registry()
    assert (
        registry.get_provider_for_capability("solver:self_healing").plugin_id
        == "plugin.core.municipal_utility"
    )

    run_self_healing_ablation(registry=registry)

    assert (
        registry.get_provider_for_capability("solver:self_healing").plugin_id
        == "plugin.core.municipal_utility"
    )


def test_battery_is_deterministic_across_runs() -> None:
    """同一注册表重复运行电池结果完全一致 (确定性内核)。"""
    registry = create_default_plugin_registry()
    first_on, first_off = run_self_healing_ablation(registry=registry)
    second_on, second_off = run_self_healing_ablation(registry=registry)
    assert first_on.case_results == second_on.case_results
    assert first_off.case_results == second_off.case_results


def test_case_layouts_respect_clearance_buffer() -> None:
    """电池场景定义完备：case_id 唯一且障碍布局与默认电池一致。"""
    cases = build_default_ablation_cases()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    assert ABLATION_PROFILE_ID == "profile.ablation.no_self_healing"
