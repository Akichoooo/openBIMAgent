"""几何数值误差指标测试：reference_inverts（求解器参考解）/ compare_inverts（候选 diff）。"""

from __future__ import annotations

import pytest

from openbimagent.benchmark.academic_bench import MethodBenchmarkMetrics
from openbimagent.benchmark.geometric_diff import compare_inverts, reference_inverts
from openbimagent.benchmark.llm_direct_baseline import LLMBaselineConfig
from openbimagent.benchmark.m1_5_t7 import build_benchmark_scenarios


@pytest.fixture(scope="module")
def b1_payload() -> dict:
    sc = {s.scenario_id: s for s in build_benchmark_scenarios()}
    return sc["B1"].input_payload


def test_reference_inverts_deterministic(b1_payload: dict) -> None:
    first = reference_inverts(b1_payload)
    second = reference_inverts(b1_payload)
    assert first == second
    assert set(first) == {n["node_id"] for n in b1_payload["nodes"]}


def test_identical_candidate_zero_error(b1_payload: dict) -> None:
    ref = reference_inverts(b1_payload)
    report = compare_inverts(b1_payload, ref, dict(ref))
    assert report.invert_error_mean_m == 0.0
    assert report.invert_error_max_m == 0.0
    assert report.slope_error_mean == 0.0
    assert report.cover_error_mean_m == 0.0


def test_uniform_shift_keeps_slope_zero(b1_payload: dict) -> None:
    ref = reference_inverts(b1_payload)
    cand = {k: v + 0.1 for k, v in ref.items()}
    report = compare_inverts(b1_payload, ref, cand)
    assert report.invert_error_mean_m == pytest.approx(0.1, abs=1e-3)
    assert report.slope_error_mean == 0.0  # 平移不改坡度(几何事实,非 bug)


def test_single_node_offset_creates_slope_error(b1_payload: dict) -> None:
    ref = reference_inverts(b1_payload)
    cand = dict(ref)
    last = list(ref)[-1]
    cand[last] = ref[last] + 0.5
    report = compare_inverts(b1_payload, ref, cand)
    assert report.invert_error_max_m == pytest.approx(0.5, abs=1e-3)
    assert report.slope_error_max > 0.0


def test_missing_candidate_nodes_not_counted(b1_payload: dict) -> None:
    ref = reference_inverts(b1_payload)
    partial = dict(list(ref.items())[:1])
    report = compare_inverts(b1_payload, ref, partial)
    assert report.node_count == len(ref)  # 计数如实(参考侧全量)
    assert report.invert_error_max_m == 0.0  # 缺失不计入误差统计


def test_metrics_dataclass_geometric_fields_default_none() -> None:
    m = MethodBenchmarkMetrics(
        method_name="x", total_cases=1,
        topology_valid_rate=100.0, rule_compliance_rate=100.0, hydraulic_valid_rate=100.0,
        avg_latency_ms=1.0, avg_tool_calls=1.0, avg_token_count=0,
    )
    assert m.invert_error_mean_m is None
    assert m.invert_error_max_m is None
    assert m.slope_error_mean is None


def test_baseline_run_emits_geometric_errors(b1_payload: dict) -> None:
    """注入 post_fn 的闭环:LLM-Direct 基线运行后 metrics 带几何误差字段。"""
    import json as _json

    from openbimagent.benchmark.llm_direct_baseline import run_llm_direct_baseline

    ref = reference_inverts(b1_payload)
    content = _json.dumps({"nodes": [{"node_id": k, "invert_z": v + 0.25} for k, v in ref.items()]})

    class _Resp:
        def raise_for_status(self): ...
        def json(self):
            return {"choices": [{"message": {"content": content}}],
                    "usage": {"total_tokens": 10}}

    def post_fn(url, json=None, headers=None):
        return _Resp()

    cfg = LLMBaselineConfig(base_url="https://x.invalid/v1", model="m", api_key="k",
                            max_scenarios=1, repetitions=1)
    metrics = run_llm_direct_baseline(scenarios=("B1",), config=cfg, post_fn=post_fn)
    assert metrics is not None
    assert metrics.invert_error_mean_m == pytest.approx(0.25, abs=1e-3)
    assert metrics.invert_error_max_m == pytest.approx(0.25, abs=1e-3)
    assert metrics.slope_error_mean == 0.0  # 均匀偏移不改坡度
