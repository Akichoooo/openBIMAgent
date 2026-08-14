"""BIMBench-Municipal 自动化学术实验与消融对比评测套件测试。"""

from __future__ import annotations

from pathlib import Path

from openbimagent.benchmark.academic_bench import (
    run_academic_benchmark,
)


def test_academic_benchmark_runs_and_formats_table(tmp_path: Path) -> None:
    output_file = tmp_path / "academic_report.md"
    report = run_academic_benchmark(scenarios=("B1", "B2"), output_path=output_file)

    assert report.benchmark_id == "BIMBench-Municipal-2026"
    assert report.scenario_count == 2
    assert len(report.methods) == 3

    # 验证 openBIMAgent 方法的各项指标有效性
    agent_method = report.methods[0]
    assert agent_method.method_name.startswith("openBIMAgent")
    assert agent_method.topology_valid_rate == 100.0
    assert agent_method.rule_compliance_rate == 100.0
    assert agent_method.hydraulic_valid_rate == 100.0

    # 验证生成的 Markdown 表格
    md_table = report.to_markdown_table()
    assert "| 方法范式 (Method Paradigm)" in md_table
    assert "| **openBIMAgent" in md_table
    assert "| **LLM-Direct Prompting" in md_table
    assert "| **Heuristic Linear Solver" in md_table

    # 验证文件输出
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == md_table
