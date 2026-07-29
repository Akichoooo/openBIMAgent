# OPENBIMAGENT vectorworks-mcp toolsets 预设生成器 (M1 phase 2)
# 从 vs_index.json 按命名规则筛选真实存在的函数,生成三档工具集:
#   - minimal (~40): 最基础,几何创建 + 类/图层 + 移动旋转 + 基础句柄访问
#   - modeling (~80): 建模核心,minimal + Create*/Extrude/Sweep/Loft/Wall/IFC
#   - full: 全量
# 产物: mcp_servers/vectorworks_mcp/toolsets.json

"""生成 toolsets.json:三档工具集预设 (full/modeling/minimal)。

OPENBIMAGENT (phase2 C): vs_index 函数总量远超 MCP 上下文预算,按场景
裁剪到 40-100 个常用函数。所有函数名都从 vs_index.json 实际存在清单
中挑选 (任务书 C1 示例函数名 vs.Rectangle/vs.CreateWall 等多为估算,
vs.py 实际命名不同,如 vs.Rect/vs.AddSymToWall)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_VS_INDEX = "mcp_servers/vectorworks_mcp/vs_index.json"
DEFAULT_OUTPUT = "mcp_servers/vectorworks_mcp/toolsets.json"

# modeling 关键词:覆盖这些前缀/子串的函数 (排除明显非建模的 UI/数据库类)
MODELING_PREFIXES = (
    "vs.Create",
    "vs.AddSymToWall",
    "vs.BreakWall",
    "vs.CreateWallFeature",
    "vs.CreateWallStyle",
    "vs.ConvertToUnstyledWall",
    "vs.DeleteWallPeak",
    "vs.Extrude",
    "vs.HExtrude",
    "vs.BeginSweep",
    "vs.EndSweep",
    "vs.CreateLoftSurfaces",
    "vs.IFC_Export",
    "vs.IFC_Import",
    "vs.IFC_DefPsetImport",
    "vs.GetObjMaterialName",
)

# UI 控件/对话框类 Create* 排除清单 (不属于建模核心)
UI_CONTROL_KEYWORDS = (
    "CheckBox", "GroupBox", "ColorPopup", "Control", "PullDownMenu",
    "StaticText", "EditInteger", "EditReal", "EditText", "Button",
    "ThumbnailPopup", "CustThumbPopup", "CustomControl", "Palette",
    "Dialog", "TabControl", "TabPane", "ListBox", "Layers",
    "DesignLayerPullDownMenu", "ClassPullDownMenu",
    "CenteredStaticText", "ChainDimension", "LinearDim",
    "BatDormer",  # 特定 dormer 工具,非通用建模
)


def load_vs_index(path: str | Path) -> dict[str, Any]:
    """加载 vs_index.json。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_minimal(all_names: list[str]) -> list[str]:
    """构建 minimal 工具集 (~40 个最基础函数)。

    策略:用白名单 (vs.py 真实函数) + 过滤不存在的。
    """
    name_set = set(all_names)
    # 白名单:已核对均为 vs.py 真实函数 (见 build 时的过滤)
    candidates = [
        # 基础几何创建 (9)
        "vs.Rect", "vs.RectangleN", "vs.Oval", "vs.OvalN",
        "vs.Line", "vs.LineTo", "vs.Arc", "vs.PolyMedialAxis", "vs.Polygonize",
        # 几何变换 (6)
        "vs.Move3D", "vs.Move3DObj", "vs.Rotate", "vs.Rotate3D",
        "vs.RotatePoint", "vs.Scale",
        # 类操作 (5)
        "vs.SetClass", "vs.SetClassByStyle", "vs.ActiveClass",
        "vs.NameClass", "vs.DelClass",
        # 图层操作 (3)
        "vs.ActLayer", "vs.Layer", "vs.LayerRef",
        # 选择/删除 (2)
        "vs.SelectObj", "vs.DelObject",
        # 消息/对话 (3)
        "vs.AlrtDialog", "vs.ClrMessage", "vs.SetToolHelpMessage",
        # 句柄访问 (6)
        "vs.HAngle", "vs.HArea", "vs.HCenter", "vs.HDuplicate",
        "vs.HLength", "vs.HHeight",
        # 颜色/线宽 (3)
        "vs.SetFillFore", "vs.SetFillBack", "vs.SetLW",
        # 视图/版本 (1)
        "vs.GetVersion",
    ]
    # 过滤不存在的
    return [n for n in candidates if n in name_set]


def build_modeling(all_names: list[str], minimal: list[str]) -> list[str]:
    """构建 modeling 工具集 (~80 个核心建模函数)。

    策略:minimal + 建模前缀 (Create*/Extrude/Sweep/Loft/Wall/IFC/Material),
    但排除 UI 控件类 Create* (CheckBox/Dialog/PullDownMenu 等)。
    """
    result = list(minimal)
    for name in all_names:
        if name in result:
            continue
        if not name.startswith(MODELING_PREFIXES):
            continue
        # 排除 UI 控件/对话框类 (CreateCheckBox/CreateColorPopup 等)
        if any(kw in name for kw in UI_CONTROL_KEYWORDS):
            continue
        result.append(name)
    return result


def build_toolsets(vs_index: dict[str, Any]) -> dict[str, Any]:
    """构建三档工具集。

    Args:
        vs_index: vs_index.json 解析后的字典

    Returns:
        {"full": {...}, "modeling": {...}, "minimal": {...}}
    """
    all_names = [f["name"] for f in vs_index["functions"]]
    minimal = build_minimal(all_names)
    modeling = build_modeling(all_names, minimal)
    full = list(all_names)

    return {
        "full": {
            "description": "全量工具集 (vs.py 全部公开函数)",
            "count": len(full),
            "functions": full,
        },
        "modeling": {
            "description": "建模核心工具集 (几何 + Create*/Extrude/Sweep/Loft/Wall/IFC)",
            "count": len(modeling),
            "functions": modeling,
        },
        "minimal": {
            "description": "最小工具集 (基础几何 + 类/图层 + 句柄访问)",
            "count": len(minimal),
            "functions": minimal,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口:从 vs_index.json 生成 toolsets.json。"""
    parser = argparse.ArgumentParser(
        description="从 vs_index.json 生成三档工具集预设 toolsets.json"
    )
    parser.add_argument(
        "--vs-index", default=DEFAULT_VS_INDEX, help="vs_index.json 路径"
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 路径")
    args = parser.parse_args(argv)

    vs_index_path = Path(args.vs_index)
    if not vs_index_path.exists():
        print(f"ERROR: vs_index.json not found: {vs_index_path}", file=sys.stderr)
        return 1

    vs_index = load_vs_index(vs_index_path)
    toolsets = build_toolsets(vs_index)

    # 校验:所有 toolset 函数都必须在 vs_index 中
    all_names = set(f["name"] for f in vs_index["functions"])
    for tier in ("minimal", "modeling", "full"):
        invalid = [n for n in toolsets[tier]["functions"] if n not in all_names]
        if invalid:
            print(
                f"ERROR: tier {tier} has {len(invalid)} invalid names: "
                f"{invalid[:5]}",
                file=sys.stderr,
            )
            return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(toolsets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Generated {out}: "
        f"full={toolsets['full']['count']}, "
        f"modeling={toolsets['modeling']['count']}, "
        f"minimal={toolsets['minimal']['count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
