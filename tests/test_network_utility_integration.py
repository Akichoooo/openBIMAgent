"""M1.5 多节点 Solver 到 typed 双宿主语义比较的离线 E2E。"""

from __future__ import annotations

from openbimagent.assembly.blender_plan import (
    BlenderBuilder,
    BlenderOperationKind,
    BlenderReceiptStatus,
    FakeBlenderExecutor,
)
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
from openbimagent.schema_gate.gate import SchemaGate
from openbimagent.utility import solve_network_gravity_utility
from test_network_utility_solver import network_payload


def test_network_solver_drives_typed_dual_host_semantic_e2e() -> None:
    compiled = solve_network_gravity_utility(network_payload()).compiled_ir
    blender_plan = BlenderBuilder().build(compiled)
    vectorworks_plan = VectorworksBuilder().build(compiled)
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

    blender_snapshot = FakeBlenderSemanticExecutor().execute(compiled)
    vectorworks_snapshot = FakeVectorworksSemanticExecutor().execute(compiled)
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
    snapshot = FakeBlenderSemanticExecutor().execute(compiled)
    package = build_ifc_ids_package(snapshot, output_dir=tmp_path / "network-delivery")
    model = ifcopenshell.open(str(package.ifc_path))

    assert model.schema.startswith("IFC4X3")
    assert len(model.by_type("IfcDistributionSystem")) == 1
    assert len(model.by_type("IfcDistributionChamberElement")) == 4
    assert len(model.by_type("IfcDistributionPort")) == 6
    assert len(model.by_type("IfcPipeSegment")) == 3
    assert len(model.by_type("IfcRelConnectsPorts")) == 6
    assert package.report.ok is True
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
