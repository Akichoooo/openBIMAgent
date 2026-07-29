"""VLMCritic 测试(M0 阶段3b;ARCH §3 防放水五件套;providers 统一入口)。

覆盖:合法响应解析、markdown fence 容错、无数字反馈被拒(<8 分强制量化)、
重试 1 次后成功(校验错/非 JSON 两种)、重试仍失败抛 CriticInvalidError、
reasoning 通道回退、A/B swap 提示词含上版引用、角色 md 与 rubric 常量同步。
全程禁网络:providers.registry.Registry.chat 以 _FakeRegistry 桩替换。
"""

import base64
import json

import pytest

from openbimagent.vision import critic
from openbimagent.vision.critic import CriticInvalidError, VLMCritic
from openbimagent.vision.rubric import (
    ANCHORS,
    ANTI_INFLATION_FIVE,
    BLENDER_DIMENSIONS,
    SCAD_DIMENSIONS,
    Critic,
    check_score_payload,
)

# 1x1 PNG(离线用;只要求合法 PNG 头 + 非空)
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PREV_BYTES = _PNG_1PX + b"prev-version"
_CURR_BYTES = _PNG_1PX + b"curr-version"

VALID_SCAD_REPLY = json.dumps(
    {
        "reasoning": "CoT 推理:等轴测/正面/顶视三个视角全面评估,物体无漂浮无穿插现象,主体位置居中合理;对照几何正确性与基础构图两维锚点词后逐项打分,整体符合预期标准。",
        "rubric_scores": {"geometry": 9.0, "composition": 8.5},
        "anchor_ref": "anchor:geometry=10(遵循物理空间);composition=10(前景遮挡英雄机位)",
        "actionable_feedback": "无需返工:Object base 可再沿 Z 降 0.1 贴地",
    },
    ensure_ascii=False,
)

VAGUE_LOW_REPLY = json.dumps(
    {
        "reasoning": "CoT 推理:从正面视角观察发现 pole 柱体与 base 底座存在明显的几何重叠问题,两者位置关系不符合物理空间约束,几何正确性评分较低,需要调整物体间距以消除穿插。",
        "rubric_scores": {"geometry": 6.0, "composition": 9.0},
        "anchor_ref": "anchor:geometry=5(轻微重叠)",
        "actionable_feedback": "整体再做好看一点",  # <8 分但无量化参数,门禁必拒
    },
    ensure_ascii=False,
)


class _FakeRegistry:
    """providers registry 桩:按队列吐出 content 字符串/完整 result dict/异常;记录 messages 供断言。"""

    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, role, messages, **kwargs):
        self.calls.append({"role": role, "messages": messages, "kwargs": kwargs})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            reply.setdefault("model_resolved", "gpt-5.5-test")
            return reply
        return {
            "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "model_resolved": "gpt-5.5-test",
        }


def _png(path, data: bytes = _PNG_1PX):
    path.write_bytes(data)
    return path


def _user_parts(call: dict) -> list:
    return call["messages"][1]["content"]


def _image_urls(call: dict) -> list[str]:
    return [p["image_url"]["url"] for p in _user_parts(call) if p.get("type") == "image_url"]


def _user_text(call: dict) -> str:
    return "\n".join(p["text"] for p in _user_parts(call) if p.get("type") == "text")


# ---------- 合法解析与消息形态 ----------


def test_valid_response_parsed(tmp_path) -> None:
    """合法 JSON 响应:解析成 CritiqueResult,字段/分数/模型名正确;payload 过 check_score_payload。"""
    registry = _FakeRegistry([VALID_SCAD_REPLY])
    vlm = VLMCritic(registry)
    assert isinstance(vlm, Critic)  # 满足 Critic 协议
    img = _png(tmp_path / "iter1_iso.png")
    result = vlm.critique([img], SCAD_DIMENSIONS, {"iteration": 1})

    assert result.rubric_scores == {"geometry": 9.0, "composition": 8.5}
    assert result.critic_model == "gpt-5.5-test"
    assert result.ab_swap_ref is None
    check_score_payload(result.to_score_payload(phase="scad"), phase="scad")

    assert len(registry.calls) == 1  # 一次通过,无重试
    call = registry.calls[0]
    assert call["role"] == "critic_scad"  # role 参数注入 providers.chat
    assert call["messages"][0]["role"] == "system"
    urls = _image_urls(call)
    assert len(urls) == 1 and urls[0].startswith("data:image/png;base64,")
    assert urls[0] == "data:image/png;base64," + base64.b64encode(_PNG_1PX).decode("ascii")


def test_system_prompt_is_role_md_with_anti_inflation_spec(tmp_path) -> None:
    """system prompt = agents/<role>.md 正文:含防放水五件套、锚点词、输出契约字段。"""
    registry = _FakeRegistry([VALID_SCAD_REPLY])
    VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})
    system = registry.calls[0]["messages"][0]["content"]
    assert "防放水五件套" in system and "强制 CoT" in system
    for rule in ANTI_INFLATION_FIVE:
        assert rule in system
    for field in ("rubric_scores", "reasoning", "anchor_ref", "actionable_feedback"):
        assert field in system
    assert "frontmatter" not in system and not system.startswith("---")  # 已剥 frontmatter


def test_markdown_fence_tolerated(tmp_path) -> None:
    """模型输出包 ```json fence 时容错提取,解析成功。"""
    reply = "先说明一句\n```json\n" + VALID_SCAD_REPLY + "\n```\n"
    registry = _FakeRegistry([reply])
    result = VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {"iteration": 1})
    assert result.rubric_scores["geometry"] == 9.0
    assert len(registry.calls) == 1


def test_reasoning_channel_fallback(tmp_path) -> None:
    """reasoning 模型:content 为空时回退 message.reasoning 解析(providers 方言兼容)。"""
    reply = {
        "choices": [{"message": {"role": "assistant", "content": "", "reasoning": VALID_SCAD_REPLY}}],
    }
    registry = _FakeRegistry([reply])
    result = VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})
    assert result.rubric_scores == {"geometry": 9.0, "composition": 8.5}


# ---------- 防放水:无数字反馈被拒 + 重试 ----------


def test_vague_feedback_rejected_then_retry_succeeds(tmp_path) -> None:
    """<8 分但 actionable_feedback 无数字:首试被拒,重试消息附「量化」错误说明,第二次合法即成功。"""
    registry = _FakeRegistry([VAGUE_LOW_REPLY, VALID_SCAD_REPLY])
    result = VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {"iteration": 2})
    assert result.rubric_scores["geometry"] == 9.0
    assert len(registry.calls) == 2  # 首试 + 恰好 1 次重试
    retry_messages = registry.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["system", "user", "assistant", "user"]
    assert retry_messages[2]["content"] == VAGUE_LOW_REPLY  # 回填上次输出供对照
    assert "量化" in retry_messages[3]["content"]  # 附带错误说明


def test_invalid_json_retry_then_succeeds(tmp_path) -> None:
    """首试输出非 JSON:重试 1 次后成功。"""
    registry = _FakeRegistry(["我无法给出 JSON,随便聊聊", VALID_SCAD_REPLY])
    result = VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})
    assert result.critic_model == "gpt-5.5-test"
    assert len(registry.calls) == 2
    assert "JSON" in registry.calls[1]["messages"][3]["content"]


def test_two_invalid_replies_raise_critic_invalid(tmp_path) -> None:
    """重试仍非法(两次都非 JSON):抛 CriticInvalidError,且恰好 2 次调用不再多试。"""
    registry = _FakeRegistry(["垃圾输出一", "垃圾输出二"])
    with pytest.raises(CriticInvalidError, match="连续 2 次输出非法"):
        VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})
    assert len(registry.calls) == 2


def test_two_vague_replies_raise_critic_invalid(tmp_path) -> None:
    """重试仍非法(两次都无量化反馈):抛 CriticInvalidError,错误链带「量化」原因。"""
    registry = _FakeRegistry([VAGUE_LOW_REPLY, VAGUE_LOW_REPLY])
    with pytest.raises(CriticInvalidError, match="量化"):
        VLMCritic(registry).critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})
    assert len(registry.calls) == 2


# ---------- A/B swap 与上下文 ----------


def test_ab_swap_prompt_references_previous_version(tmp_path) -> None:
    """有上版截图:user 文本含 ab_swap_ref 引用与 A/B swap 说明;图片顺序 = 上版在前、当批在后。"""
    prev1 = _png(tmp_path / "iter1_iso.png", _PREV_BYTES)
    prev2 = _png(tmp_path / "iter1_front.png", _PREV_BYTES)
    curr = _png(tmp_path / "iter2_iso.png", _CURR_BYTES)
    registry = _FakeRegistry([VALID_SCAD_REPLY])
    context = {
        "iteration": 2,
        "ir": {"version": "0.1", "assets": [{"id": "base", "primitive": "cube", "size": [4, 2, 0.5], "position": [0, 0, 0.25]}]},
        "previous_image_paths": [prev1, prev2],
        "ab_swap_ref": "work/best_ir.json",
    }
    result = VLMCritic(registry).critique([curr], SCAD_DIMENSIONS, context)

    text = _user_text(registry.calls[0])
    assert "work/best_ir.json" in text  # 上版引用进提示词
    assert "A/B swap" in text and "对比组 A" in text and "对比组 B" in text
    assert '"base"' in text  # IR 快照进提示词
    urls = _image_urls(registry.calls[0])
    prev_uri = "data:image/png;base64," + base64.b64encode(_PREV_BYTES).decode("ascii")
    curr_uri = "data:image/png;base64," + base64.b64encode(_CURR_BYTES).decode("ascii")
    assert urls == [prev_uri, prev_uri, curr_uri]  # A 组(上版)先于 B 组(当批)
    assert result.ab_swap_ref == "work/best_ir.json"


def test_blender_phase_six_dimensions(tmp_path) -> None:
    """critic_render 角色:六维全出,phase 推导为 blender;system prompt 取自 critic_render.md。"""
    reply = json.dumps(
        {
            "reasoning": "CoT 推理:几何正确性评估-物体无漂浮穿插,符合物理空间(9分);风格一致性-六维逐一对照锚点词,整体风格统一(9分);材质真实感-略显纯色,未达PBR真实感(9分);经年磨损-自然磨损表现良好(9分);灯光氛围-光影层次丰富(9分);镜头构图-构图合理居中(9分)。",
            "rubric_scores": {d: 9.0 for d in ("geometry", "style", "material", "wear", "lighting", "composition")},
            "anchor_ref": "anchor:material=5(低分重复)",
            "actionable_feedback": "整体保持",
        },
        ensure_ascii=False,
    )
    registry = _FakeRegistry([reply])
    result = VLMCritic(registry, role="critic_render").critique(
        [_png(tmp_path / "render.png")], BLENDER_DIMENSIONS, {"iteration": 1}
    )
    check_score_payload(result.to_score_payload(phase="blender"), phase="blender")
    assert "六维" in registry.calls[0]["messages"][0]["content"]


# ---------- 角色 md 单一事实源同步守卫 ----------


def test_role_md_files_in_sync_with_rubric_constants() -> None:
    """agents/critic_*.md 与 rubric 常量同步:锚点词逐字一致、五件套与输出契约字段齐全。"""
    for role, dims in (("critic_scad", SCAD_DIMENSIONS), ("critic_render", BLENDER_DIMENSIONS)):
        text = (critic.AGENTS_DIR / f"{role}.md").read_text(encoding="utf-8")
        for dim in dims:
            assert dim.value in text
            for anchor in ANCHORS[dim].values():
                assert anchor in text, f"{role}.md 缺锚点词 {anchor!r}"
        for rule in ANTI_INFLATION_FIVE:
            assert rule in text, f"{role}.md 缺防放水规则 {rule!r}"
        for field in ("rubric_scores", "reasoning", "anchor_ref", "actionable_feedback"):
            assert field in text
        assert "CoT" in text and "<8" in text


def test_missing_role_md_reports_clear_error(tmp_path) -> None:
    """角色 md 缺失:报 FileNotFoundError(单一事实源不在即不可评分)。"""
    registry = _FakeRegistry([VALID_SCAD_REPLY])
    vlm = VLMCritic(registry, role="critic_nowhere", agents_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="critic_nowhere"):
        vlm.critique([_png(tmp_path / "a.png")], SCAD_DIMENSIONS, {})


def test_registry_required() -> None:
    """registry=None 直接拒绝:真实 VLM critic 必须走 providers 统一入口。"""
    with pytest.raises(ValueError, match="Registry"):
        VLMCritic(None)
