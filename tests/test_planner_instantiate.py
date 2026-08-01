"""planner.instantiate 测试(M0 阶段4a;COMPONENTS §2.3;ARCH §2 步骤 2-3、§4)。

覆盖:真实 playbook 加载 + plan.schema 校验、frontmatter 缺失/漂移报错、
确定性默认模板(无 registry,按 batches 每批一个占位资产)、LLM 路径(mock registry:
合法 / 非 JSON 重试后合法 / 连续非法抛 PlanInvalidError / C2 坐标哨兵 / 批次引用完整性 /
batches 缺省补全)、PLAN.md/TODO.md 派生内容。
全程禁网络:providers registry 以 _FakeRegistry 桩替换。
"""

import json
from pathlib import Path

import pytest

from openbimagent.planner.instantiate import (
    PlanInvalidError,
    instantiate,
    load_playbook,
    normalize_plan,
)
from openbimagent.schema_gate import gate as schema_gate

PACKS = Path(__file__).resolve().parents[1] / "domain_packs"
SINGLE = PACKS / "single_asset_hero" / "playbook.md"
EDO = PACKS / "edo_cyberpunk_district" / "playbook.md"
MUNICIPAL = PACKS / "municipal_utility" / "playbook.md"

VALID_IR_REPLY = json.dumps(
    {
        "version": "0.1",
        "assets": [
            {
                "id": "ground",
                "category": "road",
                "description": "沥青路面,雨后反光,靛蓝色调",
                "count": 1,
                "material_ref": "asphalt_wet",
                "tags": ["base"],
            },
            {
                "id": "vending_machine",
                "category": "prop",
                "description": "日式自动售货机,靛蓝机身带锈迹与霓虹贴纸",
                "count": 1,
                "material_ref": "painted_metal_worn",
                "tags": ["hero"],
            },
        ],
        "spatial_constraints": [
            {"type": "adjacency", "subject": "vending_machine", "relation": "贴墙布置在路缘", "object": "ground"}
        ],
        "batches": [["ground"], ["vending_machine"]],
    },
    ensure_ascii=False,
)


class _FakeRegistry:
    """providers registry 桩:按队列吐出 content 字符串/完整 result dict/异常;记录调用供断言。"""

    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, role, messages, **kwargs):
        self.calls.append({"role": role, "messages": messages, "kwargs": kwargs})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            reply.setdefault("model_resolved", "planner-model-test")
            return reply
        return {
            "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "model_resolved": "planner-model-test",
        }


# ---------- load_playbook:解析 + plan.schema 校验 ----------


def test_load_playbook_single_asset_hero() -> None:
    """真实单资产包:frontmatter 字段齐全,归一化 plan 过 schema,正文保留。"""
    pb = load_playbook(SINGLE)
    assert pb["name"] == "single_asset_hero"
    assert pb["targets"] == ["blender"]
    assert pb["batches"] == ["主体"]
    assert pb["deliverables"] == [".blend 工程", "英雄镜头渲染 x1"]
    assert pb["acceptance"]["scad_loop"] == {"min_score": 8.0, "max_iters": 6}
    assert len(pb["slot_defs"]) == 3
    assert "任务书" in pb["body"]
    assert schema_gate.validate_artifact("plan", pb["plan"]) == []  # 归一化 plan 必过门禁
    assert pb["plan"]["slots"]["asset"] == "一台日式自动售货机"  # slots 快照 = 默认值


def test_load_playbook_municipal_solver_metadata_enters_plan() -> None:
    """领域执行 metadata 必须进入正式 Plan，不能只停留在 raw Playbook。"""
    pb = load_playbook(MUNICIPAL)
    route = next(phase for phase in pb["plan"]["phases"] if phase["id"] == "route_planning")
    assert route["solver"] == "municipal-straight-gravity-solver"
    assert route["solver_version"] == "0.3.0"
    assert route["input_schema"] == "utility_solver_input.schema.json"
    assert route["rule_source"] == "knowledge/constraints.yaml"
    assert route["rule_set_schema"] == "municipal_rule_set.schema.json"
    assert route["output"] == "compiled_utility_ir.json"
    assert route["acceptance"] == [
        "diameter_in_spec",
        "slope_in_spec",
        "cover_depth_in_spec",
        "manhole_spacing_in_spec",
    ]
    assert schema_gate.validate_artifact("plan", pb["plan"]) == []


def test_load_playbook_edo_cyberpunk_district() -> None:
    """真实街区包:6 批次按声明序展开,blender_loop 阈值 8.5。"""
    pb = load_playbook(EDO)
    assert pb["name"] == "edo_cyberpunk_district"
    assert pb["batches"] == ["路面", "建筑xN", "路灯", "自动售货机", "电线", "招牌/道具"]
    assert pb["acceptance"]["blender_loop"]["min_score"] == 8.5
    assert len(pb["deliverables"]) == 3
    assert schema_gate.validate_artifact("plan", pb["plan"]) == []


def test_load_playbook_missing_frontmatter_raises(tmp_path) -> None:
    """缺 --- 包围段:ValueError(与 clarify.slots.load_playbook_slots 同规则)。"""
    bad = tmp_path / "playbook.md"
    bad.write_text("# 没有 frontmatter 的任务书\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        load_playbook(bad)


def test_load_playbook_schema_drift_raises(tmp_path) -> None:
    """frontmatter 缺 deliverables(plan.schema required):SchemaGateError,摘要点名缺失字段。"""
    bad = tmp_path / "playbook.md"
    bad.write_text(
        "---\n"
        "name: bad_pack\n"
        "targets: [blender]\n"
        "phases:\n"
        "  - id: research\n"
        "acceptance:\n"
        "  scad_loop: { min_score: 8.0, max_iters: 6 }\n"
        "---\n\n正文\n",
        encoding="utf-8",
    )
    with pytest.raises(schema_gate.SchemaGateError, match="deliverables"):
        load_playbook(bad)


def test_normalize_plan_slots_filled_merge_and_coerce() -> None:
    """slots 快照 = 默认值 ← slots_filled 覆盖;非标量值字符串化(plan.schema 只允许 str/num/bool)。"""
    pb = load_playbook(SINGLE)
    plan = normalize_plan(pb, {"wear_level": "9", "mood": ["雨夜", "霓虹"]})
    assert plan["slots"]["wear_level"] == "9"  # 覆盖默认值 "6"
    assert plan["slots"]["mood"] == '["雨夜", "霓虹"]'  # 列表 → JSON 字符串
    assert plan["slots"]["asset"] == "一台日式自动售货机"  # 未覆盖的保默认值
    assert schema_gate.validate_artifact("plan", plan) == []


# ---------- instantiate:确定性默认模板(无 registry) ----------


def test_instantiate_deterministic_single_asset(tmp_path) -> None:
    """无 registry:每批一个占位资产,IR 过 scene_graph_ir 门禁,PLAN/TODO 由 phases/batches 派生。"""
    pb = load_playbook(SINGLE)
    artifacts = instantiate(pb, {"wear_level": "6"}, tmp_path)

    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    assert schema_gate.validate_artifact("scene_graph_ir", ir) == []
    assert [a["id"] for a in ir["assets"]] == ["batch_01_主体"]
    assert ir["assets"][0]["category"] == "placeholder"
    assert "占位资产" in ir["assets"][0]["description"]
    assert ir["batches"] == [["batch_01_主体"]]  # 批次 = 一次渲染检查单位
    assert ir["spatial_constraints"] == []

    plan_md = artifacts.plan_md.read_text(encoding="utf-8")
    assert "# PLAN · single_asset_hero" in plan_md
    assert "确定性默认模板" in plan_md
    assert "wear_level: 6" in plan_md  # slots_filled 快照进 PLAN
    assert "英雄镜头渲染 x1" in plan_md  # 交付清单(C5)

    todo_md = artifacts.todo_md.read_text(encoding="utf-8")
    assert "- [ ] research(researcher) → references.md" in todo_md
    assert "- [ ] 批次 1/1:主体" in todo_md
    for step in ("scad_check", "blender_build", "render_check"):
        assert f"- [ ] {step}" in todo_md
    assert "- [ ] lighting_render(lighter)" in todo_md
    assert "- [ ] deliver" in todo_md


def test_instantiate_deterministic_edo_six_batches(tmp_path) -> None:
    """街区包:6 批 → 6 占位资产,id slug 化(「招牌/道具」→ 招牌_道具),同输入必同输出。"""
    pb = load_playbook(EDO)
    artifacts = instantiate(pb, {}, tmp_path)
    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    ids = [a["id"] for a in ir["assets"]]
    assert ids == [
        "batch_01_路面",
        "batch_02_建筑xN",
        "batch_03_路灯",
        "batch_04_自动售货机",
        "batch_05_电线",
        "batch_06_招牌_道具",
    ]
    assert ir["batches"] == [[i] for i in ids]  # 每批一批资产
    assert schema_gate.validate_artifact("scene_graph_ir", ir) == []
    # 确定性:重跑一次字节级一致
    again = instantiate(pb, {}, tmp_path / "again")
    assert again.scene_graph_ir.read_bytes() == artifacts.scene_graph_ir.read_bytes()


# ---------- instantiate:LLM 路径(mock registry) ----------


def test_instantiate_llm_valid(tmp_path) -> None:
    """LLM 合法输出:IR 原样采用(非占位模板),role=planner 注入,PLAN 记录生成模型。"""
    registry = _FakeRegistry([VALID_IR_REPLY])
    pb = load_playbook(SINGLE)
    artifacts = instantiate(pb, {"asset": "一台日式自动售货机"}, tmp_path, registry=registry)

    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    assert [a["id"] for a in ir["assets"]] == ["ground", "vending_machine"]
    assert ir["batches"] == [["ground"], ["vending_machine"]]
    assert schema_gate.validate_artifact("scene_graph_ir", ir) == []

    assert len(registry.calls) == 1  # 一次通过,无重试
    call = registry.calls[0]
    assert call["role"] == "planner"
    assert call["messages"][0]["role"] == "system"
    assert "C2" in call["messages"][0]["content"] or "坐标" in call["messages"][1]["content"]  # C2 写入 prompt
    assert "一台日式自动售货机" in call["messages"][1]["content"]  # 槽位进 prompt

    plan_md = artifacts.plan_md.read_text(encoding="utf-8")
    assert "LLM(role=planner, model=planner-model-test)" in plan_md


def test_instantiate_llm_retry_then_valid(tmp_path) -> None:
    """首次非 JSON → 重试 1 次(附错误说明)→ 合法即放行;重试消息带校验错误与输出契约。"""
    registry = _FakeRegistry(["这肯定不是 JSON", VALID_IR_REPLY])
    pb = load_playbook(SINGLE)
    artifacts = instantiate(pb, {}, tmp_path, registry=registry)

    assert len(registry.calls) == 2
    retry_messages = registry.calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": "这肯定不是 JSON"}  # 上轮输出回填
    assert "上一次输出未通过校验" in retry_messages[-1]["content"]
    assert "C2" in retry_messages[-1]["content"]
    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    assert [a["id"] for a in ir["assets"]] == ["ground", "vending_machine"]


def test_instantiate_llm_invalid_twice_raises(tmp_path) -> None:
    """连续两次非 JSON:PlanInvalidError(重试仅 1 次,不死循环)。"""
    registry = _FakeRegistry(["garbage", "still not json"])
    pb = load_playbook(SINGLE)
    with pytest.raises(PlanInvalidError, match="连续 2 次输出非法"):
        instantiate(pb, {}, tmp_path, registry=registry)
    assert len(registry.calls) == 2
    assert not (tmp_path / "scene_graph_ir.json").exists()  # 失败不落盘


def test_instantiate_llm_schema_drift_twice_raises(tmp_path) -> None:
    """连续两次不过 scene_graph_ir schema(assets 空 + 多余字段):PlanInvalidError。"""
    drift = json.dumps({"version": "0.1", "assets": [], "spatial_constraints": [], "bogus": 1})
    registry = _FakeRegistry([drift, drift])
    pb = load_playbook(SINGLE)
    with pytest.raises(PlanInvalidError):
        instantiate(pb, {}, tmp_path, registry=registry)
    assert len(registry.calls) == 2


def test_instantiate_llm_c2_coordinate_violation_raises(tmp_path) -> None:
    """C2 哨兵:description 夹带 (x, y, z) 绝对坐标,重试后仍犯 → PlanInvalidError 点名 C2。"""
    c2_bad = json.dumps(
        {
            "version": "0.1",
            "assets": [
                {"id": "box", "category": "prop", "description": "放置在 (1.0, 2.0, 3.0) 的箱子"}
            ],
            "spatial_constraints": [],
            "batches": [["box"]],
        },
        ensure_ascii=False,
    )
    registry = _FakeRegistry([c2_bad, c2_bad])
    pb = load_playbook(SINGLE)
    with pytest.raises(PlanInvalidError, match="C2"):
        instantiate(pb, {}, tmp_path, registry=registry)
    assert len(registry.calls) == 2
    retry_note = registry.calls[1]["messages"][-1]["content"]
    assert "C2 违反" in retry_note  # 重试说明携带具体漂移点(供 FIX)


def test_instantiate_llm_batch_ref_unknown_raises(tmp_path) -> None:
    """批次引用完整性:batches 引用未声明资产 id(schema 管不到),重试后仍犯 → PlanInvalidError。"""
    ghost = json.dumps(
        {
            "version": "0.1",
            "assets": [{"id": "real", "category": "prop", "description": "真实资产"}],
            "spatial_constraints": [],
            "batches": [["real", "ghost"]],
        },
        ensure_ascii=False,
    )
    registry = _FakeRegistry([ghost, ghost])
    pb = load_playbook(SINGLE)
    with pytest.raises(PlanInvalidError, match="未声明的资产 id"):
        instantiate(pb, {}, tmp_path, registry=registry)


def test_instantiate_llm_infra_failure_falls_back_to_template(tmp_path) -> None:
    """LLM infra 失败(ProviderError/503/熔断/缺 key)降级到确定性模板,槽位注入资产描述。

    与 assembly.builder._safe_build 同策略:不让 planner 漂移阻塞整条流水线;modeler/critic
    凭注入的语义建真实资产。PlanInvalidError(LLM 已应答但输出非法)不降级,见其他用例。
    """
    from openbimagent.providers.registry import ProviderError

    registry = _FakeRegistry([ProviderError("glm-5.2-ar: 503 Service Unavailable")])
    pb = load_playbook(SINGLE)
    slots = {"asset": "一台日式自动售货机", "style": "江户x赛博", "wear_level": 6}
    artifacts = instantiate(pb, slots, tmp_path, registry=registry)
    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    assert len(ir["assets"]) == 1  # 降级到模板:每批一个占位资产
    desc = ir["assets"][0]["description"]
    assert "一台日式自动售货机" in desc  # 槽位语义注入(modeler/critic 只从 IR 取语义)
    assert "江户x赛博" in desc
    assert "wear_level=6" in desc
    assert "LLM 不可用" in desc
    assert schema_gate.validate_artifact("scene_graph_ir", ir) == []  # 仍过 schema 门禁
    assert "LLM 失败回退" in artifacts.plan_md.read_text(encoding="utf-8")  # PLAN.md 记降级来源


def test_instantiate_llm_batches_default_filled(tmp_path) -> None:
    """LLM 省略 batches:按资产声明序补全(每资产一批),仍可过门禁落盘。"""
    no_batches = json.dumps(
        {
            "version": "0.1",
            "assets": [
                {"id": "a", "category": "building", "description": "町屋,木构架瓦屋顶"},
                {"id": "b", "category": "prop", "description": "霓虹招牌,锈红边框"},
            ],
            "spatial_constraints": [
                {"type": "orientation", "subject": "b", "relation": "面向主街悬挂", "object": "a"}
            ],
        },
        ensure_ascii=False,
    )
    registry = _FakeRegistry([no_batches])
    pb = load_playbook(SINGLE)
    artifacts = instantiate(pb, {}, tmp_path, registry=registry)
    ir = json.loads(artifacts.scene_graph_ir.read_text(encoding="utf-8"))
    assert ir["batches"] == [["a"], ["b"]]
    assert schema_gate.validate_artifact("scene_graph_ir", ir) == []
