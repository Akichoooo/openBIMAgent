"""Subagent Runtime v1 的版本化请求、状态、工件与结果契约。

契约遵循 K3 已定的 artifact-mediated 原则：子代理过程留在 child session，父代理只接收
结构化摘要、工件路径和不超过 200 字的核心提示。所有模型均 extra=forbid，协议漂移必须
在 Schema Gate 阶段失败关闭，不能静默放行。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.session.schema import uuid7

SUBAGENT_PROTOCOL_VERSION = "1.1"
ARTIFACT_MANIFEST_VERSION = "1.0"
MAX_SUBAGENT_HINT_CHARS = 200


class ContextMode(StrEnum):
    """子代理上下文来源；默认 isolated，fork 必须由父代理显式请求。"""

    ISOLATED = "isolated"
    FORK = "fork"


class ExecutionMode(StrEnum):
    """执行方式；P0 仅实现 foreground，background 为 P1 前向兼容字段。"""

    FOREGROUND = "foreground"
    BACKGROUND = "background"


class SubagentStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubagentRequest(BaseModel):
    """父代理提交给受控 Runtime 的 v1 请求；不暴露 model/tools/permissions 扩权字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=SUBAGENT_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    request_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    role: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    task: str = Field(min_length=1, max_length=100_000)
    context_mode: ContextMode = ContextMode.ISOLATED
    execution_mode: ExecutionMode = ExecutionMode.FOREGROUND
    artifact_contract: str = Field(default="summary-v1", min_length=1, max_length=128)
    depth: int = Field(default=0, ge=0, le=0, description="v1 禁止子代理再嵌套派发")
    lineage_id: str = Field(default_factory=lambda: str(uuid7()), min_length=1)
    attempt_number: int = Field(default=1, ge=1)
    resumed_from_request_id: str | None = None
    resume_id: str | None = None

    @model_validator(mode="after")
    def _attempt_lineage_is_consistent(self) -> "SubagentRequest":
        resumed = self.resumed_from_request_id is not None or self.resume_id is not None
        if self.attempt_number == 1 and resumed:
            raise ValueError("首个 attempt 不能携带 resume 关系")
        if self.attempt_number > 1 and (
            self.resumed_from_request_id is None or self.resume_id is None
        ):
            raise ValueError("恢复 attempt 必须携带 resumed_from_request_id 和 resume_id")
        return self

    @classmethod
    def create(
        cls,
        *,
        parent_session_id: str,
        role: str,
        task: str,
        context_mode: ContextMode | str = ContextMode.ISOLATED,
        execution_mode: ExecutionMode | str = ExecutionMode.FOREGROUND,
        artifact_contract: str = "summary-v1",
    ) -> "SubagentRequest":
        return cls(
            request_id=str(uuid7()),
            parent_session_id=parent_session_id,
            role=role,
            task=task,
            context_mode=context_mode,
            execution_mode=execution_mode,
            artifact_contract=artifact_contract,
        )


class SubagentHandle(BaseModel):
    """单个 attempt 的稳定身份；resume 必须创建新 handle，不能复用旧 request_id。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = SUBAGENT_PROTOCOL_VERSION
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    child_session_id: str = Field(min_length=1)
    child_session_path: str = Field(min_length=1)
    status: SubagentStatus
    lineage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    resumed_from_request_id: str | None = None

    @model_validator(mode="after")
    def _attempt_lineage_is_consistent(self) -> "SubagentHandle":
        if self.attempt_number == 1 and self.resumed_from_request_id is not None:
            raise ValueError("首个 attempt handle 不能携带 resumed_from_request_id")
        if self.attempt_number > 1 and self.resumed_from_request_id is None:
            raise ValueError("恢复 attempt handle 必须携带 resumed_from_request_id")
        return self


class ArtifactRecord(BaseModel):
    """不可变工件记录；path 指向 Runtime 管理的稳定副本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    immutable: bool = True


class ArtifactManifest(BaseModel):
    """一次子代理运行的工件清单。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = Field(default=ARTIFACT_MANIFEST_VERSION, pattern=r"^1(?:\.\d+)?$")
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    created_at: datetime
    records: tuple[ArtifactRecord, ...] = ()


class SubagentError(BaseModel):
    """失败结果的结构化错误；不再只把异常塞进 hint。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20_000)
    retryable: bool = False


class SubagentResultEnvelope(BaseModel):
    """Subagent Runtime v1 的终态结果信封。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=SUBAGENT_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    request_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    child_session_id: str = Field(min_length=1)
    child_session_path: str = Field(min_length=1)
    status: SubagentStatus
    summary: str = Field(default="", max_length=100_000)
    hint: str = Field(default="", max_length=MAX_SUBAGENT_HINT_CHARS)
    artifacts: tuple[ArtifactRecord, ...] = ()
    manifest_path: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    usage: dict[str, int | float] = Field(default_factory=dict)
    error: SubagentError | None = None
    receipt_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    resumed_from_request_id: str | None = None

    @model_validator(mode="after")
    def _terminal_state_matches_error(self) -> "SubagentResultEnvelope":
        if self.status not in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED}:
            raise ValueError("结果信封必须是 completed/failed/cancelled 终态")
        if self.status is SubagentStatus.COMPLETED and self.error is not None:
            raise ValueError("completed 结果不能携带 error")
        if self.status in {SubagentStatus.FAILED, SubagentStatus.CANCELLED} and self.error is None:
            raise ValueError("failed/cancelled 结果必须携带结构化 error")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at 不能早于 started_at")
        if self.attempt_number == 1 and self.resumed_from_request_id is not None:
            raise ValueError("首个 attempt result 不能携带 resumed_from_request_id")
        if self.attempt_number > 1 and self.resumed_from_request_id is None:
            raise ValueError("恢复 attempt result 必须携带 resumed_from_request_id")
        return self

    def llm_summary(self) -> str:
        """父代理可见的紧凑视图；不包含 child session 原始过程。"""
        paths = ", ".join(record.path for record in self.artifacts) or "无"
        base = f"{self.status.value}: {self.summary or self.hint}; artifacts={paths}; child_session={self.child_session_path}"
        if self.error is not None:
            base += f"; error={self.error.code}: {self.error.message}"
        return base

    def ui_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "ARTIFACT_MANIFEST_VERSION",
    "MAX_SUBAGENT_HINT_CHARS",
    "SUBAGENT_PROTOCOL_VERSION",
    "ArtifactManifest",
    "ArtifactRecord",
    "ContextMode",
    "ExecutionMode",
    "SubagentError",
    "SubagentHandle",
    "SubagentRequest",
    "SubagentResultEnvelope",
    "SubagentStatus",
]
