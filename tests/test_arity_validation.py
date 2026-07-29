"""VW MCP arity 校验单测:_validate_arity 拦截参数个数不符 (防 VW 崩溃)。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 server.py,
注入带小 vs_index 的 FileIPCClient (tmp_path 隔离,不污染项目目录)。

覆盖(5 个测试):
- 校验通过:vs.Rectangle(0,0,10,10) 4 个参数,与 vs_index arity=4 一致
- 参数过少:vs.Rectangle(0,0) 2 个参数 → ValueError 含 "arity 校验失败"
- 参数过多:vs.Rectangle(0,0,10,10,20) 5 个参数 → ValueError
- 非 vs 函数:print("test") 不触发校验
- 多个 vs 调用:同一 code 含多个 vs.* 调用都被校验
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


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_server_arity_test", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 构造小 vs_index (tmp_path 隔离) ----------

# 测试用 vs_index:2 个已知函数 + 1 个有默认参数的函数
_TEST_VS_INDEX = {
    "functions": [
        {
            "name": "vs.Rectangle",
            "args": ["p1X", "p1Y", "p2X", "p2Y"],
            "arity": 4,
            "min_arity": 4,
            "max_arity": 4,
            "defaults": [],
            "vararg": None,
            "kwarg": None,
            "return_type": "HANDLE",
            "doc": "创建矩形",
        },
        {
            "name": "vs.Line",
            "args": ["p1", "p2"],
            "arity": 2,
            "min_arity": 2,
            "max_arity": 2,
            "defaults": [],
            "vararg": None,
            "kwarg": None,
            "return_type": "HANDLE",
            "doc": "创建线",
        },
        {
            "name": "vs.Message",
            "args": ["msg", "title"],
            "arity": 2,
            "min_arity": 1,
            "max_arity": 2,
            "defaults": ["title"],
            "vararg": None,
            "kwarg": None,
            "return_type": "void",
            "doc": "显示消息 (title 可选)",
        },
    ],
    "total_count": 3,
}


@pytest.fixture
def arity_client(tmp_path: Path) -> Any:
    """构造带小 vs_index 的 FileIPCClient (tmp_path 隔离)。"""
    server = _load_server_module()
    # 写小 vs_index 到 tmp_path
    idx_path = tmp_path / "vs_index.json"
    idx_path.write_text(json.dumps(_TEST_VS_INDEX, ensure_ascii=False), encoding="utf-8")
    # FileIPCClient: jobs/results 也放 tmp_path,vs_index 指向小文件
    # approval_fn=None: 默认阻断高风险;测试用例均低风险,不影响
    return server.FileIPCClient(
        jobs_dir=tmp_path / "jobs",
        results_dir=tmp_path / "results",
        vs_index_path=idx_path,
        toolsets_path=tmp_path / "toolsets.json",  # 不存在,降级为空
        timeout=0.1,
    )


# ---------- 5 个测试 ----------


def test_arity_validation_pass(arity_client: Any) -> None:
    """校验通过:vs.Rectangle(0,0,10,10) 4 个参数,与 arity=4 一致,不抛异常。"""
    # 不应抛异常
    arity_client._validate_arity(
        "execute_code", {"code": "vs.Rectangle(0, 0, 10, 10)"}
    )
    # 默认参数函数:1 个参数 (min_arity=1) 通过
    arity_client._validate_arity(
        "execute_code", {"code": 'vs.Message("hello")'}
    )
    # 默认参数函数:2 个参数 (max_arity=2) 通过
    arity_client._validate_arity(
        "execute_code", {"code": 'vs.Message("hello", "title")'}
    )
    # 非 execute_code 命令不校验
    arity_client._validate_arity("ping", {})
    arity_client._validate_arity("describe_capabilities", {})


def test_arity_validation_fail_too_few(arity_client: Any) -> None:
    """参数过少:vs.Rectangle(0,0) 2 个参数 < min_arity=4 → ValueError。"""
    with pytest.raises(ValueError) as exc_info:
        arity_client._validate_arity(
            "execute_code", {"code": "vs.Rectangle(0, 0)"}
        )
    assert "arity 校验失败" in str(exc_info.value)
    assert "vs.Rectangle" in str(exc_info.value)
    assert "至少需要" in str(exc_info.value)

    # 默认参数函数:0 个参数 < min_arity=1 → ValueError
    with pytest.raises(ValueError):
        arity_client._validate_arity(
            "execute_code", {"code": "vs.Message()"}
        )


def test_arity_validation_fail_too_many(arity_client: Any) -> None:
    """参数过多:vs.Rectangle(0,0,10,10,20) 5 个参数 > max_arity=4 → ValueError。"""
    with pytest.raises(ValueError) as exc_info:
        arity_client._validate_arity(
            "execute_code", {"code": "vs.Rectangle(0, 0, 10, 10, 20)"}
        )
    assert "arity 校验失败" in str(exc_info.value)
    assert "vs.Rectangle" in str(exc_info.value)
    assert "最多接受" in str(exc_info.value)

    # 默认参数函数:3 个参数 > max_arity=2 → ValueError
    with pytest.raises(ValueError):
        arity_client._validate_arity(
            "execute_code", {"code": 'vs.Message("a", "b", "c")'}
        )


def test_arity_validation_skip_non_vs_functions(arity_client: Any) -> None:
    """非 vs 函数:print("test") 不触发校验 (未知函数跳过)。"""
    # 纯 Python 调用,不在 vs_index 中,跳过
    arity_client._validate_arity(
        "execute_code", {"code": 'print("test"); x = 1 + 2'}
    )
    # 未知 vs 函数 (不在 vs_index) 也跳过
    arity_client._validate_arity(
        "execute_code", {"code": "vs.UnknownFunc(1, 2, 3)"}
    )
    # 空 code 不抛异常
    arity_client._validate_arity("execute_code", {"code": ""})
    arity_client._validate_arity("execute_code", {})


def test_arity_validation_multiple_functions(arity_client: Any) -> None:
    """多个 vs 调用:同一 code 含多个 vs.* 调用都被校验,任一不符即抛。"""
    # 两个都正确,不抛
    arity_client._validate_arity(
        "execute_code",
        {"code": "vs.Rectangle(0, 0, 10, 10)\nvs.Line((0,0), (5,5))"},
    )
    # 第二个调用参数过少,应抛
    with pytest.raises(ValueError) as exc_info:
        arity_client._validate_arity(
            "execute_code",
            {"code": "vs.Rectangle(0, 0, 10, 10)\nvs.Line((0,0))"},
        )
    assert "vs.Line" in str(exc_info.value)
    # 第一个调用参数过多,应抛 (AST 路径会检测到)
    with pytest.raises(ValueError) as exc_info:
        arity_client._validate_arity(
            "execute_code",
            {"code": "vs.Rectangle(0, 0, 10, 10, 99)\nvs.Line((0,0), (5,5))"},
        )
    assert "vs.Rectangle" in str(exc_info.value)
    # 嵌套调用:vs.Line(vs.Func(), (5,5)) — 嵌套 vs 调用也被提取
    arity_client._validate_arity(
        "execute_code",
        {"code": "vs.Line((0,0), (5,5))  # 注释里有 vs.Rectangle(1,2) 不应触发"},
    )
