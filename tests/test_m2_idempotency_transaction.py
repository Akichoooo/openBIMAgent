"""M2 pre-G7 持久幂等事务与并发冲突纯函数测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.server.contracts import M2ControlRequest, M2ErrorCode
from openbimagent.server.control_preflight import M2ControlPreflight, M2ControlRole
from openbimagent.server.idempotency_transaction import (
    M2IdempotencyRecord,
    M2IdempotencyRecordState,
    M2IdempotencyTransaction,
    M2IdempotencyTransactionDisposition,
    M2IdempotencyTransactionError,
)

PREFLIGHT = M2ControlPreflight()
TRANSACTION = M2IdempotencyTransaction()
ACTOR = ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN)


def _plan(*, instruction: str = "inspect persisted facts"):
    request = M2ControlRequest(
        operation="attempt.steer",
        resource_id="request-1",
        idempotency_key="steer-key-1",
        instruction=instruction,
    )
    return PREFLIGHT.prepare(actor=ACTOR, role=M2ControlRole.OPERATOR, request=request)


def _reserved(*, reservation_id: str = "reservation-1", revision: int = 1) -> M2IdempotencyRecord:
    plan = _plan()
    return M2IdempotencyRecord(
        state=M2IdempotencyRecordState.RESERVED,
        revision=revision,
        idempotency_scope_sha256=plan.idempotency_scope_sha256,
        semantic_fingerprint=plan.semantic_fingerprint,
        reservation_id=reservation_id,
    )


def test_missing_record_produces_create_only_cas_command() -> None:
    plan = _plan()
    decision = TRANSACTION.reserve(plan=plan, existing=None, reservation_id="reservation-1")

    assert decision.disposition is M2IdempotencyTransactionDisposition.ACQUIRED
    assert decision.receipt_id is None
    assert decision.mutation is not None
    assert decision.mutation.idempotency_scope_sha256 == plan.idempotency_scope_sha256
    assert decision.mutation.expected_revision is None
    assert decision.mutation.replacement.state is M2IdempotencyRecordState.RESERVED
    assert decision.mutation.replacement.revision == 1
    assert decision.mutation.replacement.reservation_id == "reservation-1"
    assert decision.mutation.replacement.receipt_id is None


def test_concurrent_creators_share_expected_absence_and_only_one_cas_can_win() -> None:
    plan = _plan()
    first = TRANSACTION.reserve(plan=plan, existing=None, reservation_id="reservation-1").mutation
    second = TRANSACTION.reserve(plan=plan, existing=None, reservation_id="reservation-2").mutation
    assert first is not None and second is not None
    assert first.expected_revision is second.expected_revision is None

    stored: M2IdempotencyRecord | None = None

    def apply(command) -> bool:
        nonlocal stored
        current_revision = None if stored is None else stored.revision
        if current_revision != command.expected_revision:
            return False
        stored = command.replacement
        return True

    assert apply(first) is True
    assert apply(second) is False
    assert stored is not None
    loser = TRANSACTION.reserve(plan=plan, existing=stored, reservation_id="reservation-2")
    assert loser.disposition is M2IdempotencyTransactionDisposition.IN_PROGRESS
    assert loser.mutation is None
    assert loser.reservation_id == "reservation-1"


def test_same_owner_retry_is_acquired_without_second_mutation() -> None:
    decision = TRANSACTION.reserve(plan=_plan(), existing=_reserved(), reservation_id="reservation-1")
    assert decision.disposition is M2IdempotencyTransactionDisposition.ACQUIRED
    assert decision.reservation_id == "reservation-1"
    assert decision.mutation is None


def test_same_scope_different_semantics_fails_before_reservation() -> None:
    with pytest.raises(M2IdempotencyTransactionError) as exc_info:
        TRANSACTION.reserve(
            plan=_plan(instruction="different control semantics"),
            existing=_reserved(),
            reservation_id="reservation-2",
        )
    assert exc_info.value.code is M2ErrorCode.IDEMPOTENCY_CONFLICT


def test_persisted_scope_mismatch_fails_closed() -> None:
    existing = _reserved().model_copy(update={"idempotency_scope_sha256": "0" * 64})
    with pytest.raises(M2IdempotencyTransactionError) as exc_info:
        TRANSACTION.reserve(plan=_plan(), existing=existing, reservation_id="reservation-2")
    assert exc_info.value.code is M2ErrorCode.CONFLICT


def test_owner_completion_is_revision_checked_and_completed_is_immutable() -> None:
    plan = _plan()
    reserved = _reserved(revision=7)
    decision = TRANSACTION.complete(
        plan=plan,
        existing=reserved,
        reservation_id="reservation-1",
        receipt_id="receipt-1",
    )
    assert decision.disposition is M2IdempotencyTransactionDisposition.COMMITTED
    assert decision.receipt_id == "receipt-1"
    assert decision.mutation is not None
    assert decision.mutation.expected_revision == 7
    completed = decision.mutation.replacement
    assert completed.state is M2IdempotencyRecordState.COMPLETED
    assert completed.revision == 8
    assert completed.receipt_id == "receipt-1"

    replay = TRANSACTION.reserve(plan=plan, existing=completed, reservation_id="reservation-other")
    assert replay.disposition is M2IdempotencyTransactionDisposition.REPLAY
    assert replay.receipt_id == "receipt-1"
    assert replay.mutation is None

    repeated_completion = TRANSACTION.complete(
        plan=plan,
        existing=completed,
        reservation_id="reservation-1",
        receipt_id="receipt-1",
    )
    assert repeated_completion.disposition is M2IdempotencyTransactionDisposition.REPLAY
    assert repeated_completion.mutation is None


def test_stale_or_forged_writer_cannot_complete_reservation() -> None:
    with pytest.raises(M2IdempotencyTransactionError) as exc_info:
        TRANSACTION.complete(
            plan=_plan(),
            existing=_reserved(reservation_id="winner"),
            reservation_id="loser",
            receipt_id="receipt-loser",
        )
    assert exc_info.value.code is M2ErrorCode.CONFLICT


def test_completion_revalidates_receipt_before_emitting_cas_command() -> None:
    with pytest.raises(ValueError):
        TRANSACTION.complete(
            plan=_plan(),
            existing=_reserved(),
            reservation_id="reservation-1",
            receipt_id="receipt with spaces",
        )


def test_completion_requires_existing_reservation_and_same_receipt_on_replay() -> None:
    plan = _plan()
    with pytest.raises(M2IdempotencyTransactionError) as missing:
        TRANSACTION.complete(
            plan=plan,
            existing=None,
            reservation_id="reservation-1",
            receipt_id="receipt-1",
        )
    assert missing.value.code is M2ErrorCode.CONFLICT

    committed = TRANSACTION.complete(
        plan=plan,
        existing=_reserved(),
        reservation_id="reservation-1",
        receipt_id="receipt-1",
    ).mutation
    assert committed is not None
    with pytest.raises(M2IdempotencyTransactionError) as changed:
        TRANSACTION.complete(
            plan=plan,
            existing=committed.replacement,
            reservation_id="reservation-1",
            receipt_id="receipt-2",
        )
    assert changed.value.code is M2ErrorCode.CONFLICT


def test_record_schema_matches_model_and_rejects_semantic_drift() -> None:
    reserved = _reserved()
    payload = reserved.model_dump(mode="json")
    assert validate_artifact("m2_idempotency_record", payload) == []

    payload["receipt_id"] = "receipt-not-allowed"
    assert validate_artifact("m2_idempotency_record", payload)

    completed = TRANSACTION.complete(
        plan=_plan(),
        existing=reserved,
        reservation_id="reservation-1",
        receipt_id="receipt-1",
    ).mutation
    assert completed is not None
    completed_payload = completed.replacement.model_dump(mode="json")
    assert validate_artifact("m2_idempotency_record", completed_payload) == []
    completed_payload["receipt_id"] = None
    assert validate_artifact("m2_idempotency_record", completed_payload)


def test_record_semantics_reject_invalid_state_combinations() -> None:
    plan = _plan()
    common = {
        "idempotency_scope_sha256": plan.idempotency_scope_sha256,
        "semantic_fingerprint": plan.semantic_fingerprint,
        "reservation_id": "reservation-1",
    }
    with pytest.raises(ValueError, match="reserved"):
        M2IdempotencyRecord(
            state=M2IdempotencyRecordState.RESERVED,
            revision=1,
            receipt_id="receipt-not-allowed",
            **common,
        )
    with pytest.raises(ValueError, match="completed"):
        M2IdempotencyRecord(
            state=M2IdempotencyRecordState.COMPLETED,
            revision=2,
            receipt_id=None,
            **common,
        )


def test_transaction_error_maps_to_safe_api_error() -> None:
    error = M2IdempotencyTransactionError(M2ErrorCode.CONFLICT, "幂等 reservation 冲突")
    api_error = error.to_api_error("api-1")
    assert api_error.code is M2ErrorCode.CONFLICT
    assert api_error.request_id == "api-1"
    assert api_error.retryable is False
    assert api_error.details == {}


def test_transaction_has_no_store_runtime_ipc_or_file_side_effects(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    transaction = M2IdempotencyTransaction()
    transaction.reserve(plan=_plan(), existing=None, reservation_id="reservation-1")
    assert tuple(tmp_path.rglob("*")) == before == ()
    for forbidden in (
        "open",
        "save",
        "write",
        "connect",
        "dispatch",
        "start",
        "listen",
        "runtime",
        "ipc_client",
        "store",
    ):
        assert not hasattr(transaction, forbidden)
