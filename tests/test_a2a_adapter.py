"""E A2A v1.0 适配层测试：Agent Card / Task 状态机 / Artifact 签名映射同构性。"""

from __future__ import annotations

from datetime import datetime, timezone

from openbimagent.orchestrator.contracts import (
    ArtifactRecord,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.runtime import load_agent_profile
from openbimagent.server.a2a_adapter import (
    A2ATaskState,
    a2a_artifact_from_record,
    a2a_task_from_envelope,
    agent_card_from_profile,
    agent_card_from_role,
    map_subagent_status,
)


def test_status_mapping_covers_all_subagent_states() -> None:
    assert map_subagent_status(SubagentStatus.CREATED) == A2ATaskState.SUBMITTED
    assert map_subagent_status(SubagentStatus.QUEUED) == A2ATaskState.SUBMITTED
    assert map_subagent_status(SubagentStatus.RUNNING) == A2ATaskState.WORKING
    assert map_subagent_status(SubagentStatus.COMPLETED) == A2ATaskState.COMPLETED
    assert map_subagent_status(SubagentStatus.FAILED) == A2ATaskState.FAILED
    assert map_subagent_status(SubagentStatus.CANCELLED) == A2ATaskState.CANCELED


def test_agent_card_from_real_role() -> None:
    card = agent_card_from_role("modeler")
    assert card["protocolVersion"] == "1.0"
    assert card["name"] == "modeler"
    # 本项目有 SSE 事件流 + session JSONL 树留痕 → A2A capabilities 对齐
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["stateTransitionHistory"] is True
    assert card["skills"] and card["skills"][0]["id"] == "modeler"
    assert "artifact" in card["defaultOutputModes"]


def test_agent_card_from_profile_carries_url() -> None:
    profile = load_agent_profile("planner")
    card = agent_card_from_profile(profile, url="http://127.0.0.1:8000/a2a")
    assert card["url"] == "http://127.0.0.1:8000/a2a"
    assert card["name"] == "planner"
    assert card["description"]  # 从 system_prompt 首行派生


def _completed_envelope() -> SubagentResultEnvelope:
    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id="art-1",
        kind="blend",
        path="/abs/out/asset.blend",
        relative_path="out/asset.blend",
        media_type="application/octet-stream",
        sha256="a" * 64,
        size_bytes=2048,
    )
    return SubagentResultEnvelope(
        request_id="req-1",
        agent_id="agent-1",
        parent_session_id="parent-1",
        child_session_id="child-1",
        child_session_path="/abs/child-1.jsonl",
        status=SubagentStatus.COMPLETED,
        summary="建模完成",
        artifacts=(record,),
        manifest_path="/abs/manifest.json",
        started_at=now,
        ended_at=now,
        receipt_id="r" * 64,
        lineage_id="lin-1",
        attempt_number=1,
    )


def test_a2a_task_from_completed_envelope() -> None:
    task = a2a_task_from_envelope(_completed_envelope())
    assert task["id"] == "req-1"
    assert task["contextId"] == "lin-1"  # lineage = A2A 上下文
    assert task["status"]["state"] == "completed"
    assert task["metadata"]["receiptId"] == "r" * 64  # 幂等回执
    art = task["artifacts"][0]
    assert art["artifactId"] == "art-1"
    assert art["metadata"]["sha256"] == "a" * 64  # canonical sha256 = A2A Artifact 签名
    assert task["history"][0]["parts"][0]["text"] == "建模完成"


def test_a2a_artifact_prefers_relative_path_for_portability() -> None:
    record = ArtifactRecord(
        artifact_id="art-2",
        kind="ir",
        path="/abs/x.json",
        relative_path="out/x.json",
        sha256="b" * 64,
        size_bytes=10,
    )
    art = a2a_artifact_from_record(record)
    assert art["parts"][0]["file"]["uri"] == "out/x.json"  # 跨机器可移植
    assert art["metadata"]["immutable"] is True
