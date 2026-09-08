"""轨迹质量指标（对标 2026 Agent 评估范式：task success vs trajectory accuracy）。

背景（2026 评估危机）：结果类指标（task success）易被"刷分/scrappy win"污染——有 agent
改约 10 行测试配置就让 SWE-bench Verified 500 测试全过；scaffolding 单独能让准确率从 60%
掉到 25%。业界因此主张同时度量"过程是否可审计"（trajectory accuracy），拒绝"蒙对但过程
不可辩护"的胜利。

本模块从 M1.5 T7 ``BenchmarkScenarioResult`` 派生四个论文级指标，全部离线确定性可复现，
不依赖真实 LLM/宿主：

- ``task_success_rate``          结果正确率：observed_status == expected_status 的场景占比。
- ``trajectory_accuracy``        轨迹可审计正确率：结果对 + 失败标记匹配 + 确定性稳定 +
                                 双宿主恢复 ok + 语义一致（即"不是 scrappy win"）。
- ``evidence_chain_completeness``证据链完整率：input→result→compiled IR→semantic→IFC/IDS→determinism
                                 六环中「存在且校验通过」的占比（FAIL 场景在 compiled IR 前合法截断）。
- ``fail_closed_compliance``     失败关闭合规率：预期非 PASS 场景（FAIL/UNKNOWN/REVIEW_REQUIRED）
                                 严格未升格为 PASS —— openBIMAgent 独有的数据诚信卖点。

数据诚信：所有聚合指标携带 ``measured`` 与 ``provenance``，沿用 academic_bench 契约
（未实测禁止引用于论文/答辩）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from openbimagent.benchmark.m1_5_t7 import (
    _EXPECTED_FAILURE_MARKER,
    BenchmarkScenarioResult,
)

# 证据链六环：仅对期望 PASS 的场景逐环核验（input→result→compiled IR→semantic→IFC/IDS→determinism）。
# 非 PASS 场景合法地不产出完整交付链，不计入分母（其合规性由 fail_closed_compliance 度量）。
_PASS_CHAIN_LINKS = 6


@dataclass(frozen=True)
class ScenarioTrajectory:
    """单场景轨迹质量明细（可审计溯源；供证据链可视化消费）。"""

    scenario_id: str
    expected_status: str
    observed_status: str
    task_success: bool
    trajectory_ok: bool
    chain_links_present: int
    chain_links_total: int
    # None = 期望 PASS，不适用 fail-closed 判定（fail-closed 只约束"不该 PASS 的"）
    fail_closed_ok: bool | None


@dataclass(frozen=True)
class TrajectoryMetrics:
    """一批场景的轨迹质量聚合指标 + 数据来源声明。"""

    total_cases: int
    task_success_rate: float
    trajectory_accuracy: float
    evidence_chain_completeness: float
    fail_closed_compliance: float
    per_scenario: tuple[ScenarioTrajectory, ...]
    measured: bool = True
    provenance: str = ""


def scenario_task_success(result: BenchmarkScenarioResult) -> bool:
    """结果正确：观测状态与冻结期望状态一致（含正确拒绝 FAIL/UNKNOWN/REVIEW_REQUIRED）。"""
    return result.observed_status == result.expected_status


def scenario_trajectory_ok(result: BenchmarkScenarioResult) -> bool:
    """轨迹可审计正确（白盒）：不仅结果对，过程也必须可辩护。

    判据对齐 m1_5_t7 的验收逻辑，但显式表达为"轨迹质量"而非"是否通过"：
    结果匹配 + 失败原因标记匹配 + 确定性哈希稳定/幂等 + 双宿主恢复 ok + PASS 场景语义一致。
    任一过程维度不满足即判为不可审计（即使结果碰巧正确，也计为 scrappy win）。
    """
    if not scenario_task_success(result):
        return False
    marker = _EXPECTED_FAILURE_MARKER.get(result.scenario_id, "")
    if marker and marker not in result.failure_reason:
        return False
    rejected_before_ir = (
        result.expected_status == "FAIL" and result.determinism.canonical_sha256 is None
    )
    if not result.determinism.canonical_hash_stable and not rejected_before_ir:
        return False
    if not result.determinism.repeated_execution_idempotent and result.expected_status != "UNKNOWN":
        return False
    if not (result.recovery.blender_resume_ok and result.recovery.vectorworks_resume_ok):
        return False
    if result.expected_status == "PASS":
        return result.semantic.offline_dual_host_equal and result.semantic.ifc_ids_ok
    return True


def _chain_links(result: BenchmarkScenarioResult) -> tuple[int, int]:
    """证据链六环核验，返回 (present, total)。

    六环（仅对期望 PASS 的场景）：input 身份 → result 身份 → compiled IR canonical →
    双宿主语义 → IFC/IDS → determinism 稳定+幂等。期望非 PASS 的场景（FAIL/UNKNOWN/
    REVIEW_REQUIRED）合法地不产出完整交付链，不计入证据链完整率分母——其“不该 PASS 却没
    PASS”的合规性由 fail_closed_compliance 单独度量，避免用交付链完整率错误惩罚 fail-closed。
    """
    if result.expected_status != "PASS":
        return 0, 0
    links = (
        bool(result.input_sha256),
        bool(result.result_sha256),
        result.determinism.canonical_sha256 is not None,
        result.semantic.offline_dual_host_equal,
        result.semantic.ifc_ids_ok,
        result.determinism.canonical_hash_stable
        and result.determinism.repeated_execution_idempotent,
    )
    return sum(1 for ok in links if ok), _PASS_CHAIN_LINKS


def compute_trajectory_metrics(
    results: Sequence[BenchmarkScenarioResult],
) -> TrajectoryMetrics:
    """从一批 BenchmarkScenarioResult 计算四个轨迹质量指标（纯函数、离线、可复现）。"""
    per: list[ScenarioTrajectory] = []
    for result in results:
        present, total = _chain_links(result)
        fail_closed_ok = (
            None
            if result.expected_status == "PASS"
            else result.observed_status == result.expected_status
        )
        per.append(
            ScenarioTrajectory(
                scenario_id=result.scenario_id,
                expected_status=result.expected_status,
                observed_status=result.observed_status,
                task_success=scenario_task_success(result),
                trajectory_ok=scenario_trajectory_ok(result),
                chain_links_present=present,
                chain_links_total=total,
                fail_closed_ok=fail_closed_ok,
            )
        )

    n = len(per)
    chain_present = sum(item.chain_links_present for item in per)
    chain_total = sum(item.chain_links_total for item in per)
    fail_closed_cases = [item for item in per if item.fail_closed_ok is not None]
    task_success_n = sum(1 for item in per if item.task_success)
    trajectory_ok_n = sum(1 for item in per if item.trajectory_ok)
    fail_closed_ok_n = sum(1 for item in fail_closed_cases if item.fail_closed_ok)

    return TrajectoryMetrics(
        total_cases=n,
        task_success_rate=round(task_success_n / max(1, n) * 100.0, 1),
        trajectory_accuracy=round(trajectory_ok_n / max(1, n) * 100.0, 1),
        evidence_chain_completeness=round(chain_present / max(1, chain_total) * 100.0, 1),
        fail_closed_compliance=round(
            fail_closed_ok_n / max(1, len(fail_closed_cases)) * 100.0, 1
        ),
        per_scenario=tuple(per),
        measured=True,
        provenance=(
            f"M1.5 T7 真实运行派生（{n} 场景）：task_success=observed==expected {task_success_n}/{n}；"
            f"trajectory_accuracy=结果对+失败标记匹配+确定性稳定+双宿主恢复+语义一致 {trajectory_ok_n}/{n}"
            "（拒绝 scrappy win）；"
            f"evidence_chain=期望 PASS 场景六环存在且校验通过 {chain_present}/{chain_total}"
            "（非 PASS 场景合法不产出完整交付链，不计入分母，由 fail_closed_compliance 单独度量）；"
            f"fail_closed_compliance=预期非 PASS 场景严格未升格 {fail_closed_ok_n}/{len(fail_closed_cases)}。"
            "全部离线确定性实测，无需真实 LLM/宿主。"
        ),
    )


__all__ = [
    "ScenarioTrajectory",
    "TrajectoryMetrics",
    "compute_trajectory_metrics",
    "scenario_task_success",
    "scenario_trajectory_ok",
]
