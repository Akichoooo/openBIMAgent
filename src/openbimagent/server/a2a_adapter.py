"""A2A (Agent2Agent) v1.0 协议适配层（对标 AAIF 治理的工业标准，150+ 组织生产采用）。

2026 Agent 互联三协议：MCP(agent↔工具，本项目已达标) / A2A(agent↔agent) / AG-UI(agent↔人)。
本模块把 openBIMAgent 的 **artifact-mediated 子代理协议**对齐 A2A v1.0 四组件，证明"自研协议
与工业标准同构"——这是纯转换适配层（无网络、无服务、不改子代理运行时），符合方案"仅适配、
不做生产级全量迁移"的边界。

A2A v1.0 四组件映射：
- **Agent Card**（能力声明，类 OpenAPI）  ← agents/<role>.md frontmatter（AgentProfile）
- **Task + 状态机**                        ← orchestrator SubagentStatus / SubagentResultEnvelope
- **Message**（多模态文本）                ← 子代理 summary / error
- **Artifact**（版本 + 签名）              ← ArtifactRecord（已有 canonical sha256 + manifest，近零改造）

同构性论据（论文可用）：本项目的稳定身份(request_id/lineage_id/attempt_number)、幂等回执
(receipt_id)、不可变工件签名(sha256)、终态信封，与 A2A v1.0 的 Task 生命周期 + Artifact
签名 + 溯源追踪(Provenance)一一对应——artifact-as-protocol 设计与 A2A 不谋而合。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from openbimagent.orchestrator.contracts import (
    ArtifactRecord,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.runtime import AgentProfile, load_agent_profile

A2A_PROTOCOL_VERSION = "1.0"


class A2ATaskState(StrEnum):
    """A2A v1.0 TaskState（任务生命周期状态机）。"""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# 子代理状态机 → A2A 任务状态机（同构映射；审批门挂起在运行时体现为 input-required）
SUBAGENT_TO_A2A_STATE: dict[SubagentStatus, A2ATaskState] = {
    SubagentStatus.CREATED: A2ATaskState.SUBMITTED,
    SubagentStatus.QUEUED: A2ATaskState.SUBMITTED,
    SubagentStatus.RUNNING: A2ATaskState.WORKING,
    SubagentStatus.COMPLETED: A2ATaskState.COMPLETED,
    SubagentStatus.FAILED: A2ATaskState.FAILED,
    SubagentStatus.CANCELLED: A2ATaskState.CANCELED,
}


def map_subagent_status(status: SubagentStatus) -> A2ATaskState:
    """SubagentStatus → A2A TaskState（未知状态 fail-safe 到 UNKNOWN，不伪造终态）。"""
    return SUBAGENT_TO_A2A_STATE.get(status, A2ATaskState.UNKNOWN)


def agent_card_from_profile(profile: AgentProfile, *, url: str = "") -> dict[str, Any]:
    """从受信任 agents/<role>.md 解析出的 AgentProfile 派生 A2A v1.0 Agent Card。"""
    first_line = ""
    if profile.system_prompt:
        for line in profile.system_prompt.strip().splitlines():
            if line.strip():
                first_line = line.strip()
                break
    description = first_line or profile.name
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": profile.name,
        "description": description[:512],
        "url": url,
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,  # 本项目有 SSE 事件流（core/events.py + server SSE）
            "pushNotifications": False,
            "stateTransitionHistory": True,  # session JSONL 树全程留痕可回放
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "artifact"],
        "skills": [
            {
                "id": profile.name,
                "name": profile.name,
                "description": description[:256],
                "tags": list(profile.tools),
                "examples": [],
                "inputModes": ["text"],
                "outputModes": ["text", "artifact"],
            }
        ],
    }


def agent_card_from_role(
    role: str, *, agents_dir: Path | None = None, url: str = ""
) -> dict[str, Any]:
    """便捷入口：role → load_agent_profile → Agent Card。"""
    profile = (
        load_agent_profile(role, agents_dir) if agents_dir is not None else load_agent_profile(role)
    )
    return agent_card_from_profile(profile, url=url)


def a2a_artifact_from_record(record: ArtifactRecord) -> dict[str, Any]:
    """ArtifactRecord → A2A v1.0 Artifact（版本 + 签名：canonical sha256 即完整性签名）。"""
    return {
        "artifactId": record.artifact_id,
        "name": record.kind,
        "description": f"immutable={record.immutable} status={record.status.value}",
        "parts": [
            {
                "kind": "file",
                "file": {
                    "name": Path(record.path).name,
                    "mimeType": record.media_type or "application/octet-stream",
                    "uri": record.relative_path or record.path,
                },
            }
        ],
        "metadata": {
            "sha256": record.sha256,  # A2A Artifact 签名 ← 本项目 canonical sha256
            "sizeBytes": record.size_bytes,
            "immutable": record.immutable,
            "dependencies": list(record.dependencies),
            "status": record.status.value,
        },
    }


def a2a_task_from_envelope(envelope: SubagentResultEnvelope) -> dict[str, Any]:
    """SubagentResultEnvelope → A2A v1.0 Task（状态机 + artifacts + history + 溯源 metadata）。"""
    state = map_subagent_status(envelope.status)
    task: dict[str, Any] = {
        "id": envelope.request_id,
        "contextId": envelope.lineage_id,  # 血缘 id = A2A 上下文（跨 attempt 关联）
        "status": {
            "state": state.value,
            "timestamp": envelope.ended_at.isoformat(),
        },
        "artifacts": [a2a_artifact_from_record(r) for r in envelope.artifacts],
        "metadata": {
            "agentId": envelope.agent_id,
            "attemptNumber": envelope.attempt_number,
            "receiptId": envelope.receipt_id,  # 幂等回执
            "resumedFromRequestId": envelope.resumed_from_request_id,
            "childSessionPath": envelope.child_session_path,
            "usage": dict(envelope.usage),
        },
    }
    if envelope.error is not None:
        task["status"]["message"] = {
            "role": "agent",
            "parts": [
                {"kind": "text", "text": f"{envelope.error.code}: {envelope.error.message}"}
            ],
        }
    if envelope.summary:
        task["history"] = [
            {"role": "agent", "parts": [{"kind": "text", "text": envelope.summary}]}
        ]
    return task


__all__ = [
    "A2A_PROTOCOL_VERSION",
    "A2ATaskState",
    "SUBAGENT_TO_A2A_STATE",
    "a2a_artifact_from_record",
    "a2a_task_from_envelope",
    "agent_card_from_profile",
    "agent_card_from_role",
    "map_subagent_status",
]
