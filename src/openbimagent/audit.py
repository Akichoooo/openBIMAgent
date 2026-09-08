"""审计日志:安全相关操作留痕(actor/action/detail/result),append-only JSONL。

记录:mode 切换 / 只读拦截 / rewind / fork / 审批决策 / run 启动停止 / 命令执行等。
查询:GET /api/v1/audit?tail=N。绝不记录 api key 与敏感内容;审计失败静默不阻断主流程。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AUDIT_PATH = Path(__file__).resolve().parents[2] / "out" / "audit.jsonl"
_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_audit(
    action: str,
    detail: dict[str, Any] | None = None,
    result: str = "ok",
    actor: str = "web-operator",
) -> None:
    """追加一条审计记录;OSError 静默(审计不阻断主流程)。"""
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _utc_now(), "actor": actor, "action": action, "detail": detail or {}, "result": result}
        with _lock:
            with _AUDIT_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_audit(tail: int = 100) -> list[dict[str, Any]]:
    """读最近 tail 条审计记录(倒序,最新在前)。"""
    if not _AUDIT_PATH.is_file():
        return []
    lines = _AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for ln in lines[-tail:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))