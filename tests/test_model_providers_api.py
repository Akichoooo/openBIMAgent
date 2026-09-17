from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import uuid

import httpx

from fastapi.testclient import TestClient
import pytest

from openbimagent.server.fastapi_app import build_demo_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: Any) -> TestClient:
    custom_json = tmp_path / "custom_providers.local.json"
    baseline_toml = tmp_path / "llm_baseline.local.toml"
    monkeypatch.setattr("openbimagent.server.workbench_io._CUSTOM_PROVIDERS_FILE", custom_json)
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(baseline_toml))
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-token-123")

    monkeypatch.setenv("OPENBIMAGENT_PENDING_APPROVALS", str(tmp_path / "pending.json"))

    class _RidClient(TestClient):
        def build_request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            request = super().build_request(method, url, **kwargs)
            request.headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            request.headers.setdefault("Authorization", "Bearer test-token-123")
            return request

    with _RidClient(build_demo_app()) as test_client:
        yield test_client


def test_client_headers_preserve_authentication_and_request_id(client: TestClient) -> None:
    first = client.get("/api/v1/settings/models")
    second = client.get("/api/v1/settings/models")
    assert first.status_code == second.status_code == 200
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    explicit = client.get("/api/v1/settings/models", headers={"x-request-id": "test-explicit-id"})
    assert explicit.status_code == 200, explicit.text
    assert explicit.headers["X-Request-ID"] == "test-explicit-id"
    denied = client.get("/api/v1/settings/models", headers={"authorization": "Bearer wrong-token"})
    assert denied.status_code == 401, denied.text
    assert denied.json()["code"] == "unauthorized"
    missing_id = client.build_request("GET", "/api/v1/settings/models")
    del missing_id.headers["X-Request-ID"]
    invalid = client.send(missing_id)
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["code"] == "invalid_request"


def test_models_list_and_crud(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-token-123"}

    # 1. GET /api/v1/settings/models - returns presets and custom
    res = client.get("/api/v1/settings/models")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert len(data["custom"]) >= 1  # seeded with defaults
    assert len(data["providers"]) >= 1

    # 2. POST /api/v1/settings/providers - create custom provider
    new_prov = {
        "name": "测试供应商-Alpha",
        "base_url": "https://api.test-alpha.com/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "sk-test-key-999",
        "enabled": True,
        "models": [{"name": "test-model-1", "context_window": 128000, "max_tokens": 8192}],
    }
    create_res = client.post("/api/v1/settings/providers", json=new_prov, headers=headers)
    assert create_res.status_code == 200
    prov_id = create_res.json()["provider"]["id"]
    assert prov_id.startswith("prov_")

    # 3. PATCH /api/v1/settings/providers/{id} - update provider
    patch_res = client.patch(
        f"/api/v1/settings/providers/{prov_id}",
        json={"name": "测试供应商-Beta", "enabled": False},
        headers=headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["provider"]["name"] == "测试供应商-Beta"
    assert patch_res.json()["provider"]["enabled"] is False

    # 4. POST /api/v1/settings/providers/{id}/models - add model
    add_model_res = client.post(
        f"/api/v1/settings/providers/{prov_id}/models",
        json={"name": "test-model-2", "context_window": 1000000, "max_tokens": 128000, "capabilities": ["tools", "vision"]},
        headers=headers,
    )
    assert add_model_res.status_code == 200
    models = add_model_res.json()["provider"]["models"]
    assert any(m["name"] == "test-model-2" for m in models)

    # 5. PATCH model
    patch_model_res = client.patch(
        f"/api/v1/settings/providers/{prov_id}/models/test-model-2",
        json={"new_name": "test-model-2-v2", "context_window": 256000},
        headers=headers,
    )
    assert patch_model_res.status_code == 200
    models = patch_model_res.json()["provider"]["models"]
    assert any(m["name"] == "test-model-2-v2" and m["context_window"] == 256000 for m in models)

    # 6. Switch baseline to this custom model
    llm_switch = client.put(
        "/api/v1/settings/llm",
        json={"model": "test-model-2-v2"},
        headers=headers,
    )
    assert llm_switch.status_code == 200
    baseline_info = llm_switch.json()["baseline"]
    assert baseline_info["model"] == "test-model-2-v2"
    assert baseline_info["base_url"] == "https://api.test-alpha.com/v1"

    # 7. DELETE model
    del_m_res = client.delete(
        f"/api/v1/settings/providers/{prov_id}/models/test-model-1",
        headers=headers,
    )
    assert del_m_res.status_code == 200
    assert not any(m["name"] == "test-model-1" for m in del_m_res.json()["provider"]["models"])

    # 8. DELETE provider
    del_p_res = client.delete(f"/api/v1/settings/providers/{prov_id}", headers=headers)
    assert del_p_res.status_code == 200
    assert del_p_res.json()["deleted"] == prov_id


@pytest.mark.parametrize("upstream_status", [200, 401])
def test_provider_probe(client: TestClient, monkeypatch: pytest.MonkeyPatch, upstream_status: int) -> None:
    # Exercise the real probe handler, but never contact a configured provider.
    upstream = AsyncMock()
    upstream.get.return_value = httpx.Response(
        upstream_status, json={"data": [{"id": "test-model"}, {"name": "other-model"}, {}]}
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=upstream)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=context))
    res = client.post(
        "/api/v1/settings/providers/prov_sensenova_jy/probe",
        json={"base_url": "https://provider.invalid/v1/", "api_key": "sk-probe-test"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["latency_ms"] >= 0
    upstream.get.assert_awaited_once_with(
        "https://provider.invalid/v1/models",
        headers={"User-Agent": "openBIMAgent/1.0", "Authorization": "Bearer sk-probe-test"},
    )
    if upstream_status == 200:
        assert data["status"] == "success"
        assert data["models"] == ["test-model", "other-model"]
    else:
        assert data["status"] == "error"
        assert "HTTP 401" in data["error"]

