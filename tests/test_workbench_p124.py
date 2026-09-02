"""P1/P2/P4 端点测试：SSE 事件流、素材归档、会话分支、市政入参过 domain_gate。

municipal_utility 运行携带 pack 默认 solver_input.default.json（含完整碰撞上下文），
应越过 domain_gate 抵达 deliver 审批门（此前 UNKNOWN 阻断）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("p124")
    os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
    os.environ["OPENBIMAGENT_SESSIONS_DIR"] = str(tmp / "sessions")
    os.environ["OPENBIMAGENT_ARCHIVE_DIR"] = str(tmp / "archive")
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            headers.setdefault("Authorization", "Bearer test-wb-token")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    os.environ.pop("OPENBIMAGENT_SESSIONS_DIR", None)


@pytest.fixture(scope="module")
def municipal_run(client: TestClient) -> str:
    """市政运行（带默认入参）：批准全部审批门直至结束；返回 session_id。"""
    resp = client.post("/api/v1/runs", json={"brief": "市政入参+归档+SSE 测试", "playbook": "municipal_utility"})
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        for item in client.get("/api/v1/approvals").json()["items"]:
            client.post(f"/api/v1/approvals/{item['id']}/decide", json={"decision": "approved"})
        run = client.get("/api/v1/runs/active").json()["run"]
        if not run["active"]:
            break
        time.sleep(0.8)
    return session_id


def test_municipal_reaches_deliver_gate(client: TestClient, municipal_run: str) -> None:
    """带默认 solver_input 的市政运行应触达 deliver 审批门（不再 domain_gate UNKNOWN 阻断）。"""
    events = client.get(f"/api/v1/sessions/{municipal_run}/events?tail=500").json()["events"]
    customs = [e["payload"].get("customType") for e in events if e.get("type") == "custom"]
    assert "approval_requested" in customs, "应触达审批门（deliver）"


def test_archive_written(client: TestClient, municipal_run: str, tmp_path: Path) -> None:
    root = Path(os.environ["OPENBIMAGENT_ARCHIVE_DIR"]) / "municipal_utility"
    archive = root / municipal_run
    assert archive.is_dir(), f"归档目录缺失: {archive}"
    entries = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert any(e["session_id"] == municipal_run for e in entries)
    # 端点可见
    data = client.get("/api/v1/archive").json()
    assert data["count"] >= 1 and any(i["session_id"] == municipal_run for i in data["items"])


def test_sse_stream_replays_and_closes(client: TestClient, municipal_run: str) -> None:
    """已结束会话的 SSE 跟随：回放既有事件后自动关闭（不悬挂）。"""
    with client.stream("GET", f"/api/v1/sessions/{municipal_run}/events/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunks = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
            if len("".join(chunks)) > 200:
                break
    assert '"type"' in "".join(chunks) or '"payload"' in "".join(chunks)


def test_fork_session(client: TestClient, municipal_run: str) -> None:
    resp = client.post(f"/api/v1/sessions/{municipal_run}/fork", json={"title": "测试分支"})
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["session_id"]
    assert new_id != municipal_run
    events = client.get(f"/api/v1/sessions/{new_id}/events").json()["events"]
    assert events, "分支会话应携带主干链事件"


def test_usage_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/usage")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
