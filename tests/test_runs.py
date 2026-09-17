"""Run endpoints with a mocked pipeline and real session persistence/approval gates.

All state and artifacts are sandboxed; no LLM, CAD host, or network service runs.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("runs")
    import uuid

    from openbimagent.server.fastapi_app import build_demo_app
    from openbimagent.skills.registry import reload_skills
    from openbimagent.session.schema import EventType
    from openbimagent.session.store import SessionStore

    class _RidClient(TestClient):
        def build_request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            request = super().build_request(method, url, **kwargs)
            request.headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            request.headers.setdefault("Authorization", "Bearer test-wb-token")
            return request

    def fake_pipeline(*, session_id, sessions_dir, approval_fn, **kwargs):
        # Keep the genuine blocking web gate and session binding, mock only execution.
        assert approval_fn("deliver", {"session_id": session_id, "artifact": "mock-result"})
        store = SessionStore(sessions_dir / f"{session_id}.jsonl")
        store.append_new(EventType.MESSAGE, {"role": "assistant", "content": "mock pipeline completed"})

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")
            for name, value in {
                "SESSIONS_DIR": "sessions", "SKILLS_ROOT": "skills",
                "PENDING_APPROVALS": "pending.json", "ARCHIVE_DIR": "archive",
                "MEMORY_DIR": "memory", "WORKSPACES_FILE": "workspaces.json",
            }.items():
                patch.setenv(f"OPENBIMAGENT_{name}", str(tmp / value))
            patch.setenv("OPENBIMAGENT_APPROVAL_TIMEOUT_S", "10")
            patch.setattr("openbimagent.server.runs._REPO_ROOT", tmp)
            patch.setattr("openbimagent.server.runs._runs", {})
            patch.setattr("openbimagent.server.approvals._pending", {})
            patch.setattr("openbimagent.audit._AUDIT_PATH", tmp / "audit.jsonl")
            patch.setattr("openbimagent.assembly.pipeline.run_pipeline", fake_pipeline)
            reload_skills()
            with _RidClient(build_demo_app()) as test_client:
                yield test_client
    finally:
        reload_skills()


@pytest.fixture(scope="module")
def finished_run(client: TestClient) -> str:
    """Start the mocked pipeline, approving only this session's real pending ticket."""
    resp = client.post("/api/v1/runs", json={"brief": "测试：DN400 污水管新建任务", "playbook": "municipal_utility", "mode": "agent"})
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    approved = set()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        pending = client.get("/api/v1/approvals")
        assert pending.status_code == 200, pending.text
        for item in pending.json()["items"]:
            if item["session_id"] != session_id or item["id"] in approved:
                continue
            assert item["operation"] == "deliver"
            assert item["params"]["session_id"] == session_id
            assert item["expired"] is False
            url = f"/api/v1/approvals/{item['id']}/decide"
            spoofed = client.post(url, json={"decision": "approved", "actor": "someone-else"})
            assert spoofed.status_code == 403, spoofed.text
            decision = client.post(url, json={"decision": "approved"})
            assert decision.status_code == 200, decision.text
            assert decision.json() == {"status": "success", "id": item["id"], "decision": "approved"}
            approved.add(item["id"])
        active = client.get("/api/v1/runs/active")
        assert active.status_code == 200, active.text
        run = next(r for r in active.json()["runs"] if r["session_id"] == session_id)
        if not run["active"]:
            assert run["status"] == "done", run
            assert run["error"] is None
            assert len(approved) == 1, "Mock execution must pass the real approval gate"
            return session_id
        time.sleep(0.02)
    pytest.fail(f"Run {session_id} did not finish: {run}")


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
    assert any(e["payload"].get("content") == "mock pipeline completed" for e in events)
    decisions = [e["payload"] for e in events if e["payload"].get("customType") == "approval_decided"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "approved"
    assert decisions[0]["actor"] == "human:web-operator"


def test_events_unknown_session_404(client: TestClient) -> None:
    assert client.get("/api/v1/sessions/no-such-session/events").status_code == 404


def test_start_run_rejects_empty_brief(client: TestClient) -> None:
    assert client.post("/api/v1/runs", json={"brief": "  "}).status_code == 400


def test_archive_and_unarchive_session(client: TestClient, finished_run: str) -> None:
    # 归档会话
    patch_resp = client.patch(f"/api/v1/sessions/{finished_run}", json={"archived": True})
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["status"] == "success"
    assert data["session"]["id"] == finished_run
    assert data["session"]["archived"] is True
    assert data["session"]["archived_at"]

    # 查阅列表应包含 archived 状态
    sessions_resp = client.get("/api/v1/sessions")
    assert sessions_resp.status_code == 200
    items = sessions_resp.json().get("data", {}).get("items") or []
    target = next((s for s in items if s["session_id"] == finished_run), None)
    assert target is not None
    assert target["archived"] is True
    # The readonly list projects archived, not archived_at; the PATCH session
    # above exposes the persisted timestamp.

    # 解归档
    unpatch_resp = client.patch(f"/api/v1/sessions/{finished_run}", json={"archived": False})
    assert unpatch_resp.status_code == 200
    assert unpatch_resp.json()["session"]["id"] == finished_run
    assert unpatch_resp.json()["session"]["archived"] is False
    assert "archived_at" not in unpatch_resp.json()["session"]

    # 再次查阅应已解归档
    sessions_resp2 = client.get("/api/v1/sessions")
    items2 = sessions_resp2.json().get("data", {}).get("items") or []
    target2 = next((s for s in items2 if s["session_id"] == finished_run), None)
    assert target2 is not None
    assert target2["archived"] is False

