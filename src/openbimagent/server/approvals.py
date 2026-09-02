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
        _append_session_event(
            sessions_dir,
            session_id,
            "approval_requested",
            {"approval_id": ticket_id, "operation": operation, "params": params, "requested_at": ticket["requested_at"]},
        )
        decided = ticket["event"].wait(timeout=_timeout_s())
        with _lock:
            _pending.pop(ticket_id, None)
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
            },
        )
        return ticket["decision"] == "approved"

    return approve


def add_approvals(app: FastAPI) -> None:
    """注册审批中心端点（由 build_m2_readonly_app 调用）。"""

    @app.get("/api/v1/approvals", summary="待决审批票据列表（前端轮询）", tags=["Workbench"])
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
                }
                for t in _pending.values()
            ]
        return {"status": "success", "items": items, "count": len(items)}

    @app.post("/api/v1/approvals/{ticket_id}/decide", summary="审批决策（approved/rejected；写决策回执）", tags=["Workbench"])
    async def decide_approval(ticket_id: str, request: dict[str, Any]) -> JSONResponse:
        decision = str(request.get("decision", "")).strip().lower()
        if decision not in ("approved", "rejected"):
            return JSONResponse(status_code=400, content={"status": "error", "error": "decision 必须是 approved/rejected"})
        actor = str(request.get("actor", "human:web-operator"))
        with _lock:
            ticket = _pending.get(ticket_id)
        if ticket is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"票据不存在或已决策: {ticket_id}"})
        ticket["decision"] = decision
        ticket["actor"] = actor
        ticket["event"].set()
        return JSONResponse(content={"status": "success", "id": ticket_id, "decision": decision})
