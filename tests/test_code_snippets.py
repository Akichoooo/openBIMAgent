"""RAG few-shot 代码片段库测试：捕获（验收门）/检索（词法+结构化）/注入（builder prompt）。

隔离：OPENBIMAGENT_SNIPPETS_DIR 指向 tmp_path，运行时语料与仓库 .code_snippets 完全隔离；
种子语料来自包内 snippets_seed.json（随包分发，只读）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_snippets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENBIMAGENT_SNIPPETS_DIR", str(tmp_path / "snippets"))


def _pipe_ctx() -> dict:
    return {
        "batch": ["seg_pipe_1"],
        "ir": {
            "assets": [
                {
                    "id": "seg_pipe_1",
                    "category": "ifc_pipe_segment",
                    "tags": ["pipe"],
                    "description": "DN400 污水混凝土重力管",
                }
            ]
        },
    }


def test_seeds_retrievable_without_runtime_corpus() -> None:
    from openbimagent.assembly.code_snippets import retrieve_snippets

    entries = retrieve_snippets(_pipe_ctx())
    assert entries and entries[0]["id"] == "seed-pipe-segment"
    assert "import mathutils" in entries[0]["code"]


def test_no_match_returns_empty() -> None:
    from openbimagent.assembly.code_snippets import retrieve_snippets

    ctx = {"batch": ["x"], "ir": {"assets": [{"id": "x", "category": "zzz_nothing"}]}}
    assert retrieve_snippets(ctx) == []


def test_capture_requires_positive_score() -> None:
    from openbimagent.assembly.code_snippets import capture_snippet

    assert capture_snippet("import bpy", _pipe_ctx(), score=0.0) is None


def test_capture_dedup_and_retrieve(tmp_path: Path) -> None:
    from openbimagent.assembly.code_snippets import capture_snippet, retrieve_snippets

    code = "import bpy\nimport mathutils\n# verified pipeline code"
    first = capture_snippet(code, _pipe_ctx(), score=9.1, session_id="sess-1")
    assert first is not None
    # 同码再入池:hash 去重
    assert capture_snippet(code, _pipe_ctx(), score=9.3) is None
    # 运行时条目分数更高,检索排序在种子之前
    entries = retrieve_snippets(_pipe_ctx())
    assert entries[0]["id"] == first
    assert entries[0]["score"] == 9.1
    # 捕获的代码可从语料目录读回
    assert (tmp_path / "snippets" / f"{first}.py").is_file()


def test_format_block_truncates_long_snippets() -> None:
    from openbimagent.assembly.code_snippets import capture_snippet, format_snippet_block, retrieve_snippets

    long_code = "\n".join(f"# line {i}" for i in range(120))
    capture_snippet(long_code, _pipe_ctx(), score=9.0)
    block = format_snippet_block(retrieve_snippets(_pipe_ctx(), top_k=1))
    assert block is not None and "（截断）" in block


def test_builder_messages_include_snippet_block() -> None:
    from openbimagent.assembly.builder import _build_modeler_messages

    messages = _build_modeler_messages("brief", _pipe_ctx(), None)
    user = messages[1]["content"]
    assert "参考实现" in user
    assert "import mathutils" in user  # 种子片段的调用形态进 prompt
    # 范例块在输出契约之后、风格锚点之前(先看契约再看范例)
    assert user.index("输出契约") < user.index("参考实现") < user.index("风格锚点")


def test_builder_messages_without_match_have_no_block() -> None:
    from openbimagent.assembly.builder import _build_modeler_messages

    ctx = {"batch": ["mystery"], "ir": {"assets": [{"id": "mystery", "category": "zzz"}]}}
    messages = _build_modeler_messages("brief", ctx, None)
    assert "参考实现" not in messages[1]["content"]
