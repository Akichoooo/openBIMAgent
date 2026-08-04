"""M2 P2 pre-G7 OpenAPI 3.1 与纯只读 service adapter 正负向测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.orchestrator.contracts import ArtifactRecord, ArtifactStatus, SubagentStatus
from openbimagent.orchestrator.control_plane import ApprovalView, AttemptView, ControlPlaneError
from openbimagent.server.contracts import M2ErrorCode
from openbimagent.server.openapi import (
    build_m2_readonly_openapi,
    canonical_openapi_bytes,
    canonical_openapi_sha256,
)
from openbimagent.server.service import M2ReadOnlyService

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_BASELINE = ROOT / "schemas" / "m2_readonly.openapi.json"
NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class FakeControlPlane:
    def __init__(self) -> None:
        self.attempt = AttemptView(
            request_id="request-1",
            agent_id="agent-1",
            parent_session_id="session-parent",
            child_session_id="session-child",
            role="worker",
            lineage_id="lineage-1",
            attempt_number=1,
            resumed_from_request_id=None,
            status=SubagentStatus.COMPLETED,
            phase="terminal",
            updated_at=NOW,
            result_hint="private result hint",
            error_code=None,
            receipt_id="receipt-1",
            artifact_count=1,
        )
        self.approval = ApprovalView(
            approval_id="approval-1",
            request_id="request-1",
            agent_id="agent-1",
            parent_session_id="session-parent",
            child_session_id="session-child",
            tool_name="write",
            permission_key="write",
            args_summary="private/path.txt TOP-SECRET",
            args_sha256="a" * 64,
            requested_at=NOW,
            pending=False,
            decision="approved",
            decided_by=ActorRef(actor_id="human:jy", actor_type=ActorType.HUMAN),
            reason="private reason",
            decided_at=NOW,
            receipt_id="decision-1",
        )

    def list_attempts(self, **filters):
        if filters.get("status") == "invalid":
            raise ValueError("invalid status")
        return (self.attempt,)

    def get_attempt(self, request_id):
        if request_id != self.attempt.request_id:
            raise ControlPlaneError("unknown")
        return self.attempt

    def get_lineage(self, lineage_id):
        if lineage_id != self.attempt.lineage_id:
            raise ControlPlaneError("unknown")
        return (self.attempt,)

    def list_approvals(self, **filters):
        return (self.approval,)


def _service(*, artifact=None, control_plane=None, session_reader=None) -> M2ReadOnlyService:
    return M2ReadOnlyService(
        control_plane=control_plane or FakeControlPlane(),
        session_index_reader=session_reader
        or (
            lambda: [
                {
                    "id": "session-parent",
                    "title": "Parent",
                    "playbook": "private-playbook",
                    "created_at": NOW.isoformat(),
                    "last_active": NOW.isoformat(),
                    "event_count": 3,
                    "child_of": {"secret": "private"},
                }
            ]
        ),
        artifact_lookup=lambda artifact_id: artifact if artifact and artifact.artifact_id == artifact_id else None,
    )


def test_health_declares_pre_g7_zero_side_effect_boundaries() -> None:
    envelope = _service().health(request_id="api-1")
    assert envelope.ok is True
    assert envelope.data == {
        "service": "openbimagent-m2-readonly",
        "service_version": "0.1",
        "api_protocol_version": "1.0",
        "mode": "pre-g7-read-only",
        "status": "contract-ready",
        "network_listener_started": False,
        "runtime_lease_acquired": False,
        "write_control_enabled": False,
    }


def test_sessions_are_whitelisted_and_unknown_session_is_not_found() -> None:
    service = _service()
    listed = service.list_sessions(request_id="api-1")
    assert listed.ok is True
    assert listed.data["count"] == 1
    session = listed.data["items"][0]
    assert session["session_id"] == "session-parent"
    assert "playbook" not in session
    assert "child_of" not in session
    assert service.get_session(request_id="api-2", session_id="missing").error.code is M2ErrorCode.NOT_FOUND


def test_attempts_and_approvals_exclude_private_text_and_actor() -> None:
    service = _service()
    attempt = service.get_attempt(request_id="api-1", attempt_request_id="request-1").data["attempt"]
    assert "result_hint" not in attempt
    approval = service.list_approvals(request_id="api-2").data["items"][0]
    assert "args_summary" not in approval
    assert "reason" not in approval
    assert "decided_by" not in approval
    assert "TOP-SECRET" not in str(approval)


def test_lineage_and_attempt_filters_fail_closed() -> None:
    service = _service()
    assert service.get_lineage(request_id="api-1", lineage_id="lineage-1").data["count"] == 1
    assert service.get_lineage(request_id="api-2", lineage_id="../escape").error.code is M2ErrorCode.INVALID_REQUEST
    assert service.list_attempts(request_id="api-3", status="invalid").error.code is M2ErrorCode.INVALID_REQUEST
    assert service.get_attempt(request_id="api-4", attempt_request_id="missing").error.code is M2ErrorCode.NOT_FOUND


def test_control_plane_conflicts_do_not_expose_internal_exception() -> None:
    class Broken(FakeControlPlane):
        def list_attempts(self, **filters):
            raise ControlPlaneError("D:/private/runtime.json token=secret")

    envelope = _service(control_plane=Broken()).list_attempts(request_id="api-1")
    assert envelope.error.code is M2ErrorCode.CONFLICT
    assert "private" not in envelope.error.message
    assert envelope.error.details == {}


def test_artifact_metadata_never_exposes_path_or_enables_download() -> None:
    artifact = ArtifactRecord(
        artifact_id="artifact-1",
        kind="ifc",
        path="D:/private/result.ifc",
        relative_path="result.ifc",
        media_type="application/x-step",
        sha256="b" * 64,
        size_bytes=1024,
        source_attempt_id="request-1",
        status=ArtifactStatus.COMPLETED,
    )
    payload = _service(artifact=artifact).get_artifact_metadata(
        request_id="api-1", artifact_id="artifact-1"
    ).data["artifact"]
    assert "path" not in payload
    assert "relative_path" not in payload
    assert payload["download_available"] is False


def test_artifact_protocol_drift_maps_to_safe_conflict() -> None:
    artifact = ArtifactRecord(
        artifact_id="artifact-1",
        kind="json",
        path="D:/private/a.json",
        media_type="application/json; charset=utf-8",
        sha256="c" * 64,
        size_bytes=1,
    )
    envelope = _service(artifact=artifact).get_artifact_metadata(
        request_id="api-1", artifact_id="artifact-1"
    )
    assert envelope.error.code is M2ErrorCode.CONFLICT
    assert envelope.error.message == "artifact 元数据不满足远程协议"


def test_artifact_identity_conflict_and_invalid_resource_fail_closed() -> None:
    artifact = ArtifactRecord(
        artifact_id="different",
        kind="json",
        path="D:/private/a.json",
        media_type="application/json",
        sha256="c" * 64,
        size_bytes=1,
    )
    service = M2ReadOnlyService(
        control_plane=FakeControlPlane(),
        session_index_reader=lambda: [],
        artifact_lookup=lambda _: artifact,
    )
    assert service.get_artifact_metadata(request_id="api-1", artifact_id="artifact-1").error.code is M2ErrorCode.CONFLICT
    assert service.get_artifact_metadata(request_id="api-2", artifact_id="../secret").error.code is M2ErrorCode.INVALID_REQUEST


def test_session_reader_failure_maps_to_safe_internal_error() -> None:
    def broken_reader():
        raise RuntimeError("C:/private/index.json bearer_token=secret")

    envelope = _service(session_reader=broken_reader).list_sessions(request_id="api-1")
    assert envelope.error.code is M2ErrorCode.INTERNAL_ERROR
    assert envelope.error.retryable is False
    assert "private" not in envelope.error.message


def test_openapi_31_baseline_is_deterministic_and_matches_signed_file() -> None:
    document = build_m2_readonly_openapi()
    assert document["openapi"] == "3.1.0"
    assert document["servers"] == []
    assert OPENAPI_BASELINE.read_bytes() == canonical_openapi_bytes(document)
    assert b"#/$defs/" not in canonical_openapi_bytes(document)
    assert b"#/components/schemas/M2ApiError" in canonical_openapi_bytes(document)
    assert canonical_openapi_sha256(document) == canonical_openapi_sha256()
    assert len(canonical_openapi_sha256()) == 64


def test_openapi_components_preserve_p1_cross_field_semantics() -> None:
    document = build_m2_readonly_openapi()
    resolver = Draft202012Validator(
        {
            "$ref": "#/components/schemas/M2ApiEnvelope",
            "components": document["components"],
        }
    )
    invalid_success = {
        "protocol_version": "1.0",
        "request_id": "api-1",
        "ok": True,
        "data": None,
        "error": None,
    }
    assert list(resolver.iter_errors(invalid_success))
    artifact_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/M2ArtifactMetadata",
            "components": document["components"],
        }
    )
    invalid_download = {
        "protocol_version": "1.0",
        "artifact_id": "artifact-1",
        "kind": "checkpoint",
        "media_type": "application/json",
        "sha256": "d" * 64,
        "size_bytes": 1,
        "immutable": True,
        "status": "partial",
        "source_attempt_id": None,
        "download_available": True,
    }
    assert list(artifact_validator.iter_errors(invalid_download))


def test_openapi_contains_only_eight_read_only_operations() -> None:
    document = build_m2_readonly_openapi()
    assert len(document["paths"]) == 8
    operations = []
    for path_item in document["paths"].values():
        operations.extend(key for key in path_item if key in {"get", "post", "put", "patch", "delete"})
    assert operations == ["get"] * 8
    serialized = canonical_openapi_bytes(document).decode("utf-8").lower()
    for forbidden in ("bearer_token", "ipc_token", "authorization", "file_path"):
        assert forbidden not in serialized
    all_parameters = [
        parameter["name"].lower()
        for path_item in document["paths"].values()
        for parameter in path_item["get"]["parameters"]
    ]
    assert all("token" not in name and "path" not in name for name in all_parameters)
    assert document["x-openbimagent-boundaries"]["write_control_enabled"] is False


def test_openapi_declares_adapter_method_rejection_contract() -> None:
    document = build_m2_readonly_openapi()
    for path_item in document["paths"].values():
        method_error = path_item["get"]["responses"]["405"]
        assert method_error["headers"]["Allow"]["required"] is True
        assert method_error["headers"]["Allow"]["schema"] == {"type": "string", "const": "GET"}


def test_openapi_requires_request_id_and_has_no_security_claim() -> None:
    document = build_m2_readonly_openapi()
    assert "securitySchemes" not in document["components"]
    for path_item in document["paths"].values():
        operation = path_item["get"]
        request_header = next(item for item in operation["parameters"] if item["name"] == "X-Request-ID")
        assert request_header["required"] is True


def test_service_constructor_does_not_create_files_or_directories(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    _service()
    after = tuple(tmp_path.rglob("*"))
    assert before == after == ()


def test_service_does_not_expose_write_control_methods() -> None:
    methods = {name for name in dir(M2ReadOnlyService) if not name.startswith("_")}
    assert methods == {
        "get_artifact_metadata",
        "get_attempt",
        "get_lineage",
        "get_session",
        "health",
        "list_approvals",
        "list_attempts",
        "list_sessions",
    }
    for forbidden in ("approve", "cancel", "resume", "steer", "dispatch", "start", "listen"):
        assert forbidden not in methods


@pytest.mark.parametrize("value", ["", "../x", "x/y", "C:secret", "name with spaces"])
def test_resource_ids_reject_path_like_or_ambiguous_values(value: str) -> None:
    envelope = _service().get_session(request_id="api-1", session_id=value)
    assert envelope.error.code is M2ErrorCode.INVALID_REQUEST
