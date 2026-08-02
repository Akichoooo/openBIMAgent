"""schema_gate 单测:合法与非法工件各一例(plan.schema.json;ARCH §0 原则 8、§2 步骤 3)。"""

import pytest

from openbimagent.schema_gate import gate

VALID_PLAN = {
    "playbook": "single_asset_hero",
    "targets": ["blender"],
    "slots": {"style": "江户"},
    "phases": [{"id": "p1", "agent": "modeler", "status": "pending"}],
    "acceptance": {"scad_loop": {"min_score": 8, "max_iters": 5}},
    "deliverables": ["asset.blend"],
}

INVALID_PLAN = {
    "playbook": "single_asset_hero",
    "phases": [{"id": "p1", "status": "weird"}],  # status 不在枚举
    "bogus_key": 1,  # additionalProperties: false
    # 缺 deliverables(required)
}


def test_loads_all_schemas() -> None:
    """schemas/ 目录全部 JSON Schema 均加载，关键版本化协议必须存在。"""
    names = gate.SchemaGate().schema_names()
    assert len(names) == 26
    assert "plan.schema.json" in names
    assert "blender_execution_plan.schema.json" in names
    assert "scad_scene_ir.schema.json" in names  # 阶段3b 新增:SCAD 环编译 IR
    assert "compiled_utility_ir.schema.json" in names
    assert "utility_solver_input.schema.json" in names
    assert "municipal_rule_set.schema.json" in names
    assert "vectorworks_execution_plan.schema.json" in names
    assert "semantic_snapshot.schema.json" in names
    assert "semantic_comparison_report.schema.json" in names
    assert "ifc_ids_validation_report.schema.json" in names
    assert "subagent_request.schema.json" in names
    assert "subagent_result.schema.json" in names
    assert "artifact_manifest.schema.json" in names
    assert "actor_ref.schema.json" in names
    assert "approval_request.schema.json" in names
    assert "decision_receipt.schema.json" in names
    assert "resume_request.schema.json" in names
    assert "resume_receipt.schema.json" in names
    assert "steer_directive.schema.json" in names
    assert "steer_receipt.schema.json" in names
    assert "ipc_request.schema.json" in names
    assert "ipc_response.schema.json" in names
    assert "ipc_discovery.schema.json" in names


def test_valid_plan_passes() -> None:
    """合法工件:错误列表为空,gate_or_fix 不抛。"""
    assert gate.validate_artifact("plan", VALID_PLAN) == []
    gate.gate_or_fix("plan.schema.json", VALID_PLAN)  # 不抛即通过


def test_plan_accepts_domain_gate_requirements() -> None:
    plan = {
        **VALID_PLAN,
        "targets": ["blender", "vectorworks"],
        "acceptance": {
            **VALID_PLAN["acceptance"],
            "domain_gate": {"clash_free": True, "slope_in_spec": True},
        },
    }
    assert gate.validate_artifact("plan", plan) == []


def test_invalid_plan_reports_field_level_errors() -> None:
    """非法工件:错误列表带字段级路径;gate_or_fix 抛 SchemaGateError(摘要供 FIX 返工)。"""
    errors = gate.validate_artifact("plan", INVALID_PLAN)
    assert len(errors) >= 3
    joined = "\n".join(errors)
    assert "deliverables" in joined  # required 缺失
    assert "$.phases[0].status" in joined  # 枚举越界,路径精确到字段
    assert "bogus_key" in joined  # 多余字段

    with pytest.raises(gate.SchemaGateError) as exc_info:
        gate.gate_or_fix("plan", INVALID_PLAN)
    assert len(exc_info.value.errors) == len(errors)
    assert "$.phases[0].status" in str(exc_info.value)


def test_unknown_schema_raises() -> None:
    with pytest.raises(KeyError):
        gate.validate_artifact("不存在的schema", {})


# ---------- scad_scene_ir.schema.json(编译 IR,DECISIONS_DRAFT 附录 B 第 8 条) ----------

VALID_SCAD_IR = {
    "version": "0.1",
    "assets": [
        {"id": "base", "primitive": "cube", "size": [4, 2, 0.5], "position": [0, 0, 0.25], "color": "lightgray"},
        {"id": "pole", "primitive": "cylinder", "size": [0.3, 3], "position": [1, 0.5, 2]},
        {"id": "ball", "primitive": "sphere", "size": 0.6, "position": [-1, -0.5, 3]},
        {"id": "roof", "primitive": "cone", "size": [1.2, 0.0, 1.0], "position": [1, 0.5, 4]},
    ],
}

INVALID_SCAD_IR = {
    "assets": [
        {"id": "base", "primitive": "cube", "size": [4, 2], "position": [0, 0, 0.25]},  # cube size 须 3 元素
        {"id": "", "primitive": "torus", "size": 1, "position": [0, 0]},  # id 空 / primitive 越枚举 / position 须 3 元素
    ],
    "bogus_key": 1,  # additionalProperties: false
}


def test_valid_scad_scene_ir_passes() -> None:
    """编译 IR 合法一例:四图元(size 形态各异:cube[3]/cylinder[2]/sphere 标量/cone[3])+ color + version。"""
    assert gate.validate_artifact("scad_scene_ir", VALID_SCAD_IR) == []
    gate.gate_or_fix("scad_scene_ir.schema.json", VALID_SCAD_IR)  # 不抛即通过


def test_invalid_scad_scene_ir_reports_field_level_errors() -> None:
    """编译 IR 非法一例:size 元素数不符、id 空、primitive 越枚举、position 维数错、多余字段。"""
    errors = gate.validate_artifact("scad_scene_ir", INVALID_SCAD_IR)
    assert len(errors) >= 4
    joined = "\n".join(errors)
    assert "$.assets[0].size" in joined  # cube size 须 3 元素
    assert "$.assets[1].id" in joined  # id 非空
    assert "$.assets[1].primitive" in joined  # torus 越枚举
    assert "$.assets[1].position" in joined  # position 须 3 元素
    assert "bogus_key" in joined
    with pytest.raises(gate.SchemaGateError):
        gate.gate_or_fix("scad_scene_ir", INVALID_SCAD_IR)
