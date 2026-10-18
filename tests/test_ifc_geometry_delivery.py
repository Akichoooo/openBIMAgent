"""Real IFC hollow-pipe generation, file reopen measurement, and layered delivery checks.

The geometry tests use the actual saved IFC file and ifcopenshell.geom; they do
not treat generator input parameters as readback evidence. All data is synthetic
test geometry, not real project data.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.util.element
import pytest

from openbimagent.assembly.rule_projection import RuleDeliveryEvidence
from openbimagent.assembly.semantic_snapshot import SnapshotHost, expected_semantic_snapshot
from openbimagent.deliver.ifc_ids import (
    IFC_GENERATOR_VERSION,
    PSET_NAME,
    ValidationStatus,
    build_ifc_ids_package,
    validate_ifc_against_ids,
    validate_ifc_geometry,
)
from openbimagent.utility.candidate import validate_utility_candidate
from openbimagent.utility.rules import compile_municipal_rule_set


def _payload() -> dict[str, Any]:
    return {
        "protocol_version": "0.1",
        "request_id": "ifc-geometry-delivery",
        "source_ir_sha256": "b" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:IFC-TEST",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "system_id": "sys-sewage",
        "system_name": "IFC geometry test sewage",
        "nodes": [
            {
                "node_id": "mh-001",
                "node_type": "manhole",
                "x_m": 0.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": 10.0,
                "connection_radius_m": 0.6,
            },
            {
                "node_id": "mh-002",
                "node_type": "manhole",
                "x_m": 10.0,
                "y_m": 0.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
                "connection_radius_m": 0.6,
            },
            {
                "node_id": "mh-003",
                "node_type": "outlet",
                "x_m": 18.0,
                "y_m": 6.0,
                "ground_elevation_m": 11.0,
                "invert_anchor_m": None,
                "connection_radius_m": 0.6,
            },
        ],
        "segments": [
            {
                "segment_id": "pipe-001",
                "start_node_id": "mh-001",
                "end_node_id": "mh-002",
                "diameter_mm": 300.0,
                "inner_diameter_mm": 300.0,
                "outer_diameter_mm": 360.0,
                "material": "concrete",
                "design_slope": 0.0,
                "surface_context": "driveway",
            },
            {
                "segment_id": "pipe-002",
                "start_node_id": "mh-002",
                "end_node_id": "mh-003",
                "diameter_mm": 300.0,
                "inner_diameter_mm": 300.0,
                "outer_diameter_mm": 360.0,
                "material": "concrete",
                "design_slope": 0.04,
                "surface_context": "driveway",
            },
        ],
        "collision_context": {"coverage": "complete", "obstacles": []},
    }


def _compiled() -> Any:
    result = validate_utility_candidate({"solver_input": _payload()})
    assert result.compiled_ir is not None
    return result.compiled_ir, result


def _evidence(compiled: Any, validation: Any) -> RuleDeliveryEvidence:
    return RuleDeliveryEvidence.from_validation(
        evidence_source="confirmed_validation_result",
        rule_context_sha256=validation.rule_context_sha256,
        source_ir_sha256=compiled.canonical_sha256(),
        issues=[issue.model_dump(mode="json") for issue in validation.issues],
        checker_version="test-ifc-1.0",
        candidate_sha256=validation.candidate_sha256,
        validation_sha256=hashlib.sha256(b"validation").hexdigest(),
        rules=compile_municipal_rule_set(),
    )


def _package(tmp_path: Path) -> tuple[Any, Any, RuleDeliveryEvidence]:
    compiled, validation = _compiled()
    evidence = _evidence(compiled, validation)
    snapshot = expected_semantic_snapshot(compiled, SnapshotHost.IFC)
    package = build_ifc_ids_package(
        snapshot,
        output_dir=tmp_path / "ifc",
        ir=compiled,
        delivery_evidence=evidence,
    )
    return package, compiled, evidence


def _segment_entity(model: ifcopenshell.file, stable_id: str) -> Any:
    for entity in model.by_type("IfcPipeSegment"):
        common = ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {}
        if str(common.get("StableObjectID") or "") == stable_id:
            return entity
    raise AssertionError(f"segment {stable_id} not found")


def _rewrite(model: ifcopenshell.file, path: Path) -> Path:
    model.write(str(path))
    return path


def _pset_property(pset: Any, name: str) -> Any:
    for prop in pset.HasProperties:
        if getattr(prop, "Name", None) == name:
            return prop
    raise AssertionError(f"property {name} not found")


def test_real_ifc_hollow_pipe_is_saved_and_reopened_with_measured_geometry(tmp_path: Path) -> None:
    package, compiled, evidence = _package(tmp_path)
    assert package.report.ok is True, [
        (item.stable_id, item.field_path, item.expected, item.actual)
        for item in package.report.findings
        if item.status is ValidationStatus.FAIL
    ]
    assert package.report.delivery_evidence == evidence
    assert package.geometry_report is not None and package.geometry_report.ok
    assert package.geometry_report.generator_version == IFC_GENERATOR_VERSION
    assert "saved IFC file" in package.geometry_report.measurement_source
    measured = {item.stable_id: item for item in package.geometry_report.segments}
    assert set(measured) == {"pipe-001", "pipe-002"}
    assert measured["pipe-001"].inner_diameter_mm == pytest.approx(300.0, abs=1e-3)
    assert measured["pipe-001"].outer_diameter_mm == pytest.approx(360.0, abs=1e-3)
    assert measured["pipe-001"].length_m == pytest.approx(10.0, abs=1e-6)
    assert measured["pipe-001"].axis_start[2] == pytest.approx(measured["pipe-001"].axis_end[2], abs=1e-9)
    assert measured["pipe-002"].length_m == pytest.approx(10.007997, abs=1e-5)
    assert measured["pipe-002"].axis_start[2] > measured["pipe-002"].axis_end[2]
    for item in measured.values():
        assert abs(item.volume_consistency_relative_error) < 1e-6
        assert item.discretization_relative_error < 0
        assert item.outer_ring_vertex_count >= 8
        assert item.inner_ring_vertex_count >= 8

    # Independent reopen of the saved file (not the in-memory model).
    reopened = ifcopenshell.open(str(package.ifc_path))
    entity = _segment_entity(reopened, "pipe-001")
    representation = entity.Representation.Representations[0]
    assert representation.RepresentationIdentifier == "Body"
    assert representation.RepresentationType == "SweptSolid"
    solid = representation.Items[0]
    assert solid.is_a("IfcExtrudedAreaSolid")
    assert solid.SweptArea.is_a("IfcCircleHollowProfileDef")
    assert solid.SweptArea.Radius == pytest.approx(0.18)
    assert solid.SweptArea.WallThickness == pytest.approx(0.03)
    project = reopened.by_type("IfcProject")[0]
    psets = ifcopenshell.util.element.get_psets(project)
    delivery = psets["Pset_OpenBIMAgentDelivery"]
    assert delivery["GenerationSource"] == "compiled_utility_ir"
    assert delivery["NativeHostExport"] is False
    assert len(delivery["DeliveryRuleEvidenceAggregateSHA256"]) == 64


def test_ifc_geometry_rejects_missing_body_and_solid_cylinder(tmp_path: Path) -> None:
    package, compiled, _ = _package(tmp_path)

    missing = ifcopenshell.open(str(package.ifc_path))
    _segment_entity(missing, "pipe-001").Representation = None
    missing_path = _rewrite(missing, tmp_path / "missing-body.ifc")
    report = validate_ifc_geometry(missing_path, compiled)
    assert report.ok is False
    assert any(
        item.stable_id == "pipe-001" and item.check == "body_representation"
        and item.status is ValidationStatus.FAIL
        for item in report.findings
    )

    solid_model = ifcopenshell.open(str(package.ifc_path))
    entity = _segment_entity(solid_model, "pipe-001")
    solid = entity.Representation.Representations[0].Items[0]
    profile = solid_model.create_entity(
        "IfcCircleProfileDef", ProfileType="AREA", Radius=solid.SweptArea.Radius
    )
    solid.SweptArea = profile
    solid_path = _rewrite(solid_model, tmp_path / "solid-cylinder.ifc")
    report = validate_ifc_geometry(solid_path, compiled)
    assert report.ok is False
    assert any(
        item.stable_id == "pipe-001"
        and item.check in {"body_representation", "hollow_profile"}
        and item.status is ValidationStatus.FAIL
        for item in report.findings
    )


@pytest.mark.parametrize(
    ("mutation", "expected_check"),
    [
        ("wrong_unit", "length_unit"),
        ("wrong_length", "axis_length"),
        ("wrong_diameter", "outer_diameter"),
        ("wrong_placement", "axis_position"),
    ],
)
def test_ifc_geometry_rejects_unit_length_diameter_and_placement_errors(
    tmp_path: Path, mutation: str, expected_check: str
) -> None:
    package, compiled, _ = _package(tmp_path)
    model = ifcopenshell.open(str(package.ifc_path))
    entity = _segment_entity(model, "pipe-001")
    solid = entity.Representation.Representations[0].Items[0]
    if mutation == "wrong_unit":
        unit = next(
            item
            for item in model.by_type("IfcSIUnit")
            if item.UnitType == "LENGTHUNIT"
        )
        unit.Prefix = "MILLI"
    elif mutation == "wrong_length":
        solid.Depth = solid.Depth + 0.5
    elif mutation == "wrong_diameter":
        solid.SweptArea.Radius = solid.SweptArea.Radius + 0.01
    else:
        coordinates = list(entity.ObjectPlacement.RelativePlacement.Location.Coordinates)
        coordinates[0] += 0.5
        entity.ObjectPlacement.RelativePlacement.Location.Coordinates = tuple(coordinates)
    path = _rewrite(model, tmp_path / f"{mutation}.ifc")
    report = validate_ifc_geometry(path, compiled)
    assert report.ok is False
    assert any(
        item.check == expected_check and item.status is ValidationStatus.FAIL
        for item in report.findings
    )


def test_ifc_ids_rejects_missing_object_duplicate_id_and_broken_connection(tmp_path: Path) -> None:
    package, _, _ = _package(tmp_path)

    missing = ifcopenshell.open(str(package.ifc_path))
    missing.remove(_segment_entity(missing, "pipe-002"))
    missing_path = _rewrite(missing, tmp_path / "missing-object.ifc")
    report = validate_ifc_against_ids(missing_path, package.ids_path)
    assert report.ok is False

    duplicate = ifcopenshell.open(str(package.ifc_path))
    entity = _segment_entity(duplicate, "pipe-002")
    pset = next(
        relation.RelatingPropertyDefinition
        for relation in entity.IsDefinedBy
        if relation.is_a("IfcRelDefinesByProperties")
        and relation.RelatingPropertyDefinition.Name == PSET_NAME
    )
    _pset_property(pset, "StableObjectID").NominalValue.wrappedValue = "pipe-001"
    duplicate_path = _rewrite(duplicate, tmp_path / "duplicate-id.ifc")
    report = validate_ifc_against_ids(duplicate_path, package.ids_path)
    assert report.ok is False
    assert any(item.rule_id == "IFC-IDEMPOTENT-IDS" for item in report.findings)

    broken = ifcopenshell.open(str(package.ifc_path))
    for relation in broken.by_type("IfcRelConnectsPorts"):
        broken.remove(relation)
    broken_path = _rewrite(broken, tmp_path / "broken-connections.ifc")
    report = validate_ifc_against_ids(broken_path, package.ids_path)
    assert report.ok is False
    assert any(
        item.rule_id == "IFC-REL-CONNECTS-PORTS" and item.status is ValidationStatus.FAIL
        for item in report.findings
    )


def test_ifc_rule_identity_missing_or_mismatched_fails_closed(tmp_path: Path) -> None:
    package, compiled, evidence = _package(tmp_path)

    stripped = ifcopenshell.open(str(package.ifc_path))
    project = stripped.by_type("IfcProject")[0]
    pset = next(
        relation.RelatingPropertyDefinition
        for relation in project.IsDefinedBy
        if relation.is_a("IfcRelDefinesByProperties")
        and relation.RelatingPropertyDefinition.Name == "Pset_OpenBIMAgentDelivery"
    )
    for prop in list(pset.HasProperties):
        if str(getattr(prop, "Name", "")).startswith("Delivery"):
            stripped.remove(prop)
    stripped_path = _rewrite(stripped, tmp_path / "no-rule-identity.ifc")
    with pytest.raises(ValueError, match="规则证据身份"):
        validate_ifc_against_ids(stripped_path, package.ids_path)

    other_compiled, other_validation = _compiled()
    other_evidence = _evidence(other_compiled, other_validation)
    assert other_evidence.evidence_aggregate_sha256 == evidence.evidence_aggregate_sha256
    mismatched = evidence.model_copy(update={"validation_sha256": "f" * 64})
    with pytest.raises(ValueError, match="交付规则证据与当前确认结果不一致"):
        validate_ifc_against_ids(
            package.ifc_path,
            package.ids_path,
            expected_delivery_evidence=mismatched,
        )
    with pytest.raises(ValueError, match="SourceIRSHA256"):
        validate_ifc_against_ids(
            package.ifc_path,
            package.ids_path,
            expected_source_ir_sha256="e" * 64,
        )


def test_ifc_package_rejects_snapshot_ir_identity_mismatch(tmp_path: Path) -> None:
    compiled, validation = _compiled()
    snapshot = expected_semantic_snapshot(compiled, SnapshotHost.IFC)
    mismatched_snapshot = snapshot.model_copy(update={"source_ir_sha256": "a" * 64})
    with pytest.raises(ValueError, match="source_ir_sha256"):
        build_ifc_ids_package(
            mismatched_snapshot,
            output_dir=tmp_path / "ifc",
            ir=compiled,
            delivery_evidence=_evidence(compiled, validation),
        )


def test_ifc_schema_validation_is_saved_and_passes_for_generated_file(tmp_path: Path) -> None:
    from openbimagent.deliver.ifc_ids import validate_ifc_schema

    package, _, _ = _package(tmp_path)
    assert package.schema_report is not None
    assert package.schema_report.ok is True
    assert package.schema_report.ifc_schema.startswith("IFC4X3")
    assert package.schema_report.validator.startswith("ifcopenshell.validate")
    assert package.schema_report_path is not None and package.schema_report_path.is_file()
    report = validate_ifc_schema(package.ifc_path)
    assert report.ok is True
    assert report.duplicate_global_ids == ()
    assert report.invalid_global_ids == ()


def test_ifc_schema_validation_rejects_duplicate_and_invalid_global_ids(tmp_path: Path) -> None:
    from openbimagent.deliver.ifc_ids import validate_ifc_schema

    package, _, _ = _package(tmp_path)

    duplicate = ifcopenshell.open(str(package.ifc_path))
    project = duplicate.by_type("IfcProject")[0]
    site = duplicate.by_type("IfcSite")[0]
    site.GlobalId = project.GlobalId
    duplicate_path = _rewrite(duplicate, tmp_path / "duplicate-globalid.ifc")
    report = validate_ifc_schema(duplicate_path)
    assert report.ok is False
    assert project.GlobalId in report.duplicate_global_ids

    invalid = ifcopenshell.open(str(package.ifc_path))
    invalid.by_type("IfcSite")[0].GlobalId = "not-a-guid"
    invalid_path = _rewrite(invalid, tmp_path / "invalid-globalid.ifc")
    report = validate_ifc_schema(invalid_path)
    assert report.ok is False
    assert "not-a-guid" in report.invalid_global_ids


def test_ifc_delivery_rule_identity_kind_is_strictly_validated(tmp_path: Path) -> None:
    package, _, _ = _package(tmp_path)
    model = ifcopenshell.open(str(package.ifc_path))
    project = model.by_type("IfcProject")[0]
    pset = next(
        relation.RelatingPropertyDefinition
        for relation in project.IsDefinedBy
        if relation.is_a("IfcRelDefinesByProperties")
        and relation.RelatingPropertyDefinition.Name == "Pset_OpenBIMAgentDelivery"
    )
    _pset_property(pset, "DeliveryRuleIdentityKind").NominalValue.wrappedValue = (
        "single_rule_evaluation"
    )
    path = _rewrite(model, tmp_path / "wrong-identity-kind.ifc")
    with pytest.raises(ValueError, match="身份类型不受支持"):
        validate_ifc_against_ids(path, package.ids_path)
