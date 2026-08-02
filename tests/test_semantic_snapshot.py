"""G3 cross-host semantic snapshot E2E and injected deviation tests; offline only."""

from __future__ import annotations

from copy import deepcopy

from openbimagent.assembly.semantic_snapshot import (
    FakeBlenderSemanticExecutor,
    FakeVectorworksSemanticExecutor,
    SemanticSnapshot,
    compare_semantic_snapshots,
)
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.utility import CompiledUtilityIR
from test_compiled_utility_ir import solved_payload


def _snapshots() -> tuple[SemanticSnapshot, SemanticSnapshot]:
    compiled = CompiledUtilityIR.model_validate(solved_payload())
    return (
        FakeBlenderSemanticExecutor().execute(compiled),
        FakeVectorworksSemanticExecutor().execute(compiled),
    )


def _mutate(snapshot: SemanticSnapshot, object_id: str, path: tuple[str | int, ...], value) -> SemanticSnapshot:
    payload = snapshot.model_dump(mode="json")
    target = next(item for item in payload["objects"] if item["stable_id"] == object_id)
    cursor = target
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    payload["canonical_sha256"] = ""
    return SemanticSnapshot.model_validate(payload).finalized()


def test_benchmark_ir_passes_both_host_snapshots_and_schema_gate() -> None:
    blender, vectorworks = _snapshots()
    assert validate_artifact("semantic_snapshot", blender.model_dump(mode="json")) == []
    assert validate_artifact("semantic_snapshot", vectorworks.model_dump(mode="json")) == []
    assert len(blender.objects) == len(vectorworks.objects) == 6

    report = compare_semantic_snapshots(blender, vectorworks)
    assert report.ok is True
    assert report.compared_object_count == 6
    assert report.differences == ()
    assert validate_artifact("semantic_comparison_report", report.model_dump(mode="json")) == []


def test_host_handles_and_presentation_material_are_explicitly_ignored() -> None:
    blender, vectorworks = _snapshots()
    payload = vectorworks.model_dump(mode="json")
    for item in payload["objects"]:
        item["host_handle"] = f"VW-INTERNAL::{item['stable_id']}"
        if item["object_kind"] == "segment":
            item["presentation_material"] = "VW-Class-Material-Concrete"
    payload["canonical_sha256"] = ""
    changed = SemanticSnapshot.model_validate(payload).finalized()
    assert compare_semantic_snapshots(blender, changed).ok is True


def test_injected_coordinate_dimension_topology_and_property_deviations_fail_precisely() -> None:
    blender, vectorworks = _snapshots()
    cases = [
        ("mh-001", ("position", "x_m"), 0.25, "position.x_m"),
        ("pipe-001", ("diameter_mm",), 315.0, "diameter_mm"),
        ("pipe-001", ("topology", 1), "mh-001-out", "topology[1]"),
        ("sys-sewage", ("domain_properties", "flow_regime"), "pressure", "domain_properties.flow_regime"),
    ]
    for object_id, path, value, expected_field in cases:
        changed = _mutate(vectorworks, object_id, path, value)
        report = compare_semantic_snapshots(blender, changed)
        assert report.ok is False
        difference = next(item for item in report.differences if item.field_path == expected_field)
        assert difference.object_id == object_id
        assert difference.left_source_ir_path is not None
        assert difference.right_source_ir_path is not None


def test_missing_object_is_reported_with_source_ir_location() -> None:
    blender, vectorworks = _snapshots()
    payload = deepcopy(vectorworks.model_dump(mode="json"))
    payload["objects"] = [item for item in payload["objects"] if item["stable_id"] != "pipe-001"]
    payload["canonical_sha256"] = ""
    changed = SemanticSnapshot.model_validate(payload).finalized()
    report = compare_semantic_snapshots(blender, changed)
    assert report.ok is False
    difference = next(item for item in report.differences if item.object_id == "pipe-001")
    assert difference.field_path == "@object"
    assert difference.left_source_ir_path == "/segments/0"
    assert difference.right_source_ir_path is None


def test_canonical_snapshot_is_stable_across_object_order() -> None:
    blender, _ = _snapshots()
    payload = blender.model_dump(mode="json")
    payload["objects"].reverse()
    payload["canonical_sha256"] = ""
    reordered = SemanticSnapshot.model_validate(payload).finalized()
    assert reordered.compute_canonical_sha256() == blender.compute_canonical_sha256()
