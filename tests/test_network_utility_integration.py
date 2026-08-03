"""M1.5 多节点 Solver 到 typed 双宿主语义比较的离线 E2E。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openbimagent.assembly.blender_plan import (
    BlenderBuilder,
    BlenderOperationKind,
    BlenderReceiptStatus,
    FakeBlenderExecutor,
)
from openbimagent.assembly.rule_projection import RuleProjectionIdentity
from openbimagent.assembly.semantic_snapshot import (
    FakeBlenderSemanticExecutor,
    FakeVectorworksSemanticExecutor,
    compare_semantic_snapshots,
)
from openbimagent.assembly.vectorworks_plan import (
    FakeVectorworksExecutor,
    ReceiptStatus,
    VectorworksBuilder,
    VectorworksOperationKind,
)
import ifcopenshell

from openbimagent.deliver.ifc_ids import build_ifc_ids_package
from openbimagent.domain_gate import GateStatus, evaluate_domain_gate
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import (
    apply_grid_route_t6_to_network_input,
    build_clearance_exception_approval,
    compile_municipal_rule_evidence_bundle,
    solve_grid_route_t6,
    solve_hydraulic_network,
    solve_network_gravity_utility,
)
from test_grid_route_solver import route_payload
from test_hydraulic_solver import hydraulic_payload
from test_network_utility_solver import network_payload


def _passing_hydraulic_payload() -> dict:
    payload = hydraulic_payload()
    for scenario in payload["scenarios"]:
        scenario["segment_flows"] = [
            {"segment_id": "pipe-001", "flow_m3_s": 0.024},
            {"segment_id": "pipe-002", "flow_m3_s": 0.012},
            {"segment_id": "pipe-003", "flow_m3_s": 0.012},
        ]
    return payload


def test_t6_exception_route_expands_into_network_before_solver() -> None:
    bundle = compile_municipal_rule_evidence_bundle()
    rule = bundle.rule("MU-CLEAR-001:building")
    approved_at = datetime(2026, 8, 1, tzinfo=UTC)
    approval = build_clearance_exception_approval(
        exception_id="EXC-T6-E2E-001",
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        original_rule_id=rule.rule_id,
        original_clearance_m=2.5,
        approved_clearance_m=2.0,
        safety_measures=("增设防护套管",),
        rationale="既有构筑物约束下的专项减距。",
        risks=("检修空间缩小",),
        approver_id="engineer-001",
        approver_role="chief_engineer",
        approver_authorities=("approve_clearance_reduction",),
        valid_scope={
            "project_id": "project-001",
            "subject_ids": ("building-001",),
            "rule_ids": (rule.rule_id,),
        },
        approved_at=approved_at,
        expires_at=approved_at + timedelta(days=30),
        approval_status="approved",
        audit_references=("approval://project-001/EXC-T6-E2E-001",),
    )
    route = route_payload(width=11, height=4)
    route["request_id"] = "pipe-001-t6-route"
    route["source_ir_sha256"] = "c" * 64
    route["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    route["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    for sample in route["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    route["obstacles"] = [
        {
            "obstacle_id": "building-001",
            "kind": "aabb",
            "category": "building",
            "min_corner": {"x_m": 4.8, "y_m": -0.2, "z_m": 0.0},
            "max_corner": {"x_m": 5.2, "y_m": 0.2, "z_m": 20.0},
        }
    ]
    evaluated_at = approved_at + timedelta(days=1)
    route_result = solve_grid_route_t6(
        route,
        rule_evidence_bundle=bundle,
        project_id="project-001",
        evaluated_at=evaluated_at,
        exception_approvals={"building-001": approval},
    )
    routed = apply_grid_route_t6_to_network_input(
        network_payload(),
        segment_id="pipe-001",
        route_input=route,
        route_result=route_result,
        rule_evidence_bundle=bundle,
        project_id="project-001",
        evaluated_at=evaluated_at,
        exception_approvals={"building-001": approval},
    )
    compiled = solve_network_gravity_utility(routed).compiled_ir
    assert route_result.obstacle_constraints[0].exception_approval_sha256 == approval.canonical_sha256
    assert any(segment.segment_id.startswith("pipe-001-route-") for segment in compiled.segments)
    assert compiled.canonical_sha256()


def test_network_solver_drives_typed_dual_host_semantic_e2e() -> None:
    compiled = solve_network_gravity_utility(network_payload()).compiled_ir
    bundle = compile_municipal_rule_evidence_bundle()
    hydraulic_result = solve_hydraulic_network(
        compiled,
        _passing_hydraulic_payload(),
        rule_evidence_bundle=bundle,
    )
    domain_report = evaluate_domain_gate(
        {"hydraulic_capacity_in_spec": True, "hydraulics_in_spec": True},
        hydraulic_result.domain_evidence(),
    )
    assert domain_report.status is GateStatus.PASS
    evaluation = hydraulic_result.rule_evaluation(
        compiled_ir=compiled,
        rule_evidence_bundle=bundle,
    )
    rule_identity = RuleProjectionIdentity.from_rule_evaluation(evaluation)
    assert rule_identity.rule_decision_status == "pass"
    blender_plan = BlenderBuilder().build(compiled, rule_identity=rule_identity)
    vectorworks_plan = VectorworksBuilder().build(compiled, rule_identity=rule_identity)
    gate = SchemaGate()

    assert gate.validate_artifact("compiled_utility_ir", compiled.model_dump(mode="json")) == []
    assert gate.validate_artifact("blender_execution_plan", blender_plan.model_dump(mode="json")) == []
    assert gate.validate_artifact("vectorworks_execution_plan", vectorworks_plan.model_dump(mode="json")) == []
    assert blender_plan.compiled_ir_sha256 == vectorworks_plan.compiled_ir_sha256 == compiled.canonical_sha256()

    expected_ids = {
        compiled.systems[0].system_id,
        *(node.node_id for node in compiled.nodes),
        *(port.port_id for node in compiled.nodes for port in node.ports),
        *(segment.segment_id for segment in compiled.segments),
    }
    blender_create_ids = {
        operation.object_id
        for operation in blender_plan.operations
        if operation.operation is BlenderOperationKind.CREATE_OBJECT
    }
    vectorworks_create_ids = {
        operation.object_id
        for operation in vectorworks_plan.operations
        if operation.operation is VectorworksOperationKind.CREATE_OBJECT
    }
    assert blender_create_ids == vectorworks_create_ids == expected_ids

    blender_receipt = FakeBlenderExecutor().execute_plan(blender_plan, output_path="offline-network.blend")
    vectorworks_receipt = FakeVectorworksExecutor().execute_plan(vectorworks_plan)
    assert blender_receipt.status is BlenderReceiptStatus.COMPLETED
    assert vectorworks_receipt.status is ReceiptStatus.COMPLETED

    blender_snapshot = FakeBlenderSemanticExecutor().execute(
        compiled,
        rule_identity=rule_identity,
    )
    vectorworks_snapshot = FakeVectorworksSemanticExecutor().execute(
        compiled,
        rule_identity=rule_identity,
    )
    assert len(blender_snapshot.objects) == len(vectorworks_snapshot.objects) == len(expected_ids) == 14
    assert gate.validate_artifact("semantic_snapshot", blender_snapshot.model_dump(mode="json")) == []
    assert gate.validate_artifact("semantic_snapshot", vectorworks_snapshot.model_dump(mode="json")) == []

    comparison = compare_semantic_snapshots(blender_snapshot, vectorworks_snapshot)
    assert comparison.ok is True
    assert comparison.compared_object_count == 14
    assert comparison.differences == ()
    assert gate.validate_artifact("semantic_comparison_report", comparison.model_dump(mode="json")) == []


def test_network_solver_output_delivers_ifc_ids_with_all_relationships(tmp_path) -> None:
    compiled = solve_network_gravity_utility(network_payload()).compiled_ir
    bundle = compile_municipal_rule_evidence_bundle()
    hydraulic_result = solve_hydraulic_network(
        compiled,
        hydraulic_payload(),
        rule_evidence_bundle=bundle,
    )
    domain_report = evaluate_domain_gate(
        {"hydraulic_capacity_in_spec": True, "hydraulics_in_spec": True},
        hydraulic_result.domain_evidence(),
    )
    assert domain_report.status is GateStatus.FAIL
    evaluation = hydraulic_result.rule_evaluation(
        compiled_ir=compiled,
        rule_evidence_bundle=bundle,
    )
    rule_identity = RuleProjectionIdentity.from_rule_evaluation(evaluation)
    snapshot = FakeBlenderSemanticExecutor().execute(
        compiled,
        rule_identity=rule_identity,
    )
    package = build_ifc_ids_package(snapshot, output_dir=tmp_path / "network-delivery")
    model = ifcopenshell.open(str(package.ifc_path))

    assert model.schema.startswith("IFC4X3")
    assert len(model.by_type("IfcDistributionSystem")) == 1
    assert len(model.by_type("IfcDistributionChamberElement")) == 4
    assert len(model.by_type("IfcDistributionPort")) == 6
    assert len(model.by_type("IfcPipeSegment")) == 3
    assert len(model.by_type("IfcRelConnectsPorts")) == 6
    assert package.report.ok is True
    assert package.report.rule_evidence_bundle_sha256 == bundle.canonical_sha256
    assert package.report.rule_evaluation_sha256 == evaluation.canonical_sha256
    assert package.report.rule_decision_status == "fail"
    assert package.report.production_verification == "eligible"
    assert package.report.checked_entity_count == 14
    assert all(item.status.value == "pass" for item in package.evidence)
    assert SchemaGate().validate_artifact(
        "ifc_ids_validation_report",
        package.report.model_dump(mode="json"),
    ) == []


def test_network_typed_plans_are_stable_across_solver_input_order() -> None:
    first_payload = network_payload()
    second_payload = network_payload()
    second_payload["nodes"].reverse()
    second_payload["segments"].reverse()
    first = solve_network_gravity_utility(first_payload).compiled_ir
    second = solve_network_gravity_utility(second_payload).compiled_ir

    first_blender = BlenderBuilder().build(first)
    second_blender = BlenderBuilder().build(second)
    first_vectorworks = VectorworksBuilder().build(first)
    second_vectorworks = VectorworksBuilder().build(second)
    assert first_blender.canonical_json() == second_blender.canonical_json()
    assert first_blender.idempotency_key == second_blender.idempotency_key
    assert first_vectorworks.canonical_json() == second_vectorworks.canonical_json()
    assert first_vectorworks.idempotency_key == second_vectorworks.idempotency_key
