"""LLM-Direct 真实基线 (llm_direct_baseline) 专项测试。

全部用注入的 post_fn mock，绝不触网。真实网络调用仅在显式 opt-in + 本地配置时由用户手动触发。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import httpx

from openbimagent.benchmark.academic_bench import run_academic_benchmark
from openbimagent.benchmark.llm_direct_baseline import (
    LLMBaselineConfig,
    _evaluate_scenario,
    _parse_inverts,
    load_llm_baseline_config,
    run_llm_direct_baseline,
)
from openbimagent.benchmark.m1_5_t7 import build_benchmark_scenarios


def _fake_response(content: str, tokens: int = 1500):
    class _R:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": tokens},
            }

    return _R()


def _b1_payload() -> dict:
    return next(s for s in build_benchmark_scenarios() if s.scenario_id == "B1").input_payload


def _derive_compliant_inverts(payload: dict) -> dict[str, float]:
    """从 B1 几何派生一组完全合规的管底标高（覆盖土/坡度/流速全过）。"""
    nodes = {n["node_id"]: n for n in payload["nodes"]}
    inverts: dict[str, float] = {}
    source = payload["nodes"][0]
    inverts[source["node_id"]] = float(source.get("invert_anchor_m") or 10.0)
    changed = True
    while changed:
        changed = False
        for seg in payload["segments"]:
            s_id, e_id = seg["start_node_id"], seg["end_node_id"]
            if s_id in inverts and e_id not in inverts:
                s = nodes[s_id]
                e = nodes[e_id]
                length = math.hypot(e["x_m"] - s["x_m"], e["y_m"] - s["y_m"])
                inverts[e_id] = inverts[s_id] - seg.get("design_slope", 0.003) * length
                changed = True
    return inverts


def test_load_config_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_llm_baseline_config(tmp_path / "nope.toml") is None


def test_load_config_returns_none_for_placeholder_key(tmp_path: Path) -> None:
    cfg_file = tmp_path / "llm_baseline.local.toml"
    cfg_file.write_text(
        'base_url = "https://x/v1"\nmodel = "m"\napi_key = "sk-replace-me"\n', encoding="utf-8"
    )
    assert load_llm_baseline_config(cfg_file) is None


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "llm_baseline.local.toml"
    cfg_file.write_text(
        'base_url = "https://x/v1/"\nmodel = "gpt-test"\napi_key = "sk-real"\n'
        'max_scenarios = 3\nrepetitions = 1\n',
        encoding="utf-8",
    )
    cfg = load_llm_baseline_config(cfg_file)
    assert cfg is not None
    assert cfg.base_url == "https://x/v1"  # 尾斜杠去除
    assert cfg.model == "gpt-test"
    assert cfg.max_scenarios == 3 and cfg.repetitions == 1


def test_parse_inverts_handles_plain_and_fenced() -> None:
    assert _parse_inverts('{"nodes":[{"node_id":"a","invert_z":10.0}]}') == {"a": 10.0}
    fenced = '```json\n{"nodes":[{"node_id":"a","invert_z":9.5}]}\n```'
    assert _parse_inverts(fenced) == {"a": 9.5}
    assert _parse_inverts("no json here") is None
    assert _parse_inverts('{"nodes":[{"node_id":"a"}]}') is None  # 缺 invert_z


def test_evaluate_scenario_compliant_inverts_pass_all() -> None:
    payload = _b1_payload()
    inverts = _derive_compliant_inverts(payload)
    verdict = _evaluate_scenario(payload, inverts)
    assert verdict == {"topology": True, "rule": True, "hydraulic": True}


def test_evaluate_scenario_detects_cover_violation() -> None:
    payload = _b1_payload()
    inverts = _derive_compliant_inverts(payload)
    # 整体抬高标高使覆土不足 (<0.70m)；相对高差不变故坡度/流速维度不变, 隔离覆土维度
    inverts = {k: v + 5.0 for k, v in inverts.items()}
    verdict = _evaluate_scenario(payload, inverts)
    assert verdict["rule"] is False


def test_evaluate_scenario_detects_uphill_flow() -> None:
    payload = _b1_payload()
    inverts = _derive_compliant_inverts(payload)
    # 反转流向：管底沿 start->end 上升
    inverts = {k: -v for k, v in inverts.items()}
    verdict = _evaluate_scenario(payload, inverts)
    assert verdict["rule"] is False


def test_evaluate_scenario_missing_node_fails_topology() -> None:
    payload = _b1_payload()
    inverts = _derive_compliant_inverts(payload)
    inverts = dict(list(inverts.items())[:-1])  # 丢一个节点
    verdict = _evaluate_scenario(payload, inverts)
    assert verdict["topology"] is False


def test_run_baseline_with_mocked_good_llm() -> None:
    """注入合规 LLM 响应 → 实测行 100% 合规。"""
    payload = _b1_payload()
    inverts = _derive_compliant_inverts(payload)
    content = json.dumps({"nodes": [{"node_id": k, "invert_z": v} for k, v in inverts.items()]})

    calls = []

    def post_fn(url, json=None, headers=None):
        calls.append(url)
        return _fake_response(content, tokens=1234)

    cfg = LLMBaselineConfig(
        base_url="https://mock.local/v1",
        model="mock-model",
        api_key="sk-mock",
        max_scenarios=1,
        repetitions=1,
    )
    row = run_llm_direct_baseline(scenarios=("B1",), config=cfg, post_fn=post_fn)

    assert row is not None
    assert row.measured is True
    assert row.topology_valid_rate == 100.0
    assert row.rule_compliance_rate == 100.0
    assert row.hydraulic_valid_rate == 100.0
    assert row.avg_token_count == 1234
    assert calls and calls[0] == "https://mock.local/v1/chat/completions"


def test_run_baseline_with_llm_failure_records_unmeasured_zero() -> None:
    """LLM 调用异常 → 该场景全不达标，行仍 measured=True。"""

    def post_fn(url, json=None, headers=None):
        raise httpx.ConnectError("simulated network failure")

    cfg = LLMBaselineConfig(
        base_url="https://mock.local/v1", model="mock", api_key="sk-mock",
        max_scenarios=1, repetitions=1,
    )
    row = run_llm_direct_baseline(scenarios=("B1",), config=cfg, post_fn=post_fn)

    assert row is not None
    assert row.measured is True
    assert row.rule_compliance_rate == 0.0
    assert "LLM-Direct 真实调用" in row.provenance


def test_academic_benchmark_keeps_placeholder_when_not_opted_in() -> None:
    """默认 (未 opt-in) LLM 行保持 UNMEASURED 占位，绝不发起网络调用。"""
    report = run_academic_benchmark(scenarios=("B1",))
    llm_row = report.methods[4]
    assert llm_row.measured is False
    assert "PLACEHOLDER" in llm_row.method_name
