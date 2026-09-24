"""Scene Graph IR 空间约束回验 linter 测试。"""

from __future__ import annotations

import pytest

from openbimagent.planner.constraint_lint import constraint_lint_violations
from openbimagent.planner.instantiate import _validate_scene_ir


def _ir(constraints: list[dict], assets: list[dict] | None = None) -> dict:
    return {
        "version": "0.1",
        "assets": assets if assets is not None else [
            {"id": "pipe_a", "category": "ifc_pipe_segment", "description": "干管"},
            {"id": "mh_b", "category": "ifc_fitting", "description": "检查井"},
        ],
        "spatial_constraints": constraints,
        "batches": [["pipe_a"], ["mh_b"]],
    }


def test_valid_constraints_pass() -> None:
    ir = _ir([
        {"type": "adjacency", "subject": "pipe_a", "relation": "紧邻", "object": "mh_b"},
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "mh_b", "value": 1.5},
        {"type": "orientation", "subject": "pipe_a", "relation": "沿道路走向"},
    ])
    assert constraint_lint_violations(ir) == []


def test_unknown_subject_and_object() -> None:
    ir = _ir([
        {"type": "adjacency", "subject": "ghost_x", "relation": "紧邻", "object": "mh_b"},
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "ghost_y", "value": 2.0},
    ])
    errors = constraint_lint_violations(ir)
    assert any("ghost_x" in e for e in errors)
    assert any("ghost_y" in e for e in errors)


def test_binary_relation_requires_object() -> None:
    ir = _ir([
        {"type": "containment", "subject": "pipe_a", "relation": "包含于"},
        {"type": "adjacency", "subject": "pipe_a", "relation": "紧邻"},
    ])
    errors = constraint_lint_violations(ir)
    assert len([e for e in errors if "缺 object" in e]) == 2


def test_spacing_value_must_be_positive_number() -> None:
    assets = [{"id": f"pipe_{i}", "category": "ifc_pipe_segment", "description": "干管"} for i in range(4)]
    ir = _ir(
        [
            {"type": "spacing", "subject": "pipe_0", "relation": "净距", "object": "mh_b", "value": 0},
            {"type": "spacing", "subject": "pipe_1", "relation": "净距", "object": "mh_b", "value": -3},
            {"type": "spacing", "subject": "pipe_2", "relation": "净距", "object": "mh_b", "value": "2m"},
            {"type": "spacing", "subject": "pipe_3", "relation": "净距", "object": "mh_b", "value": 5_000_000},
        ],
        assets=assets,
    )
    errors = constraint_lint_violations(ir)
    assert len([e for e in errors if "value" in e or "数值" in e]) == 4


def test_conflicting_duplicate_constraint() -> None:
    ir = _ir([
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "mh_b", "value": 1.5},
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "mh_b", "value": 3.0},
    ])
    assert any("数值矛盾" in e for e in constraint_lint_violations(ir))


def test_identical_duplicate_allowed() -> None:
    ir = _ir([
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "mh_b", "value": 1.5},
        {"type": "spacing", "subject": "pipe_a", "relation": "净距", "object": "mh_b", "value": 1.5},
    ])
    assert constraint_lint_violations(ir) == []


def test_lint_wired_into_validate_scene_ir() -> None:
    ir = _ir([{"type": "adjacency", "subject": "ghost_x", "relation": "紧邻", "object": "mh_b"}])
    with pytest.raises(ValueError, match="ghost_x"):
        _validate_scene_ir(ir)
