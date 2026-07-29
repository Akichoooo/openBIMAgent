# OPENBIMAGENT vectorworks-mcp vs_index 离线生成器 (M1 phase 2)
# 从 openBIMForge/forge_core/design_agent/vs.py (1.4MB vs.* 绑定) 用 ast
# 离线提取每个函数的 name/args/arity/return_type/docstring。
# 产物: mcp_servers/vectorworks_mcp/vs_index.json
# 用于: server 侧 arity 校验 (发送前拦截防 VW 引擎崩溃) + 工具集预设。

"""离线生成 vs_index.json:从 vs.py 提取函数签名 (args/arity/ret/doc)。

OPENBIMAGENT (phase2 A): 避免 LLM 编造 vs 函数,提供准确函数签名 + 参数 +
返回值文档。同一函数若重载 (vs.py 中重复定义) 取最后一次定义 (vs.py 末尾
出现更完整的版本)。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# vs.py 默认路径 (openBIMForge 单体,1.4MB vs.* 绑定)
DEFAULT_VS_PY = (
    "D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/"
    "forge_core/design_agent/vs.py"
)
DEFAULT_OUTPUT = "mcp_servers/vectorworks_mcp/vs_index.json"


def _extract_return_type(node: ast.FunctionDef) -> str:
    """从函数末尾的 `return 'TYPE' #` 语句推断返回类型。

    vs.py 没有真正的 ast.returns 注解,而是在函数体末尾写
    `return 'HANDLE' #` 作为类型标记。提取该字面量字符串作为 return_type;
    无 return 或非字面量返回时返回 "void" (过程式) 或 "unknown"。
    """
    if not node.body:
        return "unknown"
    last = node.body[-1]
    if isinstance(last, ast.Return) and isinstance(last.value, ast.Constant):
        if isinstance(last.value.value, str):
            return last.value.value
        return "unknown"
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Constant):
        # 末尾仅有 docstring/字符串,无返回值 -> 过程式
        return "void"
    return "unknown"


def _extract_args(node: ast.FunctionDef) -> dict[str, Any]:
    """提取参数列表 (位置参数 + 默认值 + *args + **kwargs)。

    Returns:
        {"args": [参数名], "defaults": [有默认值的参数名],
         "vararg": str|None, "kwarg": str|None,
         "min_arity": 必填位置参数个数, "max_arity": 含默认的位置参数个数|None}
    """
    pos_args = [a.arg for a in node.args.args]
    n_defaults = len(node.args.defaults)
    # 有默认值的位置参数 = 末尾 n_defaults 个
    defaulted = pos_args[len(pos_args) - n_defaults:] if n_defaults else []
    vararg = node.args.vararg.arg if node.args.vararg else None
    kwarg = node.args.kwarg.arg if node.args.kwarg else None
    min_arity = len(pos_args) - n_defaults
    # max_arity: 含默认值的位置参数总数; *args 时为 None (可变)
    max_arity = len(pos_args) if not vararg else None
    return {
        "args": pos_args,
        "defaults": defaulted,
        "vararg": vararg,
        "kwarg": kwarg,
        "min_arity": min_arity,
        "max_arity": max_arity,
    }


def extract_vs_functions(vs_py_path: str | Path) -> dict[str, Any]:
    """从 vs.py 提取所有函数签名。

    Args:
        vs_py_path: vs.py 文件路径

    Returns:
        {
            "functions": [{"name","args","arity","min_arity","max_arity",
                            "return_type","doc","defaults","vararg","kwarg"}, ...],
            "total_count": N,
            "generated_at": "ISO 时间"
        }

    重载处理:同名函数取最后定义 (vs.py 末尾版本通常更完整)。
    """
    vs_py_path = Path(vs_py_path)
    src = vs_py_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 同名取最后定义:用 dict 按 name 覆盖,最终转 list
    by_name: dict[str, dict[str, Any]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        full_name = f"vs.{node.name}"
        args_info = _extract_args(node)
        doc = ast.get_docstring(node) or ""
        # docstring 限制 200 字符 (任务书 A1 要求)
        doc = doc[:200]
        ret = _extract_return_type(node)
        by_name[full_name] = {
            "name": full_name,
            "args": args_info["args"],
            "arity": len(args_info["args"]),
            "min_arity": args_info["min_arity"],
            "max_arity": args_info["max_arity"],
            "defaults": args_info["defaults"],
            "vararg": args_info["vararg"],
            "kwarg": args_info["kwarg"],
            "return_type": ret,
            "doc": doc,
        }

    functions = sorted(by_name.values(), key=lambda f: f["name"])
    return {
        "functions": functions,
        "total_count": len(functions),
        "generated_at": datetime.now().isoformat(),
        "source": str(vs_py_path),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口:解析参数,生成 vs_index.json。

    Returns:
        0 成功,1 失败
    """
    parser = argparse.ArgumentParser(
        description="从 vs.py 离线生成 vs_index.json"
    )
    parser.add_argument("--vs-path", default=DEFAULT_VS_PY, help="vs.py 路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 路径")
    args = parser.parse_args(argv)

    vs_path = Path(args.vs_path)
    if not vs_path.exists():
        print(f"ERROR: vs.py not found: {vs_path}", file=sys.stderr)
        return 1

    print(f"Extracting from {vs_path}...")
    index = extract_vs_functions(str(vs_path))
    print(f"Extracted {index['total_count']} functions")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_kb = out.stat().st_size / 1024
    print(f"Generated {out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
