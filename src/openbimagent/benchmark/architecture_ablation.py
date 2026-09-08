"""架构消融统一电池与贡献归因（对标 SWE-bench 式严谨消融 + 2026 数据诚信范式）。

设计原则（fail-closed 数据诚信）：消融必须语义诚实。经核对 rules.py / plugin.py 代码，
本项目架构环节分两类，绝不用同一套 ON/OFF 手法强套：

1. **运行时质量降级点**——适合能力 ON/OFF 对照消融（关闭后输出质量可测下降）：
   - ``solver:self_healing``：关闭冲突驱动自愈（profile.ablation.no_self_healing 补丁重定向
     到单轮直连）→ 收敛率/迭代数可测下降。复用 self_healing_ablation 实测。
   - 自适应路由 vs 直线插值：academic_bench 的 heuristic 行已隔离（无走廊避障/无标高自适应）。

2. **编译期验证门 / 检测环节 / 鲁棒性环节**——正常路径下关闭**不产生运行时质量差异**，
   强行 ON/OFF 会产出"无差异"或误导数据（评审必问"关闭规则编译为何结果不变"），违背本项目
   fail-closed 立身原则，故**不做伪消融**，改以"环节有效性证据"量化贡献，并标注正确的
   注入式实验设计（拦截率/检出率/恢复率）作为其消融口径：
   - ``rules:gb50289`` 规则编译：贡献 = 编译期 validate_rule_self_tests 重放全部自检样例，
     任一失效即拒绝整个规则集（防护门），不改变合法规则的运行时输出。
   - 双宿主语义比较：贡献 = SemanticMetrics.offline_dual_host_equal 的宿主一致性检出能力。
   - 中断恢复：贡献 = RecoveryMetrics.blender_resume_ok/vectorworks_resume_ok 的恢复成功率。

每行携带 measured/provenance，沿用 academic_bench 数据诚信契约（未实测禁止引用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from openbimagent.benchmark.m1_5_t7 import BenchmarkScenarioResult
from openbimagent.benchmark.self_healing_ablation import run_self_healing_ablation


@dataclass(frozen=True)
class RuntimeAblation:
    """运行时质量消融：能力 ON/OFF，有可测质量差异（论文消融表主行）。"""

    dimension: str
    isolated_module: str
    on_label: str
    off_label: str
    on_converged: int
    off_converged: int
    total_cases: int
    delta_convergence_pp: float  # ON − OFF 收敛率差（百分点）
    measured: bool = True
    provenance: str = ""


@dataclass(frozen=True)
class VerificationLayerEvidence:
    """验证/鲁棒性环节贡献证据（非 ON/OFF 消融；诚实标注不适合伪消融的原因与正确口径）。"""

    layer: str
    contribution_kind: str  # compile_time_gate / detection / robustness
    effectiveness_metric: str
    effectiveness_value: float
    correct_ablation_design: str  # 该环节贡献的正确量化实验（注入式）
    not_onoff_reason: str
    measured: bool = True
    provenance: str = ""


@dataclass(frozen=True)
class ArchitectureAblationReport:
    """架构消融统一报告：运行时消融 + 验证环节证据，供论文消融章节引用。"""

    runtime_ablations: tuple[RuntimeAblation, ...]
    verification_layers: tuple[VerificationLayerEvidence, ...]
    measured: bool = True
    provenance: str = ""

    def to_markdown_table(self) -> str:
        lines = [
            "### 架构消融与模块贡献归因 (Architecture Ablation)",
            "",
            "**运行时质量消融（能力 ON/OFF）：**",
            "",
            "| 消融维度 | 隔离模块 | ON 收敛 | OFF 收敛 | Δ收敛率 |",
            "| :--- | :--- | :---: | :---: | :---: |",
        ]
        for a in self.runtime_ablations:
            lines.append(
                f"| {a.dimension} | {a.isolated_module} | {a.on_converged}/{a.total_cases} | "
                f"{a.off_converged}/{a.total_cases} | {a.delta_convergence_pp:+.1f}pp |"
            )
        lines.extend([
            "",
            "**验证/鲁棒性环节贡献（不做伪 ON/OFF，附正确消融口径）：**",
            "",
            "| 环节 | 类型 | 有效性指标 | 值 | 正确消融设计 |",
            "| :--- | :--- | :--- | :---: | :--- |",
        ])
        for v in self.verification_layers:
            lines.append(
                f"| {v.layer} | {v.contribution_kind} | {v.effectiveness_metric} | "
                f"{v.effectiveness_value} | {v.correct_ablation_design} |"
            )
        lines.extend(["", "**数据来源 (Provenance)：**"])
        for a in self.runtime_ablations:
            lines.append(f"- [运行时] {a.dimension}: {a.provenance}")
        for v in self.verification_layers:
            lines.append(f"- [验证环节] {v.layer}: {v.provenance}（为何不做 ON/OFF：{v.not_onoff_reason}）")
        return "\n".join(lines)


def _self_healing_runtime_ablation(registry=None) -> RuntimeAblation:
    """复用 profile.ablation.no_self_healing 补丁的 ON/OFF 电池（唯一干净的运行时质量消融）。"""
    on, off = run_self_healing_ablation(registry=registry)
    on_rate = on.converged_count / max(1, on.total_cases) * 100.0
    off_rate = off.converged_count / max(1, off.total_cases) * 100.0
    return RuntimeAblation(
        dimension="self_healing",
        isolated_module="冲突驱动自愈循环（缓冲区膨胀 + A* 重规划）",
        on_label=on.label,
        off_label=off.label,
        on_converged=on.converged_count,
        off_converged=off.converged_count,
        total_cases=on.total_cases,
        delta_convergence_pp=round(on_rate - off_rate, 1),
        measured=True,
        provenance=(
            f"profile.ablation.no_self_healing 补丁重定向 solver:self_healing：ON 收敛 "
            f"{on.converged_count}/{on.total_cases}（{on_rate:.1f}%），OFF 单轮直连 "
            f"{off.converged_count}/{off.total_cases}（{off_rate:.1f}%），Δ={on_rate - off_rate:+.1f}pp"
        ),
    )


def _verification_layers(
    t7_results: Sequence[BenchmarkScenarioResult] | None,
) -> tuple[VerificationLayerEvidence, ...]:
    """验证/鲁棒性环节的有效性证据（从规则集与 M1.5 T7 真实结果派生）。"""
    from openbimagent.utility import compile_municipal_rule_set

    layers: list[VerificationLayerEvidence] = []

    rule_set = compile_municipal_rule_set()
    n_self_tests = sum(
        len(rule.self_tests.match) + len(rule.self_tests.not_match) for rule in rule_set.rules
    )
    layers.append(
        VerificationLayerEvidence(
            layer="rule_compile",
            contribution_kind="compile_time_gate",
            effectiveness_metric="编译期自检样例数（任一失效即拒绝整个规则集）",
            effectiveness_value=float(n_self_tests),
            correct_ablation_design="注入一条缺陷规则，测 validate_rule_self_tests 的拦截率",
            not_onoff_reason="规则编译是编译期验证门，对合法冻结规则关闭自检不改变运行时求解输出",
            measured=True,
            provenance=f"compile_municipal_rule_set() 编译期重放 {n_self_tests} 条自检样例（rules×polarity×cases）",
        )
    )

    if t7_results:
        pass_results = [r for r in t7_results if r.expected_status == "PASS"]
        if pass_results:
            dual_ok = sum(1 for r in pass_results if r.semantic.offline_dual_host_equal)
            layers.append(
                VerificationLayerEvidence(
                    layer="dual_host_compare",
                    contribution_kind="detection",
                    effectiveness_metric="双宿主语义一致检出率（PASS 场景）",
                    effectiveness_value=round(dual_ok / len(pass_results) * 100.0, 1),
                    correct_ablation_design="注入宿主几何不一致，测 SemanticSnapshot 比较的检出率",
                    not_onoff_reason="双宿主比较是检测环节，关闭不改变生成几何，只是不再发现不一致",
                    measured=True,
                    provenance=f"M1.5 T7 PASS 场景 offline_dual_host_equal {dual_ok}/{len(pass_results)}",
                )
            )
        rec_ok = sum(
            1 for r in t7_results if r.recovery.blender_resume_ok and r.recovery.vectorworks_resume_ok
        )
        layers.append(
            VerificationLayerEvidence(
                layer="recovery",
                contribution_kind="robustness",
                effectiveness_metric="双宿主中断恢复成功率",
                effectiveness_value=round(rec_ok / len(t7_results) * 100.0, 1),
                correct_ablation_design="注入执行中断，测 checkpoint/resume 的恢复成功率与副作用安全",
                not_onoff_reason="恢复是鲁棒性环节，正常无中断路径下关闭不影响输出",
                measured=True,
                provenance=f"M1.5 T7 场景 blender+vectorworks resume ok {rec_ok}/{len(t7_results)}",
            )
        )

    return tuple(layers)


def run_architecture_ablation(
    *,
    registry=None,
    t7_results: Sequence[BenchmarkScenarioResult] | None = None,
) -> ArchitectureAblationReport:
    """跑架构消融统一电池：运行时质量消融（self_healing）+ 验证环节有效性证据。

    t7_results 可复用 academic_bench / m1_5_t7 已产出的真实结果（零额外运行成本）；
    未提供时仅产出 self_healing 运行时消融与 rule_compile 编译门证据。
    """
    runtime = (_self_healing_runtime_ablation(registry),)
    layers = _verification_layers(t7_results)
    return ArchitectureAblationReport(
        runtime_ablations=runtime,
        verification_layers=layers,
        measured=True,
        provenance=(
            f"架构消融统一电池：运行时质量消融 {len(runtime)} 维（self_healing ON/OFF，"
            f"profile 补丁重定向）+ 验证/鲁棒性环节有效性证据 {len(layers)} 层"
            "（rule_compile/dual_host/recovery，不做伪 ON/OFF，附注入式正确消融口径）。"
            "全部离线确定性实测，无需真实 LLM/宿主。"
        ),
    )


__all__ = [
    "ArchitectureAblationReport",
    "RuntimeAblation",
    "VerificationLayerEvidence",
    "run_architecture_ablation",
]
