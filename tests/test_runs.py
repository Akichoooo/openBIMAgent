"""runs 端点测试：新建任务 → 后台真跑 pipeline → 会话事件可读。

运行环境为离线确定性模板 + MockCritic（无 registry/CAD 宿主路径，CLAUDE.md 约定）。
OPENBIMAGENT_SESSIONS_DIR 指向 tmp_path 隔离；pipeline 产物仍写仓库 out/（与 CLI 同构）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("runs")
    import os
    import uuid

    os.environ["OPENBIMAGENT_SESSIONS_DIR"] = str(tmp / "sessions")
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        """M2 只读网关要求每请求唯一 X-Request-ID（幂等语义）。"""

        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    os.environ.pop("OPENBIMAGENT_SESSIONS_DIR", None)


@pytest.fixture(scope="module")
def finished_run(client: TestClient) -> str:
    """启动一次真实运行；遇审批门一律批准直至结束（最长 180s）；返回 session_id。"""
    resp = client.post("/api/v1/runs", json={"brief": "测试：DN400 污水管新建任务", "playbook": "municipal_utility"})
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        # 审批中心：批准所有待决票据（真实 Web 审批门，不再是 yes=True）
        for item in client.get("/api/v1/approvals").json()["items"]:
            client.post(f"/api/v1/approvals/{item['id']}/decide", json={"decision": "approved"})
        run = client.get("/api/v1/runs/active").json()["run"]
        if not run["active"]:
            break
        time.sleep(1.0)
    return session_id


def test_run_completes_and_lands_in_session_index(client: TestClient, finished_run: str) -> None:
    run = client.get("/api/v1/runs/active").json()["run"]
    assert run["active"] is False
    assert run["session_id"] == finished_run
    # 运行结果（成功或环境性失败）必须可见，不得无声
    sessions = client.get("/api/v1/sessions").json()
    items = sessions.get("data", {}).get("items") or sessions.get("items") or []
    assert any(finished_run in str(item) for item in items), f"会话未进入 index: {items}"


def test_session_events_readable(client: TestClient, finished_run: str) -> None:
    resp = client.get(f"/api/v1/sessions/{finished_run}/events")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert isinstance(events, list) and events, "会话应有事件（至少 title 登记/ pipeline 事件）"
    assert all("type" in e and "id" in e for e in events)


def test_events_unknown_session_404(client: TestClient) -> None:
    assert client.get("/api/v1/sessions/no-such-session/events").status_code == 404


def test_start_run_rejects_empty_brief(client: TestClient) -> None:
    assert client.post("/api/v1/runs", json={"brief": "  "}).status_code == 400
