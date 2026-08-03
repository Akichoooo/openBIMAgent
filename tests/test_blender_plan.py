"""G6 typed Blender execution plan tests; all offline."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from openbimagent.assembly.blender_plan import (
    BlenderBuilder,
    BlenderCapabilities,
    BlenderExecutionPlan,
    BlenderObjectType,
    BlenderOperationKind,
    BlenderPlanError,
    BlenderPrimitive,
    BlenderReceiptStatus,
    FakeBlenderExecutor,
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


def _plan(rule_identity: RuleProjectionIdentity | None = None) -> BlenderExecutionPlan:
    return BlenderBuilder().build(
        CompiledUtilityIR.model_validate(solved_payload()),
        rule_identity=rule_identity,
    )


def test_same_ir_compiles_to_canonical_equivalent_plan() -> None:
    first = solved_payload()
    second = solved_payload()
    second["nodes"].reverse()
    second["evidence"].reverse()
    plan_a = BlenderBuilder().build(first)
    plan_b = BlenderBuilder().build(second)
    assert plan_a.canonical_json() == plan_b.canonical_json()
    assert plan_a.canonical_sha256 == plan_b.canonical_sha256
    assert plan_a.idempotency_key == plan_b.idempotency_key
    assert plan_a.plan_id == plan_b.plan_id


def test_plan_has_explicit_host_semantics_and_allowlisted_operations() -> None:
    plan = _plan()
    assert {item.operation for item in plan.operations} == set(BlenderOperationKind)
    creates = [item for item in plan.operations if item.operation is BlenderOperationKind.CREATE_OBJECT]
    assert {item.object_type for item in creates} >= {
        BlenderObjectType.UTILITY_SYSTEM,
        BlenderObjectType.MANHOLE,
        BlenderObjectType.DISTRIBUTION_PORT,
        BlenderObjectType.PIPE_SEGMENT,
    }
    assert all(item.collection_name == "M1-Municipal-Utility" for item in creates)
    assert all(item.object_name and item.primitive for item in creates)
    segment = next(item for item in creates if item.object_type is BlenderObjectType.PIPE_SEGMENT)
    assert segment.primitive is BlenderPrimitive.POLYLINE_CURVE
    assert segment.diameter_mm == 300.0
    assert len(segment.centerline) == 2
    properties = next(
        item
        for item in plan.operations
        if item.operation is BlenderOperationKind.SET_PROPERTIES and item.object_id == "pipe-001"
    )
    values = {item.property_name: item.value for item in properties.properties}
    assert values["openbim_ifc_class"] == "IfcPipeSegment"
    assert values["openbim_geometry_slope"] == pytest.approx(0.003)
    assert all(not hasattr(item, "code") for item in plan.operations)


def test_plan_passes_json_schema() -> None:
    assert SchemaGate().validate_artifact(
        "blender_execution_plan",
        _plan().model_dump(mode="json"),
    ) == []


def test_rule_identity_is_bound_to_plan_and_typed_properties() -> None:
    identity = _rule_identity()
    plan = _plan(identity)
    assert plan.rule_identity == identity
    for operation in plan.operations:
        if operation.operation is not BlenderOperationKind.SET_PROPERTIES:
            continue
        values = {item.property_name: item.value for item in operation.properties}
        assert values["openbim_domain_rule_evidence_bundle_sha256"] == "a" * 64
        assert values["openbim_domain_rule_evaluation_sha256"] == "b" * 64
        assert values["openbim_domain_rule_decision_status"] == "fail"
        assert values["openbim_domain_production_verification"] == "eligible"
    assert SchemaGate().validate_artifact(
        "blender_execution_plan",
        plan.model_dump(mode="json"),
    ) == []


def test_blender_plan_rejects_rule_identity_property_drift() -> None:
    payload = _plan(_rule_identity()).model_dump(mode="json")
    properties = next(
        item
        for item in payload["operations"]
        if item["operation"] == "set_properties"
    )["properties"]
    rule_hash = next(
        item
        for item in properties
        if item["property_name"] == "openbim_domain_rule_evaluation_sha256"
    )
    rule_hash["value"] = "c" * 64
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    with pytest.raises(ValidationError, match="rule identity"):
        BlenderExecutionPlan.model_validate(payload)


def test_unknown_field_missing_required_and_protocol_drift_fail_closed() -> None:
    payload = _plan().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        BlenderExecutionPlan.model_validate(payload)
    payload = _plan().model_dump(mode="json")
    create = next(item for item in payload["operations"] if item["operation"] == "create_object")
    create["object_name"] = None
    with pytest.raises(ValidationError, match="object_name"):
        BlenderExecutionPlan.model_validate(payload)
    payload = _plan().model_dump(mode="json")
    payload["host_api_version"] = "6.0"
    with pytest.raises(ValidationError):
        BlenderExecutionPlan.model_validate(payload)


def test_canonical_hash_tampering_fails_closed() -> None:
    payload = _plan().model_dump(mode="json")
    payload["canonical_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical_sha256"):
        BlenderExecutionPlan.model_validate(payload)


def test_unknown_operation_object_type_and_primitive_fail_closed() -> None:
    for field, value in (
        ("operation", "execute_script"),
        ("object_type", "freeform_mesh"),
        ("primitive", "python_generator"),
    ):
        payload = _plan().model_dump(mode="json")
        create = next(item for item in payload["operations"] if item["operation"] == "create_object")
        create[field] = value
        with pytest.raises(ValidationError):
            BlenderExecutionPlan.model_validate(payload)


def test_capability_validation_rejects_unauthorized_operation_object_and_primitive() -> None:
    plan = _plan()
    with pytest.raises(BlenderPlanError, match="不允许操作"):
        FakeBlenderExecutor().execute_plan(
            plan,
            capabilities=BlenderCapabilities(operations=(BlenderOperationKind.CREATE_OBJECT,)),
        )
    with pytest.raises(BlenderPlanError, match="不允许对象类型"):
        FakeBlenderExecutor().execute_plan(
            plan,
            capabilities=BlenderCapabilities(object_types=(BlenderObjectType.PIPE_SEGMENT,)),
        )
    with pytest.raises(BlenderPlanError, match="不允许 primitive"):
        FakeBlenderExecutor().execute_plan(
            plan,
            capabilities=BlenderCapabilities(primitives=(BlenderPrimitive.EMPTY,)),
        )


def test_reference_validation_rejects_unknown_port() -> None:
    payload = _plan().model_dump(mode="json")
    topology = next(item for item in payload["operations"] if item["operation"] == "connect_topology")
    topology["references"][1] = "missing-port"
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    tampered = BlenderExecutionPlan.model_validate(payload).finalized()
    with pytest.raises(BlenderPlanError, match="未知端口"):
        FakeBlenderExecutor().execute_plan(tampered)


def test_fake_executor_partial_retry_is_idempotent() -> None:
    plan = _plan()
    executor = FakeBlenderExecutor(fail_after_operations=4)
    partial = executor.execute_plan(plan, output_path="offline.blend")
    assert partial.status is BlenderReceiptStatus.PARTIAL
    confirmed_before = dict(executor.objects)
    executor.fail_after_operations = None
    completed = executor.execute_plan(plan, output_path="offline.blend")
    assert completed.status is BlenderReceiptStatus.COMPLETED
    assert len(executor.objects) == len(
        {
            item.object_id
            for item in plan.operations
            if item.operation is BlenderOperationKind.CREATE_OBJECT
        }
    )
    for object_id, operation in confirmed_before.items():
        assert executor.objects[object_id] == operation
    apply_calls = executor.apply_calls
    assert executor.execute_plan(plan, output_path="offline.blend") == completed
    assert executor.apply_calls == apply_calls


def test_fake_executor_recovers_across_restart(tmp_path) -> None:
    plan = _plan()
    state_path = tmp_path / "blender-state.json"
    first = FakeBlenderExecutor(fail_after_operations=5, state_path=state_path)
    assert first.execute_plan(plan, output_path=tmp_path / "case.blend").status is BlenderReceiptStatus.PARTIAL
    applied_before = sum(len(value) for value in first._applied.values())
    second = FakeBlenderExecutor(state_path=state_path)
    receipt = second.execute_plan(plan, output_path=tmp_path / "case.blend")
    assert receipt.status is BlenderReceiptStatus.COMPLETED
    assert second.apply_calls == len(plan.operations) - applied_before


def test_same_idempotency_key_with_different_semantics_conflicts() -> None:
    plan = _plan()
    executor = FakeBlenderExecutor()
    assert executor.execute_plan(plan).status is BlenderReceiptStatus.COMPLETED
    payload = deepcopy(plan.model_dump(mode="json"))
    create = next(item for item in payload["operations"] if item["operation"] == "create_object")
    create["object_name"] = "BL_M1_DIFFERENT"
    payload["canonical_sha256"] = ""
    payload["idempotency_key"] = ""
    different = BlenderExecutionPlan.model_validate(payload).finalized()
    conflict = different.model_copy(update={"idempotency_key": plan.idempotency_key})
    with pytest.raises((ValidationError, BlenderPlanError)):
        BlenderExecutionPlan.model_validate(conflict.model_dump(mode="json"))
