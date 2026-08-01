"""freetokenfaucet 真实模型 + Subagent Runtime 集成测试（显式开启）。

默认跳过，避免常规 pytest 依赖外网。运行：
    OPENBIMAGENT_RUN_REAL_LLM=1 pytest tests/test_faucet_integration_real.py -v

API key 只从 FREETOKENFAUCET_API_KEY 读取，不写日志、Session 或测试快照。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openbimagent.orchestrator.contracts import SubagentRequest, SubagentStatus
from openbimagent.orchestrator.runtime import LocalSubagentRuntime
from openbimagent.providers.registry import ModelRegistry
from openbimagent.session.store import SessionStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "models.toml"
RUN_REAL = os.environ.get("OPENBIMAGENT_RUN_REAL_LLM") == "1"
HAS_KEY = bool(os.environ.get("FREETOKENFAUCET_API_KEY", "").strip())

requires_real_llm = pytest.mark.skipif(
    not RUN_REAL or not HAS_KEY,
    reason=(
        "跳过 freetokenfaucet 真实模型测试；显式设置 "
        "OPENBIMAGENT_RUN_REAL_LLM=1 和 FREETOKENFAUCET_API_KEY 后运行"
    ),
)


def _agents_dir(tmp_path: Path) -> Path:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "clarify.md").write_text(
        "---\n"
        "name: clarify\n"
        "model: profile-managed\n"
        "tools: []\n"
        "permissions: {}\n"
        "context_mode: isolated\n"
        "max_turns: 2\n"
        "artifact_contract: summary-v1\n"
        "nesting: false\n"
        "---\n"
        "你是最小连通性验证代理。不要调用工具，只按用户指定格式回答。\n",
        encoding="utf-8",
    )
    return agents


def _runtime(tmp_path: Path) -> tuple[LocalSubagentRuntime, SessionStore]:
    registry = ModelRegistry.load(CONFIG_PATH, profile="faucet")
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="faucet-live-parent")
    runtime = LocalSubagentRuntime(
        sessions_dir=sessions,
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=_agents_dir(tmp_path),
        chat_fn=registry.chat,
        workdir=tmp_path,
    )
    return runtime, parent


@requires_real_llm
def test_faucet_foreground_subagent_creates_receipted_artifact(tmp_path) -> None:
    runtime, parent = _runtime(tmp_path)
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="clarify",
        task="只回复 FAUCET_FOREGROUND_OK，不要添加其他文字。",
    )
    result = runtime.run(request, parent_session=parent)
    assert result.status is SubagentStatus.COMPLETED
    assert "FAUCET_FOREGROUND_OK" in result.summary
    assert result.receipt_id
    assert Path(result.manifest_path).is_file()
    assert all(Path(record.path).is_file() for record in result.artifacts)
    runtime.shutdown()


@requires_real_llm
def test_faucet_background_subagent_status_join_and_receipt(tmp_path) -> None:
    runtime, parent = _runtime(tmp_path)
    request = SubagentRequest.create(
        parent_session_id=parent.session_id,
        role="clarify",
        task="只回复 FAUCET_BACKGROUND_OK，不要添加其他文字。",
        execution_mode="background",
    )
    handle = runtime.submit(request, parent_session=parent)
    assert handle.status is SubagentStatus.QUEUED
    result = runtime.join(request.request_id, timeout_s=180)
    assert result.status is SubagentStatus.COMPLETED
    assert "FAUCET_BACKGROUND_OK" in result.summary
    assert runtime.status(request.request_id).status is SubagentStatus.COMPLETED
    assert result.receipt_id
    runtime.shutdown()
