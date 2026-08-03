"""G4 real IFC4X3 parsing, IDS facets, RuleEvidence, and Manifest integration tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import ifcopenshell
import ifcopenshell.api.pset
import ifcopenshell.util.element
import pytest

from openbimagent.assembly.semantic_snapshot import FakeBlenderSemanticExecutor
from openbimagent.deliver.ifc_ids import (
    IDS_NS,
    IDS_XSD_PATH,
    IDS_XSD_SHA256,
    PSET_NAME,
    build_ifc_ids_package,
    commit_ifc_ids_package,
    validate_ifc_against_ids,
)
from openbimagent.deliver.manifest import verify_manifest_files
from openbimagent.domain_gate import GateStatus, evaluate_domain_gate
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.utility import CompiledUtilityIR
from test_compiled_utility_ir import solved_payload


def _package(tmp_path):
    compiled = CompiledUtilityIR.model_validate(solved_payload())
    snapshot = FakeBlenderSemanticExecutor().execute(compiled)
    return build_ifc_ids_package(snapshot, output_dir=tmp_path / "delivery")


def _remove_property(ifc_path: Path, stable_id: str, property_name: str) -> None:
    model = ifcopenshell.open(str(ifc_path))
    entity = next(
        item
        for item in model.by_type("IfcObjectDefinition")
        if (ifcopenshell.util.element.get_psets(item).get(PSET_NAME) or {}).get("StableObjectID") == stable_id
    )
    pset_id = (ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {})["id"]
    pset = model.by_id(pset_id)
    prop = next(item for item in pset.HasProperties if item.Name == property_name)
    model.remove(prop)
    model.write(str(ifc_path))


def test_baseline_ifc4x3_ids_validation_and_rule_evidence_pass(tmp_path) -> None:
    package = _package(tmp_path)
    model = ifcopenshell.open(str(package.ifc_path))
    assert model.schema.startswith("IFC4X3")
    assert len(model.by_type("IfcDistributionSystem")) == 1
    assert len(model.by_type("IfcDistributionChamberElement")) == 2
    assert len(model.by_type("IfcDistributionPort")) == 2
    assert len(model.by_type("IfcPipeSegment")) == 1
    connections = model.by_type("IfcRelConnectsPorts")
    assert len(connections) == 2
    assert all(item.RealizingElement.is_a("IfcPipeSegment") for item in connections)

    # build_ifc_ids_package() already validated this IDS with the immutable,
    # hash-pinned production schema. Recompiling the same official XSD here is
    # redundant and can make libxml re-resolve remote W3C meta-schema imports
    # despite no_network=True.
    assert hashlib.sha256(IDS_XSD_PATH.read_bytes()).hexdigest() == IDS_XSD_SHA256
    ids_root = ET.parse(package.ids_path).getroot()
    specifications = ids_root.find(f"{{{IDS_NS}}}specifications")
    assert specifications is not None
    assert all(item.attrib["ifcVersion"] == "IFC4X3_ADD2" for item in specifications)
    assert not ids_root.findall(f".//{{{IDS_NS}}}connection")

    assert package.report.ok is True, [
        (item.stable_id, item.field_path, item.expected, item.actual)
        for item in package.report.findings
        if item.status.value == "FAIL"
    ]
    assert package.report.checked_entity_count == 6
    gate = evaluate_domain_gate({"ifc_ids_compliant": True}, package.report.domain_evidence())
    assert gate.status is GateStatus.PASS
    assert all(item.status.value == "pass" for item in package.evidence)
    assert validate_artifact("ifc_ids_validation_report", package.report.model_dump(mode="json")) == []
    assert json.loads(package.evidence_path.read_text(encoding="utf-8"))


def test_missing_required_property_fails_with_object_and_ir_location(tmp_path) -> None:
    package = _package(tmp_path)
    _remove_property(package.ifc_path, "pipe-001", "DiameterMM")
    report = validate_ifc_against_ids(package.ifc_path, package.ids_path)
    finding = next(item for item in report.findings if item.stable_id == "pipe-001" and item.field_path.endswith("DiameterMM"))
    assert report.ok is False
    assert evaluate_domain_gate({"ifc_ids_compliant": True}, report.domain_evidence()).status is GateStatus.FAIL
    assert finding.actual is None
    assert finding.source_ir_path == "/segments/0"


def test_broken_classification_requirement_fails(tmp_path) -> None:
    package = _package(tmp_path)
    tree = ET.parse(package.ids_path)
    ns = {"ids": IDS_NS}
    entity_name = tree.find(".//ids:specification[@identifier='IDS-pipe-001']/ids:applicability/ids:entity/ids:name/ids:simpleValue", ns)
    assert entity_name is not None
    entity_name.text = "IFCFLOWMETER"
    tree.write(package.ids_path, encoding="utf-8", xml_declaration=True)
    report = validate_ifc_against_ids(package.ifc_path, package.ids_path)
    assert report.ok is False
    assert any(item.rule_id == "IDS-pipe-001" and item.field_path == "@entity" for item in report.findings)


def test_nonstandard_ids_extension_fails_official_xsd_before_semantic_validation(tmp_path) -> None:
    package = _package(tmp_path)
    tree = ET.parse(package.ids_path)
    requirements = tree.find(
        ".//ids:specification[@identifier='IDS-pipe-001']/ids:requirements",
        {"ids": IDS_NS},
    )
    assert requirements is not None
    ET.SubElement(requirements, f"{{{IDS_NS}}}connection", {"portIds": "mh-001-out,mh-002-in"})
    tree.write(package.ids_path, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ValueError, match="IDS 1.0 XSD 校验失败"):
        validate_ifc_against_ids(package.ifc_path, package.ids_path)


def test_broken_required_relationship_fails(tmp_path) -> None:
    package = _package(tmp_path)
    model = ifcopenshell.open(str(package.ifc_path))
    for relation in model.by_type("IfcRelConnectsPorts"):
        model.remove(relation)
    model.write(str(package.ifc_path))
    report = validate_ifc_against_ids(package.ifc_path, package.ids_path)
    finding = next(
        item
        for item in report.findings
        if item.stable_id == "pipe-001" and item.field_path == "@IfcRelConnectsPorts.portIds"
    )
    assert report.ok is False
    assert finding.rule_id == "IFC-REL-CONNECTS-PORTS"
    assert finding.expected == "mh-001-out,mh-002-in"
    assert finding.actual == ""


def test_broken_standard_part_of_relationship_fails(tmp_path) -> None:
    package = _package(tmp_path)
    model = ifcopenshell.open(str(package.ifc_path))
    for relation in list(model.by_type("IfcRelAssignsToGroup")):
        if any(item.is_a("IfcPipeSegment") for item in relation.RelatedObjects):
            model.remove(relation)
    model.write(str(package.ifc_path))
    report = validate_ifc_against_ids(package.ifc_path, package.ids_path)
    finding = next(
        item
        for item in report.findings
        if item.stable_id == "pipe-001" and item.field_path.startswith("@partOf.IFCRELASSIGNSTOGROUP")
    )
    assert report.ok is False
    assert finding.actual == ""


def test_validated_package_commits_all_artifacts_to_manifest(tmp_path) -> None:
    package = _package(tmp_path)
    result = commit_ifc_ids_package(
        package,
        workdir=tmp_path,
        idempotency_key="ifc-ids:case-001",
        request_id="request-001",
        source_attempt_id="attempt-001",
    )
    assert len(result.manifest.records) == 4
    assert {item.kind for item in result.manifest.records} == {
        "ifc-model",
        "ids-requirements",
        "ifc-ids-validation",
        "rule-evidence",
    }
    verify_manifest_files(result.manifest)


def test_failed_validation_cannot_enter_completed_manifest(tmp_path) -> None:
    package = _package(tmp_path)
    _remove_property(package.ifc_path, "pipe-001", "DiameterMM")
    failed_report = validate_ifc_against_ids(package.ifc_path, package.ids_path)
    failed_package = package.__class__(
        package.ifc_path,
        package.ids_path,
        package.report_path,
        package.evidence_path,
        failed_report,
        package.evidence,
    )
    with pytest.raises(ValueError, match="验证未通过"):
        commit_ifc_ids_package(
            failed_package,
            workdir=tmp_path,
            idempotency_key="ifc-ids:failed",
            request_id="request-failed",
            source_attempt_id="attempt-failed",
        )
