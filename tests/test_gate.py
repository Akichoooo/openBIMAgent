"""VW MCP 三重门禁 (handoff/hash/approval) 单测:gate.py 4 个函数全覆盖。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 gate.py
(mcp_servers 不在 pythonpath)。

覆盖(6 个测试):
- generate_handoff_summary: execute_code + vs.CreateWall 返回含 "创建墙体"
- compute_params_hash: 返回 16 字符 sha256 前缀
- requires_approval 高风险: ExportIFC 返回 True
- requires_approval 低风险: CreateWall 返回 False
- check_gate 已审批: approval_fn=True → ok=True
- check_gate 被拒绝: approval_fn=False → ok=False
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

# ---------- 按路径加载 gate.py ----------

_GATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "server" / "gate.py"
)


def _load_gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_gate_test", _GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 6 个测试 ----------


def test_generate_handoff_summary() -> None:
    """handoff 摘要:execute_code + vs.CreateWall(...) 返回含 '创建墙体'。"""
    gate = _load_gate_module()
    summary = gate.generate_handoff_summary(
        "execute_code", {"code": "vs.CreateWall(0, 0, 10, 5)"}
    )
    assert "创建墙体" in summary
    # 摘要应包含 code 片段
    assert "CreateWall" in summary or "0, 0, 10, 5" in summary


def test_compute_params_hash() -> None:
    """params hash:返回 16 字符 sha256 前缀,同参数稳定。"""
    gate = _load_gate_module()
    params = {"code": "vs.Rect(0, 0, 10, 10)"}
    h1 = gate.compute_params_hash(params)
    h2 = gate.compute_params_hash(params)
    assert len(h1) == 16
    assert h1 == h2  # 稳定性
    # 不同参数不同 hash
    h3 = gate.compute_params_hash({"code": "vs.Rect(0, 0, 20, 20)"})
    assert h1 != h3


def test_requires_approval_high_risk() -> None:
    """高风险:ExportIFC 操作 requires_approval 返回 True。"""
    gate = _load_gate_module()
    assert gate.requires_approval(
        "execute_code", {"code": "vs.IFC_ExportWithUI('/tmp/x.ifc')"}
    ) is True
    # Delete 类也是高风险
    assert gate.requires_approval(
        "execute_code", {"code": "vs.DelObject(handle)"}
    ) is True


def test_requires_approval_low_risk() -> None:
    """低风险:CreateWall 操作 requires_approval 返回 False。"""
    gate = _load_gate_module()
    assert gate.requires_approval(
        "execute_code", {"code": "vs.CreateWall(0, 0, 10, 5)"}
    ) is False
    # 非 execute_code 命令永远不审批
    assert gate.requires_approval("ping", {}) is False
    assert gate.requires_approval("describe_capabilities", {}) is False


def test_check_gate_approved() -> None:
    """check_gate 已审批:approval_fn 返回 True → ok=True,approved=True。"""
    gate = _load_gate_module()
    # 高风险操作 + approval_fn 返回 True
    result = gate.check_gate(
        "execute_code",
        {"code": "vs.IFC_ExportWithUI('/tmp/x.ifc')"},
        approval_fn=lambda summary, h: True,
    )
    assert result["ok"] is True
    assert result["approved"] is True
    assert result["requires_approval"] is True
    assert "handoff" in result
    assert "params_hash" in result
    assert len(result["params_hash"]) == 16

    # 低风险操作无需审批,ok=True approved=False
    result2 = gate.check_gate(
        "execute_code",
        {"code": "vs.CreateWall(0, 0, 10, 5)"},
        approval_fn=lambda summary, h: True,
    )
    assert result2["ok"] is True
    assert result2["approved"] is False
    assert result2["requires_approval"] is False


def test_typed_execute_plan_always_requires_approval_and_has_stable_identity() -> None:
    """typed plan 创建对象并保存工程，必须审批；控制字段不得改变语义 hash。"""
    gate = _load_gate_module()
    plan = {
        "plan_id": "vw-plan-abc",
        "canonical_sha256": "a" * 64,
        "idempotency_key": f"vw-plan:{'a' * 64}",
        "operations": [{"operation_id": "create:node-1"}],
    }
    params = {"plan": plan, "output_path": r"D:\devloop\G6_Test\case.vwx"}

    assert gate.requires_approval("execute_plan", params) is True
    summary = gate.generate_handoff_summary("execute_plan", params)
    assert "vw-plan-abc" in summary
    assert "1" in summary
    assert "case.vwx" in summary
    assert len(summary) <= gate.SUMMARY_MAX_LEN

    approved = {**params, "_approved": True}
    assert gate.compute_params_hash(params) == gate.compute_params_hash(approved)
    result = gate.check_gate("execute_plan", approved)
    assert result["ok"] is True
    assert result["approved"] is True


def test_check_gate_rejected() -> None:
    """check_gate 被拒绝:approval_fn 返回 False → ok=False,reason 非空。"""
    gate = _load_gate_module()
    result = gate.check_gate(
        "execute_code",
        {"code": "vs.IFC_ExportWithUI('/tmp/x.ifc')"},
        approval_fn=lambda summary, h: False,
    )
    assert result["ok"] is False
    assert result["approved"] is False
    assert result["requires_approval"] is True
    assert result["reason"] is not None
    assert "未审批" in result["reason"]

    # 显式 _approved=True 放行 (无 approval_fn)
    result2 = gate.check_gate(
        "execute_code",
        {"code": "vs.IFC_ExportWithUI('/tmp/x.ifc')", "_approved": True},
        approval_fn=None,
    )
    assert result2["ok"] is True
    assert result2["approved"] is True

    # 高风险但无 approval_fn 且无 _approved → 阻断
    result3 = gate.check_gate(
        "execute_code",
        {"code": "vs.IFC_ExportWithUI('/tmp/x.ifc')"},
        approval_fn=None,
    )
    assert result3["ok"] is False
    assert result3["reason"] is not None
