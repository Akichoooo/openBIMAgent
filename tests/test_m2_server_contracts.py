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
    m2_error_is_retryable,
    make_m2_api_error,
)
from openbimagent.server.artifact_path import (
    M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION,
    is_m2_artifact_relative_path,
    validate_m2_artifact_relative_path,
)
from openbimagent.server.correlation_identity import (
    M2_CORRELATION_ID_POLICY_VERSION,
    is_m2_correlation_id,
    validate_m2_correlation_id,
)
from openbimagent.server.resource_identity import (
    M2_RESOURCE_ID_POLICY_VERSION,
    is_m2_resource_id,
    validate_m2_resource_id,
)
from openbimagent.server.sse_identity import (
    M2_SSE_STREAM_ID_POLICY_VERSION,
    is_m2_sse_stream_id,
    validate_m2_sse_stream_id,
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


@pytest.mark.parametrize(
    "value",
    [".", "..", "../api-1", "api/1", r"api\1", "C:secret", "tenant:request", " name", "name ", "会话-1", "a" * 129],
)
def test_correlation_identity_policy_rejects_path_or_ambiguous_values(value: str) -> None:
    assert is_m2_correlation_id(value) is False
    with pytest.raises(ValueError, match="关联标识"):
        validate_m2_correlation_id(value)
    with pytest.raises(ValidationError, match="关联标识"):
        M2ApiEnvelope(request_id=value, ok=True, data={})
    with pytest.raises(ValidationError, match="关联标识"):
        make_m2_api_error(code=M2ErrorCode.INVALID_REQUEST, message="invalid", request_id=value)


def test_correlation_identity_policy_is_versioned_and_does_not_reclassify_sse_attempt_id() -> None:
    assert M2_CORRELATION_ID_POLICY_VERSION == "0.1"
    for value in ("api-1", "trace_parent.1", "tenant-request", "client@node"):
        assert is_m2_correlation_id(value) is True
        assert validate_m2_correlation_id(value) == value
    sse_request_schema = M2SseEvent.model_json_schema()["properties"]["request_id"]
    assert "x-openbimagent-correlation-id-policy" not in sse_request_schema
    sse_attempt_identity = next(branch for branch in sse_request_schema["anyOf"] if branch.get("type") == "string")
    assert sse_attempt_identity["pattern"] == "^[A-Za-z0-9_.:@/-]+$"


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
            retryable=False,
            request_id="api-1",
            details={"bearer_token": "secret"},
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        M2ApiEnvelope(request_id="api-1", ok=True, data={}, actor={"actor_id": "human:spoof"})


def test_api_error_retryability_is_derived_from_stable_code() -> None:
    assert m2_error_is_retryable(M2ErrorCode.RATE_LIMITED) is True
    assert m2_error_is_retryable(M2ErrorCode.RUNTIME_UNAVAILABLE) is True
    for code in set(M2ErrorCode) - {M2ErrorCode.RATE_LIMITED, M2ErrorCode.RUNTIME_UNAVAILABLE}:
        assert m2_error_is_retryable(code) is False

    runtime_error = make_m2_api_error(
        code=M2ErrorCode.RUNTIME_UNAVAILABLE,
        message="Runtime unavailable",
        request_id="api-runtime",
    )
    invalid_error = make_m2_api_error(
        code=M2ErrorCode.INVALID_REQUEST,
        message="Invalid request",
        request_id="api-invalid",
    )
    assert runtime_error.retryable is True
    assert invalid_error.retryable is False


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (M2ErrorCode.INTERNAL_ERROR, True),
        (M2ErrorCode.CONFLICT, True),
        (M2ErrorCode.RATE_LIMITED, False),
        (M2ErrorCode.RUNTIME_UNAVAILABLE, False),
    ],
)
def test_api_error_rejects_retryability_that_conflicts_with_code(
    code: M2ErrorCode,
    retryable: bool,
) -> None:
    with pytest.raises(ValidationError, match="retryable 必须由稳定错误码决定"):
        M2ApiError(
            code=code,
            message="safe error",
            retryable=retryable,
            request_id="api-retry-policy",
        )


def test_api_error_schema_freezes_retryability_policy() -> None:
    runtime_schema = M2ApiError.model_json_schema()
    baseline = json.loads((ROOT / "schemas" / "m2_api_envelope.schema.json").read_text(encoding="utf-8"))
    error_schema = baseline["$defs"]["error"]
    assert runtime_schema["x-openbimagent-retry-policy"] == "0.1"
    assert runtime_schema["allOf"] == error_schema["allOf"]
    assert error_schema["x-openbimagent-retry-policy"] == "0.1"

    retryable_runtime = make_m2_api_error(
        code=M2ErrorCode.RUNTIME_UNAVAILABLE,
        message="Runtime unavailable",
        request_id="api-runtime",
    )
    payload = M2ApiEnvelope(request_id="api-runtime", ok=False, error=retryable_runtime).model_dump(mode="json")
    assert validate_artifact("m2_api_envelope", payload) == []
    payload["error"]["retryable"] = False
    assert validate_artifact("m2_api_envelope", payload)


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
            retryable=False,
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


@pytest.mark.parametrize(
    "value",
    [".", "..", "../event-1", "event/1", r"event\1", "C:event-1", "tenant:event", " event", "事件-1", "a" * 129],
)
def test_sse_stream_identity_policy_rejects_path_or_ambiguous_values(value: str) -> None:
    assert is_m2_sse_stream_id(value) is False
    with pytest.raises(ValueError, match="SSE 流标识"):
        validate_m2_sse_stream_id(value)
    for field in ("event_id", "session_id"):
        with pytest.raises(ValidationError, match="SSE 流标识"):
            _event(**{field: value})
    with pytest.raises(ValidationError, match="SSE 流标识"):
        M2SseCursor(session_id=value, last_event_id="event-1", last_sequence=1)
    with pytest.raises(ValidationError, match="SSE 流标识"):
        M2SseCursor(session_id="session-1", last_event_id=value, last_sequence=1)


def test_sse_stream_identity_policy_is_versioned_without_reclassifying_attempt_identity() -> None:
    assert M2_SSE_STREAM_ID_POLICY_VERSION == "0.1"
    for value in ("session-1", "evt-" + "a" * 64, "stream.node_1", "tenant@session"):
        assert is_m2_sse_stream_id(value) is True
        assert validate_m2_sse_stream_id(value) == value
    event = _event(request_id="tenant:attempt/1", lineage_id="lineage:branch/1")
    assert event.request_id == "tenant:attempt/1"
    assert event.lineage_id == "lineage:branch/1"


def test_sse_schemas_declare_stream_identity_policy() -> None:
    event_runtime = M2SseEvent.model_json_schema()["properties"]
    cursor_runtime = M2SseCursor.model_json_schema()["properties"]
    event_baseline = json.loads((ROOT / "schemas" / "m2_sse_event.schema.json").read_text(encoding="utf-8"))[
        "properties"
    ]
    cursor_baseline = json.loads((ROOT / "schemas" / "m2_sse_cursor.schema.json").read_text(encoding="utf-8"))[
        "properties"
    ]
    for schema in (
        event_runtime["event_id"],
        event_runtime["session_id"],
        cursor_runtime["session_id"],
        cursor_runtime["last_event_id"],
        event_baseline["event_id"],
        event_baseline["session_id"],
        cursor_baseline["session_id"],
        cursor_baseline["last_event_id"],
    ):
        assert schema["x-openbimagent-sse-stream-id-policy"] == "0.1"
        assert schema["pattern"] == "^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$"


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "./result.ifc",
        "folder/../result.ifc",
        "/result.ifc",
        r"folder\result.ifc",
        r"folder/result\file.ifc",
        "C:result.ifc",
        "C:/result.ifc",
        r"\\server\share\result.ifc",
        r"\\?\C:\result.ifc",
        "result.ifc:secret",
        "result?.ifc",
        "result*.ifc",
        "result|draft.ifc",
        'result"draft.ifc',
        "result<draft>.ifc",
        "CON",
        "aux.json",
        "folder/NUL.txt",
    ],
)
def test_artifact_relative_path_policy_rejects_windows_and_ambiguous_paths(value: str) -> None:
    assert is_m2_artifact_relative_path(value) is False
    with pytest.raises(ValueError, match="工件相对路径"):
        validate_m2_artifact_relative_path(value)


def test_artifact_relative_path_policy_is_versioned_and_preserves_canonical_posix() -> None:
    assert M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION == "0.1"
    for value in ("result.ifc", "agent-1/result.ifc", "nested.v1/data_01.json"):
        assert is_m2_artifact_relative_path(value) is True
        assert validate_m2_artifact_relative_path(value) == value
    assert is_m2_artifact_relative_path("a" * 512) is True
    assert is_m2_artifact_relative_path("a" * 513) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "token=artifact-secret"),
        ("kind", "D:/private/artifact-kind"),
        ("source_attempt_id", "D:/private/request-1"),
    ],
)
def test_artifact_metadata_applies_shared_remote_payload_privacy_policy(field: str, value: str) -> None:
    payload = {
        "artifact_id": "artifact-privacy",
        "kind": "ifc",
        "media_type": "application/x-step",
        "sha256": "a" * 64,
        "size_bytes": 128,
        "status": "completed",
        "source_attempt_id": "request-1",
    }
    payload[field] = value
    with pytest.raises(ValidationError, match="远程载荷"):
        M2ArtifactMetadata(**payload)


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


@pytest.mark.parametrize(
    "value",
    [".", "..", "../request-1", "request/1", r"request\1", "D:request-1", "name with spaces", "会话-1"],
)
def test_external_resource_identity_policy_rejects_path_or_ambiguous_values(value: str) -> None:
    assert is_m2_resource_id(value) is False
    with pytest.raises(ValueError, match="外部资源标识"):
        validate_m2_resource_id(value)
    with pytest.raises(ValidationError, match="外部资源标识"):
        M2ControlRequest(
            operation="attempt.cancel",
            resource_id=value,
            idempotency_key="cancel-invalid-resource",
        )


def test_external_resource_identity_policy_is_versioned_and_preserves_safe_ids() -> None:
    assert M2_RESOURCE_ID_POLICY_VERSION == "0.1"
    for value in ("request-1", "session_parent", "artifact.v1", "tenant@resource"):
        assert is_m2_resource_id(value) is True
        assert validate_m2_resource_id(value) == value
    assert is_m2_resource_id("a" * 200) is True
    assert is_m2_resource_id("a" * 201) is False


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


def test_api_schema_declares_shared_correlation_identity_policy() -> None:
    runtime = M2ApiEnvelope.model_json_schema()
    baseline = json.loads((ROOT / "schemas" / "m2_api_envelope.schema.json").read_text(encoding="utf-8"))
    runtime_error = runtime["$defs"]["M2ApiError"]
    baseline_error = baseline["$defs"]["error"]
    for schema in (
        runtime["properties"]["request_id"],
        runtime_error["properties"]["request_id"],
        baseline["properties"]["request_id"],
        baseline_error["properties"]["request_id"],
    ):
        assert schema["x-openbimagent-correlation-id-policy"] == "0.1"
        assert schema["pattern"] == "^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$"

    payload = M2ApiEnvelope(request_id="api-1", ok=True, data={}).model_dump(mode="json")
    for invalid in (".", "..", "api/1", r"api\1", "C:secret", "tenant:request", "a" * 129):
        payload["request_id"] = invalid
        assert validate_artifact("m2_api_envelope", payload)


def test_protocol_schemas_declare_shared_resource_identity_policy() -> None:
    control_runtime = M2ControlRequest.model_json_schema()["properties"]["resource_id"]
    artifact_schema = M2ArtifactMetadata.model_json_schema()
    artifact_runtime = artifact_schema["properties"]["artifact_id"]
    assert artifact_schema["x-openbimagent-remote-payload-policy"] == "0.1"
    control_baseline = json.loads(
        (ROOT / "schemas" / "m2_control_request.schema.json").read_text(encoding="utf-8")
    )["properties"]["resource_id"]
    artifact_baseline_schema = json.loads(
        (ROOT / "schemas" / "m2_artifact_metadata.schema.json").read_text(encoding="utf-8")
    )
    artifact_baseline = artifact_baseline_schema["properties"]["artifact_id"]
    assert artifact_baseline_schema["x-openbimagent-remote-payload-policy"] == "0.1"
    for schema in (control_runtime, artifact_runtime, control_baseline, artifact_baseline):
        assert schema["x-openbimagent-resource-id-policy"] == "0.1"
        assert schema["pattern"] == "^[A-Za-z0-9_@-][A-Za-z0-9_.@-]{0,199}$"
    assert artifact_runtime["maxLength"] == artifact_baseline["maxLength"] == 128

    control = M2ControlRequest(
        operation="attempt.cancel",
        resource_id="request-1",
        idempotency_key="cancel-schema-resource",
    ).model_dump(mode="json")
    artifact = M2ArtifactMetadata(
        artifact_id="artifact-1",
        kind="ifc",
        media_type="application/x-step",
        sha256="a" * 64,
        size_bytes=1,
        status="completed",
    ).model_dump(mode="json")
    for invalid in (".", "..", "D:secret", "request/1"):
        control["resource_id"] = invalid
        artifact["artifact_id"] = invalid
        assert validate_artifact("m2_control_request", control)
        assert validate_artifact("m2_artifact_metadata", artifact)


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
