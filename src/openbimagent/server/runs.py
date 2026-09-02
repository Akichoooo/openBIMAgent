"""真实 Agent 运行端点：新建任务 → 后台真跑 pipeline → 会话事件可读。

设计约束：
- 单并发运行锁（pipeline 为重型任务；409 拒绝并发新任务），状态经 ``GET /api/v1/runs/active`` 轮询。
- 会话落 ``sessions_dir``（默认 ``out/sessions``，``OPENBIMAGENT_SESSIONS_DIR`` 可覆盖，测试隔离）。
- 离线安全：无 providers registry / 无 CAD 宿主时 pipeline 走确定性模板 + MockCritic（CLAUDE.md 约定路径）。
- Web 运行审批门：触门（execute_code 前 / deliver 前）挂起，待 ``/api/v1/approvals`` 人工决策；超时失败关闭。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

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
        # Web 审批门：触门即挂起，待前端 /api/v1/approvals 人工决策（撤掉 yes=True 自动放行）
        from openbimagent.server.approvals import make_web_approval_fn

        # 市政主线补 utility_solver_input（pack 内默认输入；否则 domain_gate 因证据缺失 UNKNOWN 阻断）
        solver_input: Path | None = None
        default_input = playbook.parent / "solver_input.default.json"
        if default_input.is_file():
            solver_input = default_input

        run_pipeline(
            playbook_path=playbook,
            out_dir=_REPO_ROOT / "out",
            sessions_dir=sessions_dir,
            session_id=session_id,
            input_func=lambda _prompt="": "",
            approval_fn=make_web_approval_fn(session_id, sessions_dir),
            utility_solver_input=solver_input,
        )
        _run_state.update(active=False, done_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 — 运行失败必须可视化而非吞掉
        _run_state.update(active=False, done_at=datetime.now(timezone.utc).isoformat(), error=str(exc))
    finally:
        try:
            _archive_run_artifacts(playbook, session_id, brief)
        except Exception:  # noqa: BLE001 — 归档失败不影响运行结论
            pass


#: 运行结束后归档的关键工件名（存在才拷，缺省跳过）
_ARCHIVE_FILES = (
    "artifact_manifest.json",
    "compiled_utility_ir.json",
    "municipal_rule_set.json",
    "domain_gate_report.json",
    "rule_evidence_bundle.json",
    "domain_gate_report.md",
    "PLAN.md",
)


def _archive_root(pack: Path) -> Path:
    """归档根目录：OPENBIMAGENT_ARCHIVE_DIR 覆盖（测试沙箱）→ <root>/<pack>/；缺省 <pack>/assets/auto_archive/。"""
    override = os.environ.get("OPENBIMAGENT_ARCHIVE_DIR")
    if override:
        return Path(override) / pack.name
    return pack / "assets" / "auto_archive"


def _archive_run_artifacts(playbook: Path, session_id: str, brief: str) -> None:
    """P2 素材积累：交付工件只增不改地归档进 Domain Pack assets/auto_archive/<session>/。

    设计：纯增量（不写回 knowledge/ 受信任规则）；目录经 .gitignore 忽略；
    index.json 记录每次归档（session/时间/文件清单/sha256），供 researcher 角色后续引用。
    """
    import hashlib

    out_dir = _REPO_ROOT / "out"
    pack = playbook.parent
    archive_root = _archive_root(pack)
    dest_dir = archive_root / session_id
    copied: list[dict[str, Any]] = []
    for name in _ARCHIVE_FILES:
        src = out_dir / name
        if not src.is_file():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        (dest_dir / name).write_bytes(data)
        copied.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if not copied:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    index_path = archive_root / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []
    index.append(
        {
            "session_id": session_id,
            "brief": brief[:120],
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "files": copied,
        }
    )
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


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

    @app.get(
        "/api/v1/sessions/{session_id}/events/stream",
        summary="会话事件 SSE 实时跟随（P1：回放后持续推送新增，运行结束自动关闭）",
        tags=["Workbench"],
    )
    async def session_events_stream(session_id: str):
        import asyncio
        import time

        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})

        async def _follow() -> Any:
            sent = 0
            deadline = time.monotonic() + 600  # 10 分钟上限，防悬挂连接泄漏
            while True:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                for line in lines[sent:]:
                    if line.strip():
                        yield f"data: {line}\n\n"
                sent = len(lines)
                is_active = _run_state["active"] and _run_state["session_id"] == safe
                if not is_active or time.monotonic() > deadline:
                    # 活动结束：最后 drain 一次再关闭
                    try:
                        lines = path.read_text(encoding="utf-8").splitlines()
                    except OSError:
                        lines = []
                    for line in lines[sent:]:
                        if line.strip():
                            yield f"data: {line}\n\n"
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(0.6)

        return StreamingResponse(
            _follow(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/v1/sessions/{session_id}/fork", summary="会话分支（/tree fork：from_event_id 缺省取当前头）", tags=["Workbench"])
    async def fork_session(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        from openbimagent.session.store import SessionStore

        try:
            store = SessionStore(path)
            from_event_id = str(request.get("from_event_id") or "").strip()
            title = request.get("title")
            title = title if isinstance(title, str) else None
            if from_event_id:
                new_store = store.fork(from_event_id, title=title)
            else:
                chain = store.get_event_chain()
                if not chain:
                    return JSONResponse(status_code=400, content={"status": "error", "error": "空会话无事件可分支"})
                new_store = store.branch(chain[-1].id, title=title)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"分支失败: {exc}"})
        return JSONResponse(content={"status": "success", "session_id": new_store.session_id, "forked_from": safe})

    @app.get("/api/v1/archive", summary="Domain Pack 素材归档索引（P2：只增不改）", tags=["Workbench"])
    async def list_archive() -> dict:
        items: list[dict[str, Any]] = []
        for playbook in {p for p in _PLAYBOOKS.values()}:
            index_path = _archive_root(playbook.parent) / "index.json"
            if not index_path.is_file():
                continue
            try:
                entries = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in entries:
                items.append({"pack": playbook.parent.name, **entry})
        items.sort(key=lambda e: e.get("archived_at", ""), reverse=True)
        return {"status": "success", "items": items, "count": len(items)}
