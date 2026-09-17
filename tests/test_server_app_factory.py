"""Offline formal-factory contracts: no network, LLM or host calls."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from openbimagent.server.fastapi_app import AppSettings, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, name in {
        "SESSIONS_DIR": "sessions", "WORKSPACES_FILE": "workspaces.json",
        "PENDING_APPROVALS": "pending.json", "AUDIT_LOG": "audit.jsonl",
        "LLM_BASELINE": "baseline.toml", "CUSTOM_PROVIDERS": "providers.json",
    }.items():
        monkeypatch.setenv("OPENBIMAGENT_" + key, str(tmp_path / name))
    app = create_app(AppSettings(token="fixture-token", workspace_roots=(tmp_path,)))
    return TestClient(app, base_url="http://localhost", headers={
        "Authorization": "Bearer fixture-token", "X-Request-ID": "factory-contract",
    })


def test_formal_route_inventory_and_catchall_order(client):
    paths = [r.path for r in client.app.routes]
    expected = {
        "/api/v1/chat", "/api/v1/chat/stream", "/api/v1/runs", "/api/v1/runs/active",
        "/api/v1/workspaces", "/api/v1/settings/llm", "/api/v1/settings/mcp",
        "/api/v1/approvals", "/api/v1/approvals/{ticket_id}/decide",
        "/api/v1/memory", "/api/v1/hooks", "/api/v1/uploads",
        "/api/v1/sessions/{session_id}/events", "/api/v1/sessions/{session_id}/events/stream",
    }
    assert expected <= set(paths)
    assert all(paths.index(p) < paths.index("/api/v1/{path:path}") for p in expected)
    assert paths.count("/api/v1/sessions/{session_id}/events") == 1
    assert client.get("/api/v1/workspaces").status_code == 200
    assert client.get("/api/v1/runs/active").status_code == 200
    assert client.get("/api/v1/approvals").status_code == 200
    assert client.get("/api/v1/hooks").status_code == 503
    assert client.get("/api/v1/attempts").json()["code"] == "control_plane_unavailable"
    assert client.get("/api/v1/runtime").json()["mode"] == "local-production"


def test_auth_and_request_id_before_side_effect_handlers(client, tmp_path):
    client.headers.pop("Authorization")
    for path in ("/api/v1/chat", "/api/v1/plugins/invoke", "/api/v1/demo/export-blender",
                 "/api/v1/demo/export-vectorworks", "/api/v1/workspaces"):
        assert client.post(path, json={"confirm": True}).status_code == 401
    assert client.get("/api/v1/settings/llm").status_code == 401
    assert client.get("/healthz").status_code == 200
    client.headers["Authorization"] = "Bearer fixture-token"
    client.headers.pop("X-Request-ID")
    assert client.post("/api/v1/sessions", json={}).status_code == 400
    assert not (tmp_path / "sessions").exists()


def test_local_boundary_and_authorized_workspace_root(client, tmp_path):
    assert client.get("/healthz", headers={"Host": "external.invalid"}).status_code == 403
    assert client.get("/healthz", headers={"Origin": "https://external.invalid"}).status_code == 403
    assert client.post("/api/v1/workspaces", json={"path": str(tmp_path.parent / "unapproved")}).status_code == 403
    assert client.post("/api/v1/workspaces", json={"path": str(tmp_path / "project")}).status_code == 200


def test_confirm_does_not_authorize_host_export_or_prompt_plugin(client, monkeypatch):
    from openbimagent.core.plugin import default_plugin_registry

    def must_not_execute(*args, **kwargs):
        pytest.fail("host/plugin execution is not allowed in this contract fixture")

    monkeypatch.setattr(default_plugin_registry, "invoke", must_not_execute)
    assert client.post("/api/v1/plugins/invoke", json={
        "capability": "cad_host:blender.execute", "confirm": True,
    }).status_code == 503
    for host in ("blender", "vectorworks"):
        r = client.post(f"/api/v1/demo/export-{host}", json={"confirm": True})
        assert r.status_code == 503
        assert r.json()["code"] == "approval_binding_required"
    assert client.post("/api/v1/runs", json={"brief": "fixture", "mode": "yolo"}).status_code == 403


def test_approval_identity_single_decision_and_expiry(client, monkeypatch):
    from openbimagent.server import approvals

    ticket = {"id": "fixture", "session_id": "session-fixture", "operation": "fake-write",
              "params": {"version": 1}, "requested_at": "now", "_mono": time.monotonic(),
              "decision": None, "event": threading.Event()}
    monkeypatch.setattr(approvals, "_pending", {"fixture": ticket})
    path = "/api/v1/approvals/fixture/decide"
    assert client.post(path, json={"decision": "approved", "actor": "human:other"}).status_code == 403
    assert not ticket["event"].is_set()
    assert client.post(path, json={"decision": "approved"}).status_code == 200
    assert ticket["actor"] == "human:web-operator"
    assert client.post(path, json={"decision": "rejected"}).status_code == 409
    ticket.update(decision=None, _mono=time.monotonic() - 4000)
    ticket["event"].clear()
    assert client.post(path, json={"decision": "approved"}).status_code == 410
    assert not ticket["event"].is_set()


def test_session_creation_json_history_and_stream_are_distinct(client):
    created = client.post("/api/v1/sessions", json={"title": "fixture"})
    assert created.status_code == 200
    sid = created.json()["session_id"]
    history = client.get(f"/api/v1/sessions/{sid}/events")
    assert history.status_code == 200 and "events" in history.json()
    stream = client.get(f"/api/v1/sessions/{sid}/events/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["X-Request-ID"] == "factory-contract"
