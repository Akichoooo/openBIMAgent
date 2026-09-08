"""D 学习曲线测试：离线诚实占位 + post_fn mock 验证链路 + exemplar 构造。"""

from __future__ import annotations

import json

from openbimagent.benchmark.learning_curve import (
    _approx_correct_inverts,
    _build_exemplar_pool,
    run_learning_curve,
)
from openbimagent.benchmark.llm_direct_baseline import LLMBaselineConfig
from openbimagent.benchmark.m1_5_t7 import build_benchmark_scenarios


def _b1_payload():
    return {s.scenario_id: s for s in build_benchmark_scenarios()}["B1"].input_payload


def test_offline_no_key_is_unmeasured(monkeypatch, tmp_path) -> None:
    # 隔离本地 toml 配置 + 环境变量，确保离线诚实降级（绝不造合成曲线、不真调 LLM）
    import openbimagent.benchmark.llm_direct_baseline as ldb

    monkeypatch.setattr(ldb, "DEFAULT_CONFIG_PATH", tmp_path / "nonexistent_baseline.toml")
    monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
    monkeypatch.delenv("OPENBIMAGENT_LLM_BASELINE_KEY", raising=False)
    report = run_learning_curve(config=None)
    assert report.measured is False
    assert report.points == ()
    assert "禁止引用" in report.provenance


def test_approx_inverts_monotonic_decreasing() -> None:
    payload = _b1_payload()
    inv = _approx_correct_inverts(payload)
    vals = list(inv.values())
    assert len(vals) == len(payload["nodes"])
    # 重力流沿程单调递减
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))


def test_exemplar_pool_excludes_target_and_valid_json() -> None:
    registry = {s.scenario_id: s for s in build_benchmark_scenarios()}
    pool = _build_exemplar_pool(registry, "B1", ("B2", "B3", "B9"))
    assert len(pool) >= 1
    for ex in pool:
        assert set(ex) == {"query", "answer"}
        json.loads(ex["answer"])  # answer 是合法 JSON


def test_learning_curve_with_mock_post_measured() -> None:
    correct = _approx_correct_inverts(_b1_payload())
    answer = json.dumps({"nodes": [{"node_id": k, "invert_z": v} for k, v in correct.items()]})

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": answer}}],
                "usage": {"total_tokens": 120},
            }

    cfg = LLMBaselineConfig(base_url="http://mock", model="mock-model", api_key="sk-test")
    report = run_learning_curve("B1", rounds=3, config=cfg, post_fn=lambda *a, **k: _Resp())
    assert report.measured is True
    assert len(report.points) == 3
    # 注入范例数应随轮次递增：0,1,2
    assert [p.exemplars_injected for p in report.points] == [0, 1, 2]
    assert all(p.success for p in report.points)
    assert report.success_zero_shot == 100.0
    assert report.provenance
    assert "学习曲线" in report.to_markdown_table()
