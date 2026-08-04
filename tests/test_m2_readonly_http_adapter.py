"""M2 pre-G7 只读 HTTP 输入解析、路由和安全错误映射纯函数测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from openbimagent.orchestrator.contracts import SubagentStatus
from openbimagent.orchestrator.control_plane import AttemptView
from openbimagent.server.contracts import M2ErrorCode
from openbimagent.server.correlation_identity import M2_CORRELATION_ID_POLICY_VERSION
from openbimagent.server.readonly_http import (
    M2HttpHeader,
    M2ReadonlyHttpAdapter,
    M2ReadonlyHttpRequest,
    m2_error_http_status,
)
from openbimagent.server.service import M2ReadOnlyService

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class FakeControlPlane:
    def __init__(self) -> None:
        self.attempt = AttemptView(
            request_id="request-1",
            agent_id="agent-1",
            parent_session_id="session-1",
            child_session_id="child-1",
            role="worker",
            lineage_id="lineage-1",
            attempt_number=1,
            resumed_from_request_id=None,
            status=SubagentStatus.COMPLETED,
            phase="terminal",
            updated_at=NOW,
            result_hint="private task text",
            error_code=None,
            receipt_id="receipt-1",
            artifact_count=0,
        )

    def list_attempts(self, **filters):
        if filters.get("lineage_id") not in {None, "lineage-1"}:
            return ()
        return (self.attempt,)

    def get_attempt(self, request_id):
        if request_id != "request-1":
            raise RuntimeError("not found")
        return self.attempt

    def get_lineage(self, lineage_id):
        if lineage_id != "lineage-1":
            raise RuntimeError("not found")
        return (self.attempt,)

    def list_approvals(self, **filters):
        return ()


def _service() -> M2ReadOnlyService:
    return M2ReadOnlyService(
        control_plane=FakeControlPlane(),
        session_index_reader=lambda: [
            {
                "id": "session-1",
                "title": "Session",
                "created_at": NOW.isoformat(),
                "last_active": NOW.isoformat(),
                "event_count": 2,
            }
        ],
        artifact_lookup=lambda _: None,
    )


def _request(target: str, *, method: str = "GET", headers=None, body_size: int = 0) -> M2ReadonlyHttpRequest:
    return M2ReadonlyHttpRequest(
        method=method,
        target=target,
        headers=(M2HttpHeader(name="X-Request-ID", value="api-1"),) if headers is None else headers,
        body_size=body_size,
    )


def test_all_openapi_routes_dispatch_to_existing_readonly_service() -> None:
    adapter = M2ReadonlyHttpAdapter(_service())
    cases = {
        "/api/v1/health": "service",
        "/api/v1/sessions": "items",
        "/api/v1/sessions/session-1": "session",
        "/api/v1/attempts": "items",
        "/api/v1/attempts/request-1": "attempt",
        "/api/v1/lineages/lineage-1": "items",
        "/api/v1/approvals": "items",
        "/api/v1/artifacts/missing": None,
    }
    for target, data_key in cases.items():
        response = adapter.dispatch(_request(target))
        if data_key is None:
            assert response.status_code == 404
            assert response.envelope.error.code is M2ErrorCode.NOT_FOUND
        else:
            assert response.status_code == 200
            assert response.envelope.ok is True
            assert data_key in response.envelope.data
        assert response.envelope.request_id == "api-1"
        assert response.headers == {
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
            "X-Content-Type-Options": "nosniff",
        }


def test_query_parameters_are_typed_and_forwarded_without_private_echo() -> None:
    response = M2ReadonlyHttpAdapter(_service()).dispatch(
        _request("/api/v1/attempts?lineage_id=lineage-1&status=completed&parent_session_id=session-1")
    )
    assert response.status_code == 200
    assert response.envelope.data["count"] == 1
    assert "private task text" not in str(response.envelope.model_dump(mode="json"))

    approvals = M2ReadonlyHttpAdapter(_service()).dispatch(
        _request("/api/v1/approvals?request_id=request-1&pending_only=true")
    )
    assert approvals.status_code == 200


@pytest.mark.parametrize(
    "target",
    [
        "https://example.test/api/v1/health",
        "//example.test/api/v1/health",
        "//[invalid/api/v1/health",
        "/api/v1/health#fragment",
        "/api/v1/sessions/../health",
        "/api/v1/sessions/%2e%2e",
        "/api/v1/sessions/session%2fescape",
        "/api/v1/sessions/session-1/",
        "/api/v1/attempts?unknown=value",
        "/api/v1/attempts?status=completed&status=failed",
        "/api/v1/attempts?lineage_id=",
        "/api/v1/approvals?pending_only=1",
        "/api/v1/health?token=secret",
    ],
)
def test_ambiguous_path_query_and_unknown_parameters_fail_closed(target: str) -> None:
    response = M2ReadonlyHttpAdapter(_service()).dispatch(_request(target))
    assert response.status_code == 400
    assert response.envelope.error.code is M2ErrorCode.INVALID_REQUEST
    assert response.envelope.request_id == "api-1"
    serialized = str(response.envelope.model_dump(mode="json"))
    assert target not in serialized
    assert "secret" not in serialized


@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/sessions/.",
        "/api/v1/sessions/..",
        "/api/v1/sessions/C:secret",
        "/api/v1/attempts?lineage_id=C:secret",
        "/api/v1/approvals?request_id=tenant:request",
    ],
)
def test_external_resource_ids_share_one_fail_closed_http_policy(target: str) -> None:
    response = M2ReadonlyHttpAdapter(_service()).dispatch(_request(target))
    assert response.status_code == 400
    assert response.envelope.error.code is M2ErrorCode.INVALID_REQUEST
    assert response.envelope.request_id == "api-1"


def test_unknown_route_and_non_get_method_have_stable_status_without_body_echo() -> None:
    adapter = M2ReadonlyHttpAdapter(_service())
    missing = adapter.dispatch(_request("/api/v1/private/token-secret"))
    assert missing.status_code == 404
    assert missing.envelope.error.code is M2ErrorCode.NOT_FOUND
    assert "token-secret" not in str(missing.envelope.model_dump(mode="json"))

    wrong_method = adapter.dispatch(_request("/api/v1/health", method="POST", body_size=128))
    assert wrong_method.status_code == 405
    assert wrong_method.envelope.error.code is M2ErrorCode.INVALID_REQUEST
    assert wrong_method.headers["Allow"] == "GET"


def test_missing_duplicate_invalid_request_id_and_headers_fail_closed() -> None:
    assert M2_CORRELATION_ID_POLICY_VERSION == "0.1"
    adapter = M2ReadonlyHttpAdapter(_service())
    cases = [
        (),
        (
            M2HttpHeader(name="X-Request-ID", value="api-1"),
            M2HttpHeader(name="x-request-id", value="api-2"),
        ),
        *[
            (M2HttpHeader(name="X-Request-ID", value=value),)
            for value in ("bad id", ".", "..", "api/1", r"api\1", "C:secret", "tenant:request", "会话-1", "a" * 129)
        ],
    ]
    for headers in cases:
        response = adapter.dispatch(_request("/api/v1/health", headers=headers))
        assert response.status_code == 400
        assert response.envelope.request_id == "invalid-request"
        assert response.envelope.error.code is M2ErrorCode.INVALID_REQUEST

    with pytest.raises(ValueError):
        M2HttpHeader(name="X-Test", value="safe\r\nInjected: true")


def test_request_header_count_and_aggregate_metadata_budget_fail_closed() -> None:
    request_id = M2HttpHeader(name="X-Request-ID", value="api-budget")
    with pytest.raises(ValueError, match="header 数量"):
        M2ReadonlyHttpRequest(
            method="GET",
            target="/api/v1/health",
            headers=(request_id, *(M2HttpHeader(name=f"X-Test-{index}", value="v") for index in range(64))),
        )
    with pytest.raises(ValueError, match="header 总字节"):
        M2ReadonlyHttpRequest(
            method="GET",
            target="/api/v1/health",
            headers=(
                request_id,
                *(M2HttpHeader(name=f"X-Test-{index}", value="x" * 2_000) for index in range(17)),
            ),
        )


def test_get_body_and_oversized_or_non_ascii_target_fail_closed() -> None:
    adapter = M2ReadonlyHttpAdapter(_service())
    body = adapter.dispatch(_request("/api/v1/health", body_size=1))
    assert body.status_code == 400
    assert body.envelope.error.code is M2ErrorCode.INVALID_REQUEST

    for target in ("/api/v1/sessions/会话", "/" + "a" * 4096):
        with pytest.raises(ValueError):
            _request(target)


def test_error_code_http_mapping_is_total_and_stable() -> None:
    expected = {
        M2ErrorCode.INVALID_REQUEST: 400,
        M2ErrorCode.UNSUPPORTED_VERSION: 400,
        M2ErrorCode.UNAUTHORIZED: 401,
        M2ErrorCode.FORBIDDEN: 403,
        M2ErrorCode.NOT_FOUND: 404,
        M2ErrorCode.CONFLICT: 409,
        M2ErrorCode.IDEMPOTENCY_CONFLICT: 409,
        M2ErrorCode.APPROVAL_REQUIRED: 409,
        M2ErrorCode.TERMINAL_STATE_CONFLICT: 409,
        M2ErrorCode.REPLAY_CURSOR_EXPIRED: 409,
        M2ErrorCode.PAYLOAD_TOO_LARGE: 413,
        M2ErrorCode.RATE_LIMITED: 429,
        M2ErrorCode.RUNTIME_UNAVAILABLE: 503,
        M2ErrorCode.INTERNAL_ERROR: 500,
    }
    assert {code: m2_error_http_status(code) for code in M2ErrorCode} == expected


def test_adapter_has_no_listener_runtime_ipc_or_file_side_effects(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    adapter = M2ReadonlyHttpAdapter(_service())
    adapter.dispatch(_request("/api/v1/health"))
    assert tuple(tmp_path.rglob("*")) == before == ()
    for forbidden in ("start", "listen", "serve", "bind", "connect", "runtime", "ipc_client", "token"):
        assert not hasattr(adapter, forbidden)
