"""真实 Agent 运行端点：新建任务 → 后台真跑 pipeline → 会话事件可读。

设计约束：
- 单并发运行锁（pipeline 为重型任务；409 拒绝并发新任务），状态经 ``GET /api/v1/runs/active`` 轮询。
- 会话落 ``sessions_dir``（默认 ``out/sessions``，``OPENBIMAGENT_SESSIONS_DIR`` 可覆盖，测试隔离）。
- 离线安全：无 providers registry / 无 CAD 宿主时 pipeline 走确定性模板 + MockCritic（CLAUDE.md 约定路径）。
- Web 运行暂不接入交互式审批：``yes=True`` 自动放行；C5 交付门仍只接受 manifest 提交的产物。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SESSIONS_DIR = _REPO_ROOT / "out" / "sessions"
_PLAYBOOKS = {
    "municipal_utility": _REPO_ROOT / "domain_packs" / "municipal_utility" / "playbook.md",
    "single_asset_hero": _REPO_ROOT / "domain_packs" / "single_asset_hero" / "playbook.md",
}

_run_lock = threading.Lock()
_run_state: dict[str, Any] = {
    "active": False,
    "session_id": None,
    "brief": None,
    "started_at": None,
    "done_at": None,
    "error": None,
}


def _sessions_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    return Path(override) if override else _DEFAULT_SESSIONS_DIR


def _execute_run(brief: str, playbook: Path, session_id: str) -> None:
    """后台线程：真跑 assembly pipeline（离线走确定性模板 + MockCritic）。"""
    from openbimagent.assembly.pipeline import run_pipeline

    sessions_dir = _sessions_dir()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 预建会话并写入标题（index.json 侧边栏数据源），pipeline 复用同一 session 文件
        from openbimagent.session.store import SessionStore

        SessionStore(sessions_dir / f"{session_id}.jsonl", title=brief[:60] or session_id)
        run_pipeline(
            playbook_path=playbook,
            out_dir=_REPO_ROOT / "out",
            sessions_dir=sessions_dir,
            session_id=session_id,
            input_func=lambda _prompt="": "",
            yes=True,
        )
        _run_state.update(active=False, done_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 — 运行失败必须可视化而非吞掉
        _run_state.update(active=False, done_at=datetime.now(timezone.utc).isoformat(), error=str(exc))


def add_runs(app: FastAPI) -> None:
    """注册真实运行端点（由 build_m2_readonly_app 调用）。"""

    @app.post("/api/v1/runs", summary="新建任务：后台真跑 pipeline（单并发；离线模板安全）", tags=["Workbench"])
    async def start_run(request: dict[str, Any]) -> JSONResponse:
        brief = str(request.get("brief", "")).strip()
        if not brief:
            return JSONResponse(status_code=400, content={"status": "error", "error": "brief 不能为空"})
        playbook_key = str(request.get("playbook", "municipal_utility"))
        playbook = _PLAYBOOKS.get(playbook_key, _PLAYBOOKS["municipal_utility"])
        if not playbook.is_file():
            return JSONResponse(status_code=500, content={"status": "error", "error": f"playbook 缺失: {playbook}"})
        with _run_lock:
            if _run_state["active"]:
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "error": "已有运行中的任务", "session_id": _run_state["session_id"]},
                )
            from openbimagent.session.schema import uuid7

            session_id = str(uuid7())
            _run_state.update(
                active=True,
                session_id=session_id,
                brief=brief,
                started_at=datetime.now(timezone.utc).isoformat(),
                done_at=None,
                error=None,
            )
            thread = threading.Thread(target=_execute_run, args=(brief, playbook, session_id), daemon=True)
            thread.start()
        return JSONResponse(content={"status": "success", "session_id": session_id, "playbook": playbook_key})

    @app.get("/api/v1/runs/active", summary="当前运行状态（轮询用）", tags=["Workbench"])
    async def run_active() -> dict:
        return {"status": "success", "run": dict(_run_state)}

    @app.get("/api/v1/sessions/{session_id}/events", summary="读取会话事件（Session JSONL，倒序截尾）", tags=["Workbench"])
    async def session_events(session_id: str, tail: int = 200) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 损坏行容错：跳过（与 SessionStore 语义一致）
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"读取失败: {exc}"})
        return JSONResponse(content={"status": "success", "session_id": safe, "events": events[-max(1, min(tail, 1000)):]})
