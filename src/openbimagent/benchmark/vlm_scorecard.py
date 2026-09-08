"""A4 VLM 六维视觉评分接入（为双环视觉自检提供实验证据，对标 AECBench LLM-as-Judge）。

VLMCritic 经 providers.registry 走 vision 模型（role=critic_render，六维 BLENDER_DIMENSIONS），
对渲染图强制 CoT 打分（防放水五件套）。本模块把六维评分接入 academic_bench，产出实测评分分布。

数据诚信（fail-closed）：离线（无 vision profile / 无有效渲染图 / 无 key / 429 耗尽）时
**诚实降级为 measured=False 占位并附 provenance 警告，绝不伪造评分**——与 academic_bench
既有占位契约一致，未实测行禁止引用于论文/答辩。

429 编排：VLMCritic 走 registry.chat，providers 层已有 resilience（retry max=3 exponential
backoff，见 config/models.toml [resilience]）；本模块串行调用、不并发，低并发供应商友好。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class VLMScorecard:
    """六维 VLM 评分卡 + 数据来源声明。"""

    measured: bool
    overall_score: float | None  # 六维算术平均（关键维 pass/fail 走 domain_gate，不进均值）
    dimension_scores: dict[str, float] = field(default_factory=dict)  # Dimension value → 0-10
    critic_model: str = ""
    image_count: int = 0
    role: str = "critic_render"
    provenance: str = ""

    def to_markdown_rows(self) -> list[str]:
        if not self.measured:
            return [
                "| VLM 六维评分 | — | ⚠️ 未实测占位 |",
                f"| 原因 | {self.provenance} | 禁止引用 |",
            ]
        rows = [
            f"| VLM 六维综合 (overall) | **{self.overall_score:.2f}** / 10 | {self.critic_model} · {self.image_count} 图 |",
        ]
        for dim, score in sorted(self.dimension_scores.items()):
            rows.append(f"| ├─ {dim} | {score:.1f} | 0–10 锚点对齐 |")
        return rows


def _unmeasured(reason: str) -> VLMScorecard:
    return VLMScorecard(
        measured=False,
        overall_score=None,
        dimension_scores={},
        critic_model="",
        image_count=0,
        provenance=f"{reason}（VLM 六维未实测，占位禁止引用于论文/答辩）",
    )


def run_vlm_scorecard(
    image_paths: Sequence[Path | str],
    *,
    registry: Any = None,
    role: str = "critic_render",
    critic: Any = None,
    context: dict[str, Any] | None = None,
) -> VLMScorecard:
    """对渲染图跑六维 VLM 评分。

    - ``critic`` 可注入（测试用 MockCritic，禁网络）；缺省时按 registry 构造真实 VLMCritic。
    - 无有效图 / 无 vision provider / 调用失败（无 key、429 耗尽、网络）→ measured=False 占位。
    - 真跑成功 → 六维 rubric_scores + overall（ CritiqueResult.overall_score）。
    """
    from openbimagent.vision.rubric import BLENDER_DIMENSIONS

    imgs = [Path(p) for p in image_paths if Path(p).is_file()]
    if not imgs:
        return _unmeasured("未提供有效渲染图（需 Blender 真机渲染或既有 BIM 截图）")

    if critic is None:
        try:
            from openbimagent.vision.critic import VLMCritic

            if registry is None:
                from openbimagent.providers.registry import get_default_registry

                registry = get_default_registry()
            critic = VLMCritic(registry, role=role)
        except Exception as exc:  # noqa: BLE001 — vision provider 不可用即降级占位
            return _unmeasured(f"vision provider 不可用：{type(exc).__name__}: {str(exc)[:120]}")

    try:
        result = critic.critique(imgs, BLENDER_DIMENSIONS, context or {})
    except Exception as exc:  # noqa: BLE001 — 无 key/429/网络/非法输出统一降级占位，不伪造
        return _unmeasured(f"VLM 评分调用失败：{type(exc).__name__}: {str(exc)[:120]}")

    scores = {str(k): float(v) for k, v in dict(result.rubric_scores).items()}
    return VLMScorecard(
        measured=True,
        overall_score=round(float(result.overall_score), 2),
        dimension_scores=scores,
        critic_model=str(result.critic_model or "unknown"),
        image_count=len(imgs),
        role=role,
        provenance=(
            f"VLMCritic(role={role}, model={result.critic_model}) 对 {len(imgs)} 张渲染图六维真跑："
            f"overall={result.overall_score:.2f}/10；强制 CoT + 锚点对齐 + 防放水五件套；"
            "critical pass/fail（clash_free/clearance/connectivity）走 domain_gate 不进均值。"
        ),
    )


__all__ = ["VLMScorecard", "run_vlm_scorecard"]
