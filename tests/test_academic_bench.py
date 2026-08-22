"""BIMBench-Municipal 自动化学术实验与消融对比评测套件测试。"""

from __future__ import annotations

from pathlib import Path

from openbimagent.benchmark.academic_bench import (
    run_academic_benchmark,
)


def test_academic_benchmark_measured_rows_are_real(tmp_path: Path) -> None:
    """实测行必须来自真实求解器运行，且指标携带数据来源声明。"""
    report = run_academic_benchmark(scenarios=("B1", "B2"))

    assert report.benchmark_id.startswith("BIMBench-Municipal-2026")
    assert report.scenario_count == 2
    assert len(report.methods) == 5

    agent_row = report.methods[0]
    assert agent_row.method_name.startswith("openBIMAgent")
    assert agent_row.measured is True
    assert "M1.5 T7" in agent_row.provenance
    for rate in (agent_row.topology_valid_rate, agent_row.rule_compliance_rate, agent_row.hydraulic_valid_rate):
        assert 0.0 <= rate <= 100.0
    assert agent_row.avg_latency_ms > 0.0

    # 自愈消融电池两行：均为实测且 OFF 行经 Profile 补丁生成
    healing_on_row = report.methods[1]
    healing_off_row = report.methods[2]
    assert healing_on_row.measured is True
    assert healing_off_row.measured is True
    assert "Profile" in healing_off_row.method_name or "补丁" in healing_off_row.provenance
    assert "registry.invoke" in healing_on_row.provenance
    assert healing_on_row.rule_compliance_rate > healing_off_row.rule_compliance_rate

    heuristic_row = report.methods[3]
    assert heuristic_row.measured is True
    assert "StraightGravitySolver" in heuristic_row.provenance
    assert heuristic_row.avg_token_count == 0


def test_llm_direct_row_is_explicit_placeholder() -> None:
    """LLM-Direct 基线行必须显式标记未实测占位，禁止伪装为实验数据。"""
    report = run_academic_benchmark(scenarios=("B1",))

    llm_row = report.methods[4]
    assert llm_row.measured is False
    assert "PLACEHOLDER" in llm_row.method_name
    assert "未实测" in llm_row.provenance

    md_table = report.to_markdown_table()
    assert "未实测占位" in md_table
    assert "禁止" in md_table
    # LaTeX 输出同样强制携带占位警告
    latex = report.to_latex_table()
    assert r"$^\dagger$" in latex
    assert "MUST NOT be cited" in latex


def test_benchmark_table_file_output(tmp_path: Path) -> None:
    """Markdown / LaTeX 文件输出与内存渲染一致。"""
    output_file = tmp_path / "academic_report.md"
    report = run_academic_benchmark(scenarios=("B1",), output_path=output_file)

    md_table = report.to_markdown_table()
    assert "| 方法范式 (Method Paradigm)" in md_table
    assert "| **openBIMAgent" in md_table
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == md_table

    tex_file = tmp_path / "academic_report.tex"
    tex_report = run_academic_benchmark(scenarios=("B1",), output_path=tex_file)
    latex_str = tex_report.to_latex_table()
    assert r"\begin{table}" in latex_str
    assert r"\toprule" in latex_str
    assert tex_file.exists()
