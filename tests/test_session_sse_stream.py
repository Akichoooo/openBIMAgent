"""会话事件 SSE 跟随端点（GET /api/v1/sessions/{id}/events/stream）测试。

覆盖：?token= 查询参数认证（EventSource 无法设自定义头）、X-Request-ID 豁免
仅作用于该路由、header 认证不回退、offset 增量跟随、Last-Event-ID/cursor 恢复、
rewind 截断自愈与并发预算 429。全部离线，会话文件落在 tmp_path。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-wb-token"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", TOKEN)
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "baseline.toml"))
    monkeypatch.setenv("OPENBIMAGENT_CUSTOM_PROVIDERS", str(tmp_path / "providers.json"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    from openbimagent.server.fastapi_app import build_demo_app

    with TestClient(build_demo_app()) as test_client:
        yield test_client, tmp_path


def _write_session(tmp_path: Path, session_id: str, events: list[dict]) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def _events(count: int, prefix: str = "e") -> list[dict]:
    return [
        {"id": f"{prefix}{i}", "type": "message", "payload": {"role": "user", "content": f"第{i}条"}}
        for i in range(1, count + 1)
    ]


def _frames(text: str) -> list[tuple[str | None, str]]:
    """SSE 文本 → [(id, data)]（仅含 data 帧；keepalive 注释忽略）。"""
    out: list[tuple[str | None, str]] = []
    for block in text.split("\n\n"):
        frame_id: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("id: "):
                frame_id = line[len("id: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if data is not None:
            out.append((frame_id, data))
    return out


# ── 认证：?token= 仅对 SSE stream GET 生效 ─────────────────────────────


def test_query_token_accepted_without_any_headers(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(2))
    resp = test_client.get(f"/api/v1/sessions/sess-auth/events/stream?token={TOKEN}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _frames(resp.text)
    assert [json.loads(data)["id"] for _, data in frames] == ["e1", "e2"]


def test_query_token_wrong_rejected(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    resp = test_client.get("/api/v1/sessions/sess-auth/events/stream?token=wrong-token")
    assert resp.status_code == 401
    assert TOKEN not in resp.text


def test_query_token_missing_rejected(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    assert test_client.get("/api/v1/sessions/sess-auth/events/stream").status_code == 401


def test_query_token_duplicate_rejected(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    resp = test_client.get(f"/api/v1/sessions/sess-auth/events/stream?token={TOKEN}&token={TOKEN}")
    assert resp.status_code == 401


def test_query_token_not_accepted_on_other_read_routes(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    # 轮询端点维持原严格语义：query token 不豁免 Bearer
    assert test_client.get(f"/api/v1/sessions/sess-auth/events?token={TOKEN}").status_code == 401
    assert test_client.get(f"/api/v1/runs/active?token={TOKEN}").status_code == 401


def test_x_request_id_waiver_scoped_to_stream_route(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    headers = {"Authorization": f"Bearer {TOKEN}"}  # 无 X-Request-ID
    assert test_client.get("/api/v1/sessions/sess-auth/events/stream", headers=headers).status_code == 200
    assert test_client.get("/api/v1/sessions/sess-auth/events", headers=headers).status_code == 400


def test_header_auth_still_works_and_echoes_request_id(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Request-ID": "test-sse-rid"}
    resp = test_client.get("/api/v1/sessions/sess-auth/events/stream", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "test-sse-rid"


def test_wrong_bearer_rejected_on_stream(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-auth", _events(1))
    headers = {"Authorization": "Bearer wrong-token", "X-Request-ID": "test-sse-rid"}
    assert test_client.get("/api/v1/sessions/sess-auth/events/stream", headers=headers).status_code == 401


# ── 流语义：回放、恢复、截断自愈、预算 ─────────────────────────────────


def test_stream_replay_ids_are_consumable_offsets(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-replay", _events(3))
    resp = test_client.get(f"/api/v1/sessions/sess-replay/events/stream?token={TOKEN}")
    assert resp.status_code == 200
    frames = _frames(resp.text)
    assert len(frames) == 3
    offsets = [int(frame_id) for frame_id, _ in frames]
    assert all(frame_id is not None for frame_id, _ in frames)
    assert offsets == sorted(offsets) and len(set(offsets)) == 3
    assert all(frame_id != "0" for frame_id, _ in frames)


def test_last_event_id_resumes_after_offset(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-resume", _events(3))
    first = test_client.get(f"/api/v1/sessions/sess-resume/events/stream?token={TOKEN}")
    frames = _frames(first.text)
    second_id = frames[1][0]
    assert second_id is not None
    resumed = test_client.get(
        f"/api/v1/sessions/sess-resume/events/stream?token={TOKEN}",
        headers={"Last-Event-ID": second_id},
    )
    assert resumed.status_code == 200
    assert [json.loads(data)["id"] for _, data in _frames(resumed.text)] == ["e3"]


def test_cursor_query_param_resumes(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-cursor", _events(3))
    first = test_client.get(f"/api/v1/sessions/sess-cursor/events/stream?token={TOKEN}")
    first_id = _frames(first.text)[0][0]
    resumed = test_client.get(f"/api/v1/sessions/sess-cursor/events/stream?token={TOKEN}&cursor={first_id}")
    assert [json.loads(data)["id"] for _, data in _frames(resumed.text)] == ["e2", "e3"]


def test_cursor_beyond_file_size_replays_all(client) -> None:
    """rewind 截断后旧 offset 超出文件大小：自愈回退全量重放。"""
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-trunc", _events(1))
    resp = test_client.get(
        f"/api/v1/sessions/sess-trunc/events/stream?token={TOKEN}",
        headers={"Last-Event-ID": "999999"},
    )
    assert resp.status_code == 200
    assert [json.loads(data)["id"] for _, data in _frames(resp.text)] == ["e1"]


def test_invalid_cursor_replays_all(client) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-invalid", _events(2))
    resp = test_client.get(
        f"/api/v1/sessions/sess-invalid/events/stream?token={TOKEN}",
        headers={"Last-Event-ID": "not-an-offset"},
    )
    assert resp.status_code == 200
    assert len(_frames(resp.text)) == 2


def test_unknown_session_404(client) -> None:
    test_client, _ = client
    resp = test_client.get(f"/api/v1/sessions/no-such-session/events/stream?token={TOKEN}")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_stream_budget_exceeded_429(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, tmp_path = client
    _write_session(tmp_path, "sess-budget", _events(1))
    import openbimagent.server.runs as runs_mod
    from openbimagent.server.sse_endpoint import M2SseStreamBudget

    monkeypatch.setattr(runs_mod, "_session_sse_budget", M2SseStreamBudget(max_active=0))
    resp = test_client.get(f"/api/v1/sessions/sess-budget/events/stream?token={TOKEN}")
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"


def test_follow_jsonl_incremental_append_and_clean_close(tmp_path: Path) -> None:
    """规范跟随生成器：既有行先达 → 追加行增量到达 → 置 inactive 后 drain 干净关闭。

    本环境 TestClient 缓冲流式响应（生成器结束后才交付），增量语义在生成器层验证。
    """
    import asyncio

    from openbimagent.server.sse_endpoint import follow_session_jsonl

    path = _write_session(tmp_path, "sess-gen", _events(1))
    state = {"active": True}

    async def _drive() -> tuple[list[str], int]:
        got: list[str] = []
        keepalives = 0
        appended = False
        async for frame in follow_session_jsonl(
            path,
            is_active=lambda: state["active"],
            poll_interval_s=0.02,
            max_lifetime_s=10.0,
        ):
            if frame == ": keepalive\n\n":
                keepalives += 1
                continue
            got.append(frame)
            if not appended:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_events(1, "appended-")[0], ensure_ascii=False) + "\n")
                appended = True
            else:
                state["active"] = False  # 追加行到达后置停；drain 后应干净关闭
        return got, keepalives

    got, keepalives = asyncio.run(_drive())
    assert len(got) == 2, f"既有行 + 追加行各一，无重复: {got}"
    assert '"e1"' in got[0] and '"appended-1"' in got[1]
    offsets = [int(frame.splitlines()[0][len("id: "):]) for frame in got]
    assert offsets[0] < offsets[1], "帧 id 为单调递增的字节 offset 恢复点"
    assert keepalives >= 1, "活跃空闲 tick 应发 keepalive"


def test_read_jsonl_increment_partial_line_and_rewind(tmp_path: Path) -> None:
    """半行不消费（下轮重读续上）；rewind 截断（size < offset）回退全量重放。"""
    from openbimagent.server.sse_endpoint import read_jsonl_increment

    path = tmp_path / "sess-partial.jsonl"
    first = json.dumps(_events(1)[0], ensure_ascii=False)
    path.write_bytes(first.encode("utf-8") + b"\n" + b'{"id": "part')
    frames, offset = read_jsonl_increment(path, 0)
    assert len(frames) == 1 and offset == len(first.encode("utf-8")) + 1
    with path.open("ab") as handle:
        handle.write(b'ial"}\n')
    frames, offset = read_jsonl_increment(path, offset)
    assert len(frames) == 1 and "partial" in frames[0]
    # rewind 截断：文件小于已消费 offset → 回退 0 全量重放
    path.write_text(json.dumps(_events(1, "rewound-")[0], ensure_ascii=False) + "\n", encoding="utf-8")
    frames, new_offset = read_jsonl_increment(path, offset)
    assert len(frames) == 1 and "rewound-1" in frames[0]
    assert new_offset < offset
