"""审批票执行预览测试：typed plan 逐操作清单 / free-code 批次资产声明。"""

from __future__ import annotations

from types import SimpleNamespace

from openbimagent.assembly.approval_preview import summarize_batch_assets, summarize_operations


def _op(i: int, kind: str = "create_object", **extra):
    return SimpleNamespace(
        operation=kind,
        operation_id=f"op-{i:03d}",
        object_id=f"pipe-{i:03d}",
        object_type="pipe_segment",
        name=f"污水干管 {i}",
        **extra,
    )


def test_summarize_operations_counts_and_items() -> None:
    ops = [_op(i) for i in range(3)] + [_op(3, kind="set_properties")]
    summary = summarize_operations(ops)
    assert summary["total"] == 4
    assert summary["by_kind"] == {"create_object": 3, "set_properties": 1}
    assert summary["truncated"] is False
    assert summary["items"][0] == {
        "op": "create_object", "object": "pipe-000", "type": "pipe_segment", "name": "污水干管 0",
    }


def test_summarize_operations_truncates_beyond_limit() -> None:
    ops = [_op(i) for i in range(30)]
    summary = summarize_operations(ops, limit=20)
    assert summary["total"] == 30
    assert summary["truncated"] is True
    assert len(summary["items"]) == 20
    assert summary["by_kind"]["create_object"] == 30  # 截断不丢计数


def test_summarize_batch_assets_filters_and_truncates_description() -> None:
    ir = {
        "assets": [
            {"id": "pipe_a", "category": "ifc_pipe_segment", "count": 2, "description": "x" * 200},
            {"id": "mh_b", "category": "ifc_fitting", "count": 1, "description": "检查井"},
            {"id": "other", "category": "x", "description": "不在本批"},
        ]
    }
    declarations = summarize_batch_assets(ir, ["pipe_a", "mh_b"])
    assert [d["id"] for d in declarations] == ["pipe_a", "mh_b"]
    assert declarations[0]["description"].endswith("…")
    assert len(declarations[0]["description"]) == 81
    assert declarations[1]["count"] == 1


def test_summarize_batch_assets_empty_for_unknown_batch() -> None:
    ir = {"assets": [{"id": "pipe_a", "category": "c"}]}
    assert summarize_batch_assets(ir, ["ghost"]) == []
