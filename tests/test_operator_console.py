"""P1f 本地 Operator Console：只读投影、IPC 代理和 HTTP 安全边界。"""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.orchestrator.console import (
    CONSOLE_MAX_BODY_BYTES,
    ConsoleControlRequest,
    OperatorConsoleServer,
    OperatorConsoleService,
)
from openbimagent.orchestrator.runtime import LocalSubagentRuntime


class _EmptyPlane:
    def list_attempts(self):
        return ()

    def list_approvals(self):
        return ()

    def list_resumes(self):
        return ()

    def list_steers(self):
        return ()


class _RecordingIpc:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, operation, *, actor, idempotency_key, payload=None):
        call = {
            "operation": operation,
            "actor": actor,
            "idempotency_key": idempotency_key,
            "payload": payload or {},
        }
        self.calls.append(call)
        return {"accepted": True, "operation": operation}


def _service(tmp_path: Path, ipc=None) -> OperatorConsoleService:
    return OperatorConsoleService(
        tmp_path / "sessions",
        actor=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN, display_name="JY"),
        control_plane=_EmptyPlane(),  # type: ignore[arg-type]
        ipc_client=ipc or _RecordingIpc(),  # type: ignore[arg-type]
    )


def _connection(server: OperatorConsoleServer) -> http.client.HTTPConnection:
    parsed = urlsplit(server.url)
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)


def _json_response(response: http.client.HTTPResponse) -> dict[str, Any]:
    return json.loads(response.read().decode("utf-8"))


def _request_status(
    server: OperatorConsoleServer,
    *,
    method: str,
    path: str,
    body: str,
    headers: dict[str, str],
) -> int:
    for attempt in range(3):
        connection = _connection(server)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response.read()
            return response.status
        except (ConnectionAbortedError, ConnectionResetError, http.client.RemoteDisconnected):
            if attempt == 2:
                raise
            time.sleep(0.05)
        finally:
            connection.close()
    return 500



def test_console_request_contract_rejects_missing_and_unknown_fields() -> None:
    with pytest.raises(ValueError, match="resource_id"):
        ConsoleControlRequest(operation="attempt.cancel", idempotency_key="cancel-1")
    with pytest.raises(ValueError, match="instruction"):
        ConsoleControlRequest(operation="attempt.resume", resource_id="request-1", idempotency_key="resume-1")
    with pytest.raises(ValueError, match="extra"):
        ConsoleControlRequest.model_validate(
            {"operation": "runtime.ping", "idempotency_key": "ping-1", "extra": "forbidden"}
        )


def test_console_service_routes_control_with_server_side_actor(tmp_path: Path) -> None:
    ipc = _RecordingIpc()
    service = _service(tmp_path, ipc)
    result = service.control(
        ConsoleControlRequest(
            operation="approval.approve",
            resource_id="approval-1",
            reason="reviewed",
            idempotency_key="approval-1",
        )
    )
    assert result == {"accepted": True, "operation": "approval.decide"}
    assert ipc.calls == [
        {
            "operation": "approval.decide",
            "actor": service.actor,
            "idempotency_key": "approval-1",
            "payload": {"approval_id": "approval-1", "approved": True, "reason": "reviewed"},
        }
    ]


def test_console_http_bootstrap_snapshot_and_security_headers(tmp_path: Path) -> None:
    server = OperatorConsoleServer(_service(tmp_path), port=0)
    try:
        server.start()
        connection = _connection(server)
        connection.request("GET", "/api/v1/bootstrap")
        response = connection.getresponse()
        payload = _json_response(response)
        assert response.status == 200
        assert payload["actor"]["actor_id"] == "human:jy"
        assert payload["csrf_token"] == server.csrf_token
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")

        connection.request("GET", "/api/v1/snapshot")
        snapshot_response = connection.getresponse()
        snapshot = _json_response(snapshot_response)
        assert snapshot_response.status == 200
        assert snapshot == {
            "protocol_version": "1.0",
            "attempts": [],
            "approvals": [],
            "resumes": [],
            "steers": [],
        }

        connection.request("GET", "/")
        html_response = connection.getresponse()
        html = html_response.read().decode("utf-8")
        assert html_response.status == 200
        assert "openBIMAgent Operator Console" in html
        assert "control-ipc.token" not in html
        assert "bearer_token" not in html
        assert server.csrf_token not in html
    finally:
        server.stop()


def test_console_write_requires_valid_host_origin_csrf_and_json(tmp_path: Path) -> None:
    server = OperatorConsoleServer(_service(tmp_path), port=0)
    body = json.dumps({"operation": "runtime.ping", "idempotency_key": "ping-1"})
    try:
        server.start()
        assert _request_status(
            server,
            method="POST",
            path="/api/v1/control",
            body=body,
            headers={
                "Host": "evil.example",
                "Origin": next(iter(server.allowed_origins)),
                "Content-Type": "application/json",
                "X-OpenBIM-CSRF": server.csrf_token,
            },
        ) == 400
        assert _request_status(
            server,
            method="POST",
            path="/api/v1/control",
            body=body,
            headers={
                "Origin": "http://evil.example",
                "Content-Type": "application/json",
                "X-OpenBIM-CSRF": server.csrf_token,
            },
        ) == 403
        assert _request_status(
            server,
            method="POST",
            path="/api/v1/control",
            body=body,
            headers={
                "Origin": next(iter(server.allowed_origins)),
                "Content-Type": "application/json",
                "X-OpenBIM-CSRF": "wrong",
            },
        ) == 403
        assert _request_status(
            server,
            method="POST",
            path="/api/v1/control",
            body=body,
            headers={
                "Origin": next(iter(server.allowed_origins)),
                "Content-Type": "text/plain",
                "X-OpenBIM-CSRF": server.csrf_token,
            },
        ) == 415
    finally:
        server.stop()


def test_console_write_proxies_ping_without_exposing_ipc_token(tmp_path: Path) -> None:
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=tmp_path / "agents",
        rehydrate=False,
    )
    server = None
    try:
        discovery = runtime.start_ipc()
        service = OperatorConsoleService(
            tmp_path / "sessions",
            actor=ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN),
        )
        server = OperatorConsoleServer(service, port=0)
        server.start()
        connection = _connection(server)
        body = json.dumps({"operation": "runtime.ping", "idempotency_key": "console-ping-1"})
        connection.request(
            "POST",
            "/api/v1/control",
            body=body,
            headers={
                "Origin": f"http://127.0.0.1:{urlsplit(server.url).port}",
                "Content-Type": "application/json",
                "X-OpenBIM-CSRF": server.csrf_token,
            },
        )
        response = connection.getresponse()
        payload = _json_response(response)
        assert response.status == 200
        assert payload["result"]["runtime_instance_id"] == discovery.runtime_instance_id
        assert "token" not in json.dumps(payload).lower()
    finally:
        if server is not None:
            server.stop()
        runtime.shutdown()


def test_console_rejects_non_loopback_and_oversized_body(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        OperatorConsoleServer(_service(tmp_path), host="0.0.0.0")

    server = OperatorConsoleServer(_service(tmp_path), port=0)
    try:
        server.start()
        connection = _connection(server)
        connection.request(
            "POST",
            "/api/v1/control",
            body=b"{}",
            headers={
                "Origin": next(iter(server.allowed_origins)),
                "Content-Type": "application/json",
                "X-OpenBIM-CSRF": server.csrf_token,
                "Content-Length": str(CONSOLE_MAX_BODY_BYTES + 1),
            },
        )
        assert connection.getresponse().status == 413
    finally:
        server.stop()
