"""M2 pre-G7 持久幂等事务与并发冲突的纯函数语义。

该模块只根据调用方注入的当前事实生成 compare-and-swap 命令；不选择或实现持久 store，
不读取文件，不连接 Runtime IPC，不执行控制副作用，也不定义 reservation 超时或接管策略。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.server.contracts import M2ApiError, M2ErrorCode, make_m2_api_error
from openbimagent.server.control_preflight import M2ControlProxyPlan

M2_IDEMPOTENCY_TRANSACTION_VERSION = "0.1"
_ID_PATTERN = r"^[A-Za-z0-9_.:@/-]+$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class M2IdempotencyRecordState(StrEnum):
    """持久幂等事实的最小状态；不存在的记录由 ``None`` 表示。"""

    RESERVED = "reserved"
    COMPLETED = "completed"


class M2IdempotencyTransactionDisposition(StrEnum):
    """纯函数事务判定，不暗示 CAS 已由真实 store 提交。"""

    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    REPLAY = "replay"


class M2IdempotencyTransactionError(ValueError):
    """稳定且不回显控制正文的事务错误。"""

    def __init__(self, code: M2ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)

    def to_api_error(self, request_id: str) -> M2ApiError:
        return make_m2_api_error(
            code=self.code,
            message=str(self),
            request_id=request_id,
        )


class M2IdempotencyRecord(BaseModel):
    """由未来持久 adapter 提供的版本化幂等事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_version: str = Field(default=M2_IDEMPOTENCY_TRANSACTION_VERSION, pattern=r"^0\.1$")
    state: M2IdempotencyRecordState
    revision: int = Field(ge=1)
    idempotency_scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    reservation_id: str = Field(min_length=1, max_length=200, pattern=_ID_PATTERN)
    receipt_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=_ID_PATTERN)

    @model_validator(mode="after")
    def _state_payload_is_exact(self) -> "M2IdempotencyRecord":
        if self.state is M2IdempotencyRecordState.RESERVED and self.receipt_id is not None:
            raise ValueError("reserved 幂等事实不能包含 receipt_id")
        if self.state is M2IdempotencyRecordState.COMPLETED and self.receipt_id is None:
            raise ValueError("completed 幂等事实必须包含 receipt_id")
        return self


class M2IdempotencyCasCommand(BaseModel):
    """交给未来 store adapter 的抽象 CAS 命令；本身不执行写入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_version: str = Field(default=M2_IDEMPOTENCY_TRANSACTION_VERSION, pattern=r"^0\.1$")
    idempotency_scope_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_revision: int | None = Field(default=None, ge=1)
    replacement: M2IdempotencyRecord

    @model_validator(mode="after")
    def _scope_and_revision_are_consistent(self) -> "M2IdempotencyCasCommand":
        if self.replacement.idempotency_scope_sha256 != self.idempotency_scope_sha256:
            raise ValueError("CAS replacement 与幂等域不一致")
        expected_replacement_revision = 1 if self.expected_revision is None else self.expected_revision + 1
        if self.replacement.revision != expected_replacement_revision:
            raise ValueError("CAS replacement revision 必须严格递增")
        return self


class M2IdempotencyTransactionDecision(BaseModel):
    """事务状态机的无副作用输出。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_version: str = Field(default=M2_IDEMPOTENCY_TRANSACTION_VERSION, pattern=r"^0\.1$")
    disposition: M2IdempotencyTransactionDisposition
    reservation_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=_ID_PATTERN)
    receipt_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=_ID_PATTERN)
    mutation: M2IdempotencyCasCommand | None = None

    @model_validator(mode="after")
    def _decision_payload_is_exact(self) -> "M2IdempotencyTransactionDecision":
        if self.disposition is M2IdempotencyTransactionDisposition.IN_PROGRESS:
            if self.reservation_id is None or self.receipt_id is not None or self.mutation is not None:
                raise ValueError("in_progress 必须且只能返回 reservation_id")
        elif self.disposition is M2IdempotencyTransactionDisposition.REPLAY:
            if self.receipt_id is None or self.mutation is not None:
                raise ValueError("replay 必须返回 receipt_id 且不能生成 mutation")
        elif self.disposition is M2IdempotencyTransactionDisposition.COMMITTED:
            if self.receipt_id is None or self.mutation is None:
                raise ValueError("committed 必须返回 receipt_id 和 mutation")
        elif self.reservation_id is None:
            raise ValueError("acquired 必须返回 reservation_id")
        return self


class M2IdempotencyTransaction:
    """不持有缓存、锁、store、文件、网络 client 或 Runtime 的纯函数状态机。"""

    def reserve(
        self,
        *,
        plan: M2ControlProxyPlan,
        existing: M2IdempotencyRecord | None,
        reservation_id: str,
    ) -> M2IdempotencyTransactionDecision:
        if existing is None:
            replacement = M2IdempotencyRecord(
                state=M2IdempotencyRecordState.RESERVED,
                revision=1,
                idempotency_scope_sha256=plan.idempotency_scope_sha256,
                semantic_fingerprint=plan.semantic_fingerprint,
                reservation_id=reservation_id,
            )
            return M2IdempotencyTransactionDecision(
                disposition=M2IdempotencyTransactionDisposition.ACQUIRED,
                reservation_id=reservation_id,
                mutation=M2IdempotencyCasCommand(
                    idempotency_scope_sha256=plan.idempotency_scope_sha256,
                    expected_revision=None,
                    replacement=replacement,
                ),
            )

        self._validate_plan(plan=plan, existing=existing)
        if existing.state is M2IdempotencyRecordState.COMPLETED:
            return M2IdempotencyTransactionDecision(
                disposition=M2IdempotencyTransactionDisposition.REPLAY,
                reservation_id=existing.reservation_id,
                receipt_id=existing.receipt_id,
            )
        if existing.reservation_id == reservation_id:
            return M2IdempotencyTransactionDecision(
                disposition=M2IdempotencyTransactionDisposition.ACQUIRED,
                reservation_id=reservation_id,
            )
        return M2IdempotencyTransactionDecision(
            disposition=M2IdempotencyTransactionDisposition.IN_PROGRESS,
            reservation_id=existing.reservation_id,
        )

    def complete(
        self,
        *,
        plan: M2ControlProxyPlan,
        existing: M2IdempotencyRecord | None,
        reservation_id: str,
        receipt_id: str,
    ) -> M2IdempotencyTransactionDecision:
        if existing is None:
            raise M2IdempotencyTransactionError(
                M2ErrorCode.CONFLICT,
                "幂等完成提交缺少已持久化 reservation",
            )
        self._validate_plan(plan=plan, existing=existing)
        if existing.state is M2IdempotencyRecordState.COMPLETED:
            if existing.receipt_id != receipt_id:
                raise M2IdempotencyTransactionError(
                    M2ErrorCode.CONFLICT,
                    "已完成幂等事实不能替换原 receipt",
                )
            return M2IdempotencyTransactionDecision(
                disposition=M2IdempotencyTransactionDisposition.REPLAY,
                reservation_id=existing.reservation_id,
                receipt_id=existing.receipt_id,
            )
        if existing.reservation_id != reservation_id:
            raise M2IdempotencyTransactionError(
                M2ErrorCode.CONFLICT,
                "当前 writer 不拥有幂等 reservation",
            )

        replacement = M2IdempotencyRecord(
            state=M2IdempotencyRecordState.COMPLETED,
            revision=existing.revision + 1,
            idempotency_scope_sha256=existing.idempotency_scope_sha256,
            semantic_fingerprint=existing.semantic_fingerprint,
            reservation_id=existing.reservation_id,
            receipt_id=receipt_id,
        )
        return M2IdempotencyTransactionDecision(
            disposition=M2IdempotencyTransactionDisposition.COMMITTED,
            reservation_id=reservation_id,
            receipt_id=receipt_id,
            mutation=M2IdempotencyCasCommand(
                idempotency_scope_sha256=plan.idempotency_scope_sha256,
                expected_revision=existing.revision,
                replacement=replacement,
            ),
        )

    @staticmethod
    def _validate_plan(*, plan: M2ControlProxyPlan, existing: M2IdempotencyRecord) -> None:
        if existing.idempotency_scope_sha256 != plan.idempotency_scope_sha256:
            raise M2IdempotencyTransactionError(
                M2ErrorCode.CONFLICT,
                "持久幂等事实与当前控制域不一致",
            )
        if existing.semantic_fingerprint != plan.semantic_fingerprint:
            raise M2IdempotencyTransactionError(
                M2ErrorCode.IDEMPOTENCY_CONFLICT,
                "同一幂等键已用于不同控制语义",
            )


__all__ = [
    "M2_IDEMPOTENCY_TRANSACTION_VERSION",
    "M2IdempotencyCasCommand",
    "M2IdempotencyRecord",
    "M2IdempotencyRecordState",
    "M2IdempotencyTransaction",
    "M2IdempotencyTransactionDecision",
    "M2IdempotencyTransactionDisposition",
    "M2IdempotencyTransactionError",
]
