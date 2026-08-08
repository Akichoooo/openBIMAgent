"""M2 P2 FastAPI 只读服务测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from openbimagent.server.fastapi_app import build_m2_readonly_app
from openbimagent.server.readonly_http import M2ReadonlyHttpAdapter
from openbimagent.server.service import M2ReadOnlyService


class _MockReader:
    def list_attempts(self, **kw):
        return ()
    def get_attempt(self, _):
        raise ValueError("no runtime")
    def get_lineage(self, _):
        return ()
    def list_approvals(self, **kw):
        return ()


def _app() -> TestClient:
    service = M2ReadOnlyService(
        control_plane=_MockReader(),
        session_index_reader=lambda: [],
        artifact_lookup=lambda _: None,
    )
    adapter = M2ReadonlyHttpAdapter(service)
    return TestClient(build_m2_readonly_app(adapter))


def test_health_requires_correlation_id() -> None:
    client = _app()
    resp = client.get("/api/v1/health")
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_health_with_correlation_id() -> None:
    client = _app()
    resp = client.get("/api/v1/health", headers={"X-Request-ID": "test-001"})
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["data"]["service"] == "openbimagent-m2-readonly"


def test_unknown_path_returns_404() -> None:
    client = _app()
    resp = client.get("/api/v1/nonexistent", headers={"X-Request-ID": "test-002"})
    assert resp.status_code == 404
    assert resp.json()["ok"] is False


def test_sessions_empty() -> None:
    client = _app()
    resp = client.get("/api/v1/sessions", headers={"X-Request-ID": "test-003"})
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["data"]["items"] == []


def test_attempts_without_status() -> None:
    client = _app()
    resp = client.get("/api/v1/attempts", headers={"X-Request-ID": "test-004"})
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True


def test_post_method_rejected() -> None:
    client = _app()
    resp = client.post("/api/v1/health", headers={"X-Request-ID": "test-005"})
    assert resp.status_code == 405
    assert resp.json()["ok"] is False


def test_openapi_docs_accessible() -> None:
    client = _app()
    resp = client.get("/api/v1/docs", headers={"X-Request-ID": "test-006"})
    assert resp.status_code == 200


def test_openapi_json_accessible() -> None:
    client = _app()
    resp = client.get("/api/v1/openapi.json", headers={"X-Request-ID": "test-007"})
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "openBIMAgent M2 Read-Only API"