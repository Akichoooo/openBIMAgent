"""Rubric:六维评分 + 0/5/10 锚点 + 防放水五件套常量 + critic 协议。

对应文档:
- docs/architecture/ARCHITECTURE.md §3 双环视觉自检(定稿,rubric 来源 03 报告)
- docs/architecture/COMPONENTS.md §2.5 vision
- docs/research/07_gemini_trace_observability.md §3 VLM 评分留痕与防飘移

SCAD 环只评两维(几何正确性 + 基础构图);Blender 环激活全部六维。
critic 强制 CoT:先 reasoning 后打分;评分事件落盘
rubric_scores / reasoning / anchor_ref / actionable_feedback(见 session.schema.ScorePayload)。
critic 实现:MockCritic(本文件,测试专用)/ VLMCritic(vision/critic.py,真实 VLM)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class Dimension(StrEnum):
    """六维(ARCH §3 表);SCAD 环只用其中两维。"""

    GEOMETRY = "geometry"  # 几何正确性:比例与边界无穿插
    STYLE = "style"  # 风格一致性:符合设定流派
    MATERIAL = "material"  # 材质真实感:纹理/法线/反光
    WEAR = "wear"  # 经年磨损破损:做旧与环境互动
    LIGHTING = "lighting"  # 灯光氛围:色温阴影层次
    COMPOSITION = "composition"  # 镜头构图:焦段与主体突出


DIMENSION_LABELS: dict[Dimension, str] = {
    Dimension.GEOMETRY: "几何正确性",
    Dimension.STYLE: "风格一致性",
    Dimension.MATERIAL: "材质真实感",
    Dimension.WEAR: "经年磨损破损",
    Dimension.LIGHTING: "灯光氛围",
    Dimension.COMPOSITION: "镜头构图",
}
"""维度 → 中文名(ARCH §3 表;HTML 验收页与落盘展示用)。"""

ANCHORS: dict[Dimension, dict[int, str]] = {
    Dimension.GEOMETRY: {0: "严重漂浮", 5: "轻微重叠", 10: "遵循物理空间"},
    Dimension.STYLE: {0: "出戏", 5: "元素堆砌", 10: "浑然天成"},
    Dimension.MATERIAL: {0: "纯色", 5: "低分重复", 10: "PBR 真实"},
    Dimension.WEAR: {0: "一尘不染", 5: "均匀噪声脏", 10: "自然水渍磕碰边缘磨损"},
    Dimension.LIGHTING: {0: "全白无影", 5: "有光死板", 10: "体积光层次 GI"},
    Dimension.COMPOSITION: {0: "遮挡跑焦", 5: "居中平庸", 10: "前景遮挡英雄机位"},
}
"""六维 0/5/10 锚点词(ARCH §3 表,定稿);评分须与锚点图/锚点词对齐并落盘 anchor_ref。"""

SCAD_DIMENSIONS: tuple[Dimension, ...] = (Dimension.GEOMETRY, Dimension.COMPOSITION)
"""环 1 只评两维:几何正确性 + 基础构图(ARCH §3 环 1)。"""

BLENDER_DIMENSIONS: tuple[Dimension, ...] = tuple(Dimension)
"""环 2 激活全部六维(ARCH §3 环 2)。"""

ANTI_INFLATION_TRIO: tuple[str, ...] = ("ab_swap", "forced_rework_command", "anchor_alignment")
"""防放水三件套(五件套的前三条,ANCHOR 时代遗留名;完整版见 ANTI_INFLATION_FIVE)。"""

ANTI_INFLATION_FIVE: tuple[str, ...] = (
    "ab_swap",
    "forced_rework_command",
    "anchor_alignment",
    "critical_pass_fail_gate",
    "judge_generator_separation",
)
"""防放水五件套(写死进 critic system prompt,ARCH §3):
1. ab_swap —— A/B swap 两两比较:与上一版快照对比评分,交换顺序防位置偏置,防单向放水;
2. forced_rework_command —— <8 分强制 actionable_rework_command
   (形如「Object A 缩放 0.8 并沿 Z 降 0.2」),禁止空泛建议;
3. anchor_alignment —— 锚点图对齐:评分与金标准锚点图/锚点词对齐,落盘 anchor_ref;
   critic 强制 CoT(reasoning),temperature=0 不够,靠锚点 + 留痕防飘移(07 报告);
4. critical_pass_fail_gate —— 关键维 pass/fail 硬门禁不进平均(见 CRITICAL_PASS_FAIL_CHECKS);
5. judge_generator_separation —— judge 与生成模型分家(见 JUDGE_GENERATOR_SEPARATION)。
"""

REWORK_COMMAND_REQUIRED_BELOW = 8.0
"""低于该分必须给出 actionable_rework_command(ARCH §3 防放水第 2 条)。"""

REASONING_MIN_CHARS = 50
"""reasoning(CoT)最小字符数(防放水第 3 条,强制 CoT 留痕;50 字符确保充分阐述评分依据)。"""

CRITICAL_PASS_FAIL_CHECKS: tuple[str, ...] = ("clash_free", "clearance_height", "connectivity")
"""关键维 pass/fail 硬门禁清单(碰撞/净高/连通;ARCH §3 防放水第 4 条):
二元判定,不进六维平均,与 domain_gate(constraints.yaml 驱动)呼应;任一 fail 直接打回,不看总分。"""

JUDGE_GENERATOR_SEPARATION: str = (
    "judge(critic)与生成模型必须分家:禁止同会话/同模型自我打高分;"
    "Domain Pack 附黄金截图集,版本升级先跑 judge 校准回归(ARCH §3 防放水第 5 条)。"
)
"""防放水第 5 条说明常量(judge 与生成分家)。"""

_QUANTIFIED_COMMAND = re.compile(r"\d")
"""M0 简化判定:actionable_rework_command 须含量化参数(形如「Object A 缩放 0.8 并沿 Z 降 0.2」)。"""

_PHASE_DIMENSIONS: dict[str, tuple[Dimension, ...]] = {
    "scad": SCAD_DIMENSIONS,
    "blender": BLENDER_DIMENSIONS,
}
"""phase → 该环应评的维度集合(scad 两维裁剪,blender 六维全出)。"""


def check_score_payload(payload: dict[str, Any], *, phase: str) -> None:
    """校验评分落盘字段:rubric_scores/reasoning/anchor_ref/actionable_feedback 必填。

    防放水五件套(ARCH §3):
    - 第 2 条:任一维 < 8 分强制量化 actionable_rework_command(含数字,禁空泛建议)。
    - 第 3 条:强制 CoT(reasoning >= REASONING_MIN_CHARS 字符)+ 锚点对齐(anchor_ref 非空)。
    phase=scad 时 rubric_scores 只许两维(SCAD_DIMENSIONS);phase=blender 时六维全出。
    与 schemas/score_event.schema.json 对齐;违规抛 ValueError,消息注明违反防放水第几条。
    """
    if phase not in _PHASE_DIMENSIONS:
        raise ValueError(f"phase 须为 {sorted(_PHASE_DIMENSIONS)},实收 {phase!r}")
    required = ("rubric_scores", "reasoning", "anchor_ref", "actionable_feedback", "critic_model")
    missing = [key for key in required if key not in payload]
    if missing:
        detail = ", ".join(f"{k} 缺失" for k in missing)
        raise ValueError(f"评分落盘缺必填字段: {detail}(schemas/score_event.schema.json)")

    scores = payload["rubric_scores"]
    if not isinstance(scores, dict) or not scores:
        raise ValueError("rubric_scores 须为非空 dict(维度 → 0-10 分)")
    valid_keys = {d.value for d in Dimension}
    unknown = sorted(set(scores) - valid_keys)
    if unknown:
        raise ValueError(f"rubric_scores 含未知维度 {unknown};合法维度: {sorted(valid_keys)}")
    expected = {d.value for d in _PHASE_DIMENSIONS[phase]}
    if set(scores) != expected:
        raise ValueError(f"phase={phase} 应评 {sorted(expected)} 维,实收 {sorted(scores)}")
    for key, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 10.0:
            raise ValueError(f"rubric_scores[{key!r}] 须为 0-10 数值,实收 {value!r}")

    # 强制 CoT 与锚点留痕(防放水第 3 条)
    for key in ("reasoning", "anchor_ref", "actionable_feedback", "critic_model"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError(f"{key} 缺失或为空白(强制 CoT 与防放水留痕)")

    # reasoning 长度校验(强制 CoT,防放水第 3 条)
    if len(payload["reasoning"].strip()) < REASONING_MIN_CHARS:
        raise ValueError(
            f"reasoning 过短(< {REASONING_MIN_CHARS} 字符,违反防放水第 3 条,强制 CoT)"
        )

    # 低分强制量化 actionable_rework_command(防放水第 2 条)
    if any(value < REWORK_COMMAND_REQUIRED_BELOW for value in scores.values()):
        if not _QUANTIFIED_COMMAND.search(payload["actionable_feedback"]):
            raise ValueError(
                "actionable_feedback 缺少量化参数(须含数字,违反防放水第 2 条)"
                "(形如「Object A 缩放 0.8 并沿 Z 降 0.2」),禁止空泛建议"
            )


@dataclass(frozen=True)
class CritiqueResult:
    """critic 单轮评分结果(落盘字段与 session.schema.ScorePayload 对齐;强制 CoT:reasoning 先于打分)。"""

    rubric_scores: dict[str, float]  # Dimension value → 0-10
    reasoning: str  # CoT 全文
    anchor_ref: str  # 金标准锚点图/锚点词引用(防放水第 3 条)
    actionable_feedback: str  # 可执行返工指令;<8 分强制量化(防放水第 2 条)
    critic_model: str = "unknown"
    ab_swap_ref: str | None = None  # A/B swap 对比的上一版快照引用(防放水第 1 条)

    @property
    def overall_score(self) -> float:
        """所评维度算术平均;关键维 pass/fail 硬门禁不进平均(防放水第 4 条,走 domain_gate)。"""
        if not self.rubric_scores:
            return 0.0
        return sum(self.rubric_scores.values()) / len(self.rubric_scores)

    def to_score_payload(self, *, phase: str) -> dict[str, Any]:
        """转成 session customType=score 的 payload dict(可直接过 check_score_payload)。"""
        payload: dict[str, Any] = {
            "customType": "score",
            "phase": phase,
            "rubric_scores": dict(self.rubric_scores),
            "reasoning": self.reasoning,
            "anchor_ref": self.anchor_ref,
            "actionable_feedback": self.actionable_feedback,
            "critic_model": self.critic_model,
        }
        if self.ab_swap_ref:
            payload["ab_swap_ref"] = self.ab_swap_ref
        return payload


@runtime_checkable
class Critic(Protocol):
    """critic 协议(抽象):观察截图 → CoT 推理 → 按 rubric 维度打分。

    实现:MockCritic(测试,禁网络)/ VLMCritic(真实,见 vision.critic,经 providers.chat 走 vision 模型)。
    context 由调用方给:iteration、IR 快照、previous_image_paths(A/B swap 对比)、ab_swap_ref 等。
    """

    def critique(
        self,
        image_paths: list[Path],
        dimensions: tuple[Dimension, ...],
        context: dict[str, Any],
    ) -> CritiqueResult: ...


class MockCritic:
    """可编程 critic(测试专用,禁网络):按队列依次返回,耗尽后重复最后一条。

    队列元素可为 CritiqueResult(原样返回)/ dict(维度 → 分)/ float(全部维度同分);
    dict/float 形态自动补齐合法 reasoning/anchor_ref/量化 actionable_feedback(可过 check_score_payload)。
    """

    def __init__(self, results: list[CritiqueResult | dict[str, float] | float], *, critic_model: str = "mock-critic") -> None:
        if not results:
            raise ValueError("MockCritic 至少需要一条可编程结果")
        self._items = list(results)
        self._model = critic_model
        self.calls: list[dict[str, Any]] = []  # 调用留痕:image_paths / dimensions / context

    def critique(
        self,
        image_paths: list[Path],
        dimensions: tuple[Dimension, ...],
        context: dict[str, Any],
    ) -> CritiqueResult:
        self.calls.append({"image_paths": list(image_paths), "dimensions": dimensions, "context": dict(context)})
        item = self._items.pop(0) if len(self._items) > 1 else self._items[0]
        return self._coerce(item, dimensions)

    def _coerce(self, item: CritiqueResult | dict[str, float] | float, dimensions: tuple[Dimension, ...]) -> CritiqueResult:
        if isinstance(item, CritiqueResult):
            return item
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            scores = {d.value: float(item) for d in dimensions}
        elif isinstance(item, dict):
            scores = {str(k): float(v) for k, v in item.items()}
        else:
            raise TypeError(f"MockCritic 不支持的结果形态: {type(item)!r}")
        return CritiqueResult(
            rubric_scores=scores,
            reasoning=f"mock CoT:三视角截图观察完毕,与锚点词对齐后打分 {scores}",
            anchor_ref="mock-anchor:" + ",".join(f"{k}={v}" for k, v in scores.items()),
            actionable_feedback="mock 返工:Object A 缩放 0.8 并沿 Z 降 0.2",
            critic_model=self._model,
        )
