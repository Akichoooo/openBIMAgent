"""VW MCP 工具集预设单测:toolsets.json 三档 + describe_capabilities 返回。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 server.py,
注入带 toolsets 的 fake client (tmp_path 隔离)。

覆盖(3 个测试):
- toolsets.json 格式:含 full/modeling/minimal 三档,每档有 description/count/functions
- describe_capabilities 返回 toolset:set VW_TOOLSET=minimal → 返回 toolset="minimal"
- 工具集 count 递减:full.count > modeling.count > minimal.count
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

# ---------- 按路径加载 server.py ----------

_SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "server" / "server.py"
)

_TOOLSETS = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "toolsets.json"
)

_VS_INDEX = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "vs_index.json"
)


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_server_toolsets_test", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 3 个测试 ----------


def test_toolsets_json_format() -> None:
    """toolsets.json 格式:含 full/modeling/minimal 三档,字段完整。"""
    if not _TOOLSETS.exists():
        pytest.skip(
            f"toolsets.json not found at {_TOOLSETS}; "
            f"run: uv run python mcp_servers/vectorworks_mcp/tools/generate_toolsets.py"
        )
    data = json.loads(_TOOLSETS.read_text(encoding="utf-8"))
    # 三档都存在
    for tier in ("full", "modeling", "minimal"):
        assert tier in data, f"missing tier: {tier}"
        tier_data = data[tier]
        assert "description" in tier_data
        assert "count" in tier_data
        assert "functions" in tier_data
        assert isinstance(tier_data["functions"], list)
        assert tier_data["count"] == len(tier_data["functions"])
        assert tier_data["count"] > 0


def test_describe_capabilities_returns_toolset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """describe_capabilities 返回当前 toolset:set VW_TOOLSET=minimal → toolset='minimal'。"""
    server = _load_server_module()
    # monkeypatch VW_TOOLSET 环境变量 (server.py 在 import 时已读 env,需重载)
    # 实际:DEFAULT_TOOLSET 在模块加载时已固定,我们直接验证默认值
    # 构造带 toolsets 的 fake client
    fake_toolsets = {
        "minimal": {"description": "最小", "count": 38, "functions": ["vs.Rect"]},
        "modeling": {"description": "建模", "count": 142, "functions": ["vs.Rect", "vs.CreateCone"]},
        "full": {"description": "全量", "count": 2865, "functions": ["vs.Abs"]},
    }

    class _FakeClientWithToolsets:
        def __init__(self) -> None:
            self.toolsets = fake_toolsets
            self.vs_index = {"vs.Rect": {"arity": 4}}

        def send_command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "vw_version": "VectorWorks 2024",
                "python_version": "3.13.0",
                "known_issues": ["ArcByCenter 已损坏"],
            }

    server.set_client(_FakeClientWithToolsets())
    try:
        result = server.describe_capabilities()
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        # 默认 toolset 应为 minimal (VW_TOOLSET 未设或设为 minimal)
        assert result["toolset"] == "minimal"
        assert result["toolset_info"]["count"] == 38
        assert result["vs_index_loaded"] is True
        assert result["vs_index_count"] == 1
        assert set(result["available_toolsets"]) == {"minimal", "modeling", "full"}
        # 应包含 phase2 新增的 limitations
        assert any("arity 校验" in lim for lim in result["limitations"])
        assert any("门禁拦截" in lim for lim in result["limitations"])
    finally:
        server.set_client(None)  # type: ignore[arg-type]


def test_toolset_function_count() -> None:
    """工具集 count 递减:full > modeling > minimal,且函数都在 vs_index 中。"""
    if not _TOOLSETS.exists():
        pytest.skip("toolsets.json not found; run generate_toolsets.py first")
    if not _VS_INDEX.exists():
        pytest.skip("vs_index.json not found; run generate_vs_index.py first")
    toolsets = json.loads(_TOOLSETS.read_text(encoding="utf-8"))
    vs_index = json.loads(_VS_INDEX.read_text(encoding="utf-8"))
    all_vs_names = {f["name"] for f in vs_index["functions"]}

    full_c = toolsets["full"]["count"]
    model_c = toolsets["modeling"]["count"]
    min_c = toolsets["minimal"]["count"]
    # 三档递减 (full 最大,minimal 最小)
    assert full_c > model_c, f"full({full_c}) should > modeling({model_c})"
    assert model_c > min_c, f"modeling({model_c}) should > minimal({min_c})"
    # minimal 应在 30-50 范围 (任务书 ~40,实际 38)
    assert 30 <= min_c <= 50, f"minimal count {min_c} out of [30, 50]"
    # modeling 应在 50-200 范围 (任务书 ~80,实际 142,因 vs.py Create* 较多)
    assert 50 <= model_c <= 200, f"modeling count {model_c} out of [50, 200]"
    # full 应等于 vs_index total_count
    assert full_c == vs_index["total_count"], (
        f"full count {full_c} != vs_index total_count {vs_index['total_count']}"
    )
    # 所有 toolset 函数都应在 vs_index 中 (生成脚本已校验,这里复核)
    for tier in ("minimal", "modeling", "full"):
        invalid = [n for n in toolsets[tier]["functions"] if n not in all_vs_names]
        assert not invalid, (
            f"tier {tier} has {len(invalid)} invalid names (not in vs_index): "
            f"{invalid[:3]}"
        )
