"""工作台对话端点：composer 普通文本 → 真实 LLM 循环（P0-2）。

设计（对照主流 agent 的对话主循环，与「新建任务」pipeline 路径解耦）：
- 端点 ``POST /api/v1/chat``，body ``{message, session_id?}``；属变更方法，天然被 workbench token 守卫。
- 模型 = 基线模型（``llm_baseline.local.toml``，与设置页「设为基线」同一数据源）；
  缺 key/缺 base_url 时 fail-closed 返回 422 引导语，绝不假成功。
- 会话持久化：给定 ``session_id`` 时，用户消息与助手回复成对落 Session JSONL
  （与 pipeline 共用同一事件源——SSE/轮询/导出/检索全部复用，不另起存储）。
- 上下文 = 同会话最近 N 条 message 事件 + 系统提示（BIM 工程助手人设）。
- LLM 调用走 providers.dialects（openai-completions 方言，与 registry 同一底座），
  外层套一次有界重试（429/5xx/瞬时网络错误），不做降级链（对话不吞错，直接可见）。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from openbimagent.providers.dialects import Dialect, DialectError, chat as dialect_chat, reasoning_payload, stream_openai_completions

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BASELINE = _REPO_ROOT / "config" / "llm_baseline.local.toml"
_PLACEHOLDER_PREFIX = "sk-replace-me"

#: 同会话取多少条历史 message 作为上下文（防 context 爆炸；足够维持多轮指代）
_HISTORY_LIMIT = 20
#: 请求超时（秒）——与基线评测配置 request_timeout_s 缺省一致
_TIMEOUT_S = 60
#: 有界重试（429/5xx/瞬时网络）；对话路径不静默降级，失败直接回显
_RETRY_MAX = 2
_RETRY_BASE_MS = 800

_SYSTEM_PROMPT = (
    "你是 openBIMAgent 数字化工程工作台的对话助手，专注市政管网（GB 50289-2016）与 BIM 生成式设计。"
    "回答保持工程口径：涉及净距、覆土、坡度、管径时给出规范条文依据；"
    "需要真实求解、CAD 写盘交付或多步 pipeline 时，引导用户点击「新任务」发起运行（含审批门），"
    "而不是口头模拟执行结果。中文简洁作答。"
)

_baseline_lock = threading.Lock()


def _baseline_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_LLM_BASELINE")
    return Path(override) if override else _DEFAULT_BASELINE


def _read_baseline() -> dict[str, Any]:
    import tomllib

    path = _baseline_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sanitize_no_proxy() -> None:
    for k in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(k)
        if val:
            cleaned = ",".join(p.strip() for p in val.split(",") if not p.strip().startswith("::"))
            os.environ[k] = cleaned

_sanitize_no_proxy()

def _resolve_llm() -> tuple[str, str, str] | None:
    """(model, base_url, api_key)；若 baseline 为空则回退至首个已启用的自定义供应商。"""
    _sanitize_no_proxy()
    raw = _read_baseline()
    model = str(raw.get("model", "")).strip()
    base_url = str(raw.get("base_url", "")).strip().rstrip("/")
    api_key = str(raw.get("api_key", "")).strip()
    if api_key.startswith(_PLACEHOLDER_PREFIX):
        api_key = ""
    if model and base_url and api_key:
        return model, base_url, api_key

    # 回退至自定义供应商列表
    try:
        from openbimagent.server.workbench_io import _read_custom_providers
        for cp in _read_custom_providers():
            if cp.get("enabled", True) and cp.get("base_url") and cp.get("api_key"):
                b_url = str(cp.get("base_url", "")).strip().rstrip("/")
                a_key = str(cp.get("api_key", "")).strip()
                models = cp.get("models") or []
                for m in models:
                    m_name = m.get("name") or m.get("id")
                    if m_name:
                        return str(m_name), b_url, a_key
    except Exception:
        pass
    return None


def _session_history(session_id: str) -> list[dict[str, str]]:
    """读同会话最近 N 条 message 事件 → OpenAI messages 形态（无会话返回空）。"""
    from openbimagent.session.schema import EventType
    from openbimagent.session.store import SessionStore

    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    sessions_dir = Path(override) if override else _REPO_ROOT / "out" / "sessions"
    path = sessions_dir / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    try:
        store = SessionStore(path)
        messages: list[dict[str, str]] = []
        for event in store.load():
            if event.type != EventType.MESSAGE:
                continue
            payload = event.payload
            if isinstance(payload, dict):  # 原始 dict 形态（旁路写入）
                role = str(payload.get("role", ""))
                content = str(payload.get("content", ""))
            else:  # MessagePayload pydantic 模型（.get 会 AttributeError，被 except 吞成空历史）
                role = str(getattr(payload, "role", ""))
                content = str(getattr(payload, "content", ""))
            if role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})
        return messages[-_HISTORY_LIMIT:]
    except Exception:
        return []


def _append_message(session_id: str, role: str, content: str) -> None:
    """向会话 JSONL 追加一条 message 事件（事件溯源，只增不改）。"""
    from openbimagent.session.schema import EventType
    from openbimagent.session.store import SessionStore

    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    sessions_dir = Path(override) if override else _REPO_ROOT / "out" / "sessions"
    path = sessions_dir / f"{session_id}.jsonl"
    if not path.is_file():
        return  # 会话不存在：不隐式新建（新建走 /api/v1/runs），仅本次返回不落盘
    store = SessionStore(path)
    store.append_new(EventType.MESSAGE, {"role": role, "content": content})


def _call_llm(model: str, base_url: str, api_key: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """单次方言调用 + 有界重试（429/5xx/瞬时网络）；4xx 不重试（鉴权/参数错直接见错）。"""
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            return dialect_chat(
                Dialect.OPENAI_COMPLETIONS,
                model=model,
                messages=messages,
                base_url=base_url,
                api_key=api_key,
                timeout_s=_TIMEOUT_S,
            )
        except Exception as exc:
            last_exc = exc
            retryable = isinstance(exc, DialectError) and _is_retryable(exc)
            if not retryable or attempt == _RETRY_MAX - 1:
                raise
            time.sleep(_RETRY_BASE_MS / 1000 * (2**attempt))
    raise last_exc  # pragma: no cover - 防御性


def _is_retryable(exc: Exception) -> bool:
    """429/5xx/网络层瞬时故障才重试；与 registry._is_retryable 同口径（对话路径不复用熔断器）。"""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _extract_text(completion: dict[str, Any]) -> str:
    """OpenAI chat.completion 形态 → 文本（choices[0].message.content；缺字段如实报空）。"""
    try:
        content = completion["choices"][0]["message"]["content"]
        return str(content or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def add_chat(app: FastAPI) -> None:
    """注册 POST /api/v1/chat（由 build_demo_app 调用；变更方法，受 token 守卫）。"""

    @app.post(
        "/api/v1/chat",
        summary="对话主循环：composer 普通文本 → 基线 LLM 真实回复（会话事件成对落盘）",
        tags=["Workbench"],
    )
    async def workbench_chat(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体须为 JSON"})
        message = str(body.get("message", "")).strip()
        session_id = str(body.get("session_id", "") or "").strip()
        session_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
        if not message:
            return JSONResponse(status_code=400, content={"status": "error", "error": "消息不能为空"})
        if len(message) > 8000:
            return JSONResponse(status_code=413, content={"status": "error", "error": "消息过长（>8000 字符）"})

        resolved = _resolve_llm()
        if resolved is None:
            return JSONResponse(
                status_code=422,
                content={
                    "status": "error",
                    "error": "未配置基线 LLM：请到「设置 → 模型设置」选择供应商、填写 API Key 并设为基线模型",
                    "hint": "openSettingsAt('models')",
                },
            )
        model, base_url, api_key = resolved

        history = _session_history(session_id) if session_id else []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": message},
        ]
        try:
            _t0 = time.monotonic()
            completion = _call_llm(model, base_url, api_key, messages)
            latency_ms = int((time.monotonic() - _t0) * 1000)
        except Exception as exc:
            detail = str(exc)
            if "401" in detail or "Unauthorized" in detail:
                detail = "鉴权未通过（HTTP 401）：请检查该供应商的 API Key"
            return JSONResponse(
                status_code=502,
                content={"status": "error", "error": f"上游模型调用失败：{detail[:300]}"},
            )
        reply = _extract_text(completion)
        if not reply:
            return JSONResponse(
                status_code=502,
                content={"status": "error", "error": "模型返回为空（choices 缺失或 content 为空），请稍后重试或换模型"},
            )

        if session_id:
            _append_message(session_id, "user", message)
            _append_message(session_id, "assistant", reply)

        usage = completion.get("usage") or {}
        # 真实调用入用量流水账（append-only out/usage_log.jsonl；设置页「用量」仪表盘数据源）
        try:
            from openbimagent.server.usage_ledger import record_call

            record_call(
                model=model,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                source="chat",
                session_id=session_id or None,
                latency_ms=latency_ms,
            )
        except Exception:
            pass  # 记账失败不影响对话主流程
        return JSONResponse(
            content={
                "status": "success",
                "reply": reply,
                "model": model,
                "session_id": session_id or None,
                "persisted": bool(session_id),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "latency_ms": latency_ms,
                },
            }
        )


def _sse(event: str, data: dict[str, Any]) -> str:
    """SSE 帧：event + JSON data（前端 fetch 流按 \n\n 分帧解析）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _friendly_stream_error(exc: Exception) -> str:
    """上游异常 → 对话可读口径（与非流式 /chat 的 502 语义一致）。"""
    detail = str(exc)
    if "401" in detail or "Unauthorized" in detail:
        detail = "鉴权未通过（HTTP 401）：请检查该供应商的 API Key"
    return f"上游模型调用失败：{detail[:300]}"


def add_chat_stream(app: FastAPI) -> None:
    """注册 POST /api/v1/chat/stream（真流式 SSE；由 build_demo_app 调用，受 token 守卫）。

    对照主流 agent（ZCode/Codex/OpenAI playground）的流式口径：
    - 上游走 ``stream_openai_completions`` 逐行消费，逐 delta 下发 ``event: delta``；
      reasoning 模型的思维链走 ``event: reasoning``（前端思考态展示，不落会话）。
    - 仅在**尚未产出任何正文**时的瞬时故障（429/5xx/网络）才有界重试；正文一旦开始
      流出就不再重试（半截内容不能重复投递）。
    - 中断（用户点停止/关页面）：cancel_event 置位即断上游，已生成的部分照常成对落
      Session JSONL（对齐 Codex/ZCode 中断语义——已见内容不丢）。
    - 事件序列：meta → (reasoning|delta)* → usage? → done；异常路径以 error 事件收尾。
    """

    @app.post(
        "/api/v1/chat/stream",
        summary="对话流式端点：逐字下发正文/思维链分片（SSE），结束成对落盘并记账",
        tags=["Workbench"],
        response_model=None,  # 返回 StreamingResponse | JSONResponse 二态：响应模型交给实际返回对象
    )
    async def workbench_chat_stream(request: Request):
        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体须为 JSON"})
        message = str(body.get("message", "")).strip()
        session_id = str(body.get("session_id", "") or "").strip()
        session_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
        effort = str(body.get("effort", "") or "").strip()  # 统一思考档位(off/low/medium/high/max)
        mode = str(body.get("mode", "") or "").strip()  # 权限模式(plan只读/agent审批/yolo自主)
        if not mode and session_id:
            try:
                from openbimagent.session.store import INDEX_FILENAME
                from openbimagent.server.workspaces import _sessions_dir, get_workspace_execution_mode
                import json
                index_path = _sessions_dir() / INDEX_FILENAME
                if index_path.is_file():
                    idx_data = json.loads(index_path.read_text(encoding="utf-8"))
                    s_entry = next((e for e in idx_data.get("sessions", []) if e.get("id") == session_id), None)
                    if s_entry and s_entry.get("workspace"):
                        mode = get_workspace_execution_mode(s_entry["workspace"])
            except Exception:
                pass
        if not mode:
            mode = "agent"
        if mode != "agent":
            from openbimagent.audit import log_audit

            log_audit("mode_set", {"mode": mode, "session": session_id or "(new)"})
        if not message:
            return JSONResponse(status_code=400, content={"status": "error", "error": "消息不能为空"})
        if len(message) > 8000:
            return JSONResponse(status_code=413, content={"status": "error", "error": "消息过长（>8000 字符）"})
        resolved = _resolve_llm()
        if resolved is None:
            return JSONResponse(
                status_code=422,
                content={
                    "status": "error",
                    "error": "未配置基线 LLM：请到「设置 → 模型设置」选择供应商、填写 API Key 并设为基线模型",
                    "hint": "openSettingsAt('models')",
                },
            )
        model, base_url, api_key = resolved
        history = _session_history(session_id) if session_id else []
        sys_prompt = _SYSTEM_PROMPT
        if mode == "plan":
            sys_prompt += "\n[只读模式] 仅输出方案/计划/分析,禁止调用任何写工具或修改操作。"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": sys_prompt},
            *history,
            {"role": "user", "content": message},
        ]

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        cancel = threading.Event()
        t0 = time.monotonic()
        state: dict[str, Any] = {"content": [], "usage": {}, "error": None, "persisted": False}

        def _put(item: dict[str, Any] | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def _worker() -> None:
            """消费上游 SSE：转发分片进 asyncio 队列；finally 统一落盘+记账+收尾哨兵。"""
            saw_delta = False
            try:
                for attempt in range(_RETRY_MAX):
                    try:
                        for ev in stream_openai_completions(
                            model=model,
                            messages=messages,
                            base_url=base_url,
                            api_key=api_key,
                            timeout_s=_TIMEOUT_S,
                            cancel_event=cancel,
                            extra_params=reasoning_payload(model, effort) if effort else None,
                        ):
                            if cancel.is_set():
                                break
                            kind = ev.get("type")
                            if kind == "delta":
                                saw_delta = True
                                state["content"].append(ev["text"])
                                _put(ev)
                            elif kind == "reasoning":
                                _put(ev)
                            elif kind == "usage":
                                state["usage"] = ev.get("usage") or {}
                        break  # 流正常结束
                    except Exception as exc:  # noqa: BLE001 — 错误口径统一在 SSE error 事件
                        if saw_delta or not _is_retryable(exc) or attempt == _RETRY_MAX - 1:
                            state["error"] = exc
                            break
                        time.sleep(_RETRY_BASE_MS / 1000 * (2**attempt))
            finally:
                # 落盘与记账收敛到 worker 单点（正常/中断/异常共用一条路径，杜绝双写）：
                reply = "".join(state["content"]).strip()
                if reply and session_id and state["error"] is None:
                    try:
                        _append_message(session_id, "user", message)
                        _append_message(session_id, "assistant", reply)
                        state["persisted"] = True
                    except Exception:
                        pass
                if reply:
                    usage = state["usage"] or {}
                    try:
                        from openbimagent.server.usage_ledger import record_call

                        record_call(
                            model=model,
                            prompt_tokens=usage.get("prompt_tokens"),
                            completion_tokens=usage.get("completion_tokens"),
                            total_tokens=usage.get("total_tokens"),
                            source="chat",
                            session_id=session_id or None,
                            latency_ms=int((time.monotonic() - t0) * 1000),
                        )
                    except Exception:
                        pass
                _put(None)  # 收尾哨兵

        threading.Thread(target=_worker, daemon=True).start()

        async def _gen():
            yield _sse("meta", {"model": model, "session_id": session_id or None})
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if item.get("type") == "delta":
                        yield _sse("delta", {"text": item["text"]})
                    elif item.get("type") == "reasoning":
                        yield _sse("reasoning", {"text": item["text"]})
                if state["error"] is not None:
                    yield _sse("error", {"error": _friendly_stream_error(state["error"])})
                    return
                reply = "".join(state["content"]).strip()
                if not reply:
                    yield _sse("error", {"error": "模型返回为空（未收到任何正文分片），请稍后重试或换模型"})
                    return
                usage = state["usage"] or {}
                yield _sse(
                    "usage",
                    {
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                    },
                )
                yield _sse("done", {"session_id": session_id or None, "persisted": state["persisted"]})
            except (GeneratorExit, asyncio.CancelledError):
                # 客户端断开：置 cancel 让 worker 关上游连接；落盘由 worker finally 兜底
                cancel.set()
                raise

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
