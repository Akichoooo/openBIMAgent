# OPENBIMAGENT vectorworks-mcp 三重门禁 (M1 phase 2)
# handoff 摘要 + params hash + approval 审批 (参照 openBIMForge Executor 层)。
# 在 server.FileIPCClient.send_command 写入 job 文件前调用 check_gate,
# 高风险操作未审批时阻断,防止 LLM 误调用 ExportIFC/DeleteObj 等破坏性 API。

"""handoff/hash/approval 三重门禁 (参照 openBIMForge)。

OPENBIMAGENT (phase2 D): 副作用操作 (创建墙体/导出 IFC/删除对象) 需三重
验证:摘要 + hash + 审批。高风险未审批时 raise PermissionError 阻断。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

# 高风险操作关键词 (出现于 code 即触发审批门禁)
# - ExportIFC/IFC_Export: 导出文件,副作用大
# - Delete*/Del*: 删除对象/类/记录,不可逆
# - Close: 关闭文档,可能丢数据
# - Save/SaveAs: 写文件,覆盖风险
HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "ExportIFC",
    "IFC_Export",
    "IFC_Import",  # 导入会改变文档
    "Delete",
    "DelObject",
    "DelClass",
    "DelRecord",
    "DeleteAll",
    "Close",
    "SaveAs",
    "SaveAcquire",
    "DoMenuText",  # 触发菜单命令,副作用不可控
)

# 操作摘要分类规则 (key=代码中匹配子串, value=摘要前缀)
_SUMMARY_RULES: tuple[tuple[str, str], ...] = (
    ("CreateWall", "创建墙体"),
    ("CreateSlab", "创建板"),
    ("CreateColumn", "创建柱"),
    ("CreateBeam", "创建梁"),
    ("CreateDoor", "创建门"),
    ("CreateWindow", "创建窗"),
    ("CreateRoof", "创建屋顶"),
    ("CreateCustomObject", "创建自定义对象"),
    ("CreateLoftSurfaces", "创建放样曲面"),
    ("Extrude", "拉伸"),
    ("Sweep", "扫掠"),
    ("ExportIFC", "导出 IFC"),
    ("IFC_Export", "导出 IFC"),
    ("IFC_Import", "导入 IFC"),
    ("Delete", "删除"),
    ("DelObject", "删除对象"),
    ("DelClass", "删除类"),
    ("Close", "关闭"),
    ("SaveAs", "另存为"),
    ("Rect", "绘制矩形"),
    ("Oval", "绘制椭圆"),
    ("Line", "绘制线"),
    ("Arc", "绘制弧"),
    ("Move3D", "移动"),
    ("Rotate3D", "旋转"),
    ("Scale", "缩放"),
    ("SetClass", "设置类"),
    ("SetFillFore", "设置填充前景色"),
    ("SetLW", "设置线宽"),
)

# 审批函数签名: (summary, params_hash) -> bool
ApprovalFn = Callable[[str, str], bool]

# 摘要最大长度 (任务书 D2 要求 ≤200 字符)
SUMMARY_MAX_LEN = 200


def _semantic_params(params: dict[str, Any]) -> dict[str, Any]:
    """移除审批控制字段；控制状态不得改变被审批 payload 的身份。"""
    return {key: value for key, value in params.items() if key != "_approved"}


def generate_handoff_summary(command: str, params: dict[str, Any]) -> str:
    """生成操作摘要 (handoff 第 1 重)。

    Args:
        command: 命令名 (ping/describe_capabilities/execute_code)
        params: 命令参数字典

    Returns:
        操作摘要字符串 (≤200 字符)。execute_code 时从 code 中匹配
        vs.* 调用并生成"创建墙体: ..."等中文摘要;其他命令直接描述。
    """
    if command == "execute_plan":
        plan = params.get("plan") if isinstance(params.get("plan"), dict) else {}
        operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
        output_name = str(params.get("output_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        summary = (
            f"执行 typed Vectorworks 计划 {plan.get('plan_id', 'unknown')}; "
            f"operations={len(operations)}; output={output_name or 'unknown'}"
        )
        return summary[:SUMMARY_MAX_LEN]
    if command != "execute_code":
        # 非执行代码命令,直接描述命令名 + 关键参数
        param_keys = ",".join(sorted(_semantic_params(params).keys())) if params else ""
        summary = f"执行命令: {command}" + (f" (参数: {param_keys})" if param_keys else "")
        return summary[:SUMMARY_MAX_LEN]

    code = params.get("code", "")
    # 提取 code 中所有 vs.FunctionName 调用
    vs_calls = re.findall(r"vs\.([A-Za-z_]\w*)\s*\(", code)

    if not vs_calls:
        # code 中无 vs.* 调用,可能是纯 Python 辅助逻辑
        snippet = code.strip().replace("\n", " ")[:60]
        return f"执行代码 (无 vs 调用): {snippet}"[:SUMMARY_MAX_LEN]

    # 按优先级匹配摘要规则
    summaries: list[str] = []
    for call in vs_calls:
        matched = None
        for keyword, label in _SUMMARY_RULES:
            if keyword in call:
                matched = label
                break
        if matched is None:
            matched = f"调用 vs.{call}"
        summaries.append(matched)

    # 拼接摘要 + code 片段
    prefix = " + ".join(summaries)
    snippet = code.strip().replace("\n", " ")[:60]
    summary = f"{prefix}: {snippet}"
    return summary[:SUMMARY_MAX_LEN]


def compute_params_hash(params: dict[str, Any]) -> str:
    """计算参数 hash (handoff 第 2 重)。

    Args:
        params: 命令参数字典

    Returns:
        sha256(params_json) 前 16 字符 (防篡改,任务书 D2 要求 16 字符)
    """
    params_str = json.dumps(
        _semantic_params(params),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(params_str.encode("utf-8")).hexdigest()[:16]


def requires_approval(command: str, params: dict[str, Any]) -> bool:
    """判断是否需要审批 (handoff 第 3 重)。

    Args:
        command: 命令名
        params: 命令参数字典

    Returns:
        True 表示高风险操作,需用户审批
    """
    if command == "execute_plan":
        return True
    if command != "execute_code":
        return False

    code = params.get("code", "")
    return any(kw in code for kw in HIGH_RISK_KEYWORDS)


def check_gate(
    command: str,
    params: dict[str, Any],
    approval_fn: ApprovalFn | None = None,
) -> dict[str, Any]:
    """三重门禁检查。

    Args:
        command: 命令名
        params: 命令参数字典
        approval_fn: 审批回调 (summary, params_hash) -> bool;None 时
            高风险操作直接阻断 (除非 params 含 _approved=True 显式放行)

    Returns:
        {
            "ok": bool,              # 是否通过门禁
            "handoff": str,          # 操作摘要 (第 1 重)
            "params_hash": str,      # 参数 hash (第 2 重)
            "requires_approval": bool,  # 是否高风险 (第 3 重)
            "approved": bool,        # 是否已审批
            "reason": str | None,    # 失败原因 (ok=False 时)
        }

    Raises:
        PermissionError: 高风险操作未审批时 (调用方应捕获并返回错误)
    """
    # 第 1 重:摘要
    summary = generate_handoff_summary(command, params)

    # 第 2 重:hash
    params_hash = compute_params_hash(params)

    # 第 3 重:审批
    needs_approval = requires_approval(command, params)
    approved = False

    if needs_approval:
        # 显式放行:params 含 _approved=True (测试/批处理场景)
        if params.get("_approved") is True:
            approved = True
        elif approval_fn is not None:
            approved = bool(approval_fn(summary, params_hash))

    ok = not needs_approval or approved
    reason = None
    if not ok:
        reason = (
            f"高风险操作未审批: {summary} (hash={params_hash}); "
            f"需通过 approval_fn 或 params['_approved']=True 显式放行"
        )

    return {
        "ok": ok,
        "handoff": summary,
        "params_hash": params_hash,
        "requires_approval": needs_approval,
        "approved": approved,
        "reason": reason,
    }
