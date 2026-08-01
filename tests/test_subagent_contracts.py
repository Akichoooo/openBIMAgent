"""Subagent Runtime v1 请求/结果契约与 JSON Schema 测试。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from openbimagent.orchestrator.contracts import (
    ArtifactRecord,
    ContextMode,
    SubagentError,
    SubagentHandle,
    SubagentRequest,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.control import (
    SteerDirective,
    SteerStatus,
    make_resume_receipt,
    make_resume_request,
)
from openbimagent.schema_gate.gate import validate_artifact


def test_subagent_request_schema_and_no_privilege_fields() -> None:
    request = SubagentRequest.create(parent_session_id="parent-1", role="planner", task="生成计划")
    data = request.model_dump(mode="json")
    assert validate_artifact("subagent_request", data) == []
    assert data["context_mode"] == "isolated"
    assert not ({"model", "tools", "permissions"} & set(data))

    with pytest.raises(ValidationError):
        SubagentRequest(**data, tools=["bash"])


def test_subagent_request_forbids_nested_depth() -> None:
    with pytest.raises(ValidationError):
        SubagentRequest(
            request_id="req",
            parent_session_id="parent",
            role="planner",
            task="x",
            depth=1,
        )


def test_subagent_result_terminal_invariants_and_schema() -> None:
    now = datetime.now(timezone.utc)
    artifact = ArtifactRecord(
        artifact_id="a1",
        kind="summary",
        path="C:/tmp/summary.md",
        sha256="0" * 64,
        size_bytes=0,
    )
    result = SubagentResultEnvelope(
        request_id="r1",
        agent_id="a1",
        parent_session_id="p1",
        child_session_id="c1",
        child_session_path="C:/tmp/c1.jsonl",
        status=SubagentStatus.COMPLETED,
        summary="完成",
        hint="完成",
        artifacts=(artifact,),
        manifest_path="C:/tmp/manifest.json",
        started_at=now,
        ended_at=now,
        receipt_id="receipt",
        lineage_id="lineage-1",
        attempt_number=1,
    )
    assert validate_artifact("subagent_result", result.model_dump(mode="json")) == []

    with pytest.raises(ValidationError, match="failed/cancelled"):
        SubagentResultEnvelope(**{**result.model_dump(), "status": "failed", "error": None})
    with pytest.raises(ValidationError, match="completed"):
        SubagentResultEnvelope(
            **{
                **result.model_dump(),
                "error": SubagentError(code="x", message="bad"),
            }
        )


def test_context_mode_enum_is_explicit() -> None:
    request = SubagentRequest.create(
        parent_session_id="parent",
        role="planner",
        task="x",
        context_mode=ContextMode.FORK,
    )
    assert request.context_mode is ContextMode.FORK


def test_attempt_lineage_contracts_fail_closed() -> None:
    first = SubagentRequest.create(parent_session_id="parent", role="planner", task="x")
    with pytest.raises(ValidationError, match="首个 attempt"):
        SubagentRequest(**{
            **first.model_dump(),
            "resumed_from_request_id": "old",
            "resume_id": "resume",
        })
    with pytest.raises(ValidationError, match="恢复 attempt"):
        SubagentRequest(**{**first.model_dump(), "attempt_number": 2})
    with pytest.raises(ValidationError, match="恢复 attempt handle"):
        SubagentHandle(
            request_id="r2",
            agent_id="a2",
            parent_session_id="parent",
            child_session_id="child",
            child_session_path="C:/tmp/child.jsonl",
            status=SubagentStatus.QUEUED,
            lineage_id=first.lineage_id,
            attempt_number=2,
        )


def test_resume_and_steer_control_schemas() -> None:
    resume = make_resume_request(
        source_request_id="r1",
        source_agent_id="a1",
        source_child_session_id="c1",
        new_request_id="r2",
        lineage_id="lineage",
        attempt_number=2,
        instruction="检查当前状态后继续",
        idempotency_key="test:resume:r1:continue",
        requested_by="parent",
    )
    receipt = make_resume_receipt(resume, new_agent_id="a2", new_child_session_id="c2")
    directive = SteerDirective.create(
        request_id="r2",
        agent_id="a2",
        child_session_id="c2",
        lineage_id="lineage",
        attempt_number=2,
        instruction="优先验证现有文件",
        requested_by="parent",
    )
    assert validate_artifact("resume_request", resume.model_dump(mode="json")) == []
    assert validate_artifact("resume_receipt", receipt.model_dump(mode="json")) == []
    assert validate_artifact("steer_directive", directive.model_dump(mode="json")) == []
    assert SteerStatus.APPLIED.value == "applied"
