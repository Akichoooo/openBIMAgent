"""reasoning 统一抽象单测：5 档→各家 wire 映射 + clamp 降级 + per-role 默认档位。

对应 dialects.reasoning_payload / registry._REASONING_DEFAULTS（2026-09 调研落地）。
"""

from __future__ import annotations

from openbimagent.providers.dialects import REASONING_LEVELS, reasoning_payload
from openbimagent.providers.registry import _REASONING_DEFAULTS


def test_levels_constant() -> None:
    assert REASONING_LEVELS == ("off", "low", "medium", "high", "max")


def test_mimo_maps_to_thinking_type() -> None:
    """mimo-v2.5 用 thinking.type（实测忽略 reasoning_effort；disabled 直出治 524）。"""
    assert reasoning_payload("mimo-v2.5", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_payload("mimo-v2.5", "low") == {"thinking": {"type": "disabled"}}
    assert reasoning_payload("mimo-v2.5", "medium") == {"thinking": {"type": "adaptive"}}
    assert reasoning_payload("mimo-v2.5", "max") == {"thinking": {"type": "enabled"}}


def test_effort_wire_models() -> None:
    assert reasoning_payload("glm-5.2", "off") == {"reasoning_effort": "none"}
    assert reasoning_payload("glm-5.2", "max") == {"reasoning_effort": "max"}
    assert reasoning_payload("gpt-5.6-luna", "high") == {"reasoning_effort": "high"}
    assert reasoning_payload("claude-opus-4-8", "high") == {"output_config": {"effort": "high"}}
    assert reasoning_payload("gemini-3.1-pro", "high") == {"thinking_level": "high"}


def test_force_think_clamp_no_error() -> None:
    """kimi-k3 始终推理：off/low clamp 到其最低档 max，不报错。"""
    assert reasoning_payload("kimi-k3", "off") == {"reasoning_effort": "max"}
    assert reasoning_payload("kimi-k3", "low") == {"reasoning_effort": "max"}
    assert reasoning_payload("kimi-k3", "max") == {"reasoning_effort": "max"}


def test_qwen_enable_thinking_budget() -> None:
    assert reasoning_payload("qwen3.8-max", "off") == {"enable_thinking": False}
    assert reasoning_payload("qwen3.8-max", "high") == {"enable_thinking": True, "thinking_budget": 262144}
    assert reasoning_payload("qwen3.8-max", "low") == {"enable_thinking": True, "thinking_budget": 4096}


def test_deepseek_thinking_type_plus_effort() -> None:
    assert reasoning_payload("deepseek-v4-flash", "off") == {"thinking": {"type": "disabled"}}
    assert reasoning_payload("deepseek-v4-flash", "max") == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }


def test_unknown_model_fallback_generic_effort() -> None:
    assert reasoning_payload("some-unknown-model", "medium") == {"reasoning_effort": "medium"}
    assert reasoning_payload("some-unknown-model", "off") == {"reasoning_effort": "none"}


def test_invalid_level_falls_back_to_medium() -> None:
    """非法档位回退 medium（glm 的 medium 映射 high）。"""
    assert reasoning_payload("glm-5.2", "bogus") == {"reasoning_effort": "high"}
    assert reasoning_payload("mimo-v2.5", "bogus") == {"thinking": {"type": "adaptive"}}


def test_per_role_defaults() -> None:
    """质量咽喉高档、杂活低档（per-role 默认）。"""
    assert _REASONING_DEFAULTS["critic_render"] == "high"
    assert _REASONING_DEFAULTS["critic_scad"] == "high"
    assert _REASONING_DEFAULTS["modeler"] == "high"
    assert _REASONING_DEFAULTS["planner"] == "high"
    assert _REASONING_DEFAULTS["clarify"] == "low"
    assert _REASONING_DEFAULTS["deliver"] == "low"
    assert _REASONING_DEFAULTS["orchestrator"] == "medium"
