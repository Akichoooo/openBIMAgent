"""Scene Graph IR 空间约束回验（确定性 linter）。

此前 spatial_constraints 只过 schema 形状 + C2 坐标哨兵：LLM 可以声明指向不存在资产的
约束、给 spacing 0/负值、对同一对资产给出互相矛盾的数值——没有任何层回验（schema 管
不到跨字段约束，同 _batch_ref_errors 的道理）。

本模块逐条断言：
- 引用完整性：subject/object 必须指向已声明资产（object 可省 = 绝对约束）；
- 类型结构：containment/adjacency 天然是二元关系，必须带 object；
- 数值合理性：spacing 的 value 必须是正的有限数（0/负/非数都是漂移）；
- 一致性：同一 (type, subject, object) 重复声明且 value 互相矛盾即冲突。

不过 → ValueError 进 planner 重试环（instantiate._validate_scene_ir 总闸），
漂移即 FIX。几何数值级回验（位置/间距 vs 编译产物）需要 scene-IR 资产 id 与
compiled IR id 的桥接，当前不存在——不做猜测式匹配，留待 id 桥接建立后接入。
"""

from __future__ import annotations

from typing import Any

_SPACING_MAX_M = 10_000.0
"""spacing 数值上限（米）：超出视为单位漂移（如把 mm 当 m）。"""


def constraint_lint_violations(ir: Any) -> list[str]:
    """逐条校验 spatial_constraints；返回漂移描述列表（空 = 通过）。"""
    if not isinstance(ir, dict):
        return []
    known = {
        str(a.get("id"))
        for a in ir.get("assets") or []
        if isinstance(a, dict) and a.get("id")
    }
    errors: list[str] = []
    seen: dict[tuple[str, str, str], Any] = {}
    for i, constraint in enumerate(ir.get("spatial_constraints") or []):
        if not isinstance(constraint, dict):
            continue
        ctype = str(constraint.get("type") or "")
        subject = str(constraint.get("subject") or "")
        relation = str(constraint.get("relation") or "")
        obj = constraint.get("object")
        obj = str(obj) if obj else ""
        if subject and subject not in known:
            errors.append(
                f"空间约束[{i}] subject {subject!r} 未在 assets 声明(引用完整性)"
            )
        if obj and obj not in known:
            errors.append(
                f"空间约束[{i}] object {obj!r} 未在 assets 声明(引用完整性)"
            )
        if ctype in ("containment", "adjacency") and not obj:
            errors.append(
                f"空间约束[{i}] type={ctype} 是二元关系但缺 object(subject={subject!r})"
            )
        if ctype == "spacing":
            value = constraint.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(
                    f"空间约束[{i}] spacing 的 value 必须是数值,实际 {value!r}(relation={relation!r})"
                )
            elif not (0 < float(value) <= _SPACING_MAX_M):
                errors.append(
                    f"空间约束[{i}] spacing 数值 {value!r} 越界(须为 (0, {_SPACING_MAX_M}] 米;"
                    f"疑似单位漂移或占位值;relation={relation!r})"
                )
        key = (ctype, subject, obj)
        if key in seen:
            prev = seen[key]
            value = constraint.get("value")
            if prev != value and not (prev is None and value is None):
                errors.append(
                    f"空间约束[{i}] 与前一条 (type={ctype}, subject={subject!r}, object={obj!r}) "
                    f"数值矛盾:value {prev!r} vs {value!r}"
                )
        else:
            seen[key] = constraint.get("value")
    return errors


__all__ = ["constraint_lint_violations"]
