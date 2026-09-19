"""G3:M2IdempotencyStore 协议的 SQLite 持久 adapter。

idempotency_transaction.py 只定义纯函数语义与本端口;本模块提供正式持久实现:
- 记录以 scope_sha256 为主键存 SQLite 单表(record JSON + revision 列);
- compare_and_swap 用 BEGIN IMMEDIATE 事务做真 CAS:期望 revision 匹配才落盘,
  失败返回观察到的当前事实(applied=False),绝不静默覆盖;
- WAL 模式 + 进程内锁;跨进程由 SQLite 文件锁保证串行提交;
- 读取/写入损坏时抛 M2IdempotencyStoreError(fail-loud,不做静默重建)。

注:HTTP 控制面(control endpoint)接入本 adapter 仍为后续工作;本模块先落地
可独立测试的持久事实源(openapi 边界的 idempotency_store_implemented 以
HTTP 接线完成为准)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from openbimagent.server.idempotency_transaction import (
    M2IdempotencyCasCommand,
    M2IdempotencyCasResult,
    M2IdempotencyRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_scope_sha256 TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    record_json TEXT NOT NULL
)
"""


class M2IdempotencyStoreError(RuntimeError):
    """持久 store 读写失败(损坏/IO);fail-loud,不静默重建。"""


class M2SqliteIdempotencyStore:
    """M2IdempotencyStore 协议的 SQLite 实现(单文件、无外部服务依赖)。"""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- M2IdempotencyStore 协议 ----------

    def read(self, idempotency_scope_sha256: str) -> M2IdempotencyRecord | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT record_json FROM idempotency_records WHERE idempotency_scope_sha256 = ?",
                    (idempotency_scope_sha256,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise M2IdempotencyStoreError(f"幂等记录读取失败: {exc}") from exc
        if row is None:
            return None
        try:
            return M2IdempotencyRecord.model_validate(json.loads(row[0]))
        except (json.JSONDecodeError, ValueError) as exc:
            raise M2IdempotencyStoreError(
                f"幂等记录损坏 scope={idempotency_scope_sha256[:12]}: {exc}"
            ) from exc

    def compare_and_swap(self, command: M2IdempotencyCasCommand) -> M2IdempotencyCasResult:
        """真 CAS:BEGIN IMMEDIATE 下核对期望 revision,匹配才提交;失败返回观察事实。"""
        scope = command.idempotency_scope_sha256
        replacement = command.replacement
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT revision, record_json FROM idempotency_records WHERE idempotency_scope_sha256 = ?",
                    (scope,),
                ).fetchone()
                if row is None:
                    if command.expected_revision is not None:
                        self._conn.execute("ROLLBACK")
                        return M2IdempotencyCasResult(
                            applied=False,
                            idempotency_scope_sha256=scope,
                            observed_revision=None,
                            record=None,
                        )
                    self._conn.execute(
                        "INSERT INTO idempotency_records (idempotency_scope_sha256, revision, record_json) VALUES (?, ?, ?)",
                        (scope, replacement.revision, replacement.model_dump_json()),
                    )
                    self._conn.execute("COMMIT")
                    return M2IdempotencyCasResult(
                        applied=True,
                        idempotency_scope_sha256=scope,
                        observed_revision=replacement.revision,
                        record=replacement,
                    )
                current_revision = int(row[0])
                try:
                    current_record = M2IdempotencyRecord.model_validate(json.loads(row[1]))
                except (json.JSONDecodeError, ValueError) as exc:
                    self._conn.execute("ROLLBACK")
                    raise M2IdempotencyStoreError(f"幂等记录损坏 scope={scope[:12]}: {exc}") from exc
                if command.expected_revision != current_revision:
                    self._conn.execute("ROLLBACK")
                    return M2IdempotencyCasResult(
                        applied=False,
                        idempotency_scope_sha256=scope,
                        observed_revision=current_revision,
                        record=current_record,
                    )
                self._conn.execute(
                    "UPDATE idempotency_records SET revision = ?, record_json = ? WHERE idempotency_scope_sha256 = ?",
                    (replacement.revision, replacement.model_dump_json(), scope),
                )
                self._conn.execute("COMMIT")
                return M2IdempotencyCasResult(
                    applied=True,
                    idempotency_scope_sha256=scope,
                    observed_revision=replacement.revision,
                    record=replacement,
                )
            except sqlite3.Error as exc:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise M2IdempotencyStoreError(f"幂等 CAS 失败: {exc}") from exc


__all__ = ["M2IdempotencyStoreError", "M2SqliteIdempotencyStore"]
