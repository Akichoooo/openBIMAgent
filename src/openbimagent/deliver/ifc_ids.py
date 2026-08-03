"""G4 IFC4X3/IDS minimum municipal delivery slice with independent validation."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from functools import lru_cache
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.system
import ifcopenshell.guid
import ifcopenshell.util.element
from lxml import etree as LET
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.assembly.semantic_snapshot import SemanticObjectKind, SemanticSnapshot
from openbimagent.deliver.manifest import DeliveryManifestResult, commit_delivery_manifest
from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.utility.contracts import EvidenceStatus, EvidenceSubjectType, RuleEvidence

IDS_NS = "http://standards.buildingsmart.org/IDS"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
IFC_IDS_VALIDATION_VERSION = "1.0"
PSET_NAME = "Pset_OpenBIMAgentMunicipalUtility"
PROJECT_PSET_NAME = "Pset_OpenBIMAgentDelivery"
IDS_XSD_PATH = Path(__file__).resolve().parents[3] / "schemas" / "buildingsmart_ids_1_0.xsd"
IDS_XSD_SHA256 = "528d0969f0ba16bb211a77c431f450f6b4ca788e0839ed45929b285c81c6aa30"

ET.register_namespace("", IDS_NS)
ET.register_namespace("xsi", XSI_NS)


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class IfcIdsFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=256)
    status: ValidationStatus
    stable_id: str = Field(min_length=1, max_length=256)
    ifc_class: str = Field(min_length=1, max_length=128)
    field_path: str = Field(min_length=1, max_length=512)
    expected: str | float | int | bool | None = None
    actual: str | float | int | bool | None = None
    source_ir_path: str = Field(min_length=1, max_length=512)
    detail: str = Field(min_length=1, max_length=2000)


class IfcIdsValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_version: str = Field(default=IFC_IDS_VALIDATION_VERSION, pattern=r"^1(?:\.\d+)?$")
    ifc_schema: str = Field(pattern=r"^IFC4X3(?:_ADD2)?$")
    ids_version: str = Field(default="1.0", pattern=r"^1\.0$")
    source_ir_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_decision_status: str = Field(pattern=r"^(pass|fail|unknown|review_required)$")
    production_verification: str = Field(pattern=r"^(eligible|review_required)$")
    status: ValidationStatus
    checked_entity_count: int = Field(ge=0)
    findings: tuple[IfcIdsFinding, ...] = ()

    @model_validator(mode="after")
    def _status_matches_findings(self) -> "IfcIdsValidationReport":
        expected = ValidationStatus.FAIL if any(item.status is ValidationStatus.FAIL for item in self.findings) else ValidationStatus.PASS
        if self.status is not expected:
            raise ValueError("IFC/IDS validation status 与 findings 不一致")
        return self

    @property
    def ok(self) -> bool:
        return self.status is ValidationStatus.PASS

    def domain_evidence(self) -> dict[str, dict[str, bool | str]]:
        failed = tuple(item for item in self.findings if item.status is ValidationStatus.FAIL)
        detail = (
            f"IFC/IDS checks={len(self.findings)} failed={len(failed)} "
            f"source_ir={self.source_ir_id}@{self.source_ir_sha256}"
        )
        return {"ifc_ids_compliant": {"ok": self.ok, "detail": detail}}


@dataclass(frozen=True)
class IfcIdsPackage:
    ifc_path: Path
    ids_path: Path
    report_path: Path
    evidence_path: Path
    report: IfcIdsValidationReport
    evidence: tuple[RuleEvidence, ...]


def build_ifc_ids_package(
    snapshot: SemanticSnapshot | dict[str, Any],
    *,
    output_dir: Path,
) -> IfcIdsPackage:
    semantic = snapshot if isinstance(snapshot, SemanticSnapshot) else SemanticSnapshot.model_validate(snapshot)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ifc_path = root / "municipal_utility.ifc"
    ids_path = root / "municipal_utility.ids"
    report_path = root / "ifc_ids_validation_report.json"
    evidence_path = root / "ifc_ids_rule_evidence.json"

    model = _build_ifc_model(semantic)
    model.write(str(ifc_path))
    _write_ids(semantic, ids_path)
    report = validate_ifc_against_ids(ifc_path, ids_path)
    evidence = _report_evidence(report)
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return IfcIdsPackage(ifc_path, ids_path, report_path, evidence_path, report, evidence)


def commit_ifc_ids_package(
    package: IfcIdsPackage,
    *,
    workdir: Path,
    idempotency_key: str,
    request_id: str,
    source_attempt_id: str,
) -> DeliveryManifestResult:
    if not package.report.ok:
        raise ValueError("IFC/IDS 验证未通过，禁止提交完成态 Artifact Manifest")
    root = Path(workdir).resolve()
    artifacts = []
    for path, kind, media_type in (
        (package.ifc_path, "ifc-model", "application/x-step"),
        (package.ids_path, "ids-requirements", "application/xml"),
        (package.report_path, "ifc-ids-validation", "application/json"),
        (package.evidence_path, "rule-evidence", "application/json"),
    ):
        artifacts.append(
            {
                "path": path.resolve().relative_to(root).as_posix(),
                "kind": kind,
                "media_type": media_type,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "dependencies": [],
                "status": "completed",
            }
        )
    return commit_delivery_manifest(
        workdir=root,
        artifacts=artifacts,
        idempotency_key=idempotency_key,
        domain_gate_status="PASS",
        domain_gate_required=True,
        request_id=request_id,
        source_attempt_id=source_attempt_id,
        generator=ActorRef(
            actor_id="service:ifc-ids-delivery",
            actor_type=ActorType.SERVICE,
            display_name="IFC4X3/IDS delivery validator",
        ),
    )


def validate_ifc_against_ids(ifc_path: Path, ids_path: Path) -> IfcIdsValidationReport:
    _validate_ids_xml(ids_path)
    model = ifcopenshell.open(str(Path(ifc_path)))
    if not model.schema.startswith("IFC4X3"):
        raise ValueError(f"IFC Schema 必须为 IFC4X3，实际 {model.schema}")
    ids_root = ET.parse(ids_path).getroot()
    specifications = ids_root.find(_tag("specifications"))
    if specifications is None:
        raise ValueError("IDS 缺少 specifications")
    project = next(iter(model.by_type("IfcProject")), None)
    if project is None:
        raise ValueError("IFC 缺少 IfcProject")
    project_metadata = ifcopenshell.util.element.get_psets(project).get(PROJECT_PSET_NAME) or {}
    source_ir_id = str(project_metadata.get("SourceIRID") or "")
    source_ir_sha256 = str(project_metadata.get("SourceIRSHA256") or "")
    if not source_ir_id or len(source_ir_sha256) != 64:
        raise ValueError(f"IFC {PROJECT_PSET_NAME} 缺少合法 SourceIRID/SourceIRSHA256")
    rule_evidence_bundle_sha256 = str(
        project_metadata.get("RuleEvidenceBundleSHA256") or ""
    )
    rule_evaluation_sha256 = str(project_metadata.get("RuleEvaluationSHA256") or "")
    rule_decision_status = str(project_metadata.get("RuleDecisionStatus") or "")
    production_verification = str(project_metadata.get("ProductionVerification") or "")
    if (
        len(rule_evidence_bundle_sha256) != 64
        or len(rule_evaluation_sha256) != 64
        or rule_decision_status not in {"pass", "fail", "unknown", "review_required"}
        or production_verification not in {"eligible", "review_required"}
    ):
        raise ValueError(f"IFC {PROJECT_PSET_NAME} 缺少完整规则证据身份")
    if (
        production_verification == "review_required"
        and rule_decision_status in {"pass", "fail"}
    ):
        raise ValueError("IFC review_required 规则证据身份不得声明规范 PASS/FAIL")
    findings: list[IfcIdsFinding] = []
    checked_ids: set[str] = set()

    for specification in specifications.findall(_tag("specification")):
        rule_id = specification.attrib.get("identifier") or specification.attrib.get("name") or "IDS-UNKNOWN"
        applicability = specification.find(_tag("applicability"))
        requirements = specification.find(_tag("requirements"))
        if applicability is None or requirements is None:
            raise ValueError(f"IDS specification {rule_id!r} 缺少 applicability/requirements")
        entity_facet = applicability.find(_tag("entity"))
        if entity_facet is None:
            raise ValueError(f"IDS specification {rule_id!r} 缺少 entity facet")
        ifc_class = _facet_value(entity_facet, "name")
        entities = list(model.by_type(ifc_class))
        for facet in applicability:
            if _local_name(facet.tag) != "property":
                continue
            property_set = _facet_value(facet, "propertySet")
            base_name = _facet_value(facet, "baseName")
            expected_value = _facet_value(facet, "value")
            entities = [
                entity
                for entity in entities
                if _normalized(
                    (ifcopenshell.util.element.get_psets(entity).get(property_set) or {}).get(base_name)
                )
                == _normalized(expected_value)
            ]
        if not entities:
            findings.append(
                IfcIdsFinding(
                    rule_id=rule_id,
                    status=ValidationStatus.FAIL,
                    stable_id=f"@ids:{rule_id}",
                    ifc_class=ifc_class,
                    field_path="@entity",
                    expected=ifc_class,
                    actual=None,
                    source_ir_path="@ids-applicability",
                    detail=f"IDS applicability 未找到实体: {ifc_class}",
                )
            )
        for entity in entities:
            psets = ifcopenshell.util.element.get_psets(entity)
            common = dict(psets.get(PSET_NAME) or {})
            stable_id = str(common.get("StableObjectID") or entity.GlobalId or f"#{entity.id()}")
            source_ir_path = str(common.get("SourceIRPath") or "@missing")
            checked_ids.add(stable_id)
            for requirement in requirements:
                local = _local_name(requirement.tag)
                if local == "entity":
                    expected_class = _facet_value(requirement, "name")
                    expected_predefined_type = _optional_facet_value(requirement, "predefinedType")
                    actual_class = entity.is_a().upper()
                    findings.append(
                        _finding(
                            rule_id,
                            stable_id,
                            entity.is_a(),
                            "@entity.name",
                            expected_class,
                            actual_class,
                            source_ir_path,
                        )
                    )
                    if expected_predefined_type is not None:
                        findings.append(
                            _finding(
                                rule_id,
                                stable_id,
                                entity.is_a(),
                                "@entity.predefinedType",
                                expected_predefined_type,
                                getattr(entity, "PredefinedType", None),
                                source_ir_path,
                            )
                        )
                elif local == "property":
                    property_set = _facet_value(requirement, "propertySet")
                    base_name = _facet_value(requirement, "baseName")
                    expected = _facet_value(requirement, "value")
                    actual = (psets.get(property_set) or {}).get(base_name)
                    findings.append(_finding(rule_id, stable_id, entity.is_a(), f"{property_set}.{base_name}", expected, actual, source_ir_path))
                elif local == "attribute":
                    name = _facet_value(requirement, "name")
                    expected = _facet_value(requirement, "value")
                    actual = getattr(entity, name, None)
                    findings.append(_finding(rule_id, stable_id, entity.is_a(), f"@{name}", expected, actual, source_ir_path))
                elif local == "partOf":
                    relation_name = requirement.attrib.get("relation", "")
                    target = requirement.find(_tag("entity"))
                    if target is None:
                        raise ValueError(f"IDS partOf {rule_id!r} 缺少 entity")
                    expected_class = _facet_value(target, "name")
                    expected_predefined_type = _optional_facet_value(target, "predefinedType")
                    matches = _part_of_matches(entity, relation_name, expected_class, expected_predefined_type)
                    expected = _part_of_expectation(relation_name, expected_class, expected_predefined_type)
                    actual = expected if matches else ",".join(_part_of_actual(entity, relation_name))
                    findings.append(
                        _finding(
                            rule_id,
                            stable_id,
                            entity.is_a(),
                            f"@partOf.{relation_name or 'ANY'}.{expected_class}",
                            expected,
                            actual,
                            source_ir_path,
                        )
                    )
                else:
                    raise ValueError(f"IDS requirement facet 不受支持: {local}")

    findings.extend(_relationship_findings(model))
    status = ValidationStatus.FAIL if any(item.status is ValidationStatus.FAIL for item in findings) else ValidationStatus.PASS
    return IfcIdsValidationReport(
        ifc_schema=model.schema,
        source_ir_id=source_ir_id,
        source_ir_sha256=source_ir_sha256,
        rule_evidence_bundle_sha256=rule_evidence_bundle_sha256,
        rule_evaluation_sha256=rule_evaluation_sha256,
        rule_decision_status=rule_decision_status,
        production_verification=production_verification,
        status=status,
        checked_entity_count=len(checked_ids),
        findings=tuple(findings),
    )


def _build_ifc_model(snapshot: SemanticSnapshot) -> ifcopenshell.file:
    model = ifcopenshell.api.project.create_file(version="IFC4X3")
    model.header.file_name.name = "municipal_utility.ifc"
    model.header.file_name.time_stamp = "1970-01-01T00:00:00"
    project = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="openBIMAgent M1 Municipal Utility")
    project_pset = ifcopenshell.api.pset.add_pset(model, product=project, name=PROJECT_PSET_NAME)
    project_properties = {
        "SourceIRID": snapshot.source_ir_id,
        "SourceIRSHA256": snapshot.source_ir_sha256,
    }
    if snapshot.rule_identity is not None:
        project_properties.update(_ifc_rule_identity_properties(snapshot.rule_identity))
    ifcopenshell.api.pset.edit_pset(
        model,
        pset=project_pset,
        properties=project_properties,
    )
    site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite", name="Municipal Utility Site")
    ifcopenshell.api.aggregate.assign_object(model, products=[site], relating_object=project)

    created: dict[str, Any] = {}
    systems: dict[str, Any] = {}
    for item in snapshot.objects:
        if item.object_kind is SemanticObjectKind.SYSTEM:
            entity = ifcopenshell.api.system.add_system(model, ifc_class="IfcDistributionSystem")
            entity.Name = item.stable_id
            if item.ifc_predefined_type and hasattr(entity, "PredefinedType"):
                entity.PredefinedType = item.ifc_predefined_type
            systems[item.stable_id] = entity
            created[item.stable_id] = entity
            _set_guid(entity, item.stable_id)
            _attach_properties(model, entity, item)

    for item in snapshot.objects:
        if item.object_kind is SemanticObjectKind.SYSTEM:
            continue
        if item.object_kind is SemanticObjectKind.PORT:
            entity = ifcopenshell.api.system.add_port(model)
            entity.Name = item.stable_id
        else:
            entity = ifcopenshell.api.root.create_entity(
                model,
                ifc_class=item.ifc_class,
                predefined_type=item.ifc_predefined_type,
                name=item.stable_id,
            )
        _set_guid(entity, item.stable_id)
        created[item.stable_id] = entity
        _attach_properties(model, entity, item)
        if item.object_kind in {SemanticObjectKind.NODE, SemanticObjectKind.SEGMENT}:
            ifcopenshell.api.spatial.assign_container(model, products=[entity], relating_structure=site)
        system = systems.get(item.system_id)
        if system is not None and item.object_kind is not SemanticObjectKind.PORT:
            ifcopenshell.api.system.assign_system(model, products=[entity], system=system)

    for item in snapshot.objects:
        if item.object_kind is SemanticObjectKind.PORT:
            node_id = _port_owner_id(item.source_ir_path, snapshot)
            ifcopenshell.api.system.assign_port(model, element=created[node_id], port=created[item.stable_id])
        elif item.object_kind is SemanticObjectKind.SEGMENT:
            start, end = item.topology
            ifcopenshell.api.system.connect_port(
                model,
                port1=created[start],
                port2=created[end],
                direction="SOURCEANDSINK",
                element=created[item.stable_id],
            )
    return model


def _attach_properties(model: ifcopenshell.file, entity: Any, item: Any) -> None:
    properties: dict[str, Any] = {
        "StableObjectID": item.stable_id,
        "ObjectKind": item.object_kind.value,
        "SystemID": item.system_id,
        "SourceIRPath": item.source_ir_path,
    }
    if item.position is not None:
        properties.update({"X": item.position.x_m, "Y": item.position.y_m, "Z": item.position.z_m})
    if item.centerline:
        properties["CenterlineJSON"] = json.dumps([point.model_dump() for point in item.centerline], sort_keys=True, separators=(",", ":"))
    if item.object_kind is SemanticObjectKind.SEGMENT:
        properties["TopologyPortIDs"] = ",".join(sorted(item.topology))
    for field in ("diameter_mm", "horizontal_length_m", "start_invert_m", "end_invert_m", "slope", "material"):
        value = getattr(item, field)
        if value is not None:
            properties[_property_name(field)] = value
    for name, value in sorted(item.domain_properties.items()):
        if value is not None:
            properties[f"Domain_{name}"] = value
    if item.domain_properties.get("rule_evidence_bundle_sha256") is not None:
        properties.update(
            {
                "RuleEvidenceBundleSHA256": item.domain_properties["rule_evidence_bundle_sha256"],
                "RuleEvaluationSHA256": item.domain_properties["rule_evaluation_sha256"],
                "RuleDecisionStatus": item.domain_properties["rule_decision_status"],
                "ProductionVerification": item.domain_properties["production_verification"],
            }
        )
        if item.domain_properties.get("exception_approval_id") is not None:
            properties["ExceptionApprovalID"] = item.domain_properties["exception_approval_id"]
            properties["ExceptionApprovalSHA256"] = item.domain_properties["exception_approval_sha256"]
    pset = ifcopenshell.api.pset.add_pset(model, product=entity, name=PSET_NAME)
    ifcopenshell.api.pset.edit_pset(model, pset=pset, properties=properties)


def _write_ids(snapshot: SemanticSnapshot, path: Path) -> None:
    root = ET.Element(
        _tag("ids"),
        {f"{{{XSI_NS}}}schemaLocation": f"{IDS_NS} https://standards.buildingsmart.org/IDS/1.0/ids.xsd"},
    )
    info = ET.SubElement(root, _tag("info"))
    ET.SubElement(info, _tag("title")).text = "openBIMAgent M1 Municipal Utility IDS"
    ET.SubElement(info, _tag("version")).text = "1.0.0"
    ET.SubElement(info, _tag("description")).text = "IDS 1.0 requirements generated from a trusted SemanticSnapshot v1."
    ET.SubElement(info, _tag("purpose")).text = "M1 deterministic municipal utility delivery validation"
    ET.SubElement(info, _tag("milestone")).text = "G4"
    specs = ET.SubElement(root, _tag("specifications"))
    system_by_id = {
        item.stable_id: item
        for item in snapshot.objects
        if item.object_kind is SemanticObjectKind.SYSTEM
    }
    for item in sorted(snapshot.objects, key=lambda value: value.stable_id):
        spec = ET.SubElement(
            specs,
            _tag("specification"),
            {
                "name": f"Validate {item.stable_id}",
                "identifier": f"IDS-{item.stable_id}",
                "ifcVersion": "IFC4X3_ADD2",
            },
        )
        applicability = ET.SubElement(spec, _tag("applicability"), {"minOccurs": "1", "maxOccurs": "1"})
        entity = ET.SubElement(applicability, _tag("entity"))
        _simple_value(entity, "name", item.ifc_class.upper())
        identity = ET.SubElement(applicability, _tag("property"))
        _simple_value(identity, "propertySet", PSET_NAME)
        _simple_value(identity, "baseName", "StableObjectID")
        _simple_value(identity, "value", item.stable_id)
        requirements = ET.SubElement(spec, _tag("requirements"))
        if item.ifc_predefined_type is not None:
            required_entity = ET.SubElement(requirements, _tag("entity"))
            _simple_value(required_entity, "name", item.ifc_class.upper())
            _simple_value(required_entity, "predefinedType", item.ifc_predefined_type)
        if item.object_kind in {SemanticObjectKind.NODE, SemanticObjectKind.SEGMENT}:
            system = system_by_id[item.system_id]
            part_of = ET.SubElement(
                requirements,
                _tag("partOf"),
                {"relation": "IFCRELASSIGNSTOGROUP", "cardinality": "required"},
            )
            parent_entity = ET.SubElement(part_of, _tag("entity"))
            _simple_value(parent_entity, "name", system.ifc_class.upper())
            if system.ifc_predefined_type is not None:
                _simple_value(parent_entity, "predefinedType", system.ifc_predefined_type)
        for property_name, expected in (
            ("StableObjectID", item.stable_id),
            ("ObjectKind", item.object_kind.value),
            ("SystemID", item.system_id),
            ("SourceIRPath", item.source_ir_path),
        ):
            _property_requirement(requirements, property_name, expected)
        if snapshot.rule_identity is not None:
            for property_name, expected in sorted(
                _ifc_rule_identity_properties(snapshot.rule_identity).items()
            ):
                _property_requirement(requirements, property_name, expected)
        if item.object_kind is SemanticObjectKind.SEGMENT:
            for property_name, expected in (
                ("DiameterMM", item.diameter_mm),
                ("Slope", item.slope),
                ("Material", item.material),
                ("TopologyPortIDs", ",".join(sorted(item.topology))),
            ):
                _property_requirement(requirements, property_name, expected)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    _validate_ids_xml(path)


def _property_requirement(parent: ET.Element, name: str, value: Any) -> None:
    element = ET.SubElement(parent, _tag("property"))
    _simple_value(element, "propertySet", PSET_NAME)
    _simple_value(element, "baseName", name)
    _simple_value(element, "value", value)


def _simple_value(parent: ET.Element, name: str, value: Any) -> None:
    container = ET.SubElement(parent, _tag(name))
    ET.SubElement(container, _tag("simpleValue")).text = str(value)


def _facet_value(parent: ET.Element, name: str) -> str:
    if name in parent.attrib:
        return parent.attrib[name]
    container = parent.find(_tag(name))
    if container is None:
        raise ValueError(f"IDS facet 缺少 {name}")
    value = container.find(_tag("simpleValue"))
    if value is None or value.text is None:
        raise ValueError(f"IDS facet {name} 缺少 simpleValue")
    return value.text


def _optional_facet_value(parent: ET.Element, name: str) -> str | None:
    container = parent.find(_tag(name))
    if container is None:
        return None
    return _facet_value(parent, name)


@lru_cache(maxsize=1)
def _ids_xml_schema() -> LET.XMLSchema:
    actual_sha256 = hashlib.sha256(IDS_XSD_PATH.read_bytes()).hexdigest()
    if actual_sha256 != IDS_XSD_SHA256:
        raise ValueError(
            f"IDS 1.0 XSD 摘要漂移: expected={IDS_XSD_SHA256}, actual={actual_sha256}"
        )
    parser = LET.XMLParser(no_network=True)
    return LET.XMLSchema(LET.parse(str(IDS_XSD_PATH), parser))


def _validate_ids_xml(path: Path) -> None:
    try:
        document = LET.parse(str(Path(path)), LET.XMLParser(no_network=True))
        _ids_xml_schema().assertValid(document)
    except (LET.XMLSchemaParseError, LET.XMLSyntaxError, LET.DocumentInvalid, OSError) as exc:
        raise ValueError(f"IDS 1.0 XSD 校验失败: {exc}") from exc


def _finding(rule_id: str, stable_id: str, ifc_class: str, field_path: str, expected: Any, actual: Any, source_ir_path: str) -> IfcIdsFinding:
    matches = _normalized(actual) == _normalized(expected)
    return IfcIdsFinding(
        rule_id=rule_id,
        status=ValidationStatus.PASS if matches else ValidationStatus.FAIL,
        stable_id=stable_id,
        ifc_class=ifc_class,
        field_path=field_path,
        expected=expected,
        actual=actual,
        source_ir_path=source_ir_path,
        detail=f"{stable_id} {field_path}: expected={expected!r}, actual={actual!r}",
    )


def _normalized(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int | float):
        return format(float(value), ".12g")
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return value
        if math.isfinite(numeric):
            return format(numeric, ".12g")
    return str(value)


def _connected_port_ids(entity: Any) -> tuple[str, ...]:
    port_ids: set[str] = set()
    for relation in entity.file.by_type("IfcRelConnectsPorts"):
        if relation.RealizingElement == entity:
            for port in (relation.RelatingPort, relation.RelatedPort):
                psets = ifcopenshell.util.element.get_psets(port)
                stable_id = (psets.get(PSET_NAME) or {}).get("StableObjectID")
                if stable_id:
                    port_ids.add(str(stable_id))
    return tuple(sorted(port_ids))


def _part_of_matches(entity: Any, relation_name: str, expected_class: str, expected_predefined_type: str | None) -> bool:
    return any(
        target.is_a().upper() == expected_class.upper()
        and (expected_predefined_type is None or getattr(target, "PredefinedType", None) == expected_predefined_type)
        for target in _part_of_targets(entity, relation_name)
    )


def _part_of_actual(entity: Any, relation_name: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            f"{target.is_a().upper()}:{getattr(target, 'PredefinedType', None) or ''}"
            for target in _part_of_targets(entity, relation_name)
        )
    )


def _part_of_targets(entity: Any, relation_name: str) -> tuple[Any, ...]:
    if relation_name == "IFCRELASSIGNSTOGROUP":
        return tuple(
            relation.RelatingGroup
            for relation in getattr(entity, "HasAssignments", ())
            if relation.is_a("IfcRelAssignsToGroup")
        )
    raise ValueError(f"G4 IDS partOf relation 不受支持: {relation_name}")


def _part_of_expectation(relation_name: str, ifc_class: str, predefined_type: str | None) -> str:
    return f"{relation_name}:{ifc_class.upper()}:{predefined_type or ''}"


def _relationship_findings(model: ifcopenshell.file) -> list[IfcIdsFinding]:
    findings: list[IfcIdsFinding] = []
    segments = sorted(
        model.by_type("IfcPipeSegment"),
        key=lambda entity: str((ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {}).get("StableObjectID")),
    )
    for entity in segments:
        common = ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {}
        stable_id = str(common.get("StableObjectID") or entity.GlobalId or f"#{entity.id()}")
        source_ir_path = str(common.get("SourceIRPath") or "@missing")
        encoded = common.get("TopologyPortIDs")
        expected_ports = tuple(sorted(str(encoded).split(","))) if encoded else ()
        actual_ports = _connected_port_ids(entity)
        findings.append(
            _finding(
                "IFC-REL-CONNECTS-PORTS",
                stable_id,
                entity.is_a(),
                "@IfcRelConnectsPorts.portIds",
                ",".join(expected_ports),
                ",".join(actual_ports),
                source_ir_path,
            )
        )
    return findings


def _report_evidence(report: IfcIdsValidationReport) -> tuple[RuleEvidence, ...]:
    evidence: list[RuleEvidence] = []
    for index, finding in enumerate(report.findings):
        evidence.append(
            RuleEvidence(
                evidence_id=f"ifc-ids-{index:04d}-{finding.stable_id}",
                rule_id=finding.rule_id,
                check_name="ifc_ids_compliant",
                status=EvidenceStatus.PASS if finding.status is ValidationStatus.PASS else EvidenceStatus.FAIL,
                subject_type=_subject_type(finding.ifc_class),
                subject_id=finding.stable_id,
                detail=finding.detail,
                measured_value=finding.actual,
                limit_value=finding.expected,
                source_clause=f"IDS 1.0 {finding.rule_id} {finding.field_path}; source={finding.source_ir_path}",
            )
        )
    return tuple(evidence)


def _subject_type(ifc_class: str) -> EvidenceSubjectType:
    if ifc_class == "IfcDistributionSystem":
        return EvidenceSubjectType.SYSTEM
    if ifc_class == "IfcDistributionPort":
        return EvidenceSubjectType.PORT
    if ifc_class == "IfcPipeSegment":
        return EvidenceSubjectType.SEGMENT
    return EvidenceSubjectType.NODE


def _port_owner_id(source_ir_path: str, snapshot: SemanticSnapshot) -> str:
    node_path = source_ir_path.split("/ports/", maxsplit=1)[0]
    for item in snapshot.objects:
        if item.object_kind is SemanticObjectKind.NODE and item.source_ir_path == node_path:
            return item.stable_id
    raise ValueError(f"port source_ir_path 无法定位所属 node: {source_ir_path}")


def _ifc_rule_identity_properties(identity: Any) -> dict[str, str]:
    properties = {
        "RuleEvidenceBundleSHA256": identity.rule_evidence_bundle_sha256,
        "RuleEvaluationSHA256": identity.rule_evaluation_sha256,
        "RuleDecisionStatus": identity.rule_decision_status,
        "ProductionVerification": identity.production_verification,
    }
    if identity.exception_approval_id is not None:
        properties["ExceptionApprovalID"] = identity.exception_approval_id
        properties["ExceptionApprovalSHA256"] = str(identity.exception_approval_sha256)
    return properties


def _property_name(field: str) -> str:
    return {
        "diameter_mm": "DiameterMM",
        "horizontal_length_m": "HorizontalLengthM",
        "start_invert_m": "StartInvertM",
        "end_invert_m": "EndInvertM",
        "slope": "Slope",
        "material": "Material",
    }[field]


def _set_guid(entity: Any, stable_id: str) -> None:
    if hasattr(entity, "GlobalId"):
        entity.GlobalId = ifcopenshell.guid.compress(uuid.uuid5(uuid.NAMESPACE_URL, f"openbimagent:{stable_id}").hex)


def _tag(name: str) -> str:
    return f"{{{IDS_NS}}}{name}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


__all__ = [
    "IfcIdsFinding",
    "IfcIdsPackage",
    "IfcIdsValidationReport",
    "ValidationStatus",
    "build_ifc_ids_package",
    "commit_ifc_ids_package",
    "validate_ifc_against_ids",
]
