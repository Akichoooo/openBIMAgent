"""A3 架构消融统一电池测试：运行时质量消融 + 验证环节诚实证据（不做伪 ON/OFF）。"""

from __future__ import annotations

from openbimagent.benchmark.architecture_ablation import run_architecture_ablation
from openbimagent.benchmark.m1_5_t7 import run_m1_5_t7_benchmark


def test_runtime_ablation_self_healing() -> None:
    report = run_architecture_ablation()
    assert len(report.runtime_ablations) == 1
    sh = report.runtime_ablations[0]
    assert sh.dimension == "self_healing"
    assert sh.measured is True
    # 自愈 ON 收敛数应 >= OFF（单轮直连）——这正是自愈模块的运行时质量贡献
    assert sh.on_converged >= sh.off_converged
    assert sh.total_cases > 0
    assert sh.provenance


def test_rule_compile_layer_is_verification_not_onoff() -> None:
    report = run_architecture_ablation()
    rule_layers = [v for v in report.verification_layers if v.layer == "rule_compile"]
    assert len(rule_layers) == 1
    rl = rule_layers[0]
    assert rl.contribution_kind == "compile_time_gate"
    assert rl.effectiveness_value > 0  # 编译期自检样例数（33 条规则×极性×样例）
    # 数据诚信：显式标注为何不做 ON/OFF + 给出正确的注入式消融口径
    assert rl.not_onoff_reason
    assert "注入" in rl.correct_ablation_design


def test_verification_layers_with_t7_results(tmp_path) -> None:
    results = run_m1_5_t7_benchmark(
        output_dir=tmp_path, scenario_ids=("B1",), repetitions=2
    ).scenarios
    report = run_architecture_ablation(t7_results=results)
    layers = {v.layer for v in report.verification_layers}
    assert "rule_compile" in layers
    assert "dual_host_compare" in layers  # B1 是 PASS 场景，应有双宿主检出证据
    assert "recovery" in layers
    # 每个验证环节都必须带诚实标注与正确消融口径
    for v in report.verification_layers:
        assert v.not_onoff_reason and v.correct_ablation_design and v.provenance


def test_markdown_table_renders() -> None:
    md = run_architecture_ablation().to_markdown_table()
    assert "架构消融" in md
    assert "self_healing" in md
    assert "rule_compile" in md
    assert "Provenance" in md
