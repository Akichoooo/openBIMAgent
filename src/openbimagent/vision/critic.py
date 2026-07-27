"""VLM critic:真实视觉模型评分实现(M0 阶段3b)。

对应文档:
- docs/architecture/ARCHITECTURE.md §3 双环视觉自检(防放水五件套、评分分层)
- docs/architecture/COMPONENTS.md §2.5 vision(critic 强制 CoT + 评分落盘字段)
- agents/critic_scad.md / agents/critic_render.md(角色 prompt = system prompt 单一事实源)

VLMCritic 经 providers.registry.Registry.chat(role=...) 调 vision 模型(judge 与生成模型
分家,防放水第 5 条);截图以 base64 data-URI 进 messages(OpenAI image_url 格式,与
tools/probe_agentrouter.py 实测的 agentrouter 通道一致)。
system prompt = 角色 md 正文(评分 rubric + 0/5/10 锚点 + 防放水五件套写死其中);
user prompt = 当批上下文(iteration、编译 IR 快照、应评维度锚点、A/B swap 对比组)。
响应解析:提取 JSON(容错 markdown fence)→ check_score_payload 防放水校验 →
非法则重试 1 次(附带错误说明)→ 仍非法抛 CriticInvalidError(拒放水评分进环)。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from openbimagent.vision.rubric import (
    ANCHORS,
    DIMENSION_LABELS,
    SCAD_DIMENSIONS,
    CritiqueResult,
    Dimension,
    check_score_payload,
)

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
"""角色 prompt 目录(src/openbimagent/vision/critic.py → 上溯三级为仓库根;system prompt 单一事实源)。"""

MAX_ATTEMPTS = 2
"""critic 输出非法时的总尝试次数(首试 + 1 次带错误说明的重试)。"""

_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
"""截图后缀 → data-URI MIME(缺省 image/png,与 html_report._img_data_uri 一致)。"""

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
"""容错提取:markdown fence 包裹的 JSON 块(模型未守「只输出 JSON」约定时兜底)。"""


class CriticInvalidError(RuntimeError):
    """VLM critic 响应经 1 次重试后仍非法(JSON 提取失败或 check_score_payload 拒绝);拒放水评分进环。"""


class VLMCritic:
    """真实 VLM critic(judge 与生成模型分家,ARCH §3 防放水第 5 条)。

    经 providers.registry.Registry.chat(role=...) 走 vision 模型;role 默认 critic_scad
    (SCAD 环两维),Blender 环传 critic_render(六维)。满足 vision.rubric.Critic 协议。
    """

    def __init__(self, registry: Any, *, role: str = "critic_scad", agents_dir: Path | None = None) -> None:
        if registry is None:
            raise ValueError("VLMCritic 需要 providers.registry.Registry;测试/联调请注入 MockCritic")
        self._registry = registry  # providers.registry.Registry;chat(role, messages) 统一入口
        self._role = role
        self._agents_dir = Path(agents_dir) if agents_dir is not None else AGENTS_DIR

    def critique(
        self,
        image_paths: list[Path],
        dimensions: tuple[Dimension, ...],
        context: dict[str, Any],
    ) -> CritiqueResult:
        """观察截图 → 强制 CoT → 按 rubric 维度打分;输出非法重试 1 次,仍非法抛 CriticInvalidError。

        context:iteration、ir(编译 IR 快照)、previous_image_paths + ab_swap_ref
        (A/B swap 对比,防放水第 1 条)、cancel_event(可选,透传 providers 层)。
        """
        dimensions = tuple(dimensions)
        phase = _phase_for_dimensions(dimensions)
        cancel_event = context.get("cancel_event")
        messages = self._build_messages(image_paths, dimensions, context)
        last_error: ValueError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result = self._registry.chat(self._role, messages, cancel_event=cancel_event)
            try:
                return self._parse_result(result, phase, context)
            except ValueError as exc:
                last_error = exc
                if attempt >= MAX_ATTEMPTS:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": _safe_content(result)},
                    {"role": "user", "content": _retry_instruction(exc)},
                ]
        raise CriticInvalidError(
            f"VLM critic(role={self._role!r})连续 {MAX_ATTEMPTS} 次输出非法,拒放水评分进环: {last_error}"
        )

    # ---------- prompt 构造 ----------

    def _build_messages(
        self,
        image_paths: list[Path],
        dimensions: tuple[Dimension, ...],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """system(角色 md 正文)+ user(任务文本 + 截图 image_url 组;有上版时 A/B 两组)。"""
        system = _load_role_brief(self._role, self._agents_dir)
        previous = [Path(p) for p in (context.get("previous_image_paths") or [])]
        current = [Path(p) for p in image_paths]
        parts: list[dict[str, Any]] = [{"type": "text", "text": _user_prompt(dimensions, context, previous)}]
        if previous:
            parts.append({"type": "text", "text": "【对比组 A:上一版快照】"})
            parts.extend(_image_part(p) for p in previous)
            parts.append({"type": "text", "text": "【对比组 B:当批截图,本轮评分对象】"})
        parts.extend(_image_part(p) for p in current)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": parts},
        ]

    # ---------- 响应解析 ----------

    def _parse_result(self, result: dict[str, Any], phase: str, context: dict[str, Any]) -> CritiqueResult:
        """chat.completion → CritiqueResult;任一步非法抛 ValueError(触发上层重试)。"""
        content = _message_content(result)
        parsed = _extract_json(content)
        required = ("rubric_scores", "reasoning", "anchor_ref", "actionable_feedback")
        missing = [key for key in required if key not in parsed]
        if missing:
            raise ValueError(f"critic 输出缺字段 {missing}(输出契约: {list(required)})")
        scores = _coerce_scores(parsed["rubric_scores"])
        payload: dict[str, Any] = {
            "customType": "score",
            "phase": phase,
            "rubric_scores": scores,
            "reasoning": parsed["reasoning"],
            "anchor_ref": parsed["anchor_ref"],
            "actionable_feedback": parsed["actionable_feedback"],
            "critic_model": str(result.get("model_resolved") or "unknown"),
        }
        check_score_payload(payload, phase=phase)  # 防放水校验:<8 分强制量化返工指令等
        return CritiqueResult(
            rubric_scores=scores,
            reasoning=payload["reasoning"],
            anchor_ref=payload["anchor_ref"],
            actionable_feedback=payload["actionable_feedback"],
            critic_model=payload["critic_model"],
            ab_swap_ref=context.get("ab_swap_ref"),
        )


# ---------- 纯函数(可单测) ----------


def _load_role_brief(role: str, agents_dir: Path) -> str:
    """加载 agents/<role>.md 正文(剥 frontmatter)作为 system prompt;缺文件报清晰错误。"""
    path = Path(agents_dir) / f"{role}.md"
    if not path.is_file():
        raise FileNotFoundError(f"critic 角色文件不存在: {path}(agents/ 是 system prompt 单一事实源)")
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1 :]).strip()
    return "\n".join(lines).strip()


def _phase_for_dimensions(dimensions: tuple[Dimension, ...]) -> str:
    """应评维度 → phase:SCAD 两维裁剪 = scad,否则 blender(与 rubric._PHASE_DIMENSIONS 对齐)。"""
    return "scad" if set(dimensions) == set(SCAD_DIMENSIONS) else "blender"


def _image_data_uri(path: Path) -> str:
    """截图文件 → base64 data-URI(与 probe 实测的 agentrouter vision 通道一致)。"""
    mime = _IMAGE_MIME.get(Path(path).suffix.lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _image_part(path: Path) -> dict[str, Any]:
    """OpenAI image_url content-part(tools/probe_agentrouter.py 实测格式)。"""
    return {"type": "image_url", "image_url": {"url": _image_data_uri(path)}}


def _dimension_brief(dimensions: tuple[Dimension, ...]) -> str:
    """应评维度 + 0/5/10 锚点词(ANCHORS 单一事实源),随 user prompt 复述。"""
    lines = []
    for dim in dimensions:
        anchor = ANCHORS[dim]
        lines.append(f"- {dim.value}({DIMENSION_LABELS[dim]}):0={anchor[0]};5={anchor[5]};10={anchor[10]}")
    return "\n".join(lines)


def _user_prompt(
    dimensions: tuple[Dimension, ...],
    context: dict[str, Any],
    previous_images: list[Path],
) -> str:
    """当批任务文本:iteration、应评维度锚点、IR 快照、A/B swap 说明、严格 JSON 输出契约。"""
    blocks = [
        f"本轮评分任务:第 {context.get('iteration', '?')} 轮;只评以下 {len(dimensions)} 维(rubric_scores 只允许这些键):",
        _dimension_brief(dimensions),
    ]
    ir = context.get("ir")
    if ir is not None:
        blocks.append("当批编译 IR 快照:\n" + json.dumps(ir, ensure_ascii=False, indent=2))
    if previous_images:
        ref = context.get("ab_swap_ref") or "(未提供)"
        blocks.append(
            "A/B swap 对比(防放水第 1 条):下方先给对比组 A = 上一版快照"
            f"(引用: {ref}),再给对比组 B = 当批截图(本轮评分对象)。"
            "先按 A→B 顺序审视,再交换为 B→A 复审一遍,防位置偏置、防单向放水;"
            "只对 B 组(当批)打分,reasoning 中写明两版差异结论。"
        )
    else:
        blocks.append("下方为当批截图(首轮,无上一版快照,本轮不做 A/B swap)。")
    blocks.append(
        '严格 JSON 输出(不要输出任何其他文字):{"reasoning": "<CoT 全文,先于打分>", '
        '"rubric_scores": {"<维度>": 0-10}, "anchor_ref": "<锚点引用>", '
        '"actionable_feedback": "<返工指令;任一维 <8 分强制量化>"}'
    )
    return "\n\n".join(blocks)


def _message_content(result: dict[str, Any]) -> str:
    """chat.completion → 正文文本;content 为空时回退 reasoning 通道(providers 方言已兼容)。"""
    try:
        message = result["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"critic 响应缺 choices[0].message: {exc}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"critic 响应 message 形态非法: {type(message).__name__}")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):  # content-part 形态(部分 OpenAI 兼容端点)
        text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if text.strip():
            return text
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    raise ValueError("critic 响应 content 为空(含 reasoning 通道)")


def _safe_content(result: dict[str, Any]) -> str:
    """尽力取回上次输出原文(回填 assistant 消息供重试对照);取不到给占位。"""
    try:
        return _message_content(result)
    except ValueError:
        return "(上一次输出无法读取)"


def _extract_json(text: str) -> dict[str, Any]:
    """模型输出 → JSON object:直解 → markdown fence 容错 → 首尾花括号切片;失败抛 ValueError。"""
    candidate = text.strip()
    fence = _JSON_FENCE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    parsed = _try_loads(candidate)
    if parsed is None and "{" in candidate:
        parsed = _try_loads(candidate[candidate.find("{") : candidate.rfind("}") + 1])
    if not isinstance(parsed, dict):
        raise ValueError("critic 输出无法解析为 JSON object")
    return parsed


def _try_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _coerce_scores(raw: Any) -> dict[str, float]:
    """rubric_scores → {维度: float};非 dict/非数值/bool 即 ValueError(bool 禁当分数)。"""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("rubric_scores 须为非空 dict(维度 → 0-10 分)")
    scores: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"rubric_scores[{key!r}] 须为数值,实收 {value!r}")
        scores[str(key)] = float(value)
    return scores


def _retry_instruction(error: ValueError) -> str:
    """重试说明:附校验错误 + 重申输出契约(含 <8 分量化返工指令硬性格式)。"""
    return (
        f"上一次输出未通过校验:{error}\n"
        "请修正后重新输出完整 JSON(reasoning 全文先于 rubric_scores);"
        "任一维 < 8 分,actionable_feedback 必须含量化参数的 actionable_rework_command"
        "(形如「Object A 缩放 0.8 并沿 Z 降 0.2」),禁止空泛建议。"
    )
