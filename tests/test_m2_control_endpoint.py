"""M2 P3 受控写控制端点测试。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.server.authentication import M2AuthenticatedPrincipal, M2ControlRole
from openbimagent.server.control_endpoint import add_control_endpoint
from openbimagent.server.control_preflight import M2ControlProxyPlan
from openbimagent.server.idempotency_transaction import M2IdempotencyRecord

OPERATOR = ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN, display_name="Operator")
PRINCIPAL = M2AuthenticatedPrincipal(
    actor=OPERATOR,
    roles=(M2ControlRole.OPERATOR,),
    authentication_context_sha256="a" * 64,
)


def _app(mock_ipc: dict | None = None) -> FastAPI:
    def _principal_provider(request: Request) -> M2AuthenticatedPrincipal:
        return PRINCIPAL

    _ipc_result = mock_ipc or {"status": "ok", "receipt_id": "receipt-001"}

    def _ipc_caller(plan: M2ControlProxyPlan) -> dict:
        return _ipc_result

    def _idempotency_store(scope: str) -> M2IdempotencyRecord | None:
        return None

    app = FastAPI()
    add_control_endpoint(
        app,
        principal_provider=_principal_provider,
        ipc_caller=_ipc_caller,
        idempotency_store=_idempotency_store,
    )
    return app


def test_control_ping_returns_ok() -> None:
    client = TestClient(_app())
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-001"},
        json={
            "operation": "approval.decide",
            "resource_id": "approval-001",
            "idempotency_key": "key-001",
            "approved": True,
            "reason": "reviewed",
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True


def test_control_requires_authentication() -> None:
    def _fail_auth(request: Request) -> M2AuthenticatedPrincipal:
        raise ValueError("auth failed")

    app = FastAPI()
    add_control_endpoint(
        app,
        principal_provider=_fail_auth,
        ipc_caller=lambda _: {"status": "ok"},
        idempotency_store=lambda _: None,
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-002"},
        json={"operation": "approval.decide", "resource_id": "approval-002", "idempotency_key": "key-002"},
    )
    assert resp.status_code == 401


def test_control_invalid_json_returns_400() -> None:
    client = TestClient(_app())
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-003", "Content-Type": "application/json"},
        content=b"not json",
    )
    assert resp.status_code == 400


def test_control_resume_with_instruction() -> None:
    client = TestClient(_app())
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-004"},
        json={
            "operation": "attempt.resume",
            "resource_id": "request-001",
            "idempotency_key": "key-003",
            "instruction": "check persisted facts first",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_control_steer_with_instruction() -> None:
    client = TestClient(_app())
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-005"},
        json={
            "operation": "attempt.steer",
            "resource_id": "request-001",
            "idempotency_key": "key-004",
            "instruction": "verify external state first",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_control_cancel() -> None:
    client = TestClient(_app())
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-006"},
        json={
            "operation": "attempt.cancel",
            "resource_id": "request-001",
            "idempotency_key": "key-005",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_control_ipc_failure_returns_503() -> None:
    def _fail_ipc(plan: M2ControlProxyPlan) -> dict:
        raise RuntimeError("IPC connection refused")

    app = FastAPI()
    add_control_endpoint(
        app,
        principal_provider=lambda _: PRINCIPAL,
        ipc_caller=_fail_ipc,
        idempotency_store=lambda _: None,
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/control",
        headers={"X-Request-ID": "t-007"},
        json={
            "operation": "approval.decide",
            "resource_id": "approval-007",
            "idempotency_key": "key-007",
            "approved": True,
            "reason": "reviewed",
        },
    )
    assert resp.status_code == 503