"""A1 轨迹质量指标测试：从真实 M1.5 T7 结果派生四个论文级指标（离线确定性）。"""

from __future__ import annotations

import pytest

from openbimagent.benchmark.m1_5_t7 import run_m1_5_t7_benchmark
from openbimagent.benchmark.trajectory_metrics import (
    compute_trajectory_metrics,
    scenario_task_success,
    scenario_trajectory_ok,
)


@pytest.fixture(scope="module")
def t7_results(tmp_path_factory):
    """跑代表性场景（PASS=B1 / FAIL=B4 / UNKNOWN=B7）拿真实 result；module 级复用避免重跑。"""
    root = tmp_path_factory.mktemp("traj")
    report = run_m1_5_t7_benchmark(
        output_dir=root, scenario_ids=("B1", "B4", "B7"), repetitions=2
    )
    return report.scenarios


def test_task_success_all_correct(t7_results) -> None:
    # B1=PASS、B4=FAIL、B7=UNKNOWN 均应 observed==expected（含正确拒绝无效场景）
    assert all(scenario_task_success(r) for r in t7_results)


def test_trajectory_metrics_four_indicators(t7_results) -> None:
    m = compute_trajectory_metrics(t7_results)
    assert m.total_cases == 3
    assert m.measured is True
    assert m.task_success_rate == 100.0
    # 确定性内核 + fail-closed：轨迹全部可审计（非 scrappy win）
    assert m.trajectory_accuracy == 100.0
    # B4(FAIL)/B7(UNKNOWN) 严格未升格为 PASS
    assert m.fail_closed_compliance == 100.0
    # 证据链只计 B1(PASS)，六环全通过
    assert m.evidence_chain_completeness == 100.0
    assert m.provenance


def test_fail_closed_only_counts_non_pass(t7_results) -> None:
    m = compute_trajectory_metrics(t7_results)
    # B1 期望 PASS → fail_closed_ok=None（不计入）；B4/B7 计入
    fc_cases = [s for s in m.per_scenario if s.fail_closed_ok is not None]
    assert {s.scenario_id for s in fc_cases} == {"B4", "B7"}


def test_evidence_chain_only_counts_pass_scenarios(t7_results) -> None:
    m = compute_trajectory_metrics(t7_results)
    b1 = next(s for s in m.per_scenario if s.scenario_id == "B1")
    b4 = next(s for s in m.per_scenario if s.scenario_id == "B4")
    b7 = next(s for s in m.per_scenario if s.scenario_id == "B7")
    assert b1.chain_links_total == 6  # PASS 场景六环
    assert b4.chain_links_total == 0  # FAIL 不计入分母
    assert b7.chain_links_total == 0  # UNKNOWN 不计入分母


def test_trajectory_ok_implies_task_success(t7_results) -> None:
    # 轨迹可审计 ⊆ 结果正确：trajectory_ok 为真时 task_success 必为真
    for r in t7_results:
        if scenario_trajectory_ok(r):
            assert scenario_task_success(r)


def test_trajectory_metrics_matches_schema(t7_results) -> None:
    """A1 契约：TrajectoryMetrics 结构符合 schemas/trajectory_metrics.schema.json（工件即协议）。"""
    import json
    from dataclasses import asdict
    from pathlib import Path

    import jsonschema

    metrics = compute_trajectory_metrics(t7_results)
    schema_path = (
        Path(__file__).resolve().parents[1] / "schemas" / "trajectory_metrics.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # tuple → list 规范化，与 JSON 序列化后的实际工件形态一致
    payload = json.loads(json.dumps(asdict(metrics)))
    jsonschema.validate(payload, schema)
