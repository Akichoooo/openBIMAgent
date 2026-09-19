"""H1 测试:clarify 表达特征规则库 + 小模型兜底抽取(论文 07 方法论落地)。

- 规则库 EF1-EF8 渲染进 prompt 片段,clarify.md 与 slots.py 单一事实源对齐;
- 兜底抽取:规则未命中槽位 → clarify 角色 JSON 兜底;编造槽位 id 忽略;
  模型失败/非法输出静默回退纯规则结果(离线确定性优先);
- pipeline brief 预填:命中的槽位不再重复追问。
"""

from __future__ import annotations

import json
from pathlib import Path

from openbimagent.clarify.slots import (
    EXPRESSION_FEATURE_RULES,
    Slot,
    extract_slots_with_fallback,
    expression_rules_fragment,
)


def _slots() -> list[Slot]:
    return [
        Slot(id="asset", question="做什么资产?"),
        Slot(id="diameter", question="管径多大?", aliases=["DN", "管径"]),
        Slot(id="city", question="哪个城市?"),
    ]


def test_expression_rules_cover_eight_features() -> None:
    ids = [rule["id"] for rule in EXPRESSION_FEATURE_RULES]
    assert ids == [f"EF{i}" for i in range(1, 9)]
    fragment = expression_rules_fragment()
    assert all(rule["feature"] in fragment for rule in EXPRESSION_FEATURE_RULES)
    assert "指称混用" in fragment and "过程压缩" in fragment


def test_clarify_md_aligns_with_rule_table() -> None:
    text = Path("agents/clarify.md").read_text(encoding="utf-8")
    for rule in EXPRESSION_FEATURE_RULES:
        assert rule["id"] in text, f"clarify.md 缺少 {rule['id']}"
    assert "EXPRESSION_FEATURE_RULES" in text  # 指回单一事实源


def test_fallback_fills_rule_missed_slots() -> None:
    slots = _slots()
    slots[0].value = "雨水管网"  # 规则已命中
    captured: list[dict] = []

    def chat_fn(role, messages, **kw):
        captured.append({"role": role, "messages": [dict(m) for m in messages]})
        return {"content": '```json\n{"diameter": "DN300", "city": "长沙"}\n```'}

    slots, filled = extract_slots_with_fallback("做一个长沙的雨水管网,管子三百的", slots, chat_fn=chat_fn)
    assert filled == ["diameter", "city"]
    assert slots[1].value == "DN300" and slots[2].value == "长沙"
    # 兜底 prompt 携带表达特征规则与槽位说明
    prompt = captured[0]["messages"][0]["content"]
    assert "EF1" in prompt and "禁止编造" in prompt
    assert "diameter" in prompt


def test_fallback_ignores_hallucinated_slot_ids() -> None:
    slots = _slots()

    def chat_fn(role, messages, **kw):
        return {"content": '{"nonexistent": "x", "city": null, "diameter": 300}'}

    slots, filled = extract_slots_with_fallback("做个管网", slots, chat_fn=chat_fn)
    assert filled == []  # int 值与 null、编造 id 全部忽略
    assert all(s.value is None for s in slots)


def test_fallback_silent_on_model_failure() -> None:
    slots = _slots()

    def boom(role, messages, **kw):
        raise RuntimeError("offline")

    slots, filled = extract_slots_with_fallback("做个管网", slots, chat_fn=boom)
    assert filled == []
    bad_output = lambda role, messages, **kw: {"content": "不是 JSON"}
    slots2 = _slots()
    _, filled2 = extract_slots_with_fallback("做个管网", slots2, chat_fn=bad_output)
    assert filled2 == []


def test_pipeline_brief_prefills_slots() -> None:
    """brief 预填命中的槽位不追问(问题数减少);用真实 single_asset_hero playbook。"""
    from pathlib import Path as _P

    from openbimagent.assembly.pipeline import run_pipeline

    playbook = _P("domain_packs/single_asset_hero/playbook.md")
    questions: list[str] = []

    def input_func(prompt: str) -> str:
        questions.append(prompt)
        return ""

    class _Registry:
        def chat(self, role, messages, **kw):
            if role == "clarify":
                return {"content": '{"asset": "一台蒸汽朋克飞艇"}'}
            raise RuntimeError("planner offline")  # 后续阶段走确定性/失败路径,不影响本断言

    import tempfile

    out_dir = _P(tempfile.mkdtemp())
    result = run_pipeline(
        playbook,
        out_dir=out_dir,
        registry=_Registry(),
        input_func=input_func,
        brief="帮我做一个蒸汽朋克飞艇",
    )
    # asset 被兜底预填,只剩 style 与 wear_level 两个问题
    assert len(questions) == 2
    assert all("资产" not in q for q in questions)
    assert result.session is not None
