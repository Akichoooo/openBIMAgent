"""G1 typed Vectorworks execution plan tests; all offline."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from openbimagent.assembly.vectorworks_plan import (
    FakeVectorworksExecutor,
    ReceiptStatus,
    VectorworksBuilder,
    VectorworksCapabilities,
    VectorworksExecutionPlan,
    VectorworksObjectType,
    VectorworksOperationKind,
    VectorworksPlanError,
)
from openbimagent.assembly.semantic_snapshot import RuleProjectionIdentity
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import CompiledUtilityIR
from test_compiled_utility_ir import solved_payload


def _rule_identity(**overrides) -> RuleProjectionIdentity:
    payload = {
        "rule_evidence_bundle_sha256": "a" * 64,
        "rule_evaluation_sha256": "b" * 64,
        "rule_decision_status": "fail",
        "production_verification": "eligible",
        "exception_approval_id": None,
        "exception_approval_sha256": None,
    }
    payload.update(overrides)
    return RuleProjectionIdentity(**payload)


def _plan(rule_identity: RuleProjectionIdentity | None = None) -> VectorworksExecutionPlan:
    return VectorworksBuilder().build(
        CompiledUtilityIR.model_validate(solved_payload()),
        rule_identity=rule_identity,
    )


def test_same_ir_compiles_to_canonical_equivalent_plan() -> None:
    first = solved_payload()
    second = solved_payload()
    second["nodes"].reverse()
    second["evidence"].reverse()
    plan_a = VectorworksBuilder().build(first)
    plan_b = VectorworksBuilder().build(second)
    assert plan_a.canonical_json() == plan_b.canonical_json()
    assert plan_a.canonical_sha256 == plan_b.canonical_sha256
    assert plan_a.idempotency_key == plan_b.idempotency_key
    assert plan_a.plan_id == plan_b.plan_id


def test_plan_has_explicit_host_semantics_and_allowlisted_operations() -> None:
    plan = _plan()
    assert {item.operation for item in plan.operations} == set(VectorworksOperationKind)
    creates = [item for item in plan.operations if item.operation is VectorworksOperationKind.CREATE_OBJECT]
    assert {item.object_type for item in creates} >= {
        VectorworksObjectType.UTILITY_SYSTEM,
        VectorworksObjectType.MANHOLE,
        VectorworksObjectType.DISTRIBUTION_PORT,
        VectorworksObjectType.PIPE_SEGMENT,
    }
    assert all(item.layer_name == "M1-Municipal-Utility" for item in creates)
    assert all(item.class_name and item.name for item in creates)
    segment = next(item for item in creates if item.object_type is VectorworksObjectType.PIPE_SEGMENT)
    assert segment.units == "m"
    assert segment.diameter_mm == 300.0
    assert len(segment.centerline) == 2
    assert segment.ifc_class == "IfcPipeSegment"
    segment_record = next(
        item
        for item in plan.operations
        if item.operation is VectorworksOperationKind.SET_RECORD
        and item.object_id == segment.object_id
    )
    units = {item.field_name: item.unit for item in segment_record.record_fields}
    assert units["DiameterMM"] == "mm"
    assert units["HorizontalLengthM"] == "m"
    assert units["StartInvertM"] == "m"
    assert units["EndInvertM"] == "m"
    assert units["Slope"] is None


def test_plan_passes_json_schema() -> None:
    plan = _plan()
    compiled = CompiledUtilityIR.model_validate(solved_payload())
    assert plan.compiled_ir_sha256 == compiled.canonical_sha256()
    assert SchemaGate().validate_artifact("vectorworks_execution_plan", plan.model_dump(mode="json")) == []


def test_rule_identity_is_bound_to_plan_and_typed_records() -> None:
    identity = _rule_identity()
    plan = _plan(identity)
    assert plan.rule_identity == identity
    for operation in plan.operations:
        if operation.operation is not VectorworksOperationKind.SET_RECORD:
            continue
        values = {item.field_name: item.value for item in operation.record_fields}
        assert values["Domain_rule_evidence_bundle_sha256"] == "a" * 64
        assert values["Domain_rule_evaluation_sha256"] == "b" * 64
        assert values["Domain_rule_decision_status"] == "fail"
        assert values["Domain_production_verification"] == "eligible"
    assert SchemaGate().validate_artifact(
        "vectorworks_execution_plan",
        plan.model_dump(mode="json"),
    ) == []


def test_vectorworks_plan_rejects_rule_identity_record_drift() -> None:
    payload = _plan(_rule_identity()).model_dump(mode="json")
    fields = next(
        item
        for item in payload["operations"]
        if item["operation"] == "set_record"
    )["record_fields"]
    rule_hash = next(
        item
        for item in fields
        if item["field_name"] == "Domain_rule_evaluation_sha256"
    )
    rule_hash["value"] = "c" * 64
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    with pytest.raises(ValidationError, match="rule identity"):
        VectorworksExecutionPlan.model_validate(payload)


def test_missing_compiled_ir_identity_fails_closed() -> None:
    payload = _plan().model_dump(mode="json")
    del payload["compiled_ir_sha256"]
    with pytest.raises(ValidationError):
        VectorworksExecutionPlan.model_validate(payload)
    errors = SchemaGate().validate_artifact("vectorworks_execution_plan", payload)
    assert any("compiled_ir_sha256" in item for item in errors)


def test_layer_scope_escape_fails_closed() -> None:
    payload = _plan().model_dump(mode="json")
    create = next(item for item in payload["operations"] if item["operation"] == "create_object")
    create["layer_name"] = "Escaped-Layer"
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    with pytest.raises(ValidationError, match="范围锁"):
        VectorworksExecutionPlan.model_validate(payload)


def test_missing_fields_fail_closed() -> None:
    payload = _plan().model_dump(mode="json")
    del payload["operations"][0]["layer_name"]
    with pytest.raises(ValidationError):
        VectorworksExecutionPlan.model_validate(payload)
    errors = SchemaGate().validate_artifact("vectorworks_execution_plan", payload)
    assert any("layer_name" in item for item in errors)


def test_illegal_version_and_tampered_hash_fail_closed() -> None:
    payload = _plan().model_dump(mode="json")
    payload["host_api_version"] = "2026"
    with pytest.raises(ValidationError):
        VectorworksExecutionPlan.model_validate(payload)
    payload = _plan().model_dump(mode="json")
    payload["canonical_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical_sha256"):
        VectorworksExecutionPlan.model_validate(payload)
    payload = _plan().model_dump(mode="json")
    payload["compiled_ir_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical_sha256"):
        VectorworksExecutionPlan.model_validate(payload)


def test_unknown_object_type_and_operation_fail_closed() -> None:
    for field, value in (("object_type", "freeform_script"), ("operation", "execute_script")):
        payload = _plan().model_dump(mode="json")
        payload["operations"][0][field] = value
        with pytest.raises(ValidationError):
            VectorworksExecutionPlan.model_validate(payload)


def test_capability_validation_rejects_unauthorized_operation_and_object() -> None:
    plan = _plan()
    with pytest.raises(VectorworksPlanError, match="不允许操作"):
        FakeVectorworksExecutor().execute_plan(
            plan,
            capabilities=VectorworksCapabilities(operations=(VectorworksOperationKind.CREATE_OBJECT,)),
        )
    with pytest.raises(VectorworksPlanError, match="不允许对象类型"):
        FakeVectorworksExecutor().execute_plan(
            plan,
            capabilities=VectorworksCapabilities(object_types=(VectorworksObjectType.PIPE_SEGMENT,)),
        )


def test_reference_validation_rejects_unknown_port() -> None:
    payload = _plan().model_dump(mode="json")
    topology = next(item for item in payload["operations"] if item["operation"] == "connect_topology")
    topology["references"][1] = "missing-port"
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    tampered = VectorworksExecutionPlan.model_validate(payload).finalized()
    with pytest.raises(VectorworksPlanError, match="未知端口"):
        FakeVectorworksExecutor().execute_plan(tampered)


def test_fake_executor_retry_does_not_duplicate_confirmed_objects() -> None:
    plan = _plan()
    executor = FakeVectorworksExecutor(fail_after_operations=4)
    partial = executor.execute_plan(plan)
    assert partial.status is ReceiptStatus.PARTIAL
    confirmed_before = dict(executor.objects)
    assert len(confirmed_before) > 0

    executor.fail_after_operations = None
    completed = executor.execute_plan(plan)
    assert completed.status is ReceiptStatus.COMPLETED
    assert len(executor.objects) == len({item.object_id for item in plan.operations if item.operation is VectorworksOperationKind.CREATE_OBJECT})
    for object_id, operation in confirmed_before.items():
        assert executor.objects[object_id] == operation

    repeated = executor.execute_plan(plan)
    assert repeated == completed
    assert executor.execute_calls == 3


def test_same_idempotency_key_with_different_semantics_conflicts() -> None:
    plan = _plan()
    executor = FakeVectorworksExecutor()
    assert executor.execute_plan(plan).status is ReceiptStatus.COMPLETED
    payload = deepcopy(plan.model_dump(mode="json"))
    payload["operations"][0]["name"] = "VW_M1_DIFFERENT"
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    different = VectorworksExecutionPlan.model_validate(payload).finalized()
    conflict = different.model_copy(update={"idempotency_key": plan.idempotency_key})
    with pytest.raises((ValidationError, VectorworksPlanError)):
        VectorworksExecutionPlan.model_validate(conflict.model_dump(mode="json"))
