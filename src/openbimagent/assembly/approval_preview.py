"""审批票执行预览（对标 Codex 审批时看 diff 的体验）。

此前审批票 params 只有 batch/plan_id/hash——审批人看到的是"某批次要执行"却看不到
"将要创建/修改什么对象"。本模块把门禁现场已有的信息编成人类可读预览：

- typed plan 门（blender/vectorworks）：plan 的逐操作清单（kind/object/type/name,
  超限截断 + 按类型计数）——typed plan 的操作即 diff 的右侧（全是确定性新增,无删除）。
- execute_code 门（free-code 路径）：代码在门后才生成,预览诚实降级为"批次资产
  声明 + 可编辑范围"（将要操作的对象清单）,不假装能给出代码 diff。

预览进 ticket params,经 GET /api/v1/approvals 自动到前端审批卡,无需新端点。
"""

from __future__ import annotations

from typing import Any

_PREVIEW_LIMIT = 20
_DESC_LIMIT = 80


def summarize_operations(operations: Any, *, limit: int = _PREVIEW_LIMIT) -> dict[str, Any]:
    """typed plan 操作清单 → 审批预览（截断 + 按类型计数）。"""
    ops = list(operations)
    by_kind: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for op in ops[:limit]:
        kind = str(getattr(op, "operation", ""))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        items.append({
            "op": kind,
            "object": str(getattr(op, "object_id", "")),
            "type": str(getattr(op, "object_type", "")),
            "name": getattr(op, "name", None) or getattr(op, "object_name", None),
        })
    for op in ops[limit:]:
        kind = str(getattr(op, "operation", ""))
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "total": len(ops),
        "by_kind": by_kind,
        "items": items,
        "truncated": len(ops) > limit,
    }


def summarize_batch_assets(ir: dict[str, Any], batch_assets: list[str]) -> list[dict[str, Any]]:
    """free-code 门的目标对象清单:批次资产的 id/category/count/描述（截断）。"""
    wanted = {str(a) for a in batch_assets}
    declarations: list[dict[str, Any]] = []
    for asset in ir.get("assets") or []:
        if not isinstance(asset, dict) or str(asset.get("id")) not in wanted:
            continue
        desc = str(asset.get("description") or "")
        declarations.append({
            "id": str(asset.get("id") or ""),
            "category": str(asset.get("category") or ""),
            "count": asset.get("count", 1),
            "description": desc[:_DESC_LIMIT] + ("…" if len(desc) > _DESC_LIMIT else ""),
        })
    return declarations


__all__ = ["summarize_batch_assets", "summarize_operations"]
