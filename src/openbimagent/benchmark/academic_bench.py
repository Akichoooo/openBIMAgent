"""BIMBench-Municipal 自动化学术实验与对比评测套件 (Academic Benchmark Suite)。

执行三范式消融实验对比：
  1. Neuro-Symbolic openBIMAgent —— 真实运行 M1.5 T7 基准 (B1–B10) 实测得出全部指标
  2. Heuristic Baseline —— 真实运行 StraightGravitySolver 逐段直线插值 (无走廊/无自适应/无自愈)
  3. LLM-Direct Prompting —— 显式占位 (measured=False)，待接入真实 LLM 基线运行

数据诚信契约：
  - 每个方法的指标必须携带 ``measured`` 与 ``provenance`` 字段；
  - ``measured=False`` 的行在 Markdown/LaTeX 输出中强制标注"未实测占位"，
    其数值仅为结构演示，禁止作为实验数据写入论文或答辩材料。
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from openbimagent.benchmark.m1_5_t7 import (
    _hydraulic_payload,
    build_benchmark_scenarios,
    run_m1_5_t7_benchmark,
)
from openbimagent.benchmark.self_healing_ablation import (
    AblationMethodStats,
    run_self_healing_ablation,
)
from openbimagent.utility.hydraulic_solver import solve_hydraulic_network
from openbimagent.utility.solver import solve_straight_gravity_utility

_LLM_PLACEHOLDER_PROVENANCE = (
    "PLACEHOLDER/占位：未实测。以下数值仅为表格结构演示的文献经验估计，"
    "必须替换为真实 LLM 基线运行数据 (需接入 providers 真实调用并留存 trace) 后方可用于论文。"
)


@dataclass(frozen=True)
class MethodBenchmarkMetrics:
    """单个方法的量化评测指标与数据来源声明。"""

    method_name: str
    total_cases: int
    topology_valid_rate: float  # 拓扑合法与闭合率 (%)
    rule_compliance_rate: float  # GB 50289 规范合规率 (%)
    hydraulic_valid_rate: float  # Manning 水力流速达标率 (%)
    avg_latency_ms: float  # 平均耗时 (ms)
    avg_tool_calls: float  # 平均工具调用/求解器运行轮次
    avg_token_count: int  # 平均消耗 Token 数 (离线确定性方法为 0)
    measured: bool = True  # 指标是否来自真实运行测量
    provenance: str = ""  # 数据来源与计算口径说明


@dataclass(frozen=True)
class AcademicBenchmarkReport:
    """学术基准对比测试报告。"""

    benchmark_id: str
    generated_at: str
    scenario_count: int
    scenarios: tuple[str, ...]
    methods: tuple[MethodBenchmarkMetrics, ...]

    def to_markdown_table(self) -> str:
        """生成学术论文标准 Markdown 表格。"""
        lines = [
            "### BIMBench-Municipal 消融实验与方法对比 (Ablation Study)",
            "",
            "| 方法范式 (Method Paradigm) | 拓扑有效率 | 规范合规率 (ACC) | 水力达标率 | 平均延迟 (ms) | 求解器调用数 | Token 消耗 |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for m in self.methods:
            mark = "" if m.measured else " †"
            lines.append(
                f"| **{m.method_name}**{mark} | {m.topology_valid_rate:.1f}% | {m.rule_compliance_rate:.1f}% | "
                f"{m.hydraulic_valid_rate:.1f}% | {m.avg_latency_ms:.1f} ms | {m.avg_tool_calls:.1f} | "
                f"{'~' + str(m.avg_token_count) if m.avg_token_count else '0 (离线)'} |"
            )
        lines.append("")
        lines.append(
            f"> 场景覆盖：市政管网基准 B 系列 {self.scenario_count} 个场景 ({', '.join(self.scenarios)})，"
            "涵盖串联、汇流、分流、断网、有向环、规则歧义与多节点复杂管网。"
        )
        lines.append("")
        lines.append("**数据来源 (Provenance)：**")
        for m in self.methods:
            status = "✅ 实测" if m.measured else "⚠️ 未实测占位"
            lines.append(f"- {m.method_name}: {status}。{m.provenance}")
        unmeasured = [m for m in self.methods if not m.measured]
        if unmeasured:
            lines.append("")
            lines.append(
                "> † **警告**：标注 † 的方法未经过真实运行测量，其数值禁止作为实验结果引用于论文、"
                "开题材料或答辩演示；仅代表评测框架的目标范式占位。"
            )
        return "\n".join(lines)

    def to_latex_table(self) -> str:
        """生成符合 SCI / 硕士论文排版规范的 LaTeX 格式三线表。"""
        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Ablation Study on BIMBench-Municipal Benchmark}",
            r"\label{tab:bimbench_ablation}",
            r"\begin{tabular}{lcccccc}",
            r"\toprule",
            r"Method Paradigm & Topology (\%) & ACC Rate (\%) & Hydraulic (\%) & Latency (ms) & Tools & Tokens \\",
            r"\midrule",
        ]
        for m in self.methods:
            mark = r"$^\dagger$" if not m.measured else ""
            lines.append(
                f"{m.method_name}{mark} & {m.topology_valid_rate:.1f} & {m.rule_compliance_rate:.1f} & "
                f"{m.hydraulic_valid_rate:.1f} & {m.avg_latency_ms:.1f} & {m.avg_tool_calls:.1f} & {m.avg_token_count} \\\\"
            )
        lines.extend([
            r"\bottomrule",
            r"\end{tabular}",
        ])
        if any(not m.measured for m in self.methods):
            lines.append(
                r"\par\smallskip\footnotesize $^\dagger$ UNMEASURED placeholder row: "
                r"values are illustrative estimates only and MUST NOT be cited as experimental results."
            )
        lines.append(r"\end{table}")
        return "\n".join(lines)


def _run_agent_method_row(
    scenarios: Sequence[str],
    *,
    work_dir: Path,
    repetitions: int,
) -> MethodBenchmarkMetrics:
    """真实运行 M1.5 T7 基准，实测 openBIMAgent (神经-符号) 各项指标。"""
    m15_report = run_m1_5_t7_benchmark(
        output_dir=work_dir / "m1_5_t7",
        scenario_ids=scenarios,
        repetitions=repetitions,
    )
    results = m15_report.scenarios
    total = len(results)
    verdict_correct = sum(1 for r in results if r.observed_status == r.expected_status)
    expected_pass = [r for r in results if r.expected_status == "PASS"]
    hydraulic_ok = sum(1 for r in expected_pass if r.conclusion.value == "PASS")
    rule_ok = sum(1 for r in expected_pass if r.semantic.ifc_ids_ok)
    avg_latency = sum(r.performance.duration_ms for r in results) / max(1, total)
    avg_solver_runs = sum(r.performance.solver_runs for r in results) / max(1, total)

    return MethodBenchmarkMetrics(
        method_name="openBIMAgent (Neuro-Symbolic + Solvers)",
        total_cases=total,
        topology_valid_rate=round(verdict_correct / max(1, total) * 100.0, 1),
        rule_compliance_rate=round(rule_ok / max(1, len(expected_pass)) * 100.0, 1),
        hydraulic_valid_rate=round(hydraulic_ok / max(1, len(expected_pass)) * 100.0, 1),
        avg_latency_ms=round(avg_latency, 1),
        avg_tool_calls=round(avg_solver_runs, 1),
        avg_token_count=0,
        measured=True,
        provenance=(
            f"M1.5 T7 基准真实运行 (repetitions={repetitions})：拓扑判定正确 {verdict_correct}/{total} "
            "(含正确拒绝断网/有向环等无效拓扑场景)；"
            f"规范合规口径=期望 PASS 场景中 IFC/IDS 证据通过 {rule_ok}/{len(expected_pass)}；"
            f"水力口径=期望 PASS 场景中水力达标 {hydraulic_ok}/{len(expected_pass)}；"
            "Token=0 (离线确定性内核，LLM 在线链路 token 计量待接入)。"
        ),
    )


def _run_heuristic_method_row(scenarios: Sequence[str]) -> MethodBenchmarkMetrics:
    """真实运行逐段直线插值基线 (StraightGravitySolver)，实测传统启发式方法指标。

    基线协议：对每个场景的全部管段逐段执行两点直埋求解（无走廊避障、无标高自适应、
    无自愈重规划、无拓扑校验），任一段异常或 Domain Gate 非 PASS 即判该场景不达标。
    """
    registry = {s.scenario_id: s for s in build_benchmark_scenarios()}
    total = len(scenarios)
    latencies: list[float] = []
    completed = 0
    gate_ok = 0
    hydraulic_ok = 0

    for sid in scenarios:
        scenario = registry[sid]
        payload = deepcopy(scenario.input_payload)
        if "nodes" not in payload or "segments" not in payload:
            # 非几何场景 (如 B8 规则歧义核验)：直线基线无法表示，计为不达标
            latencies.append(0.0)
            continue
        nodes = {n["node_id"]: n for n in payload["nodes"]}
        t0 = time.perf_counter()
        run_count = 0
        scenario_completed = True
        scenario_gate_ok = True
        scenario_hydraulic_ok = True

        for idx, seg in enumerate(payload["segments"]):
            start_node = nodes[seg["start_node_id"]]
            end_node = nodes[seg["end_node_id"]]
            try:
                result = solve_straight_gravity_utility(
                    {
                        "request_id": f"{sid}-heuristic-{idx}",
                        "source_ir_sha256": scenario.input_sha256,
                        "coordinate_reference": payload["coordinate_reference"],
                        "start": {
                            "node_id": start_node["node_id"],
                            "x_m": start_node["x_m"],
                            "y_m": start_node["y_m"],
                            "ground_elevation_m": start_node["ground_elevation_m"],
                        },
                        "end": {
                            "node_id": end_node["node_id"],
                            "x_m": end_node["x_m"],
                            "y_m": end_node["y_m"],
                            "ground_elevation_m": end_node["ground_elevation_m"],
                        },
                        "diameter_mm": seg.get("diameter_mm", 300.0),
                        "material": seg.get("material", "concrete"),
                        "design_slope": seg.get("design_slope", 0.003),
                        "surface_context": seg.get("surface_context", "driveway"),
                        "start_invert_m": start_node.get("invert_anchor_m"),
                    }
                )
                run_count += 1
                if result.domain_gate.status.value != "pass":
                    scenario_gate_ok = False
                if idx == 0 and result.compiled_ir is not None:
                    try:
                        hydraulic = solve_hydraulic_network(
                            result.compiled_ir,
                            _hydraulic_payload(result.compiled_ir, payload),
                        )
                        if hydraulic.hydraulics_in_spec != "pass":
                            scenario_hydraulic_ok = False
                    except Exception:  # noqa: BLE001 — 基线无法评测水力即记不达标
                        scenario_hydraulic_ok = False
            except Exception:  # noqa: BLE001 — 基线求解失败即记该场景未完成
                scenario_completed = False
                scenario_gate_ok = False
                scenario_hydraulic_ok = False
                run_count += 1

        latencies.append((time.perf_counter() - t0) * 1000.0)
        if scenario_completed:
            completed += 1
        if scenario_gate_ok:
            gate_ok += 1
        if scenario_hydraulic_ok:
            hydraulic_ok += 1

    return MethodBenchmarkMetrics(
        method_name="Heuristic Straight Interpolation (Non-Adaptive)",
        total_cases=total,
        topology_valid_rate=round(completed / max(1, total) * 100.0, 1),
        rule_compliance_rate=round(gate_ok / max(1, total) * 100.0, 1),
        hydraulic_valid_rate=round(hydraulic_ok / max(1, total) * 100.0, 1),
        avg_latency_ms=round(sum(latencies) / max(1, len(latencies)), 1),
        avg_tool_calls=1.0,
        avg_token_count=0,
        measured=True,
        provenance=(
            "StraightGravitySolver 逐段直线插值真实运行："
            f"{total} 场景全部管段两点直埋求解（无走廊避障/标高自适应/自愈/拓扑校验）；"
            "非几何场景 (规则歧义类) 基线无法表示，计为不达标；"
            "拓扑口径=全部段求解无异常，合规口径=全部段 Domain Gate PASS，"
            "水力口径=首段 Manning 核算达标；Token=0 (纯确定性基线)。"
        ),
    )


def _ablation_metrics_row(stats: AblationMethodStats) -> MethodBenchmarkMetrics:
    """把自愈消融电池统计转为论文表格指标行。"""
    total = max(1, stats.total_cases)
    is_off = "OFF" in stats.label
    converge_rate = round(stats.converged_count / total * 100.0, 1)
    return MethodBenchmarkMetrics(
        method_name=(
            "openBIMAgent-Ablation (Self-Healing OFF, Profile Patch)"
            if is_off
            else "openBIMAgent (Self-Healing ON, SH Battery)"
        ),
        total_cases=stats.total_cases,
        topology_valid_rate=round(stats.route_feasible_count / total * 100.0, 1),
        rule_compliance_rate=converge_rate,
        hydraulic_valid_rate=converge_rate,
        avg_latency_ms=stats.avg_latency_ms,
        avg_tool_calls=stats.avg_iterations,
        avg_token_count=0,
        measured=True,
        provenance=(
            f"自愈消融电池 (SH-1–SH-6) 真实运行，全部经 registry.invoke('solver:self_healing') 调度："
            f"收敛 {stats.converged_count}/{stats.total_cases}，平均迭代 {stats.avg_iterations}，"
            + (
                "OFF 行 = 激活 profile.ablation.no_self_healing 补丁层 (max_iterations=1 单轮直连基线)"
                if is_off
                else "ON 行 = 默认冲突驱动自愈绑定"
            )
            + "；拓扑口径=路线可行率，合规/水力口径=自愈收敛率 (final IR 产出)；Token=0 (离线确定性)。"
        ),
    )


def run_academic_benchmark(
    scenarios: Sequence[str] = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10"),
    output_path: Path | None = None,
    *,
    work_dir: Path | None = None,
    repetitions: int = 2,
    include_llm_baseline: bool | None = None,
) -> AcademicBenchmarkReport:
    """运行学术基准评测并输出带数据来源声明的量化对比报告。

    openBIMAgent、自愈 ON/OFF 与 Heuristic 三行均来自真实求解器运行 (measured=True)；
    LLM-Direct 行默认保持占位 (measured=False)；仅在显式 opt-in
    (``include_llm_baseline=True`` 或 ``OPENBIMAGENT_RUN_LLM_BASELINE=1``) 且
    本地配置存在时才发起真实 LLM 网络调用替换为实测行。
    """
    import os as _os
    import tempfile

    total = len(scenarios)

    with tempfile.TemporaryDirectory(prefix="bimbench-academic-") as tmp:
        root = Path(work_dir) if work_dir is not None else Path(tmp)
        agent_row = _run_agent_method_row(scenarios, work_dir=root, repetitions=repetitions)

    heuristic_row = _run_heuristic_method_row(scenarios)

    on_stats, off_stats = run_self_healing_ablation()
    healing_on_row = _ablation_metrics_row(on_stats)
    healing_off_row = _ablation_metrics_row(off_stats)

    llm_enabled = (
        include_llm_baseline
        if include_llm_baseline is not None
        else _os.environ.get("OPENBIMAGENT_RUN_LLM_BASELINE") == "1"
    )
    llm_row = None
    if llm_enabled:
        from openbimagent.benchmark.llm_direct_baseline import run_llm_direct_baseline

        llm_row = run_llm_direct_baseline()

    if llm_row is None:
        llm_row = MethodBenchmarkMetrics(
            method_name="LLM-Direct Prompting (UNMEASURED PLACEHOLDER)",
            total_cases=total,
            topology_valid_rate=42.5,
            rule_compliance_rate=36.0,
            hydraulic_valid_rate=28.0,
            avg_latency_ms=4200.0,
            avg_tool_calls=18.5,
            avg_token_count=14200,
            measured=False,
            provenance=_LLM_PLACEHOLDER_PROVENANCE,
        )

    report = AcademicBenchmarkReport(
        benchmark_id="BIMBench-Municipal-2026-Full",
        generated_at=datetime.now(UTC).isoformat(),
        scenario_count=total,
        scenarios=tuple(scenarios),
        methods=(agent_row, healing_on_row, healing_off_row, heuristic_row, llm_row),
    )

    if output_path is not None:
        p = Path(output_path)
        if p.suffix == ".tex":
            p.write_text(report.to_latex_table(), encoding="utf-8")
        else:
            p.write_text(report.to_markdown_table(), encoding="utf-8")

    return report
