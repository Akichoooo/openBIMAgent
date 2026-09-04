"""真实 Agent 运行端点：新建任务 → 后台真跑 pipeline → 会话事件可读。

设计约束：
- 有界多并发（默认 2，``OPENBIMAGENT_MAX_CONCURRENT_RUNS`` 可调；409 拒绝超额），
  每个运行独占 ``out/runs/<session_id>/`` 产物目录，互不覆盖；状态经 ``GET /api/v1/runs/active`` 轮询。
- 会话落 ``sessions_dir``（默认 ``out/sessions``，``OPENBIMAGENT_SESSIONS_DIR`` 可覆盖，测试隔离）。
- 离线安全：无 providers registry / 无 CAD 宿主时 pipeline 走确定性模板 + MockCritic（CLAUDE.md 约定路径）。
- Web 运行审批门：触门（execute_code 前 / deliver 前）挂起，待 ``/api/v1/approvals`` 人工决策；超时失败关闭。
- 归档反哺（缺陷一修复）：新任务启动前检索本包归档 Top-3 相似交付，作为**会话首条用户消息**注入
  （In-Context Retrieval 注入会话上下文；读取会话历史的角色可消费，确定性模板路径不消费——如实标注）；
  机制=事件溯源 + In-Context Retrieval，非权重更新（论文表述边界，见 docs/architecture/LIMITATIONS.md）。
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
_runs: dict[str, dict[str, Any]] = {}


def _max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("OPENBIMAGENT_MAX_CONCURRENT_RUNS", "2")))
    except ValueError:
        return 2


def _sessions_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    return Path(override) if override else _DEFAULT_SESSIONS_DIR


def _bigrams(text: str) -> set[str]:
    """CJK 友好的二元 token 集（英文按词、其余按字 bigram），用于归档相似度检索。"""
    tokens: set[str] = set()
    word = ""
    for ch in text.lower():
        if ch.isascii() and ch.isalnum():
            word += ch
            continue
        if word:
            tokens.add(word)
            word = ""
        if not ch.isspace():
            tokens.add(ch)
    if word:
        tokens.add(word)
    return tokens


def _retrieve_exemplars(brief: str, pack: Path, *, top_k: int = 3) -> list[dict[str, Any]]:
    """缺陷一修复（归档反哺）：从本包素材归档检索 Top-K 相似交付作为 in-context 范例。

    评分 = brief token 重合度（Jaccard）+ 时效衰减；无任何向量库/权重更新——
    机制边界=In-Context Retrieval（论文严禁表述为自进化/RL）。
    """
    index_path = _archive_root(pack) / "index.json"
    if not index_path.is_file():
        return []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    query = _bigrams(brief)
    if not query:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, entry in enumerate(entries):
        cand = _bigrams(str(entry.get("brief", "")))
        if not cand or not (query & cand):
            continue  # 零重合不入选（防"无相似也硬凑"）
        jaccard = len(query & cand) / len(query | cand)
        recency = (i + 1) / len(entries) * 0.1  # 近者小幅加权
        scored.append((jaccard + recency, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[:top_k]]


def _execute_run(brief: str, playbook: Path, session_id: str, enriched_context: str | None = None) -> None:
    """后台线程：真跑 assembly pipeline（离线走确定性模板 + MockCritic）。"""
    from openbimagent.assembly.pipeline import run_pipeline

    sessions_dir = _sessions_dir()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out_dir = _REPO_ROOT / "out" / "runs" / session_id  # 缺陷四修复：每运行独占产物目录，互不覆盖
    try:
        # 预建会话并写入标题（index.json 侧边栏数据源），pipeline 复用同一 session 文件
        from openbimagent.session.store import SessionStore

        store = SessionStore(sessions_dir / f"{session_id}.jsonl", title=brief[:60] or session_id, playbook=playbook.parent.name)
        # 缺陷一修复：检索范例作为会话首条用户消息注入（In-Context Retrieval 注入会话上下文；
        # 读取会话历史的角色（clarify 续跑/后续 researcher）可消费；确定性模板路径不消费——如实记录）
        if enriched_context:
            from openbimagent.session.schema import EventType

            store.append_new(EventType.MESSAGE, {"role": "user", "content": enriched_context})
        # Web 审批门：触门即挂起，待前端 /api/v1/approvals 人工决策（撤掉 yes=True 自动放行）
        from openbimagent.server.approvals import make_web_approval_fn

        # 市政主线补 utility_solver_input（pack 内默认输入；否则 domain_gate 因证据缺失 UNKNOWN 阻断）
        solver_input: Path | None = None
        default_input = playbook.parent / "solver_input.default.json"
        if default_input.is_file():
            solver_input = default_input

        run_pipeline(
            playbook_path=playbook,
            out_dir=out_dir,
            sessions_dir=sessions_dir,
            session_id=session_id,
            input_func=lambda _prompt="": "",
            approval_fn=make_web_approval_fn(session_id, sessions_dir),
            utility_solver_input=solver_input,
        )
        _runs[session_id].update(active=False, done_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 — 运行失败必须可视化而非吞掉
        _runs[session_id].update(active=False, done_at=datetime.now(timezone.utc).isoformat(), error=str(exc))
    finally:
        try:
            entry = _archive_run_artifacts(playbook, session_id, brief, out_dir)
            # P0-1 自蒸馏钩子：仅成功交付（有归档工件且无错误）才蒸馏 SKILL.md 候选；
            # 候选落 skills/_candidates/，永不自动生效，须人工批准转正（fail-closed 人工门）
            if entry and not _runs[session_id].get("error"):
                from openbimagent.skills.registry import builtin_skills_root, distill_candidate

                candidate = distill_candidate(
                    builtin_skills_root(),
                    session_id=session_id,
                    brief=brief,
                    playbook=playbook.parent.name,
                    files=[f["name"] for f in entry["files"]],
                    archived_at=entry["archived_at"],
                )
                if candidate is not None:
                    _runs[session_id]["skill_candidate"] = candidate.name
        except Exception:  # noqa: BLE001 — 归档/蒸馏失败不影响运行结论
            pass
        # P1-2 hooks：run_end 观测事件（无论成败必触发）
        try:
            from openbimagent.core.hooks import default_hook_bus

            default_hook_bus().emit(
                "run_end",
                session_id=session_id,
                playbook=playbook.parent.name,
                error=_runs[session_id].get("error") or "",
                done_at=_runs[session_id].get("done_at") or "",
            )
        except Exception:  # noqa: BLE001 — hooks 故障不影响运行结论
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


def _archive_run_artifacts(playbook: Path, session_id: str, brief: str, out_dir: Path) -> dict[str, Any] | None:
    """P2 素材积累：交付工件只增不改地归档进 Domain Pack assets/auto_archive/<session>/。

    设计：纯增量（不写回 knowledge/ 受信任规则）；目录经 .gitignore 忽略；
    index.json 记录每次归档（session/时间/文件清单/sha256），供 researcher 角色后续引用。
    返回本次归档的 index 条目（无工件可归档时返回 None）。
    """
    import hashlib

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
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    index_path = archive_root / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []
    entry = {
        "session_id": session_id,
        "brief": brief[:120],
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "files": copied,
    }
    index.append(entry)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def add_runs(app: FastAPI) -> None:
    """注册真实运行端点（由 build_m2_readonly_app 调用）。"""

    @app.post("/api/v1/runs", summary="新建任务：后台真跑 pipeline（有界多并发；离线模板安全；归档范例反哺）", tags=["Workbench"])
    async def start_run(request: dict[str, Any]) -> JSONResponse:
        brief = str(request.get("brief", "")).strip()
        if not brief:
            return JSONResponse(status_code=400, content={"status": "error", "error": "brief 不能为空"})
        playbook_key = str(request.get("playbook", "municipal_utility"))
        playbook = _PLAYBOOKS.get(playbook_key, _PLAYBOOKS["municipal_utility"])
        if not playbook.is_file():
            return JSONResponse(status_code=500, content={"status": "error", "error": f"playbook 缺失: {playbook}"})
        with _run_lock:
            active_ids = [sid for sid, r in _runs.items() if r["active"]]
            if len(active_ids) >= _max_concurrent():
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "error": f"并发上限 {_max_concurrent()} 已满", "active": active_ids},
                )
            from openbimagent.session.schema import uuid7

            session_id = str(uuid7())
            # 缺陷一修复：检索本包归档 Top-3 相似交付，作为 in-context 范例注入本轮 brief
            exemplars = _retrieve_exemplars(brief, playbook.parent)
            enriched_context = brief
            if exemplars:
                lines = "\n".join(
                    f"{i + 1}. {e.get('brief', '')}（交付于 {str(e.get('archived_at', ''))[:10]}，工件："
                    f"{'、'.join(f['name'] for f in e.get('files', []))}）"
                    for i, e in enumerate(exemplars)
                )
                enriched_context = (
                    f"{brief}\n\n[相似历史交付参考（系统自素材归档检索注入，仅供对齐口径，不得照抄坐标）]\n{lines}"
                )
            # P0-1 渐进披露：技能目录（仅 name/description，不含正文）注入上下文，供规划阶段按需调用
            from openbimagent.skills.registry import default_skill_registry

            skill_fragment = default_skill_registry().catalog_fragment()
            if skill_fragment:
                enriched_context = f"{enriched_context}\n\n{skill_fragment}"
            # P0-4 记忆层：末 N 条长期记忆/用户画像注入（人工审批写入的偏好，非工程证据）
            from openbimagent.core.memory import default_memory_store

            memory_fragment = default_memory_store().prompt_fragment()
            if memory_fragment:
                enriched_context = f"{enriched_context}\n\n{memory_fragment}"
            _runs[session_id] = {
                "active": True,
                "session_id": session_id,
                "brief": brief,
                "playbook": playbook_key,
                "exemplars": [e.get("brief", "") for e in exemplars],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "done_at": None,
                "error": None,
            }
            thread = threading.Thread(
                target=_execute_run, args=(brief, playbook, session_id, enriched_context), daemon=True
            )
            thread.start()
        return JSONResponse(
            content={"status": "success", "session_id": session_id, "playbook": playbook_key, "exemplars_used": len(exemplars)}
        )

    @app.get("/api/v1/runs/active", summary="运行状态（轮询用；runs 全量 + run 兼容字段）", tags=["Workbench"])
    async def run_active() -> dict:
        runs = sorted(_runs.values(), key=lambda r: r["started_at"] or "", reverse=True)
        active = next((r for r in runs if r["active"]), runs[0] if runs else None)
        return {"status": "success", "runs": runs, "run": active, "max_concurrent": _max_concurrent()}

    @app.post("/api/v1/runs/{session_id}/stop", summary="停止运行：拒绝其全部待决审批票据唤醒阻塞线程（在下一个审批门处中止，不杀线程）", tags=["Workbench"])
    async def stop_run(session_id: str) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        run = _runs.get(safe)
        if run is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"运行不存在: {safe}"})
        if not run.get("active"):
            return JSONResponse(status_code=409, content={"status": "error", "error": "运行已结束，无需停止"})
        from openbimagent.server.approvals import reject_pending_for_session

        run["stop_requested"] = True
        woken = reject_pending_for_session(safe)
        return JSONResponse(
            content={
                "status": "success",
                "session_id": safe,
                "woken_approvals": woken,
                "note": "已拒绝该运行全部待决票据；线程将在当前审批门处按拒绝路径退出（确定性求解中段不打断，语义安全）",
            }
        )

    @app.get("/api/v1/sessions/{session_id}/export", summary="导出会话（fmt=jsonl 原始事件流 / fmt=md 可读纪要）", tags=["Workbench"])
    async def export_session(session_id: str, fmt: str = "jsonl"):
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        from fastapi.responses import Response

        raw = path.read_bytes()
        if fmt == "jsonl":
            return Response(
                content=raw,
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="session-{safe[:8]}.jsonl"'},
            )
        if fmt == "md":
            lines = [f"# 会话导出 {safe}", ""]
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = e.get("payload") or {}
                ts = str(e.get("timestamp", ""))[:19].replace("T", " ")
                if e.get("type") == "message":
                    role = "用户" if p.get("role") == "user" else "Agent"
                    lines.append(f"**{role}**（{ts}）：{p.get('content', '')}\n")
                elif e.get("type") == "tool_call":
                    lines.append(f"- `{p.get('toolName', 'tool')}`（{ts}）：{str(p.get('result_ui_view') or p.get('args_summary') or '')[:200]}")
                elif e.get("type") == "custom":
                    lines.append(f"- ◆ {p.get('customType', 'custom')}（{ts}）")
            return Response(
                content="\n".join(lines).encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="session-{safe[:8]}.md"'},
            )
        return JSONResponse(status_code=400, content={"status": "error", "error": "fmt 仅支持 jsonl / md"})

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

    @app.delete("/api/v1/sessions/{session_id}", summary="删除会话（JSONL + index 条目 + FTS 索引行一致性清理；运行中 409）", tags=["Workbench"])
    async def delete_session(session_id: str) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        if safe in _runs and _runs[safe].get("active"):
            return JSONResponse(status_code=409, content={"status": "error", "error": "会话所属运行仍在进行，不能删除（先等收敛或拒绝审批门）"})
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        try:
            path.unlink()
            # index.json 条目移除（与 SessionStore 同一把索引锁 + 原子写）
            from openbimagent.session.store import INDEX_FILENAME, _atomic_write_json, _locked_index

            index_path = _sessions_dir() / INDEX_FILENAME
            with _locked_index(index_path):
                if index_path.is_file():
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    index["sessions"] = [e for e in index.get("sessions", []) if e.get("id") != safe]
                    _atomic_write_json(index_path, index)
            # FTS 索引行清理（删过的会话不再被 /recall 命中）
            from openbimagent.session.search import default_search_index

            default_search_index(_sessions_dir()).delete_session(safe)
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"删除失败: {exc}"})
        return JSONResponse(content={"status": "success", "deleted": safe})

    @app.get("/api/v1/sessions/search", summary="会话全文检索（FTS5；返回可溯源 session/event/snippet）", tags=["Workbench"])
    async def sessions_search(q: str, limit: int = 10) -> JSONResponse:
        from openbimagent.session.search import default_search_index

        index = default_search_index(_sessions_dir())
        index.sync()
        return JSONResponse(content={"status": "success", "query": q, "items": index.search(q, limit=limit)})

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
                run = _runs.get(safe)
                is_active = bool(run and run["active"])
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

    @app.patch("/api/v1/sessions/{session_id}", summary="更新会话（重命名/归档/解归档）", tags=["Workbench"])
    async def rename_session(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        index_path = _sessions_dir() / "index.json"
        if not index_path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": "会话索引不存在"})
        from openbimagent.session.store import _atomic_write_json, _locked_index

        new_title = request.get("title")
        archived = request.get("archived")

        if new_title is None and archived is None:
            return JSONResponse(status_code=400, content={"status": "error", "error": "缺少更新参数(title 或 archived)"})

        with _locked_index(index_path):
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                target = None
                for s in data.get("sessions", []):
                    if s.get("id") == safe:
                        target = s
                        break
                if not target:
                    return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})

                if new_title is not None:
                    title_str = str(new_title).strip()
                    if not title_str:
                        return JSONResponse(status_code=400, content={"status": "error", "error": "标题不能为空"})
                    target["title"] = title_str

                if archived is not None:
                    is_archived = bool(archived)
                    target["archived"] = is_archived
                    if is_archived:
                        target["archived_at"] = datetime.now(timezone.utc).isoformat()
                    else:
                        target["archived"] = False
                        target.pop("archived_at", None)

                _atomic_write_json(index_path, data)
            except Exception as exc:
                return JSONResponse(status_code=500, content={"status": "error", "error": f"更新会话失败: {exc}"})
        return JSONResponse(
            content={
                "status": "success",
                "session_id": safe,
                "title": target.get("title", ""),
                "archived": target.get("archived", False),
            }
        )

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

    @app.get(
        "/api/v1/runs/artifact",
        summary="运行工件读取（白名单 + sha256/mtime；缺陷六修复：视口流式生长的数据源）",
        tags=["Workbench"],
    )
    async def run_artifact(session: str, name: str) -> JSONResponse:
        safe_session = "".join(c for c in session if c.isalnum() or c in "-_")
        if name not in _ARCHIVE_FILES:
            return JSONResponse(status_code=400, content={"status": "error", "error": f"工件名不在白名单: {name}"})
        path = _REPO_ROOT / "out" / "runs" / safe_session / name
        if not path.is_file():
            for playbook in _PLAYBOOKS.values():
                cand = _archive_root(playbook.parent) / safe_session / name
                if cand.is_file():
                    path = cand
                    break
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"工件不存在: {safe_session}/{name}"})
        import hashlib

        try:
            data = path.read_bytes()
            payload: dict[str, Any] = {
                "status": "success",
                "session": safe_session,
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime": path.stat().st_mtime,
                "size": len(data),
            }
            if name.endswith(".json"):
                payload["data"] = json.loads(data.decode("utf-8"))
            return JSONResponse(content=payload)
        except (OSError, json.JSONDecodeError) as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"工件读取失败: {exc}"})
