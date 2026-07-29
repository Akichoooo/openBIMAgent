"""vision rubric 测试(ARCH §3:六维 + 0/5/10 锚点 + 防放水五件套 + critic 协议)。

覆盖:维度枚举/锚点文本、SCAD 两维裁剪、五件套常量、check_score_payload 校验、MockCritic 协议。
"""

import pytest

from openbimagent.vision import rubric
from openbimagent.vision.rubric import (
    ANCHORS,
    ANTI_INFLATION_FIVE,
    ANTI_INFLATION_TRIO,
    BLENDER_DIMENSIONS,
    CRITICAL_PASS_FAIL_CHECKS,
    DIMENSION_LABELS,
    JUDGE_GENERATOR_SEPARATION,
    REWORK_COMMAND_REQUIRED_BELOW,
    SCAD_DIMENSIONS,
    Critic,
    CritiqueResult,
    Dimension,
    MockCritic,
)


def _scad_payload(**overrides):
    payload = {
        "customType": "score",
        "phase": "scad",
        "rubric_scores": {"geometry": 9.0, "composition": 8.5},
        "reasoning": "CoT 推理:从等轴测/正面/顶视三个视角全面评估,物体无漂浮无穿插现象,主体位置居中合理,符合几何正确性标准;基础构图评分良好,整体布局合理。",
        "anchor_ref": "anchor:geometry=10(遵循物理空间)",
        "actionable_feedback": "无需返工:整体保持,Object A 可再降 0.1 贴地",
        "critic_model": "mock-critic",
    }
    payload.update(overrides)
    return payload


# ---------- 维度与锚点 ----------


def test_dimension_enum_and_labels() -> None:
    """六维枚举:6 个成员,值与 score schema 键一致;每个维度有中文名。"""
    assert [d.value for d in Dimension] == ["geometry", "style", "material", "wear", "lighting", "composition"]
    assert set(DIMENSION_LABELS) == set(Dimension)
    assert DIMENSION_LABELS[Dimension.GEOMETRY] == "几何正确性"
    assert DIMENSION_LABELS[Dimension.COMPOSITION] == "镜头构图"


def test_anchors_cover_all_dimensions_with_0_5_10() -> None:
    """六维锚点全覆盖:每维恰好 0/5/10 三档,文本与 ARCH §3 定稿表逐字一致。"""
    assert set(ANCHORS) == set(Dimension)
    for dim, anchors in ANCHORS.items():
        assert set(anchors) == {0, 5, 10}
        assert all(isinstance(text, str) and text for text in anchors.values())
    assert ANCHORS[Dimension.GEOMETRY] == {0: "严重漂浮", 5: "轻微重叠", 10: "遵循物理空间"}
    assert ANCHORS[Dimension.LIGHTING] == {0: "全白无影", 5: "有光死板", 10: "体积光层次 GI"}
    assert ANCHORS[Dimension.COMPOSITION][10] == "前景遮挡英雄机位"


def test_scad_dimensions_trimmed_to_two() -> None:
    """SCAD 环两维裁剪:只评几何正确性 + 镜头构图;Blender 环六维全出。"""
    assert SCAD_DIMENSIONS == (Dimension.GEOMETRY, Dimension.COMPOSITION)
    assert BLENDER_DIMENSIONS == tuple(Dimension)


# ---------- 防放水五件套常量 ----------


def test_anti_inflation_constants() -> None:
    """五件套常量:A/B swap、<8 强制 rework、锚点引用、关键维 pass/fail、judge 与生成分家。"""
    assert ANTI_INFLATION_FIVE == (
        "ab_swap",
        "forced_rework_command",
        "anchor_alignment",
        "critical_pass_fail_gate",
        "judge_generator_separation",
    )
    assert set(ANTI_INFLATION_TRIO) <= set(ANTI_INFLATION_FIVE)  # 三件套是前五件套的子集
    assert REWORK_COMMAND_REQUIRED_BELOW == 8.0
    assert CRITICAL_PASS_FAIL_CHECKS  # 关键维硬门禁清单非空(碰撞/净高/连通)
    assert "clash_free" in CRITICAL_PASS_FAIL_CHECKS
    assert "分家" in JUDGE_GENERATOR_SEPARATION


# ---------- check_score_payload ----------


def test_check_score_payload_accepts_valid_scad() -> None:
    """合法 scad payload(两维 + 全留痕)校验通过。"""
    rubric.check_score_payload(_scad_payload(), phase="scad")


def test_check_score_payload_requires_fields() -> None:
    """必填字段缺失即拒绝(rubric_scores/reasoning/anchor_ref/actionable_feedback/critic_model)。"""
    for key in ("rubric_scores", "reasoning", "anchor_ref", "actionable_feedback", "critic_model"):
        payload = _scad_payload()
        del payload[key]
        with pytest.raises(ValueError, match=key):
            rubric.check_score_payload(payload, phase="scad")


def test_check_score_payload_phase_dimension_trimming() -> None:
    """phase 维度裁剪:scad 只许两维(多/少都拒),blender 须六维全出。"""
    extra_dim = _scad_payload(rubric_scores={"geometry": 9.0, "composition": 8.5, "style": 9.0})
    with pytest.raises(ValueError, match="phase=scad"):
        rubric.check_score_payload(extra_dim, phase="scad")
    missing_dim = _scad_payload(rubric_scores={"geometry": 9.0})
    with pytest.raises(ValueError, match="phase=scad"):
        rubric.check_score_payload(missing_dim, phase="scad")

    blender = _scad_payload(rubric_scores={d.value: 9.0 for d in Dimension})
    rubric.check_score_payload(blender, phase="blender")
    with pytest.raises(ValueError, match="phase=blender"):
        rubric.check_score_payload(_scad_payload(), phase="blender")
    with pytest.raises(ValueError, match="phase"):
        rubric.check_score_payload(_scad_payload(), phase="unknown")


def test_check_score_payload_score_range_and_unknown_dimension() -> None:
    """分数须在 0-10;维度键必须是合法 Dimension。"""
    with pytest.raises(ValueError, match="0-10"):
        rubric.check_score_payload(_scad_payload(rubric_scores={"geometry": 11.0, "composition": 8.5}), phase="scad")
    with pytest.raises(ValueError, match="未知维度"):
        rubric.check_score_payload(_scad_payload(rubric_scores={"geometry": 9.0, "vibe": 8.5}), phase="scad")


def test_check_score_payload_forces_quantified_rework_below_8() -> None:
    """任一维 <8:actionable_feedback 必须含量化参数(数字),禁空泛建议(防放水第 2 条)。"""
    vague = _scad_payload(
        rubric_scores={"geometry": 6.0, "composition": 9.0},
        actionable_feedback="整体再做好看一点",
    )
    with pytest.raises(ValueError, match="量化"):
        rubric.check_score_payload(vague, phase="scad")
    quantified = _scad_payload(
        rubric_scores={"geometry": 6.0, "composition": 9.0},
        actionable_feedback="Object A 缩放 0.8 并沿 Z 降 0.2",
    )
    rubric.check_score_payload(quantified, phase="scad")
    # 全维 ≥8 时空泛文案不触发强制量化规则
    rubric.check_score_payload(_scad_payload(actionable_feedback="整体保持"), phase="scad")


def test_check_score_payload_rejects_empty_trace_fields() -> None:
    """强制 CoT 与锚点留痕:reasoning/anchor_ref 空白即拒。"""
    with pytest.raises(ValueError, match="reasoning"):
        rubric.check_score_payload(_scad_payload(reasoning="  "), phase="scad")
    with pytest.raises(ValueError, match="anchor_ref"):
        rubric.check_score_payload(_scad_payload(anchor_ref=""), phase="scad")


# ---------- critic 协议 / MockCritic / VLMCritic ----------


def test_critique_result_overall_and_payload() -> None:
    """CritiqueResult:overall_score = 维度均值;to_score_payload 过 check_score_payload。"""
    result = CritiqueResult(
        rubric_scores={"geometry": 9.0, "composition": 7.0},
        reasoning="CoT 推理:从等轴测/正面/顶视三个视角全面观察,几何正确性评估为无漂浮无穿插现象,符合物理空间约束;构图方面主体居中合理,与锚点标准对齐良好。",
        anchor_ref="anchor",
        actionable_feedback="Object A 缩放 0.8",
        critic_model="m",
        ab_swap_ref="best_ir.json",
    )
    assert result.overall_score == 8.0
    payload = result.to_score_payload(phase="scad")
    assert payload["customType"] == "score" and payload["ab_swap_ref"] == "best_ir.json"
    rubric.check_score_payload(payload, phase="scad")


def test_mock_critic_programmable_queue() -> None:
    """MockCritic:按队列返回,耗尽重复最后一条;dict/float 形态自动补合法留痕;满足 Critic 协议。"""
    critic = MockCritic([5.0, {"geometry": 9.0, "composition": 9.0}])
    assert isinstance(critic, Critic)
    r1 = critic.critique([], SCAD_DIMENSIONS, {"iteration": 1})
    assert r1.rubric_scores == {"geometry": 5.0, "composition": 5.0}  # float → 全部维度同分
    rubric.check_score_payload(r1.to_score_payload(phase="scad"), phase="scad")
    r2 = critic.critique([], SCAD_DIMENSIONS, {"iteration": 2})
    assert r2.rubric_scores == {"geometry": 9.0, "composition": 9.0}
    r3 = critic.critique([], SCAD_DIMENSIONS, {"iteration": 3})  # 队列耗尽 → 重复最后一条
    assert r3.rubric_scores == r2.rubric_scores
    assert len(critic.calls) == 3 and critic.calls[0]["context"]["iteration"] == 1
    with pytest.raises(ValueError):
        MockCritic([])


def test_vlm_critic_moved_to_critic_module() -> None:
    """真实 VLM critic 已实现(M0 阶段3b):位于 vision.critic,满足 Critic 协议;详见 test_vlm_critic.py。"""
    from openbimagent.vision.critic import VLMCritic

    assert hasattr(VLMCritic, "critique")
    assert not hasattr(rubric, "VLMCritic")  # rubric.py 只留协议与常量,单一事实源


# ---------- check_score_payload 防放水强化(Relay 015 任务 C2) ----------


def test_check_score_payload_missing_reasoning() -> None:
    """payload 缺少 reasoning 字段 → 抛 ValueError,消息含「reasoning 缺失」(防放水第 3 条)。"""
    payload = _scad_payload()
    del payload["reasoning"]
    with pytest.raises(ValueError, match="reasoning 缺失"):
        rubric.check_score_payload(payload, phase="scad")


def test_check_score_payload_short_reasoning() -> None:
    """reasoning 仅 10 字符(< REASONING_MIN_CHARS=20)→ 抛 ValueError,消息含「reasoning 过短」(防放水第 3 条)。"""
    payload = _scad_payload(reasoning="短CoT不足以说明。")
    assert len(payload["reasoning"]) == 10
    with pytest.raises(ValueError, match="reasoning 过短"):
        rubric.check_score_payload(payload, phase="scad")


def test_check_score_payload_missing_anchor_ref() -> None:
    """payload 缺少 anchor_ref 字段 → 抛 ValueError,消息含「anchor_ref 缺失」(防放水第 3 条,锚点对齐)。"""
    payload = _scad_payload()
    del payload["anchor_ref"]
    with pytest.raises(ValueError, match="anchor_ref 缺失"):
        rubric.check_score_payload(payload, phase="scad")


def test_check_score_payload_low_score_no_feedback() -> None:
    """overall=6.0(< 8.0)且 actionable_feedback 为空 → 抛 ValueError,消息含「actionable_feedback 缺失」(防放水第 2 条)。"""
    payload = _scad_payload(
        rubric_scores={"geometry": 6.0, "composition": 6.0},
        actionable_feedback="",
    )
    with pytest.raises(ValueError, match="actionable_feedback 缺失"):
        rubric.check_score_payload(payload, phase="scad")


def test_check_score_payload_feedback_no_quantified() -> None:
    """overall=7.0(< 8.0)且 actionable_feedback 无数字 → 抛 ValueError,消息含「缺少量化参数」(防放水第 2 条)。"""
    payload = _scad_payload(
        rubric_scores={"geometry": 7.0, "composition": 7.0},
        actionable_feedback="改进材质让整体更好看",
    )
    with pytest.raises(ValueError, match="缺少量化参数"):
        rubric.check_score_payload(payload, phase="scad")
