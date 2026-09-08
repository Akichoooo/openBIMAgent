"""工作区/项目注册表（ZCode 式「选择项目」后端）。

方案一（注册表 + 会话归属过滤）：工作区 = {id, name, path} 登记项，
落盘 ``out/workspaces.json``（``OPENBIMAGENT_WORKSPACES_FILE`` 可覆盖，测试隔离用）。
会话 index 条目携带 ``workspace`` 字段（``stamp_session_workspace`` 写入；
``SessionStore._sync_index`` 只更新已知键，不会清掉该字段）。
数据仍统一存 repo ``out/``，不做数据根切换。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _registry_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_WORKSPACES_FILE")
    return Path(override) if override else _REPO_ROOT / "out" / "workspaces.json"


def _sessions_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    return Path(override) if override else _REPO_ROOT / "out" / "sessions"


def _load() -> dict[str, Any]:
    path = _registry_path()
    if not path.is_file():
        return {"current": None, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"current": None, "items": []}
    if not isinstance(data, dict):
        return {"current": None, "items": []}
    data.setdefault("current", None)
    data.setdefault("items", [])
    for it in data.get("items", []):
        it.setdefault("execution_mode", "agent")
        it.setdefault("outside_file_access", "allow")
        it.setdefault("terminal_auto_exec", "ask")
        it.setdefault("artifact_review_policy", "proceed")
        it.setdefault("folders", [it.get("path")] if it.get("path") else [])
    return data


def _save(data: dict[str, Any]) -> None:
    from openbimagent.session.store import _atomic_write_json, _locked_index

    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked_index(path):
        _atomic_write_json(path, data)


def current_workspace_id() -> str | None:
    """当前工作区 id（未选择 = None，即「不在项目中工作」）。供 runs.py 建会话时盖章。"""
    current = _load().get("current")
    return str(current) if current else None


def get_workspace_execution_mode(workspace_id: str | None) -> str:
    """获取工作区配置的执行模式（agent/yolo/plan），未配置时默认 agent（审批模式）。"""
    if not workspace_id:
        return "agent"
    data = _load()
    item = next((it for it in data.get("items", []) if it.get("id") == workspace_id), None)
    if item and item.get("execution_mode"):
        return str(item["execution_mode"])
    return "agent"


def stamp_session_workspace(session_id: str, workspace_id: str | None) -> None:
    """把 workspace 归属写进会话 index 条目（条目不存在则先建最小条目）。"""
    if not workspace_id:
        return
    from openbimagent.session.store import INDEX_FILENAME, _atomic_write_json, _locked_index

    index_path = _sessions_dir() / INDEX_FILENAME
    with _locked_index(index_path):
        index: dict[str, Any] = {"sessions": []}
        if index_path.is_file():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                index = {"sessions": []}
        sessions = index.setdefault("sessions", [])
        entry = next((e for e in sessions if e.get("id") == session_id), None)
        if entry is None:
            entry = {"id": session_id}
            sessions.append(entry)
        entry["workspace"] = workspace_id
        _atomic_write_json(index_path, index)


def _session_counts() -> dict[str, int]:
    from openbimagent.session.store import INDEX_FILENAME

    index_path = _sessions_dir() / INDEX_FILENAME
    counts: dict[str, int] = {}
    if not index_path.is_file():
        return counts
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return counts
    for entry in data.get("sessions", []):
        ws = entry.get("workspace")
        if ws:
            counts[str(ws)] = counts.get(str(ws), 0) + 1
    return counts


def register_workspace_routes(app: FastAPI) -> None:
    """注册工作区端点（由 build_m2_readonly_app 调用；变更方法受 token 守卫）。"""

    @app.get("/api/v1/workspaces", summary="工作区注册表（current + 最近项目列表 + 各项目会话数）", tags=["Workbench"])
    async def list_workspaces() -> dict[str, Any]:
        data = _load()
        counts = _session_counts()
        items = sorted(data["items"], key=lambda it: it.get("last_opened", 0), reverse=True)
        return {
            "status": "success",
            "current": data.get("current"),
            "items": [{**it, "session_count": counts.get(str(it.get("id")), 0)} for it in items],
        }

    @app.post("/api/v1/workspaces", summary="登记/创建项目文件夹（path 不存在则创建；同 path 复用 id）", tags=["Workbench"])
    async def create_workspace(request: dict[str, Any]) -> JSONResponse:
        raw_path = str(request.get("path") or "").strip()
        if not raw_path:
            return JSONResponse(status_code=400, content={"status": "error", "error": "缺少文件夹路径 path"})
        folder = Path(raw_path).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": f"文件夹不可用: {exc}"})
        resolved = str(folder.resolve())
        name = str(request.get("name") or "").strip() or folder.name

        data = _load()
        now = time.time()
        item = next((it for it in data["items"] if it.get("path") == resolved), None)
        if item is not None:
            item["name"] = name
            item["last_opened"] = now
        else:
            from openbimagent.session.schema import uuid7

            item = {
                "id": f"ws-{str(uuid7())[:8]}",
                "name": name,
                "path": resolved,
                "created_at": now,
                "last_opened": now,
                "execution_mode": "agent",
                "outside_file_access": "allow",
                "terminal_auto_exec": "ask",
                "artifact_review_policy": "proceed",
                "folders": [resolved],
            }
            data["items"].append(item)
        _save(data)
        return JSONResponse(content={"status": "success", "item": item})

    @app.post("/api/v1/workspaces/current", summary="切换当前工作区（id=null 即「不在项目中工作」）", tags=["Workbench"])
    async def set_current_workspace(request: dict[str, Any]) -> JSONResponse:
        ws_id = request.get("id")
        data = _load()
        if ws_id is None:
            data["current"] = None
            _save(data)
            return JSONResponse(content={"status": "success", "current": None})
        ws_id = str(ws_id)
        item = next((it for it in data["items"] if it.get("id") == ws_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"工作区不存在: {ws_id}"})
        item["last_opened"] = time.time()
        data["current"] = ws_id
        _save(data)
        return JSONResponse(content={"status": "success", "current": ws_id})

    @app.delete("/api/v1/workspaces/{workspace_id}", summary="从注册表移除工作区（不删磁盘目录）", tags=["Workbench"])
    async def delete_workspace(workspace_id: str) -> JSONResponse:
        data = _load()
        before = len(data["items"])
        data["items"] = [it for it in data["items"] if it.get("id") != workspace_id]
        if len(data["items"]) == before:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"工作区不存在: {workspace_id}"})
        if data.get("current") == workspace_id:
            data["current"] = None
        _save(data)
        return JSONResponse(content={"status": "success", "deleted": workspace_id})

    @app.patch("/api/v1/workspaces/{workspace_id}", summary="更新工作区配置、执行模式与安全策略", tags=["Workbench"])
    async def update_workspace(workspace_id: str, request: dict[str, Any]) -> JSONResponse:
        data = _load()
        item = next((it for it in data["items"] if it.get("id") == workspace_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"工作区不存在: {workspace_id}"})
        if "name" in request:
            name = str(request["name"]).strip()
            if name:
                item["name"] = name
        if "execution_mode" in request:
            mode = str(request["execution_mode"]).strip().lower()
            if mode in ("agent", "yolo", "plan"):
                item["execution_mode"] = mode
        if "outside_file_access" in request:
            ofa = str(request["outside_file_access"]).strip().lower()
            if ofa in ("allow", "ask", "deny"):
                item["outside_file_access"] = ofa
        if "terminal_auto_exec" in request:
            tae = str(request["terminal_auto_exec"]).strip().lower()
            if tae in ("proceed", "ask"):
                item["terminal_auto_exec"] = tae
        if "artifact_review_policy" in request:
            arp = str(request["artifact_review_policy"]).strip().lower()
            if arp in ("proceed", "ask"):
                item["artifact_review_policy"] = arp
        if "folders" in request and isinstance(request["folders"], list):
            item["folders"] = [str(f).strip() for f in request["folders"] if str(f).strip()]
        _save(data)
        return JSONResponse(content={"status": "success", "item": item})

