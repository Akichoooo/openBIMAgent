"""M2 P3 pre-G7 写控制纯函数预检核正负向测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.server.authentication import (
    M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION,
    M2AuthenticatedPrincipal,
)
from openbimagent.server.contracts import M2ControlRequest, M2ErrorCode
from openbimagent.server.control_preflight import (
    M2ControlPreflight,
    M2ControlPreflightError,
    M2ControlRole,
    M2IdempotencyDisposition,
    M2IdempotencyFact,
)

PREFLIGHT = M2ControlPreflight()
OPERATOR = ActorRef(actor_id="human:operator", actor_type=ActorType.HUMAN, display_name="Operator")
OPERATOR_PRINCIPAL = M2AuthenticatedPrincipal(
    actor=OPERATOR,
    roles=(M2ControlRole.OPERATOR,),
    authentication_context_sha256="a" * 64,
)


def _request(operation: str, **overrides) -> M2ControlRequest:
    payload = {
        "operation": operation,
        "resource_id": "request-1",
        "idempotency_key": f"key-{operation.replace('.', '-')}",
    }
    if operation == "approval.decide":
        payload.update(resource_id="approval-1", approved=True, reason="reviewed")
    elif operation in {"attempt.resume", "attempt.steer"}:
        payload["instruction"] = "check persisted facts first"
    payload.update(overrides)
    return M2ControlRequest(**payload)


def test_authenticated_principal_is_provider_neutral_secret_free_contract() -> None:
    assert M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION == "0.1"
    assert OPERATOR_PRINCIPAL.actor == OPERATOR
    assert OPERATOR_PRINCIPAL.roles == (M2ControlRole.OPERATOR,)
    payload = OPERATOR_PRINCIPAL.model_dump(mode="json")
    assert validate_artifact("m2_authenticated_principal", payload) == []
    serialized = str(payload).lower()
    for forbidden in ("token", "cookie", "password", "secret", "claims", "issuer", "subject"):
        assert forbidden not in serialized


@pytest.mark.parametrize("forbidden_field", ["token", "claims", "issuer", "subject", "cookie"])
def test_authenticated_principal_rejects_authentication_secret_or_provider_fields(
    forbidden_field: str,
) -> None:
    payload = OPERATOR_PRINCIPAL.model_dump(mode="json")
    payload[forbidden_field] = "sensitive"
    with pytest.raises(ValidationError):
        M2AuthenticatedPrincipal.model_validate(payload)
    assert validate_artifact("m2_authenticated_principal", payload)


def test_control_preflight_rejects_separate_actor_and_role_arguments() -> None:
    with pytest.raises(TypeError):
        PREFLIGHT.prepare(  # type: ignore[call-arg]
            actor=OPERATOR,
            role=M2ControlRole.OPERATOR,
            request=_request("attempt.cancel"),
        )


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.RUNTIME, ActorType.LEGACY])
def test_authenticated_principal_rejects_untrusted_remote_actor_types(actor_type: ActorType) -> None:
    with pytest.raises(ValidationError):
        M2AuthenticatedPrincipal(
            actor=ActorRef(actor_id=f"{actor_type.value}:spoof", actor_type=actor_type),
            roles=(M2ControlRole.OPERATOR,),
            authentication_context_sha256="a" * 64,
        )


@pytest.mark.parametrize("roles", [(), (M2ControlRole.OPERATOR, M2ControlRole.OPERATOR)])
def test_authenticated_principal_requires_nonempty_unique_roles(roles: tuple[M2ControlRole, ...]) -> None:
    with pytest.raises(ValidationError):
        M2AuthenticatedPrincipal(
            actor=OPERATOR,
            roles=roles,
            authentication_context_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    ("operation", "expected_payload"),
    [
        (
            "approval.decide",
            {"approval_id": "approval-1", "approved": True, "reason": "reviewed"},
        ),
        (
            "attempt.resume",
            {"source_request_id": "request-1", "instruction": "check persisted facts first"},
        ),
        (
            "attempt.steer",
            {"request_id": "request-1", "instruction": "check persisted facts first"},
        ),
        ("attempt.cancel", {"request_id": "request-1"}),
    ],
)
def test_operator_request_maps_to_exact_ipc_proxy_plan(operation: str, expected_payload: dict) -> None:
    request = _request(operation)
    plan = PREFLIGHT.prepare(principal=OPERATOR_PRINCIPAL, request=request)
    assert plan.actor == OPERATOR
    assert plan.operation.value == operation
    assert plan.ipc_operation == operation
    assert plan.ipc_payload == expected_payload
    assert len(plan.idempotency_scope_sha256) == 64
    assert len(plan.semantic_fingerprint) == 64
    serialized = str(plan.model_dump(mode="json"))
    assert "bearer_token" not in serialized
    assert "ipc_token" not in serialized
    assert "capability" not in serialized


def test_fingerprints_are_deterministic_and_operation_scoped() -> None:
    first = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.cancel", idempotency_key="shared-key"),
    )
    repeated = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.cancel", idempotency_key="shared-key"),
    )
    other_operation = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.steer", idempotency_key="shared-key"),
    )
    assert first == repeated
    assert first.idempotency_scope_sha256 != other_operation.idempotency_scope_sha256
    assert first.semantic_fingerprint != other_operation.semantic_fingerprint


def test_display_name_does_not_change_idempotency_scope() -> None:
    request = _request("attempt.cancel")
    first = PREFLIGHT.prepare(principal=OPERATOR_PRINCIPAL, request=request)
    renamed = PREFLIGHT.prepare(
        principal=M2AuthenticatedPrincipal(
            actor=OPERATOR.model_copy(update={"display_name": "Renamed"}),
            roles=(M2ControlRole.OPERATOR,),
            authentication_context_sha256="a" * 64,
        ),
        request=request,
    )
    assert first.idempotency_scope_sha256 == renamed.idempotency_scope_sha256
    assert first.semantic_fingerprint == renamed.semantic_fingerprint


@pytest.mark.parametrize("role", [M2ControlRole.VIEWER, M2ControlRole.ADMIN])
def test_non_operator_roles_cannot_execute_control(role: M2ControlRole) -> None:
    principal = M2AuthenticatedPrincipal(
        actor=OPERATOR,
        roles=(role,),
        authentication_context_sha256="a" * 64,
    )
    with pytest.raises(M2ControlPreflightError) as exc_info:
        PREFLIGHT.prepare(principal=principal, request=_request("attempt.cancel"))
    assert exc_info.value.code is M2ErrorCode.FORBIDDEN


@pytest.mark.parametrize("resource_id", ["../request-1", "D:secret", "request/1", ".", ".."])
def test_path_style_or_ambiguous_resource_ids_fail_closed(resource_id: str) -> None:
    with pytest.raises(ValidationError):
        _request("attempt.cancel", resource_id=resource_id)

    bypassed_request = M2ControlRequest.model_construct(
        protocol_version="1.0",
        operation="attempt.cancel",
        resource_id=resource_id,
        idempotency_key="bypassed-invalid-resource",
        approved=None,
        instruction=None,
        reason="",
    )
    with pytest.raises(M2ControlPreflightError) as exc_info:
        PREFLIGHT.prepare(principal=OPERATOR_PRINCIPAL, request=bypassed_request)
    assert exc_info.value.code is M2ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize("resource_id", [r"request\\1", "request 1"])
def test_generic_request_contract_rejects_invalid_characters_before_preflight(resource_id: str) -> None:
    with pytest.raises(ValidationError):
        _request("attempt.cancel", resource_id=resource_id)


def test_idempotency_reconcile_returns_new_or_original_receipt() -> None:
    plan = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.cancel"),
    )
    new = PREFLIGHT.reconcile(plan=plan, existing=None)
    assert new.disposition is M2IdempotencyDisposition.NEW
    assert new.receipt_id is None

    fact = M2IdempotencyFact(
        idempotency_scope_sha256=plan.idempotency_scope_sha256,
        semantic_fingerprint=plan.semantic_fingerprint,
        receipt_id="receipt-1",
    )
    replay = PREFLIGHT.reconcile(plan=plan, existing=fact)
    assert replay.disposition is M2IdempotencyDisposition.REPLAY
    assert replay.receipt_id == "receipt-1"


def test_same_scope_different_semantics_is_idempotency_conflict() -> None:
    original = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.steer", idempotency_key="shared-key", instruction="first"),
    )
    changed = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.steer", idempotency_key="shared-key", instruction="second"),
    )
    fact = M2IdempotencyFact(
        idempotency_scope_sha256=original.idempotency_scope_sha256,
        semantic_fingerprint=original.semantic_fingerprint,
        receipt_id="receipt-1",
    )
    with pytest.raises(M2ControlPreflightError) as exc_info:
        PREFLIGHT.reconcile(plan=changed, existing=fact)
    assert exc_info.value.code is M2ErrorCode.IDEMPOTENCY_CONFLICT


def test_mismatched_persisted_scope_fails_closed() -> None:
    plan = PREFLIGHT.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.cancel"),
    )
    fact = M2IdempotencyFact(
        idempotency_scope_sha256="0" * 64,
        semantic_fingerprint=plan.semantic_fingerprint,
        receipt_id="receipt-1",
    )
    with pytest.raises(M2ControlPreflightError) as exc_info:
        PREFLIGHT.reconcile(plan=plan, existing=fact)
    assert exc_info.value.code is M2ErrorCode.CONFLICT


def test_preflight_error_maps_to_safe_api_error() -> None:
    error = M2ControlPreflightError(M2ErrorCode.FORBIDDEN, "当前角色无权执行远程控制")
    api_error = error.to_api_error("api-1")
    assert api_error.code is M2ErrorCode.FORBIDDEN
    assert api_error.request_id == "api-1"
    assert api_error.details == {}
    assert api_error.retryable is False


def test_preflight_has_no_runtime_ipc_or_file_side_effects(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    preflight = M2ControlPreflight()
    plan = preflight.prepare(
        principal=OPERATOR_PRINCIPAL,
        request=_request("attempt.cancel"),
    )
    preflight.reconcile(plan=plan, existing=None)
    assert tuple(tmp_path.rglob("*")) == before == ()
    for forbidden in ("call", "connect", "dispatch", "start", "listen", "runtime", "ipc_client"):
        assert not hasattr(preflight, forbidden)
