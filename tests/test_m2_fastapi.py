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


def test_web_ui_accessible() -> None:
    client = _app()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "openBIMAgent" in resp.text
    assert "three.min.js" in resp.text


def test_plugins_inventory_endpoint() -> None:
    client = _app()
    resp = client.get("/api/v1/plugins")
    assert resp.status_code == 200
    d = resp.json()
    assert d["plugin_count"] >= 6
    assert any(p["plugin_id"] == "plugin.core.municipal_utility" for p in d["active_plugins"])


def test_ui_slots_endpoint() -> None:
    client = _app()
    resp = client.get("/api/v1/ui/slots")
    assert resp.status_code == 200
    d = resp.json()
    assert d["total_slots"] >= 5
    assert any(s["slot_key"] == "workbench:tab.compiled_ir" for s in d["slots"])


def test_plugin_capability_invoke_endpoint() -> None:
    client = _app()
    resp = client.post(
        "/api/v1/plugins/invoke",
        json={"capability": "rules:gb50289", "payload": {}},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["status"] == "success"
    assert d["capability"] == "rules:gb50289"


def test_demo_municipal_pipeline_endpoint() -> None:
    """演示端点经微内核调度自愈求解器，返回真实 IR 与时间线。"""
    client = _app()
    resp = client.get("/api/v1/demo/municipal-pipeline")
    assert resp.status_code == 200
    d = resp.json()
    assert d["status"] == "success"
    assert d["converged"] is True
    assert d["iterations_spent"] >= 2  # SH-2 带障碍物，自愈需 ≥2 轮
    assert len(d["nodes"]) >= 3
    assert len(d["segments"]) >= 1
    # 管段携带真实 centerline 折线坐标
    seg = d["segments"][0]
    assert len(seg["points"]) >= 2
    assert all({"x", "y", "z"} <= set(p) for p in seg["points"])
    # 自愈时间线与消解冲突为真实求解器输出
    assert any(t["converged"] for t in d["timeline"])
    assert len(d["resolved_violations"]) >= 1

# =========================================================================
# Codex 吸收项：健康探针 / invoke 背压 (-32001) / 策略门 confirm 透传
# =========================================================================


def test_healthz_and_readyz_probes() -> None:
    client = _app()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    d = resp.json()
    assert d["status"] == "ready"
    assert d["plugin_count"] >= 7
    assert d["total_capabilities"] >= 14


def test_invoke_concurrency_guard_saturated_rejects() -> None:
    from openbimagent.server.fastapi_app import InvokeConcurrencyGuard

    guard = InvokeConcurrencyGuard(2)
    assert guard.try_acquire() is True
    assert guard.try_acquire() is True
    assert guard.try_acquire() is False  # 满载立即拒绝，不排队
    guard.release()
    assert guard.try_acquire() is True


def test_invoke_concurrency_guard_validates_limit() -> None:
    import pytest
    from openbimagent.server.fastapi_app import InvokeConcurrencyGuard

    with pytest.raises(ValueError):
        InvokeConcurrencyGuard(0)


def test_invoke_endpoint_passes_confirm_through_policy_gate() -> None:
    """prompt 策略全链路：无 confirm 报错，带 confirm=true 放行。"""
    from openbimagent.core.plugin import (
        CapabilityPolicyDecision,
        CapabilityPolicyRule,
        default_plugin_registry,
    )

    default_plugin_registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="rules:gb50289",
            decision=CapabilityPolicyDecision.PROMPT,
            justification="规则集编译属重负载能力，需人工确认",
        ),
    ])
    try:
        client = _app()
        resp = client.post(
            "/api/v1/plugins/invoke",
            json={"capability": "rules:gb50289", "payload": {}},
        )
        assert resp.status_code == 200
        d = resp.json()
        assert d["status"] == "error"
        assert "confirm=True" in d["error"]
        assert "需人工确认" in d["error"]

        resp = client.post(
            "/api/v1/plugins/invoke",
            json={"capability": "rules:gb50289", "payload": {}, "confirm": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
    finally:
        default_plugin_registry.set_capability_policies([])


def test_invoke_endpoint_missing_capability_is_400() -> None:
    client = _app()
    resp = client.post("/api/v1/plugins/invoke", json={"payload": {}})
    assert resp.status_code == 400
    assert resp.json()["error"] == "缺少 capability 参数"


def test_module_level_demo_app_entry() -> None:
    """模块级 app 入口：uvicorn openbimagent.server.fastapi_app:app 可直接启动。"""
    from openbimagent.server.fastapi_app import app

    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["status"] == "ready"
    assert client.get("/api/v1/plugins").json()["plugin_count"] >= 7
