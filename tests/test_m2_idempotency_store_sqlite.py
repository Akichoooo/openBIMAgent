"""G3 测试:SQLite 持久幂等 store(M2IdempotencyStore 协议的正式 adapter)。

- CAS 语义:首插(expected=None)、冲突不覆盖、revision 匹配才更新、失败返回观察事实;
- 事务状态机联动:reserve → complete → replay 全链路经真持久事实源;
- 跨实例持久:重开 db 文件记录仍在;
- 损坏记录 fail-loud(M2IdempotencyStoreError),不静默重建。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openbimagent.server.control_preflight import M2ControlProxyPlan
from openbimagent.server.idempotency_store_sqlite import (
    M2IdempotencyStoreError,
    M2SqliteIdempotencyStore,
)
from openbimagent.server.idempotency_transaction import (
    M2IdempotencyCasCommand,
    M2IdempotencyRecord,
    M2IdempotencyRecordState,
    M2IdempotencyTransaction,
    M2IdempotencyTransactionDisposition,
)

_SCOPE = hashlib.sha256(b"scope-1").hexdigest()
_FINGERPRINT = hashlib.sha256(b"semantic-1").hexdigest()


def _reserved_record(revision: int = 1, reservation_id: str = "resv-1") -> M2IdempotencyRecord:
    return M2IdempotencyRecord(
        state=M2IdempotencyRecordState.RESERVED,
        revision=revision,
        idempotency_scope_sha256=_SCOPE,
        semantic_fingerprint=_FINGERPRINT,
        reservation_id=reservation_id,
    )


def test_cas_insert_update_and_conflict(tmp_path: Path) -> None:
    store = M2SqliteIdempotencyStore(tmp_path / "idem.db")
    # 首插:expected_revision=None 表示"必须不存在"
    first = store.compare_and_swap(
        M2IdempotencyCasCommand(idempotency_scope_sha256=_SCOPE, expected_revision=None, replacement=_reserved_record())
    )
    assert first.applied and first.observed_revision == 1
    # 重复首插:冲突,不覆盖,返回观察事实
    dup = store.compare_and_swap(
        M2IdempotencyCasCommand(idempotency_scope_sha256=_SCOPE, expected_revision=None, replacement=_reserved_record(revision=1, reservation_id="resv-2"))
    )
    assert not dup.applied and dup.observed_revision == 1 and dup.record.reservation_id == "resv-1"
    # 期望 revision 不匹配:拒绝更新
    stale = store.compare_and_swap(
        M2IdempotencyCasCommand(
            idempotency_scope_sha256=_SCOPE,
            expected_revision=7,
            replacement=_reserved_record(revision=8),
        )
    )
    assert not stale.applied and stale.observed_revision == 1
    # 期望 revision 匹配:更新到 completed
    completed = M2IdempotencyRecord(
        state=M2IdempotencyRecordState.COMPLETED,
        revision=2,
        idempotency_scope_sha256=_SCOPE,
        semantic_fingerprint=_FINGERPRINT,
        reservation_id="resv-1",
        receipt_id="receipt-1",
    )
    update = store.compare_and_swap(
        M2IdempotencyCasCommand(idempotency_scope_sha256=_SCOPE, expected_revision=1, replacement=completed)
    )
    assert update.applied and update.observed_revision == 2 and update.record.receipt_id == "receipt-1"
    assert store.read(_SCOPE).receipt_id == "receipt-1"
    store.close()


def test_transaction_state_machine_roundtrip(tmp_path: Path) -> None:
    from openbimagent.orchestrator.actor import ActorRef, ActorType

    plan = M2ControlProxyPlan(
        actor=ActorRef(actor_id="actor-1", actor_type=ActorType.HUMAN),
        role="operator",
        operation="attempt.cancel",
        resource_id="res-1",
        idempotency_key="idem-key-1",
        idempotency_scope_sha256=_SCOPE,
        semantic_fingerprint=_FINGERPRINT,
        ipc_operation="attempt.cancel",
        ipc_payload={},
    )
    store = M2SqliteIdempotencyStore(tmp_path / "tx.db")
    tx = M2IdempotencyTransaction()

    def _apply(decision) -> None:
        if decision.mutation is not None:
            result = store.compare_and_swap(decision.mutation)
            assert result.applied, "状态机给出的 mutation 必须可提交"

    # 1) 首次 reserve → ACQUIRED(落 RESERVED)
    d1 = tx.reserve(plan=plan, existing=store.read(_SCOPE), reservation_id="resv-A")
    assert d1.disposition is M2IdempotencyTransactionDisposition.ACQUIRED
    _apply(d1)
    # 2) 同一 reservation 重放 reserve → ACQUIRED(无 mutation)
    d2 = tx.reserve(plan=plan, existing=store.read(_SCOPE), reservation_id="resv-A")
    assert d2.disposition is M2IdempotencyTransactionDisposition.ACQUIRED and d2.mutation is None
    # 3) 不同 writer → IN_PROGRESS
    d3 = tx.reserve(plan=plan, existing=store.read(_SCOPE), reservation_id="resv-B")
    assert d3.disposition is M2IdempotencyTransactionDisposition.IN_PROGRESS
    assert d3.reservation_id == "resv-A"
    # 4) complete → COMMITTED(落 COMPLETED)
    d4 = tx.complete(plan=plan, existing=store.read(_SCOPE), reservation_id="resv-A", receipt_id="receipt-9")
    assert d4.disposition is M2IdempotencyTransactionDisposition.COMMITTED
    _apply(d4)
    # 5) 完成后重放 → REPLAY(原 receipt)
    d5 = tx.complete(plan=plan, existing=store.read(_SCOPE), reservation_id="resv-A", receipt_id="receipt-9")
    assert d5.disposition is M2IdempotencyTransactionDisposition.REPLAY
    assert d5.receipt_id == "receipt-9"
    store.close()


def test_persistence_across_instances(tmp_path: Path) -> None:
    store = M2SqliteIdempotencyStore(tmp_path / "persist.db")
    store.compare_and_swap(
        M2IdempotencyCasCommand(idempotency_scope_sha256=_SCOPE, expected_revision=None, replacement=_reserved_record())
    )
    store.close()
    reopened = M2SqliteIdempotencyStore(tmp_path / "persist.db")
    record = reopened.read(_SCOPE)
    assert record is not None and record.reservation_id == "resv-1" and record.revision == 1
    reopened.close()


def test_corrupt_record_fails_loud(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "corrupt.db"
    store = M2SqliteIdempotencyStore(db)
    store.compare_and_swap(
        M2IdempotencyCasCommand(idempotency_scope_sha256=_SCOPE, expected_revision=None, replacement=_reserved_record())
    )
    store.close()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE idempotency_records SET record_json = 'not-json' WHERE idempotency_scope_sha256 = ?",
        (_SCOPE,),
    )
    conn.commit()
    conn.close()
    reopened = M2SqliteIdempotencyStore(db)
    with pytest.raises(M2IdempotencyStoreError, match="损坏"):
        reopened.read(_SCOPE)
    reopened.close()
