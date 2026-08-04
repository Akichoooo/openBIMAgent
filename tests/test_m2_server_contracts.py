"""M2 P1 pre-G7 API/SSE/Artifact/Control 协议正负向测试。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from openbimagent.core.events import SSEEventType
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.server.contracts import (
    M2ApiEnvelope,
    M2ApiError,
    M2ArtifactMetadata,
    M2ControlRequest,
    M2ErrorCode,
    M2SseCursor,
    M2SseEvent,
    M2SseEventType,
)
from openbimagent.server.payload_privacy import (
    M2_REMOTE_PAYLOAD_POLICY_VERSION,
    RemotePayloadPrivacyError,
    validate_remote_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def _event(**overrides):
    data = {
        "event_id": "event-1",
        "event_type": M2SseEventType.PROGRESS,
        "session_id": "session-1",
        "request_id": "request-1",
        "lineage_id": "lineage-1",
        "attempt_number": 1,
        "sequence": 1,
        "occurred_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "terminal": False,
        "data": {"phase": "planning", "percent": 10},
    }
    data.update(overrides)
    return M2SseEvent(**data)


def test_api_envelope_success_and_error_are_exclusive() -> None:
    success = M2ApiEnvelope(request_id="api-1", ok=True, data={"items": []})
    assert success.error is None
    assert validate_artifact("m2_api_envelope", success.model_dump(mode="json")) == []

    error = M2ApiError(
        code=M2ErrorCode.RUNTIME_UNAVAILABLE,
        message="Runtime unavailable",
        retryable=True,
        request_id="api-2",
    )
    failure = M2ApiEnvelope(request_id="api-2", ok=False, error=error)
    assert validate_artifact("m2_api_envelope", failure.model_dump(mode="json")) == []

    with pytest.raises(ValidationError, match="成功响应必须携带 data"):
        M2ApiEnvelope(request_id="api-3", ok=True)
    with pytest.raises(ValidationError, match="必须一致"):
        M2ApiEnvelope(request_id="api-4", ok=False, error=error)


def test_api_error_rejects_sensitive_details_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="禁止敏感字段"):
        M2ApiError(
            code=M2ErrorCode.INTERNAL_ERROR,
            message="safe",
            request_id="api-1",
            details={"bearer_token": "secret"},
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        M2ApiEnvelope(request_id="api-1", ok=True, data={}, actor={"actor_id": "human:spoof"})


def test_sse_event_reuses_existing_data_types_and_requires_attempt_identity() -> None:
    assert M2SseEventType.PROGRESS.value == SSEEventType.PROGRESS.value
    event = _event()
    assert event.event_type.value == "data-progress"
    assert validate_artifact("m2_sse_event", event.model_dump(mode="json")) == []

    with pytest.raises(ValidationError, match="必须同时包含"):
        _event(lineage_id=None)
    with pytest.raises(ValidationError, match="terminal"):
        _event(terminal=True)
    terminal = _event(event_type=M2SseEventType.TERMINAL, terminal=True, data={"status": "completed"})
    assert terminal.terminal is True


def test_sse_event_rejects_nested_sensitive_data() -> None:
    with pytest.raises(ValidationError, match="禁止敏感字段"):
        _event(data={"nested": {"api_key": "do-not-expose"}})
    with pytest.raises(ValidationError, match="禁止敏感字段"):
        _event(data={"instruction": "raw instruction"})


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "Authorization: Bearer abc.def.ghi"},
        {"message": "token=super-secret-value"},
        {"message": "Traceback (most recent call last): boom"},
        {"message": "validation failed input_value={'password': 'secret'}"},
        {"message": "D:/private/runtime/discovery.json"},
        {"message": r"C:\\Users\\operator\\AppData\\Local\\runtime.json"},
        {"message": "/home/operator/.config/openbimagent/token.json"},
        {"nested": [{"safe_name": "ok"}, {"client-secret": "hidden"}]},
        {"nested": {"runtime_path": "relative-but-private.json"}},
        {"nested": {"authorization_header": "redacted"}},
    ],
)
def test_remote_payload_privacy_rejects_sensitive_keys_and_values(payload: dict) -> None:
    with pytest.raises(RemotePayloadPrivacyError):
        validate_remote_payload(payload)


def test_remote_payload_privacy_accepts_bounded_json_and_is_pure() -> None:
    payload = {
        "items": [{"request_id": "request-1", "status": "running"}],
        "count": 1,
        "complete": False,
        "ratio": 0.5,
        "optional": None,
    }
    assert M2_REMOTE_PAYLOAD_POLICY_VERSION == "0.1"
    assert validate_remote_payload(payload) is payload
    assert payload["items"][0]["status"] == "running"


@pytest.mark.parametrize(
    "payload",
    [
        {"value": b"not-json"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"items": list(range(1_001))},
    ],
)
def test_remote_payload_privacy_rejects_non_json_or_unbounded_values(payload: dict) -> None:
    with pytest.raises(RemotePayloadPrivacyError):
        validate_remote_payload(payload)


def test_remote_payload_privacy_rejects_excessive_depth() -> None:
    payload: dict = {"leaf": "safe"}
    for _ in range(17):
        payload = {"nested": payload}
    with pytest.raises(RemotePayloadPrivacyError):
        validate_remote_payload(payload)


def test_remote_payload_privacy_rejects_cycles_without_mutating_input() -> None:
    payload: dict = {}
    payload["cycle"] = payload
    with pytest.raises(RemotePayloadPrivacyError, match="循环引用"):
        validate_remote_payload(payload)
    assert payload["cycle"] is payload


def test_api_and_sse_models_apply_value_privacy_gate() -> None:
    with pytest.raises(ValidationError, match="远程载荷"):
        M2ApiEnvelope(
            request_id="api-privacy",
            ok=True,
            data={"status": "failed", "message": "D:/private/runtime.json"},
        )
    with pytest.raises(ValidationError, match="远程载荷"):
        M2ApiError(
            code=M2ErrorCode.INTERNAL_ERROR,
            message="Traceback (most recent call last): secret",
            request_id="api-error",
        )
    with pytest.raises(ValidationError, match="远程载荷"):
        _event(data={"message": "token=secret-value"})


def test_sse_cursor_is_scoped_and_strict() -> None:
    cursor = M2SseCursor(session_id="session-1", last_event_id="event-4", last_sequence=4)
    assert validate_artifact("m2_sse_cursor", cursor.model_dump(mode="json")) == []
    with pytest.raises(ValidationError):
        M2SseCursor(session_id="session-1", last_event_id="event-0", last_sequence=0)
    with pytest.raises(ValidationError, match="Extra inputs"):
        M2SseCursor(session_id="session-1", last_event_id="event-1", last_sequence=1, token="bad")


def test_artifact_metadata_never_exposes_path_and_only_completed_is_downloadable() -> None:
    artifact = M2ArtifactMetadata(
        artifact_id="artifact-1",
        kind="ifc",
        media_type="application/x-step",
        sha256="a" * 64,
        size_bytes=128,
        status="completed",
        source_attempt_id="request-1",
        download_available=True,
    )
    payload = artifact.model_dump(mode="json")
    assert "path" not in payload
    assert validate_artifact("m2_artifact_metadata", payload) == []

    with pytest.raises(ValidationError, match="只有 completed"):
        M2ArtifactMetadata(
            artifact_id="artifact-2",
            kind="checkpoint",
            media_type="application/json",
            sha256="b" * 64,
            size_bytes=10,
            status="partial",
            download_available=True,
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        M2ArtifactMetadata(
            artifact_id="artifact-3",
            kind="ifc",
            media_type="application/x-step",
            sha256="c" * 64,
            size_bytes=10,
            status="completed",
            path="D:/secret.ifc",
        )


def test_control_request_has_exact_operation_payload_and_no_actor_override() -> None:
    approval = M2ControlRequest(
        operation="approval.decide",
        resource_id="approval-1",
        idempotency_key="approve-1",
        approved=True,
        reason="reviewed",
    )
    assert validate_artifact("m2_control_request", approval.model_dump(mode="json")) == []

    resume = M2ControlRequest(
        operation="attempt.resume",
        resource_id="request-1",
        idempotency_key="resume-1",
        instruction="check external state first",
    )
    assert resume.instruction

    with pytest.raises(ValidationError, match="需要 instruction"):
        M2ControlRequest(operation="attempt.steer", resource_id="request-1", idempotency_key="steer-1")
    with pytest.raises(ValidationError, match="不接受"):
        M2ControlRequest(
            operation="attempt.cancel",
            resource_id="request-1",
            idempotency_key="cancel-1",
            reason="force",
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        M2ControlRequest(
            operation="attempt.cancel",
            resource_id="request-1",
            idempotency_key="cancel-2",
            actor={"actor_id": "human:spoof"},
        )


def test_json_schemas_declare_remote_payload_runtime_policy() -> None:
    api_schema = M2ApiEnvelope.model_json_schema()
    sse_schema = M2SseEvent.model_json_schema()
    api_baseline = json.loads((ROOT / "schemas" / "m2_api_envelope.schema.json").read_text(encoding="utf-8"))
    sse_baseline = json.loads((ROOT / "schemas" / "m2_sse_event.schema.json").read_text(encoding="utf-8"))
    assert api_schema["properties"]["data"]["x-openbimagent-remote-payload-policy"] == "0.1"
    assert sse_schema["properties"]["data"]["x-openbimagent-remote-payload-policy"] == "0.1"
    assert api_baseline["properties"]["data"]["x-openbimagent-remote-payload-policy"] == "0.1"
    assert sse_baseline["properties"]["data"]["x-openbimagent-remote-payload-policy"] == "0.1"


def test_json_schemas_reject_version_unknown_field_and_semantic_drift() -> None:
    event = _event().model_dump(mode="json")
    event["protocol_version"] = "2.0"
    assert any("1.0" in error for error in validate_artifact("m2_sse_event", event))

    terminal_drift = _event().model_dump(mode="json")
    terminal_drift["terminal"] = True
    assert validate_artifact("m2_sse_event", terminal_drift)

    control = M2ControlRequest(
        operation="attempt.cancel",
        resource_id="request-1",
        idempotency_key="cancel-1",
    ).model_dump(mode="json")
    control["bearer_token"] = "forbidden"
    assert any("bearer_token" in error for error in validate_artifact("m2_control_request", control))

    invalid_cancel = M2ControlRequest(
        operation="attempt.cancel",
        resource_id="request-1",
        idempotency_key="cancel-2",
    ).model_dump(mode="json")
    invalid_cancel["reason"] = "not allowed"
    assert validate_artifact("m2_control_request", invalid_cancel)

    partial_download = M2ArtifactMetadata(
        artifact_id="artifact-4",
        kind="checkpoint",
        media_type="application/json",
        sha256="d" * 64,
        size_bytes=10,
        status="partial",
    ).model_dump(mode="json")
    partial_download["download_available"] = True
    assert validate_artifact("m2_artifact_metadata", partial_download)

    invalid_success = M2ApiEnvelope(request_id="api-5", ok=True, data={}).model_dump(mode="json")
    invalid_success["data"] = None
    assert validate_artifact("m2_api_envelope", invalid_success)
