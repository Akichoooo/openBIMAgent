"""审批中心端到端测试：pipeline 触门挂起 → 票据可见 → 决策放行/拒绝 → 事件落 session。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("approvals")
    import os

    os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
    os.environ["OPENBIMAGENT_SESSIONS_DIR"] = str(tmp / "sessions")
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            headers.setdefault("Authorization", "Bearer test-wb-token")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    os.environ.pop("OPENBIMAGENT_SESSIONS_DIR", None)


def _wait_pending(client: TestClient, timeout_s: float = 90) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = client.get("/api/v1/approvals").json()["items"]
        if items:
            return items[0]
        time.sleep(0.8)
    raise AssertionError("90s 内未出现待决审批票据")


@pytest.fixture(scope="module")
def approved_run(client: TestClient) -> str:
    """启动运行 → 批准全部审批门直至结束；返回 session_id。"""
    resp = client.post("/api/v1/runs", json={"brief": "审批中心 E2E 测试", "playbook": "single_asset_hero"})
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    decided_ids: set[str] = set()
    deadline = time.time() + 180
    while time.time() < deadline:
        for item in client.get("/api/v1/approvals").json()["items"]:
            if item["id"] in decided_ids:
                continue
            resp = client.post(f"/api/v1/approvals/{item['id']}/decide", json={"decision": "approved", "actor": "human:test"})
            assert resp.status_code == 200, resp.text
            decided_ids.add(item["id"])
        run = client.get("/api/v1/runs/active").json()["run"]
        if not run["active"]:
            break
        time.sleep(0.8)
    assert decided_ids, "离线 pipeline 应至少触达一个审批门（deliver）"
    return session_id


def test_approval_events_landed_in_session(client: TestClient, approved_run: str) -> None:
    events = client.get(f"/api/v1/sessions/{approved_run}/events?tail=500").json()["events"]
    customs = [e for e in events if e.get("type") == "custom"]
    types = [e["payload"].get("customType") for e in customs]
    assert "approval_requested" in types
    assert "approval_decided" in types
    decided = [e for e in customs if e["payload"].get("customType") == "approval_decided"]
    assert all(e["payload"].get("decision") == "approved" for e in decided)


def test_decide_unknown_ticket_404(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/no-such-ticket/decide", json={"decision": "approved"})
    assert resp.status_code == 404


def test_decide_rejects_bad_decision(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/whatever/decide", json={"decision": "maybe"})
    assert resp.status_code == 400
