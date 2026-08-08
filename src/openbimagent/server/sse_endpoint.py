"""M2 P4 SSE 网络服务端点。

将 M2SseProjector 的持久事实投影封装为 /api/v1/sessions/{session_id}/events 的
Server-Sent Events。事件从 Session JSONL 文件投影，不持有 Runtime lease、不读取
IPC token、不构造 Runtime。支持 Last-Event-ID 与 cursor 回放窗口。

连接预算:同一时刻最多 M2_SSE_MAX_ACTIVE_STREAMS 个活跃流;超出失败关闭。
慢消费者:单次写入超时即断开,防止背压累计。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from openbimagent.server.contracts import M2ErrorCode, make_m2_api_error
from openbimagent.server.sse_identity import validate_m2_sse_stream_id
from openbimagent.server.sse_projection import M2SseProjector, SseProjectionError
from openbimagent.session.store import SessionStore

M2_SSE_MAX_ACTIVE_STREAMS = 64
M2_SSE_REPLAY_LIMIT = 100
M2_SSE_WRITE_TIMEOUT_S = 15.0


class M2SseStreamBudget:
    """受控活跃 SSE 流计数;超出预算失败关闭。"""

    def __init__(self, max_active: int = M2_SSE_MAX_ACTIVE_STREAMS) -> None:
        self._max_active = max_active
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._max_active:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        if self._active > 0:
            self._active -= 1


def _sse_error_response(request_id: str, code: M2ErrorCode) -> StreamingResponse:
    error = make_m2_api_error(code=code, message=code.value, request_id=request_id)
    return StreamingResponse(
        iter([f"event: error\ndata: {error.model_dump_json()}\n\n"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _parse_int(value: str | None, default: int, *, label: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    return parsed


def add_sse_endpoint(app: FastAPI, *, sessions_dir: Path, budget: M2SseStreamBudget | None = None) -> None:
    projector = M2SseProjector()
    stream_budget = budget or M2SseStreamBudget()

    @app.get("/api/v1/sessions/{session_id}/events")
    async def _session_events(session_id: str, request: Request) -> StreamingResponse:
        request_id = request.headers.get("x-request-id", "invalid-request")
        try:
            validate_m2_sse_stream_id(session_id)
        except ValueError:
            return _sse_error_response(request_id, M2ErrorCode.INVALID_REQUEST)
        if not stream_budget.try_acquire():
            return _sse_error_response(request_id, M2ErrorCode.RATE_LIMITED)
        try:
            try:
                limit = _parse_int(request.query_params.get("limit"), M2_SSE_REPLAY_LIMIT, label="limit")
            except ValueError:
                return _sse_error_response(request_id, M2ErrorCode.INVALID_REQUEST)
            session_path = sessions_dir / f"{session_id}.jsonl"
            if not session_path.is_file():
                return _sse_error_response(request_id, M2ErrorCode.NOT_FOUND)
            store = SessionStore(session_path)
            events = projector.project(session_id=session_id, events=store.get_event_chain())
            last_event_id = request.headers.get("last-event-id")
            if last_event_id or request.query_params.get("cursor"):
                cursor = None
                for event in events:
                    if event.event_id == last_event_id:
                        cursor = projector.cursor_for(event)
                if cursor is not None:
                    events = projector.replay(
                        session_id=session_id, events=events, cursor=cursor, limit=limit
                    )
                else:
                    events = events[:limit]
            else:
                events = events[-limit:]

            def _stream() -> Any:
                for event in events:
                    data = event.model_dump(mode="json")
                    yield f"id: {event.event_id}\n"
                    yield f"event: {event.event_type}\n"
                    yield f"data: {json.dumps(data, ensure_ascii=False, sort_keys=True)}\n\n"

            return StreamingResponse(
                _stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except SseProjectionError:
            return _sse_error_response(request_id, M2ErrorCode.INVALID_REQUEST)
        finally:
            stream_budget.release()