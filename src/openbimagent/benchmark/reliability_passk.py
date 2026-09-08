"""pass^k 可靠性指标（对标 τ-bench pass^k, Sierra AI 2024）。

pass^k = 同一任务连续 k 次独立运行**全部成功**的概率，区别于单次成功率 pass^1：它暴露
"偶尔蒙对"（scrappy win）与真实可靠性的差距。2026 评估界用 pass^k 揭示：一个 pass^1 看似
不错的 agent，pass^k 可能随 k 急剧衰减（方差大、不可复现）。

预期结论（本项目的核心可靠性主张）：
- openBIMAgent 确定性内核：pass^k ≈ pass^1（重复运行完全一致，determinism_confirmed=True）；
- LLM-Direct 基线：pass^k ≪ pass^1（由 llm_direct_baseline 提供，需真实调用，此处不伪造）。

离线可复现：对确定性内核真实重跑 k 次 run_m1_5_t7_benchmark，用 trajectory_metrics 的
白盒轨迹判据（scenario_trajectory_ok）判定每次每场景是否成功，不依赖真实 LLM/宿主。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from openbimagent.benchmark.m1_5_t7 import run_m1_5_t7_benchmark
from openbimagent.benchmark.trajectory_metrics import scenario_trajectory_ok


@dataclass(frozen=True)
class ScenarioPassK:
    """单场景在 k 次独立运行中的成功计数。"""

    scenario_id: str
    successes: int
    k: int


@dataclass(frozen=True)
class PassKReport:
    """pass^k 可靠性报告 + 数据来源声明。"""

    k: int
    scenario_ids: tuple[str, ...]
    pass_at_1: float  # 单次运行成功率（%）= 总成功次数 / (k × 场景数)
    pass_at_k: float  # k 次全部成功的场景比例（%）
    per_scenario: tuple[ScenarioPassK, ...]
    determinism_confirmed: bool  # pass^k == pass^1（确定性内核特征）
    measured: bool = True
    provenance: str = ""


def run_passk(
    scenario_ids: Sequence[str] = ("B1", "B2", "B3"),
    *,
    k: int = 5,
    work_dir: Path | None = None,
    repetitions: int = 2,
) -> PassKReport:
    """对给定场景独立重跑 k 次完整 benchmark，统计 pass^1 与 pass^k。

    默认场景集取轻量确定性场景（B1–B3），避免 B10（102 节点）拖累；k 与 scenario_ids
    可配。每次运行使用独立 output_dir，保证"独立重复"语义（非复用缓存）。
    """
    if k < 1:
        raise ValueError("k 必须 >= 1")
    ids = tuple(scenario_ids)
    if not ids:
        raise ValueError("scenario_ids 不能为空")

    success_counts = {sid: 0 for sid in ids}
    with tempfile.TemporaryDirectory(prefix="bimbench-passk-") as tmp:
        root = Path(work_dir) if work_dir is not None else Path(tmp)
        for run_idx in range(k):
            report = run_m1_5_t7_benchmark(
                output_dir=root / f"run-{run_idx}",
                scenario_ids=ids,
                repetitions=repetitions,
            )
            for result in report.scenarios:
                if result.scenario_id in success_counts and scenario_trajectory_ok(result):
                    success_counts[result.scenario_id] += 1

    per = tuple(
        ScenarioPassK(scenario_id=sid, successes=success_counts[sid], k=k) for sid in ids
    )
    total_success = sum(success_counts.values())
    pass_at_1 = round(total_success / max(1, k * len(ids)) * 100.0, 1)
    pass_at_k = round(
        sum(1 for item in per if item.successes == k) / max(1, len(ids)) * 100.0, 1
    )
    return PassKReport(
        k=k,
        scenario_ids=ids,
        pass_at_1=pass_at_1,
        pass_at_k=pass_at_k,
        per_scenario=per,
        determinism_confirmed=abs(pass_at_k - pass_at_1) < 1e-6,
        measured=True,
        provenance=(
            f"pass^{k} 真实重跑：场景 {', '.join(ids)} 各独立运行 {k} 次完整 M1.5 T7 基准"
            f"（每次 repetitions={repetitions}、独立 output_dir），成功判据=trajectory_ok（白盒可审计）。"
            f"pass^1={pass_at_1}%（总成功 {total_success}/{k * len(ids)}），"
            f"pass^{k}={pass_at_k}%（{sum(1 for i in per if i.successes == k)}/{len(ids)} 场景 k 次全过）。"
            + (
                "pass^k==pass^1 → 确定性内核重复运行零方差（可靠性主张实测支撑）。"
                if abs(pass_at_k - pass_at_1) < 1e-6
                else "pass^k<pass^1 → 存在跨运行方差，需排查非确定性来源。"
            )
        ),
    )


__all__ = ["PassKReport", "ScenarioPassK", "run_passk"]
