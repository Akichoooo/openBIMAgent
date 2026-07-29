"""VW MCP vs_index.json 生成与格式单测:generate_vs_index.py + 已生成产物。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载
generate_vs_index.py (mcp_servers 不在 pythonpath)。

覆盖(4 个测试):
- generate_vs_index 成功:对真实 vs.py 调用 extract_vs_functions,断言返回结构
- vs_index.json 格式:加载项目根已生成的 vs_index.json,含 functions/total_count
- 函数 schema 完整性:第一个函数含 name/args/arity/return_type/doc
- total_count 合理:在 100-5000 范围 (vs.py 实际 2865)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

# ---------- 按路径加载 generate_vs_index.py ----------

_GEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "tools" / "generate_vs_index.py"
)

_VS_PY = (
    Path(__file__).resolve().parents[2]
    / "openBIMForge" / "forge_core" / "design_agent" / "vs.py"
)

_VS_INDEX = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "vs_index.json"
)


def _load_gen_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_gen_vs_index_test", _GEN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 4 个测试 ----------


def test_generate_vs_index_success() -> None:
    """生成成功:对真实 vs.py 调用 extract_vs_functions,返回含 functions/total_count。"""
    if not _VS_PY.exists():
        pytest.skip(f"vs.py not found at {_VS_PY} (openBIMForge 源不可用)")
    gen = _load_gen_module()
    index = gen.extract_vs_functions(str(_VS_PY))
    assert "functions" in index
    assert "total_count" in index
    assert isinstance(index["functions"], list)
    assert index["total_count"] == len(index["functions"])
    assert index["total_count"] > 0
    # generated_at 应为 ISO 时间字符串
    assert "generated_at" in index
    assert "T" in index["generated_at"]


def test_vs_index_format() -> None:
    """格式校验:加载项目根 vs_index.json,含 functions/total_count/generated_at。"""
    if not _VS_INDEX.exists():
        pytest.skip(
            f"vs_index.json not found at {_VS_INDEX}; "
            f"run: uv run python mcp_servers/vectorworks_mcp/tools/generate_vs_index.py"
        )
    data = json.loads(_VS_INDEX.read_text(encoding="utf-8"))
    assert "functions" in data
    assert "total_count" in data
    assert "generated_at" in data
    assert "source" in data
    assert isinstance(data["functions"], list)
    assert data["total_count"] == len(data["functions"])


def test_vs_index_function_schema() -> None:
    """函数 schema 完整性:第一个函数含 name/args/arity/return_type/doc。"""
    if not _VS_INDEX.exists():
        pytest.skip("vs_index.json not found; run generate_vs_index.py first")
    data = json.loads(_VS_INDEX.read_text(encoding="utf-8"))
    assert len(data["functions"]) > 0
    f = data["functions"][0]
    # 必需字段
    for field in ("name", "args", "arity", "return_type", "doc"):
        assert field in f, f"function missing field: {field}"
    # name 应为 vs. 前缀
    assert f["name"].startswith("vs."), f"unexpected name: {f['name']}"
    # args 应为 list
    assert isinstance(f["args"], list)
    # arity 应等于 args 长度
    assert f["arity"] == len(f["args"])
    # return_type 应为非空字符串
    assert isinstance(f["return_type"], str)
    # doc 应为字符串 (可能为空)
    assert isinstance(f["doc"], str)
    # phase2 扩展字段
    for field in ("min_arity", "max_arity"):
        assert field in f, f"function missing phase2 field: {field}"


def test_vs_index_count_reasonable() -> None:
    """total_count 合理:vs.py 应有数百到数千个函数 (实际 2865)。"""
    if not _VS_INDEX.exists():
        pytest.skip("vs_index.json not found; run generate_vs_index.py first")
    data = json.loads(_VS_INDEX.read_text(encoding="utf-8"))
    # vs.py 是 1.4MB 绑定,函数数应在 100-5000 范围
    # (任务书估算 248,实际 ast 解析得 2865,如实报告)
    assert 100 <= data["total_count"] <= 5000, (
        f"total_count {data['total_count']} out of expected range [100, 5000]"
    )
    # 抽样:已知存在的函数 (vs.Abs 是 vs.py 第一个函数)
    names = {f["name"] for f in data["functions"]}
    assert "vs.Abs" in names
    # vs.Rect 应存在 (任务书 C1 写 vs.Rectangle 是估算,实际命名 vs.Rect)
    assert "vs.Rect" in names, "vs.Rect not found (任务书估算的 vs.Rectangle 实际为 vs.Rect)"
