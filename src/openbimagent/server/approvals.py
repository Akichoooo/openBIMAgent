"""Web 审批中心：pipeline 审批门 ↔ 前端人工决策（撤掉 yes=True 自动放行）。

工作机理：
- pipeline 触达审批门（execute_code 前 / deliver 前）→ ``approval_fn`` 创建票据并**阻塞运行线程**；
- 前端轮询 ``GET /api/v1/approvals`` 拿到待决票据 → 人工批准/拒绝 → ``POST …/decide`` 放行线程；
- 请求与决策均写 Session JSONL（``approval_requested`` / ``approval_decided``，对齐 schemas/decision_receipt）；
- 超时（默认 30 分钟）**失败关闭**：视为拒绝（与策略门 fail-closed 语义一致）。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TIMEOUT_S = 1800.0

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pending_file() -> Path:
    override = os.environ.get("OPENBIMAGENT_PENDING_APPROVALS")
    return Path(override) if override else _REPO_ROOT / "out" / "pending_approvals.json"


def _persist_locked() -> None:
    """票据落盘（缺陷四修复：进程重启后前端仍可见并显式作废，不再无声死任务）。调用时须持 _lock。"""
    try:
        payload = [
            {
                "id": t["id"],
                "session_id": t["session_id"],
                "operation": t["operation"],
                "params": t["params"],
                "requested_at": t["requested_at"],
            }
            for t in _pending.values()
        ]
        path = _pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # 持久化失败不阻断审批主链路


def _load_pending() -> None:
    """启动时装载未决票据为 expired 形态：可列表可见、可显式作废（410），不可放行（运行线程已死）。"""
    path = _pending_file()
    if not path.is_file():
        return
    try:
        entries = __import__("json").loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    with _lock:
        for entry in entries:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            _pending[entry["id"]] = {
                **entry,
                "event": threading.Event(),
                "decision": "expired",
                "decided_at": None,
                "actor": None,
                "_mono": time.monotonic(),
                "expired": True,
            }


def _timeout_s() -> float:
    try:
        return float(os.environ.get("OPENBIMAGENT_APPROVAL_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def _append_session_event(sessions_dir: Path, session_id: str, custom_type: str, fields: dict[str, Any]) -> None:
    """审批事件落 Session JSONL（损坏/并发失败不阻断审批主链路）。"""
    try:
        from openbimagent.session.schema import EventType
        from openbimagent.session.store import SessionStore

        store = SessionStore(sessions_dir / f"{session_id}.jsonl")
        store.append_new(EventType.CUSTOM, {"customType": custom_type, **fields})
    except Exception:  # noqa: BLE001 — 事件落盘失败不影响审批决策本身
        pass


def make_web_approval_fn(session_id: str, sessions_dir: Path):
    """构造阻塞式 Web 审批门：挂起运行线程直到前端 decide 或超时（失败关闭）。"""

    def approve(operation: str, params: dict[str, Any]) -> bool:
        from openbimagent.session.schema import uuid7

        ticket_id = str(uuid7())
        ticket = {
            "id": ticket_id,
            "session_id": session_id,
            "operation": operation,
            "params": params,
            "requested_at": _utcnow(),
            "event": threading.Event(),
            "decision": None,
            "decided_at": None,
            "actor": None,
            "_mono": time.monotonic(),
        }
        with _lock:
            _pending[ticket_id] = ticket
            _persist_locked()
        _append_session_event(
            sessions_dir,
            session_id,
            "approval_requested",
            {"approval_id": ticket_id, "operation": operation, "params": params, "requested_at": ticket["requested_at"]},
        )
        decided = ticket["event"].wait(timeout=_timeout_s())
        with _lock:
            _pending.pop(ticket_id, None)
            _persist_locked()
        if not decided or ticket["decision"] is None:
            ticket["decision"] = "timeout"
        ticket["decided_at"] = _utcnow()
        _append_session_event(
            sessions_dir,
            session_id,
            "approval_decided",
            {
                "approval_id": ticket_id,
                "operation": operation,
                "decision": ticket["decision"],
                "actor": ticket["actor"],
                "decided_at": ticket["decided_at"],
                **({"instruction": ticket["instruction"]} if ticket.get("instruction") else {}),
            },
        )
        return ticket["decision"] == "approved"

    return approve


def reject_pending_for_session(session_id: str, *, actor: str = "system:stop") -> int:
    """停止运行用：把该会话所有待决票据标记拒绝并唤醒阻塞线程（pipeline 在审批门处中止）。返回唤醒数。"""
    woken = 0
    with _lock:
        tickets = [t for t in _pending.values() if t["session_id"] == session_id and not t.get("expired")]
        for ticket in tickets:
            ticket["decision"] = "rejected"
            ticket["actor"] = actor
            ticket["event"].set()
            woken += 1
    return woken


def add_approvals(app: FastAPI) -> None:
    """注册审批中心端点（由 build_m2_readonly_app 调用）。"""
    _load_pending()

    @app.get("/api/v1/approvals", summary="待决审批票据列表（前端轮询；expired=重启遗留，可显式作废）", tags=["Workbench"])
    async def list_approvals() -> dict:
        with _lock:
            items = [
                {
                    "id": t["id"],
                    "session_id": t["session_id"],
                    "operation": t["operation"],
                    "params": t["params"],
                    "requested_at": t["requested_at"],
                    "waiting_s": round(time.monotonic() - t["_mono"], 1),
                    "expired": bool(t.get("expired")),
                }
                for t in _pending.values()
            ]
        return {"status": "success", "items": items, "count": len(items)}

    @app.post("/api/v1/approvals/{ticket_id}/decide", summary="审批决策（approved/rejected；写决策回执；expired 票据 410）", tags=["Workbench"])
    async def decide_approval(ticket_id: str, request: dict[str, Any]) -> JSONResponse:
        decision = str(request.get("decision", "")).strip().lower()
        if decision not in ("approved", "rejected"):
            return JSONResponse(status_code=400, content={"status": "error", "error": "decision 必须是 approved/rejected"})
        actor = str(request.get("actor", "human:web-operator"))
        with _lock:
            ticket = _pending.get(ticket_id)
            if ticket is None:
                return JSONResponse(status_code=404, content={"status": "error", "error": f"票据不存在或已决策: {ticket_id}"})
            if ticket.get("expired"):
                # 重启遗留票据：运行线程已死，显式作废并从注册表与磁盘清除（不得放行）
                if decision == "rejected":
                    _pending.pop(ticket_id, None)
                    _persist_locked()
                    return JSONResponse(content={"status": "success", "id": ticket_id, "decision": "expired_discarded"})
                return JSONResponse(
                    status_code=410,
                    content={"status": "error", "error": "票据已过期（进程重启后运行线程不可恢复），请拒绝以作废"},
                )
        ticket["decision"] = decision
        ticket["actor"] = actor
        instruction = request.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            ticket["instruction"] = instruction.strip()
        ticket["event"].set()
        return JSONResponse(content={"status": "success", "id": ticket_id, "decision": decision})
