"""Subagent Runtime v1 P1b 的可变状态存储。

状态与不可变 Artifact 分离，位于 sessions/_runtime。每个 request_id 一个 JSON，写入使用
临时文件 + fsync + os.replace；记录不包含 API key、Authorization header 或 child 原始上下文。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.orchestrator.contracts import (
    SubagentHandle,
    SubagentRequest,
    SubagentResultEnvelope,
    SubagentStatus,
)
from openbimagent.orchestrator.control import ResumeReceipt, ResumeRequest
from openbimagent.session.schema import uuid7

RUNTIME_STATE_VERSION = "1.0"
RuntimePhase = Literal["prepared", "running", "finalizing", "terminal"]


class RuntimeStateRecord(BaseModel):
    """一条可恢复的后台运行记录。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_version: str = Field(default=RUNTIME_STATE_VERSION, pattern=r"^1(?:\.\d+)?$")
    request: SubagentRequest
    handle: SubagentHandle
    status: SubagentStatus
    phase: RuntimePhase
    updated_at: datetime
    result: SubagentResultEnvelope | None = None
    resume_request: ResumeRequest | None = None
    resume_receipt: ResumeReceipt | None = None

    @model_validator(mode="after")
    def _state_is_consistent(self) -> "RuntimeStateRecord":
        if self.handle.request_id != self.request.request_id:
            raise ValueError("handle.request_id 与 request.request_id 不一致")
        if self.handle.status is not self.status:
            raise ValueError("handle.status 与状态记录 status 不一致")
        if self.handle.lineage_id != self.request.lineage_id:
            raise ValueError("handle.lineage_id 与 request.lineage_id 不一致")
        if self.handle.attempt_number != self.request.attempt_number:
            raise ValueError("handle.attempt_number 与 request.attempt_number 不一致")
        if self.handle.resumed_from_request_id != self.request.resumed_from_request_id:
            raise ValueError("handle.resumed_from_request_id 与 request 不一致")
        terminal = self.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED}
        if self.phase in {"finalizing", "terminal"} and self.result is None:
            raise ValueError("finalizing/terminal 状态必须携带 result")
        if self.phase == "terminal" and not terminal:
            raise ValueError("terminal phase 必须是终态 status")
        if (self.resume_request is None) != (self.resume_receipt is None):
            raise ValueError("resume_request 与 resume_receipt 必须同时存在")
        if self.request.attempt_number == 1 and self.resume_request is not None:
            raise ValueError("首个 attempt 不能携带 resume 控制记录")
        if self.request.attempt_number > 1:
            if self.resume_request is None or self.resume_receipt is None:
                raise ValueError("恢复 attempt 必须持久化 resume request/receipt")
            if self.resume_request.new_request_id != self.request.request_id:
                raise ValueError("resume_request.new_request_id 与 request 不一致")
            if self.resume_receipt.new_request_id != self.request.request_id:
                raise ValueError("resume_receipt.new_request_id 与 request 不一致")
            if self.resume_request.resume_id != self.request.resume_id:
                raise ValueError("resume_request.resume_id 与 request 不一致")
            if self.resume_receipt.resume_id != self.request.resume_id:
                raise ValueError("resume_receipt.resume_id 与 request 不一致")
            if self.resume_receipt.new_agent_id != self.handle.agent_id:
                raise ValueError("resume_receipt.new_agent_id 与 handle 不一致")
            if self.resume_receipt.new_child_session_id != self.handle.child_session_id:
                raise ValueError("resume_receipt.new_child_session_id 与 handle 不一致")
        if self.result is not None:
            if self.result.request_id != self.request.request_id:
                raise ValueError("result.request_id 与 request 不一致")
            if self.result.status is not self.status:
                raise ValueError("result.status 与状态记录 status 不一致")
            if self.result.agent_id != self.handle.agent_id:
                raise ValueError("result.agent_id 与 handle.agent_id 不一致")
            if self.result.child_session_id != self.handle.child_session_id:
                raise ValueError("result.child_session_id 与 handle.child_session_id 不一致")
            if self.result.lineage_id != self.request.lineage_id:
                raise ValueError("result.lineage_id 与 request.lineage_id 不一致")
            if self.result.attempt_number != self.request.attempt_number:
                raise ValueError("result.attempt_number 与 request.attempt_number 不一致")
            if self.result.resumed_from_request_id != self.request.resumed_from_request_id:
                raise ValueError("result.resumed_from_request_id 与 request 不一致")
        return self


class RuntimeStateCorruptionError(RuntimeError):
    """状态文件无法读取或不满足 RuntimeStateRecord 契约。"""


class RuntimeLeaseError(RuntimeError):
    """同一 sessions 目录已有活跃 Runtime。"""


class RuntimeLease:
    """Runtime 生命周期独占 lease；进程崩溃时由 OS 自动释放文件锁。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("xb") as initializer:
                initializer.write(b"0")
                initializer.flush()
                os.fsync(initializer.fileno())
        except FileExistsError:
            pass
        self._handle: BinaryIO | None = self.path.open("r+b")
        try:
            self._acquire_nonblocking(self._handle)
        except Exception:
            self._handle.close()
            self._handle = None
            raise

    def _acquire_nonblocking(self, handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeLeaseError(f"已有活跃 Runtime 持有 lease: {self.path}") from exc
            return
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeLeaseError(f"已有活跃 Runtime 持有 lease: {self.path}") from exc

    def release(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


class RuntimeStateStore:
    """按 request_id 持久化后台任务状态；同实例线程安全。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, request_id: str) -> Path:
        safe = Path(request_id).name
        if safe != request_id or not safe:
            raise ValueError(f"无效 request_id: {request_id!r}")
        return self.root / f"{safe}.json"

    def write(
        self,
        *,
        request: SubagentRequest,
        handle: SubagentHandle,
        status: SubagentStatus,
        phase: RuntimePhase,
        result: SubagentResultEnvelope | None = None,
        resume_request: ResumeRequest | None = None,
        resume_receipt: ResumeReceipt | None = None,
    ) -> RuntimeStateRecord:
        record = RuntimeStateRecord(
            request=request,
            handle=handle.model_copy(update={"status": status}),
            status=status,
            phase=phase,
            updated_at=datetime.now(timezone.utc),
            result=result,
            resume_request=resume_request,
            resume_receipt=resume_receipt,
        )
        encoded = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
        path = self.path_for(request.request_id)
        temp = path.with_name(f".{path.name}.{uuid7()}.tmp")
        with self._lock:
            try:
                with temp.open("xb") as file:
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
        return record

    def load(self, request_id: str) -> RuntimeStateRecord:
        path = self.path_for(request_id)
        with self._lock:
            return self._load_path(path)

    def load_all(self) -> tuple[RuntimeStateRecord, ...]:
        records: list[RuntimeStateRecord] = []
        with self._lock:
            paths = sorted(self.root.glob("*.json"))
            for path in paths:
                records.append(self._load_path(path))
        return tuple(records)

    @staticmethod
    def _load_path(path: Path) -> RuntimeStateRecord:
        try:
            return RuntimeStateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeStateCorruptionError(f"Runtime 状态文件损坏: {path}: {exc}") from exc


__all__ = [
    "RUNTIME_STATE_VERSION",
    "RuntimeLease",
    "RuntimeLeaseError",
    "RuntimePhase",
    "RuntimeStateCorruptionError",
    "RuntimeStateRecord",
    "RuntimeStateStore",
]
