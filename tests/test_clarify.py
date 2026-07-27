"""clarify 单测:规则抽取、completion_score 评分、回车接受默认、≥85 放行(COMPONENTS §2.2)。"""

from pathlib import Path

import pytest
import yaml

from openbimagent.clarify import slots as clarify

PACKS = Path(__file__).resolve().parents[1] / "domain_packs"
SINGLE = PACKS / "single_asset_hero" / "playbook.md"

FRONTMATTER = {
    "slots": [
        {"id": "style", "question": "场景风格?", "default": "江户", "aliases": ["风格"]},
        {"id": "scale", "question": "街区规模?", "aliases": ["规模"]},
        {"id": "target", "question": "目标宿主?", "default": "blender", "aliases": ["宿主", "target"]},
    ]
}


def test_load_slots_validation() -> None:
    """id/question 必填;aliases 支持字符串或列表。"""
    loaded = clarify.load_slots(FRONTMATTER)
    assert [s.id for s in loaded] == ["style", "scale", "target"]
    assert loaded[0].default == "江户" and loaded[0].aliases == ["风格"]
    assert loaded[1].default is None
    with pytest.raises(ValueError):
        clarify.load_slots({"slots": [{"id": "x"}]})  # 缺 question


def test_extract_slots_by_alias() -> None:
    """规则抽取:zh 别名 + 冒号写法回填;默认值关键词出现也算命中。"""
    state = clarify.SlotState(slots=clarify.load_slots(FRONTMATTER))
    clarify.extract_slots("风格:江户赛博,规模:5 栋楼,宿主就用 blender", state.slots)
    by_id = {s.id: s for s in state.slots}
    assert by_id["style"].value == "江户赛博"
    assert by_id["scale"].value == "5 栋楼"
    assert by_id["target"].value == "blender"  # 默认值关键词命中
    assert state.completion_score == 100.0


def test_completion_score_counts_default() -> None:
    """已填(含默认)/总数 × 100:3 槽位 2 个带默认 = 66.7 分。"""
    state = clarify.SlotState(slots=clarify.load_slots(FRONTMATTER))
    assert state.completion_score == pytest.approx(2 / 3 * 100)
    assert not clarify.may_proceed(state)
    assert clarify.SlotState().completion_score == 100.0  # 空槽位定义视为齐备


def test_run_clarify_enter_accepts_default() -> None:
    """一问一答:回车 = 接受默认;提问带 [默认值];input_func 可注入。"""
    state = clarify.SlotState(slots=clarify.load_slots(FRONTMATTER))
    answers = iter(["", "7 栋楼", ""])  # style 回车、scale 显式、target 回车
    prompts: list[str] = []
    clarify.run_clarify(state, input_func=lambda p: (prompts.append(p), next(answers))[1])
    by_id = {s.id: s for s in state.slots}
    assert by_id["style"].value == "江户"  # 回车接受默认
    assert by_id["scale"].value == "7 栋楼"
    assert by_id["target"].value == "blender"
    assert "[江户]" in prompts[0] and "[blender]" in prompts[2]
    assert clarify.may_proceed(state)


def test_run_clarify_empty_answer_without_default_stays_unfilled() -> None:
    """无默认值的槽位回车 → 保持未填,不重复追问(每槽位只问一次)。"""
    state = clarify.SlotState(slots=clarify.load_slots(FRONTMATTER))
    clarify.run_clarify(state, input_func=lambda p: "")
    by_id = {s.id: s for s in state.slots}
    assert by_id["scale"].value is None
    assert len(state.asked) == 3


def test_pass_threshold_85() -> None:
    """≥85 放行:8 槽位填 7 = 87.5 放行;填 6 = 75 不放行。"""
    make = lambda n_filled: clarify.SlotState(  # noqa: E731
        slots=[clarify.Slot(id=f"s{i}", question="?", value="v" if i < n_filled else None) for i in range(8)]
    )
    assert clarify.may_proceed(make(7))
    assert not clarify.may_proceed(make(6))


# ---------- PyYAML flow-? 怪窍修补回退(与 planner._load_frontmatter 同规则) ----------


def test_load_playbook_slots_real_single_asset_hero_qmark_fix() -> None:
    """回归:真实 single_asset_hero/playbook.md 的 `question: 做什么资产?,` 会触发 PyYAML
    flow-`?` ParserError;clarify.slots._load_frontmatter 的引号修补回退须把 `?` 包成双引号
    (值逐字保留)再解析,加载出 3 个槽位且 question 含 `?` 字符。"""
    slots = clarify.load_playbook_slots(SINGLE)
    assert [s.id for s in slots] == ["asset", "style", "wear_level"]
    by_id = {s.id: s for s in slots}
    # 值逐字保留:`?` 不能丢、不能转义
    assert by_id["asset"].question == "做什么资产?"
    assert by_id["style"].question == "风格锚点?"
    assert by_id["wear_level"].question == "磨损程度(0-10)?"
    # default 仍按原映射取值
    assert by_id["asset"].default == "一台日式自动售货机"
    assert by_id["wear_level"].default == "6"


def test_load_playbook_slots_qmark_patch_only_triggers_on_parser_error(tmp_path) -> None:
    """直解成功时不触发修补回退(常规模板能正常解析;只有 flow-`?` 才触发 ParserError)。"""
    playbook = tmp_path / "playbook.md"
    playbook.write_text(
        "---\n"
        "name: qmark_free\n"
        "slots:\n"
        "  - { id: a, question: 普通问题, default: v1 }\n"
        "  - { id: b, question: another, default: v2 }\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )
    slots = clarify.load_playbook_slots(playbook)
    assert [s.id for s in slots] == ["a", "b"]
    assert slots[0].question == "普通问题"  # 无 `?` 不会被修补影响


def test_load_playbook_slots_qmark_patch_preserves_value_verbatim(tmp_path) -> None:
    """多个 `?` 结尾的槽位都被修补;值逐字保留(包括 `?`、中文、标点)。"""
    playbook = tmp_path / "playbook.md"
    playbook.write_text(
        "---\n"
        "name: multi_qmark\n"
        "slots:\n"
        "  - { id: q1, question: 用什么材质?, default: 金属 }\n"
        "  - { id: q2, question: 几级磨损?, default: \"5\" }\n"
        "  - { id: q3, question: 风格是啥?, default: 江户赛博 }\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )
    slots = clarify.load_playbook_slots(playbook)
    assert [s.question for s in slots] == ["用什么材质?", "几级磨损?", "风格是啥?"]
    assert [s.default for s in slots] == ["金属", "5", "江户赛博"]


def test_load_playbook_slots_unparseable_even_after_patch_raises(tmp_path) -> None:
    """修补后仍非法的 YAML(结构性错误,非 flow-`?`)→ ValueError 点名 frontmatter 解析失败。"""
    playbook = tmp_path / "playbook.md"
    playbook.write_text(
        "---\n"
        "name: broken\n"
        "slots: [a, b, "  # 残缺 flow sequence,非 `?` 怪窍,修补救不了;闭合行在下一行
        "\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frontmatter YAML 解析失败"):
        clarify.load_playbook_slots(playbook)


def test_load_playbook_slots_missing_frontmatter_raises(tmp_path) -> None:
    """缺 --- 包围段:ValueError(与 planner.load_playbook 同规则)。"""
    bad = tmp_path / "playbook.md"
    bad.write_text("# 没有 frontmatter 的任务书\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        clarify.load_playbook_slots(bad)


def test_load_playbook_slots_consistent_with_planner_loader() -> None:
    """clarify._load_frontmatter 与 planner.instantiate._load_frontmatter 单一事实源:
    对同一 playbook frontmatter 解析结果一致(同 key 同 value,`?` 字符逐字保留)。"""
    from openbimagent.planner.instantiate import _load_frontmatter as planner_load

    text = SINGLE.read_text(encoding="utf-8")
    import re

    match = re.match(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", text, re.DOTALL)
    assert match is not None
    raw = match.group(1)
    via_clarify = clarify._load_frontmatter(raw, SINGLE)
    via_planner = planner_load(raw, SINGLE)
    # slots 数组逐项一致(id/question/default/aliases)
    assert via_clarify["slots"] == via_planner["slots"]
    # 直接验证 PyYAML 直解会炸(证明补丁确实在起作用,不是 YAML 本身没 bug)
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(raw)
