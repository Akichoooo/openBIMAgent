"""C7 path-scoped 规则(claude .claude/rules paths: 语义,按文件类型激活)。

触及特定扩展名的文件时注入对应规则片段(Vectorworks/Grasshopper/IFC 等格式约束),
避免把所有格式规则常驻 system prompt。read 工具命中已知扩展名时把规则追加到回灌视图。
"""

from __future__ import annotations

_PATH_SCOPED_RULES: dict[str, str] = {
    ".vwx": "Vectorworks 工程:图层命名遵循 ST-/MEP- 前缀;RecordField 写入须经 vs_index 签名校验。",
    ".gh": "Grasshopper 定义:仅作只读参考,不得直接执行;参数树解析需注意分组路径。",
    ".ghx": "Grasshopper XML 定义:仅作只读参考。",
    ".ifc": "IFC 实体:遵循 IFC4X3;实体 GUID 不可重复;空间层级须闭合(Site→Building→Storey→Space)。",
    ".aadl": "AADL 模型:组件接口与连接须显式声明,不得隐式绑定。",
}


def rule_for(path: str) -> str | None:
    """按扩展名返回 path-scoped 规则片段;无匹配返回 None。"""
    lowered = path.lower()
    for ext, rule in _PATH_SCOPED_RULES.items():
        if lowered.endswith(ext):
            return rule
    return None


__all__ = ["rule_for"]
