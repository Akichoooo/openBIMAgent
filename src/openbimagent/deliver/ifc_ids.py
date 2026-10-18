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
import ifcopenshell.geom
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.validate as ifcvalidate
from lxml import etree as LET
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.assembly.rule_projection import RuleDeliveryEvidence
from openbimagent.assembly.semantic_snapshot import SemanticObjectKind, SemanticSnapshot
from openbimagent.deliver.manifest import DeliveryManifestResult, commit_delivery_manifest
from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.utility.contracts import (
    CompiledUtilityIR,
    EvidenceStatus,
    EvidenceSubjectType,
    RuleEvidence,
)
from openbimagent.utility.geometry import execution_geometry, resolve_dimensions

IDS_NS = "http://standards.buildingsmart.org/IDS"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
IFC_IDS_VALIDATION_VERSION = "1.0"
IFC_GEOMETRY_VALIDATION_VERSION = "1.0"
IFC_SCHEMA_VALIDATION_VERSION = "1.0"
IFC_SCHEMA_VALIDATOR = "ifcopenshell.validate 0.8.5 (express_rules=True)"
IFC_SCHEMA_SCOPE = (
    "IFC4X3 STEP schema conformance via IfcOpenShell validate with EXPRESS rules, plus "
    "GlobalId format and duplicate-GlobalId checks. This is a schema-level check only: "
    "it is not engineering compliance, not full IDS coverage, and not a guarantee of "
    "semantic correctness."
)
IFC_GENERATOR_VERSION = "openbimagent-ifc-hollow-pipe-1.0"
IFC_GEOMETRY_TOLERANCE_M = 1e-6
IFC_VOLUME_RELATIVE_TOLERANCE = 1e-6
IFC_GEOMETRY_NOTE = (
    "Measurements are derived from ifcopenshell.geom tessellation of the saved IFC file in world "
    "coordinates. The circle profile is tessellated into polygons; the polygon-vs-circle area "
    "difference is reported as discretization error, not as an engineering tolerance."
)
PSET_NAME = "Pset_OpenBIMAgentMunicipalUtility"
PROJECT_PSET_NAME = "Pset_OpenBIMAgentDelivery"
IDS_XSD_PATH = Path(__file__).resolve().parents[3] / "schemas" / "buildingsmart_ids_1_0.xsd"
# The vendored schema is pinned as UTF-8/LF bytes; .gitattributes preserves them on Windows.
IDS_XSD_SHA256 = "e48683c303203305ac16731df8f9fe883c43a756aeac6cf8e8dcfe06d389a684"
# Official W3C XSD dependencies imported by the IDS 1.0 schema. They are stored
# locally with pinned hashes so validation never depends on live network access
# (lxml schema imports can fetch schemaLocation URLs even with no_network=True).
# Sources: http://www.w3.org/2001/XMLSchema.xsd and http://www.w3.org/2001/xml.xsd
W3C_XSD_DIR = Path(__file__).resolve().parents[3] / "schemas" / "w3c"
W3C_XSD_FILES = {
    "http://www.w3.org/2001/XMLSchema.xsd": (
        W3C_XSD_DIR / "XMLSchema.xsd",
        "e767a159c179bd6e4dc2bac2a63a62f50cbd027ec19d6b5cc9eb1c2f33e0cb2e",
    ),
    "http://www.w3.org/2001/xml.xsd": (
        W3C_XSD_DIR / "xml.xsd",
        "61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c",
    ),
}

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
    rule_evidence_bundle_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rule_evaluation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rule_decision_status: str | None = Field(default=None, pattern=r"^(pass|fail|unknown|review_required)$")
    production_verification: str | None = Field(default=None, pattern=r"^(eligible|review_required)$")
    delivery_evidence: RuleDeliveryEvidence | None = None
    status: ValidationStatus
    checked_entity_count: int = Field(ge=0)
    findings: tuple[IfcIdsFinding, ...] = ()

    @model_validator(mode="after")
    def _status_matches_findings(self) -> "IfcIdsValidationReport":
        expected = ValidationStatus.FAIL if any(item.status is ValidationStatus.FAIL for item in self.findings) else ValidationStatus.PASS
        if self.status is not expected:
            raise ValueError("IFC/IDS validation status 与 findings 不一致")
        return self

    @model_validator(mode="after")
    def _identity_is_explicit(self) -> "IfcIdsValidationReport":
        has_legacy = all(
            value is not None
            for value in (
                self.rule_evidence_bundle_sha256,
                self.rule_evaluation_sha256,
                self.rule_decision_status,
                self.production_verification,
            )
        )
        if self.delivery_evidence is not None and has_legacy:
            raise ValueError("IFC 规则身份不得同时声明单条评估与模型级聚合")
        if self.delivery_evidence is None and not has_legacy:
            raise ValueError("IFC 缺少规则证据身份")
        if has_legacy and self.production_verification == "review_required" and self.rule_decision_status in {"pass", "fail"}:
            raise ValueError("IFC review_required 规则证据身份不得声明规范 PASS/FAIL")
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


class IfcGeometryFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=256)
    check: str = Field(min_length=1, max_length=128)
    status: ValidationStatus
    expected: str | float | int | bool | None = None
    actual: str | float | int | bool | None = None
    detail: str = Field(min_length=1, max_length=2000)


class IfcMeasuredSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str
    axis_start: tuple[float, float, float]
    axis_end: tuple[float, float, float]
    length_m: float
    inner_diameter_mm: float
    outer_diameter_mm: float
    mesh_volume_m3: float
    polygon_volume_m3: float
    analytic_volume_m3: float
    volume_consistency_relative_error: float
    discretization_relative_error: float
    outer_ring_vertex_count: int
    inner_ring_vertex_count: int


class IfcGeometryValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_version: str = Field(default=IFC_GEOMETRY_VALIDATION_VERSION, pattern=r"^1(?:\.\d+)?$")
    ifc_schema: str = Field(pattern=r"^IFC4X3(?:_ADD2)?$")
    source_ir_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(default=IFC_GENERATOR_VERSION, min_length=1, max_length=128)
    measurement_source: str = Field(default=IFC_GEOMETRY_NOTE, min_length=1, max_length=2000)
    numeric_tolerance_m: float = Field(default=IFC_GEOMETRY_TOLERANCE_M, gt=0)
    status: ValidationStatus
    checked_segment_count: int = Field(ge=0)
    findings: tuple[IfcGeometryFinding, ...] = ()
    segments: tuple[IfcMeasuredSegment, ...] = ()

    @model_validator(mode="after")
    def _status_matches_findings(self) -> "IfcGeometryValidationReport":
        expected = ValidationStatus.FAIL if any(item.status is ValidationStatus.FAIL for item in self.findings) else ValidationStatus.PASS
        if self.status is not expected:
            raise ValueError("IFC geometry validation status 与 findings 不一致")
        return self

    @property
    def ok(self) -> bool:
        return self.status is ValidationStatus.PASS


class IfcSchemaIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = Field(default="error", max_length=32)
    message: str = Field(min_length=1, max_length=4000)
    instance: str | None = Field(default=None, max_length=512)


class IfcSchemaValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_version: str = Field(default=IFC_SCHEMA_VALIDATION_VERSION, pattern=r"^1(?:\.\d+)?$")
    ifc_schema: str = Field(min_length=1, max_length=64)
    validator: str = Field(default=IFC_SCHEMA_VALIDATOR, min_length=1, max_length=128)
    scope: str = Field(default=IFC_SCHEMA_SCOPE, min_length=1, max_length=2000)
    status: ValidationStatus
    issue_count: int = Field(ge=0)
    issues: tuple[IfcSchemaIssue, ...] = ()
    duplicate_global_ids: tuple[str, ...] = ()
    invalid_global_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_issues(self) -> "IfcSchemaValidationReport":
        failed = (
            self.issue_count > 0
            or bool(self.duplicate_global_ids)
            or bool(self.invalid_global_ids)
        )
        expected = ValidationStatus.FAIL if failed else ValidationStatus.PASS
        if self.status is not expected:
            raise ValueError("IFC schema validation status 与问题列表不一致")
        return self

    @property
    def ok(self) -> bool:
        return self.status is ValidationStatus.PASS


@dataclass(frozen=True)
class IfcIdsPackage:
    ifc_path: Path
    ids_path: Path
    report_path: Path
    evidence_path: Path
    report: IfcIdsValidationReport
    evidence: tuple[RuleEvidence, ...]
    geometry_report: IfcGeometryValidationReport | None = None
    geometry_report_path: Path | None = None
    schema_report: IfcSchemaValidationReport | None = None
    schema_report_path: Path | None = None


def build_ifc_ids_package(
    snapshot: SemanticSnapshot | dict[str, Any],
    *,
    output_dir: Path,
    ir: CompiledUtilityIR | dict[str, Any] | None = None,
    delivery_evidence: RuleDeliveryEvidence | None = None,
) -> IfcIdsPackage:
    semantic = snapshot if isinstance(snapshot, SemanticSnapshot) else SemanticSnapshot.model_validate(snapshot)
    compiled: CompiledUtilityIR | None = None
    if ir is not None:
        compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
        if compiled.protocol_version == "1.0":
            raise ValueError("IFC 实体生成要求显式 CompiledUtilityIR 1.1")
        if compiled.canonical_sha256() != semantic.source_ir_sha256:
            raise ValueError("IFC 输入 CompiledUtilityIR 与 SemanticSnapshot.source_ir_sha256 不一致")
    if delivery_evidence is not None and compiled is None:
        raise ValueError("模型级交付规则证据必须绑定 CompiledUtilityIR")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ifc_path = root / "municipal_utility.ifc"
    ids_path = root / "municipal_utility.ids"
    report_path = root / "ifc_ids_validation_report.json"
    evidence_path = root / "ifc_ids_rule_evidence.json"

    model = _build_ifc_model(semantic, compiled, delivery_evidence)
    model.write(str(ifc_path))
    _write_ids(semantic, ids_path, compiled, delivery_evidence)
    report = validate_ifc_against_ids(
        ifc_path,
        ids_path,
        expected_source_ir_sha256=compiled.canonical_sha256() if compiled is not None else None,
        expected_delivery_evidence=delivery_evidence,
    )
    geometry_report: IfcGeometryValidationReport | None = None
    geometry_report_path: Path | None = None
    schema_report: IfcSchemaValidationReport | None = None
    schema_report_path: Path | None = None
    if compiled is not None:
        # Schema/EXPRESS validation runs only on the IR-derived delivery path.
        # Metadata-only offline artifacts are not presented as validated delivery.
        schema_report = validate_ifc_schema(ifc_path)
        schema_report_path = root / "ifc_schema_validation_report.json"
        schema_report_path.write_text(
            json.dumps(schema_report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        geometry_report = validate_ifc_geometry(ifc_path, compiled)
        geometry_report_path = root / "ifc_geometry_validation_report.json"
        geometry_report_path.write_text(
            json.dumps(geometry_report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    evidence = (*_report_evidence(report), *_geometry_evidence(geometry_report))
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return IfcIdsPackage(
        ifc_path,
        ids_path,
        report_path,
        evidence_path,
        report,
        evidence,
        geometry_report,
        geometry_report_path,
        schema_report,
        schema_report_path,
    )


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
    if package.schema_report is not None and not package.schema_report.ok:
        raise ValueError("IFC 模式校验未通过，禁止提交完成态 Artifact Manifest")
    if package.geometry_report is not None and not package.geometry_report.ok:
        raise ValueError("IFC 几何读回未通过，禁止提交完成态 Artifact Manifest")
    root = Path(workdir).resolve()
    artifacts = []
    artifact_rows = [
        (package.ifc_path, "ifc-model", "application/x-step"),
        (package.ids_path, "ids-requirements", "application/xml"),
        (package.report_path, "ifc-ids-validation", "application/json"),
        (package.evidence_path, "rule-evidence", "application/json"),
    ]
    if package.schema_report_path is not None:
        artifact_rows.append(
            (package.schema_report_path, "ifc-schema-validation", "application/json")
        )
    if package.geometry_report_path is not None:
        artifact_rows.append(
            (package.geometry_report_path, "ifc-geometry-validation", "application/json")
        )
    for path, kind, media_type in artifact_rows:
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


def validate_ifc_against_ids(
    ifc_path: Path,
    ids_path: Path,
    *,
    expected_source_ir_sha256: str | None = None,
    expected_delivery_evidence: RuleDeliveryEvidence | None = None,
) -> IfcIdsValidationReport:
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
    if expected_source_ir_sha256 is not None and source_ir_sha256 != expected_source_ir_sha256:
        raise ValueError(
            "IFC SourceIRSHA256 与当前确认的 CompiledUtilityIR 不一致: "
            f"{source_ir_sha256} != {expected_source_ir_sha256}"
        )
    delivery_evidence: RuleDeliveryEvidence | None = None
    legacy_properties = {
        "rule_evidence_bundle_sha256": str(project_metadata.get("RuleEvidenceBundleSHA256") or ""),
        "rule_evaluation_sha256": str(project_metadata.get("RuleEvaluationSHA256") or ""),
        "rule_decision_status": str(project_metadata.get("RuleDecisionStatus") or ""),
        "production_verification": str(project_metadata.get("ProductionVerification") or ""),
    }
    has_delivery = any(
        project_metadata.get(name) is not None
        for name in (
            "DeliveryRuleIdentityVersion",
            "DeliveryRuleEvidenceAggregateSHA256",
            "DeliveryRuleDecisionStatus",
            "DeliveryProductionVerification",
        )
    )
    has_legacy = all(legacy_properties.values())
    if has_delivery and has_legacy:
        raise ValueError(f"IFC {PROJECT_PSET_NAME} 不得同时声明单条评估与模型级规则证据")
    if has_delivery:
        delivery_evidence = RuleDeliveryEvidence.from_ifc_properties(project_metadata)
        if delivery_evidence.source_ir_sha256 != source_ir_sha256:
            raise ValueError("IFC 交付规则证据与 IFC 自身源 IR 身份不一致")
        if expected_delivery_evidence is not None:
            _require_delivery_evidence_matches(delivery_evidence, expected_delivery_evidence)
    elif has_legacy:
        if (
            len(legacy_properties["rule_evidence_bundle_sha256"]) != 64
            or len(legacy_properties["rule_evaluation_sha256"]) != 64
            or legacy_properties["rule_decision_status"] not in {"pass", "fail", "unknown", "review_required"}
            or legacy_properties["production_verification"] not in {"eligible", "review_required"}
        ):
            raise ValueError(f"IFC {PROJECT_PSET_NAME} 缺少完整规则证据身份")
        if (
            legacy_properties["production_verification"] == "review_required"
            and legacy_properties["rule_decision_status"] in {"pass", "fail"}
        ):
            raise ValueError("IFC review_required 规则证据身份不得声明规范 PASS/FAIL")
    else:
        raise ValueError(f"IFC {PROJECT_PSET_NAME} 缺少规则证据身份")
    if expected_delivery_evidence is not None and delivery_evidence is None:
        raise ValueError("期望模型级交付规则证据，但 IFC 未携带")
    findings: list[IfcIdsFinding] = []
    checked_ids: set[str] = set()
    findings.extend(_duplicate_identity_findings(model))

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
    if delivery_evidence is not None:
        legacy_identity: dict[str, Any] = {
            "rule_evidence_bundle_sha256": None,
            "rule_evaluation_sha256": None,
            "rule_decision_status": None,
            "production_verification": None,
        }
    else:
        legacy_identity = legacy_properties
    return IfcIdsValidationReport(
        ifc_schema=model.schema,
        source_ir_id=source_ir_id,
        source_ir_sha256=source_ir_sha256,
        **legacy_identity,
        delivery_evidence=delivery_evidence,
        status=status,
        checked_entity_count=len(checked_ids),
        findings=tuple(findings),
    )


def _build_ifc_model(
    snapshot: SemanticSnapshot,
    compiled: CompiledUtilityIR | None = None,
    delivery_evidence: RuleDeliveryEvidence | None = None,
) -> ifcopenshell.file:
    model = ifcopenshell.api.project.create_file(version="IFC4X3")
    model.header.file_name.name = "municipal_utility.ifc"
    model.header.file_name.time_stamp = "1970-01-01T00:00:00"
    project = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="openBIMAgent M1 Municipal Utility")
    project_pset = ifcopenshell.api.pset.add_pset(model, product=project, name=PROJECT_PSET_NAME)
    project_properties: dict[str, Any] = {
        "SourceIRID": snapshot.source_ir_id,
        "SourceIRSHA256": snapshot.source_ir_sha256,
        "Generator": "openbimagent.deliver.ifc_ids",
        "GeneratorVersion": IFC_GENERATOR_VERSION,
        "GenerationSource": "compiled_utility_ir" if compiled is not None else "semantic_snapshot",
        "GeometrySource": (
            "ir_derived_hollow_profile_extrusion" if compiled is not None else "none_metadata_only"
        ),
        "NativeHostExport": False,
    }
    if snapshot.rule_identity is not None:
        project_properties.update(_ifc_rule_identity_properties(snapshot.rule_identity))
    if delivery_evidence is not None:
        if snapshot.rule_identity is not None:
            raise ValueError("模型级交付规则证据与单条评估身份不能写入同一个 IFC")
        project_properties.update(delivery_evidence.ifc_properties())
    ifcopenshell.api.pset.edit_pset(
        model,
        pset=project_pset,
        properties=project_properties,
    )
    body_context = _setup_geometric_context(model, project)
    site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite", name="Municipal Utility Site")
    ifcopenshell.api.aggregate.assign_object(model, products=[site], relating_object=project)
    segment_by_id = (
        {segment.segment_id: segment for segment in compiled.segments}
        if compiled is not None
        else {}
    )

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
        segment = segment_by_id.get(item.stable_id)
        _attach_properties(model, entity, item, segment=segment)
        if item.object_kind in {SemanticObjectKind.NODE, SemanticObjectKind.SEGMENT}:
            ifcopenshell.api.spatial.assign_container(model, products=[entity], relating_structure=site)
        system = systems.get(item.system_id)
        if system is not None and item.object_kind is not SemanticObjectKind.PORT:
            ifcopenshell.api.system.assign_system(model, products=[entity], system=system)
        if segment is not None and item.object_kind is SemanticObjectKind.SEGMENT:
            _attach_pipe_body(model, entity, segment, body_context)

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


def _setup_geometric_context(model: ifcopenshell.file, project: Any) -> Any:
    length_unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix=None, Name="METRE")
    area_unit = model.create_entity("IfcSIUnit", UnitType="AREAUNIT", Prefix=None, Name="SQUARE_METRE")
    volume_unit = model.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Prefix=None, Name="CUBIC_METRE")
    project.UnitsInContext = model.create_entity(
        "IfcUnitAssignment", Units=[length_unit, area_unit, volume_unit]
    )
    context = model.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-6,
        WorldCoordinateSystem=model.create_entity(
            "IfcAxis2Placement3D",
            Location=model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
        ),
    )
    project.RepresentationContexts = [context]
    return model.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=context,
        TargetView="MODEL_VIEW",
    )


def _attach_pipe_body(
    model: ifcopenshell.file,
    entity: Any,
    segment: Any,
    body_context: Any,
) -> None:
    axis, outer_mm = execution_geometry(segment)
    inner_mm, _ = resolve_dimensions(
        segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
    )
    if inner_mm is None:
        raise ValueError(f"segment {segment.segment_id!r} 缺少显式内径，无法生成空腔实体")
    if len(axis) != 2:
        raise ValueError(f"segment {segment.segment_id!r} 仅支持直管两轴端点")
    start = (axis[0].x_m, axis[0].y_m, axis[0].z_m)
    end = (axis[1].x_m, axis[1].y_m, axis[1].z_m)
    direction = tuple(b - a for a, b in zip(start, end, strict=True))
    length_m = math.sqrt(sum(value * value for value in direction))
    if not math.isfinite(length_m) or length_m <= 0:
        raise ValueError(f"segment {segment.segment_id!r} 轴线长度无效")
    unit = tuple(value / length_m for value in direction)
    up = (0.0, 0.0, 1.0) if abs(unit[2]) < 0.9 else (1.0, 0.0, 0.0)
    ref = _cross(up, unit)
    ref_length = math.sqrt(sum(value * value for value in ref))
    ref = tuple(value / ref_length for value in ref)
    placement = model.create_entity(
        "IfcAxis2Placement3D",
        Location=model.create_entity("IfcCartesianPoint", Coordinates=start),
        Axis=model.create_entity("IfcDirection", DirectionRatios=unit),
        RefDirection=model.create_entity("IfcDirection", DirectionRatios=ref),
    )
    profile = model.create_entity(
        "IfcCircleHollowProfileDef",
        ProfileType="AREA",
        Position=None,
        Radius=outer_mm / 2000.0,
        WallThickness=(outer_mm - inner_mm) / 2000.0,
    )
    solid = model.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=model.create_entity(
            "IfcAxis2Placement3D",
            Location=model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
        ),
        ExtrudedDirection=model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=length_m,
    )
    representation = model.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    entity.ObjectPlacement = model.create_entity(
        "IfcLocalPlacement", PlacementRelTo=None, RelativePlacement=placement
    )
    entity.Representation = model.create_entity(
        "IfcProductDefinitionShape", Representations=[representation]
    )


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _attach_properties(
    model: ifcopenshell.file,
    entity: Any,
    item: Any,
    *,
    segment: Any = None,
) -> None:
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
    if item.object_kind is SemanticObjectKind.NODE:
        properties["GeometryRepresentation"] = "schematic_none"
    for field in ("diameter_mm", "horizontal_length_m", "start_invert_m", "end_invert_m", "slope", "material"):
        value = getattr(item, field)
        if value is not None:
            properties[_property_name(field)] = value
    if segment is not None:
        inner_mm, outer_mm = resolve_dimensions(
            segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
        )
        if inner_mm is None or outer_mm is None:
            raise ValueError(f"segment {segment.segment_id!r} 缺少显式内外径")
        properties.update(
            {
                "NominalDiameterMM": segment.diameter_mm,
                "InnerDiameterMM": inner_mm,
                "OuterDiameterMM": outer_mm,
                "WallThicknessMM": (outer_mm - inner_mm) / 2.0,
                "GeometryRepresentation": "IfcCircleHollowProfileDef+IfcExtrudedAreaSolid",
            }
        )
    if item.object_kind is SemanticObjectKind.SEGMENT and segment is None:
        properties["GeometryRepresentation"] = "none_metadata_only"
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


def _write_ids(
    snapshot: SemanticSnapshot,
    path: Path,
    compiled: CompiledUtilityIR | None = None,
    delivery_evidence: RuleDeliveryEvidence | None = None,
) -> None:
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
    segment_by_id = (
        {segment.segment_id: segment for segment in compiled.segments}
        if compiled is not None
        else {}
    )
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
        if item.object_kind is SemanticObjectKind.NODE:
            _property_requirement(requirements, "GeometryRepresentation", "schematic_none")
        if item.object_kind is SemanticObjectKind.SEGMENT:
            for property_name, expected in (
                ("DiameterMM", item.diameter_mm),
                ("Slope", item.slope),
                ("Material", item.material),
                ("TopologyPortIDs", ",".join(sorted(item.topology))),
            ):
                _property_requirement(requirements, property_name, expected)
            segment = segment_by_id.get(item.stable_id)
            if segment is None:
                _property_requirement(requirements, "GeometryRepresentation", "none_metadata_only")
            else:
                inner_mm, outer_mm = resolve_dimensions(
                    segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
                )
                if inner_mm is None or outer_mm is None:
                    raise ValueError(f"segment {segment.segment_id!r} 缺少显式内外径")
                for property_name, expected in (
                    ("NominalDiameterMM", segment.diameter_mm),
                    ("InnerDiameterMM", inner_mm),
                    ("OuterDiameterMM", outer_mm),
                    ("WallThicknessMM", (outer_mm - inner_mm) / 2.0),
                    ("GeometryRepresentation", "IfcCircleHollowProfileDef+IfcExtrudedAreaSolid"),
                ):
                    _property_requirement(requirements, property_name, expected)
    _ = delivery_evidence
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


class _W3cSchemaResolver(LET.Resolver):
    """Resolve the IDS schema's W3C imports from pinned local files only."""

    def resolve(self, url, pubid, context):  # noqa: ANN001 - lxml resolver signature
        entry = W3C_XSD_FILES.get(url)
        if entry is None:
            return None
        path, expected_sha256 = entry
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"W3C XSD 依赖不可读: {path}: {exc}") from exc
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"W3C XSD 依赖摘要漂移: {url}: {actual_sha256} != {expected_sha256}"
            )
        return self.resolve_string(payload, context, base_url=str(path))


def _verify_w3c_dependencies() -> None:
    for url, (path, expected_sha256) in W3C_XSD_FILES.items():
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"W3C XSD 依赖不可读: {path}: {exc}") from exc
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"W3C XSD 依赖摘要漂移: {url}: {actual_sha256} != {expected_sha256}"
            )


@lru_cache(maxsize=1)
def _ids_xml_schema() -> LET.XMLSchema:
    actual_sha256 = hashlib.sha256(IDS_XSD_PATH.read_bytes()).hexdigest()
    if actual_sha256 != IDS_XSD_SHA256:
        raise ValueError(
            f"IDS 1.0 XSD 摘要漂移: expected={IDS_XSD_SHA256}, actual={actual_sha256}"
        )
    _verify_w3c_dependencies()
    parser = LET.XMLParser(no_network=True)
    parser.resolvers.add(_W3cSchemaResolver())
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




def validate_ifc_schema(ifc_path: Path) -> IfcSchemaValidationReport:
    """Validate the saved IFC with IfcOpenShell schema/EXPRESS checks.

    This is a schema-level check only; it is not engineering compliance and not
    full IDS coverage. Duplicate and malformed GlobalIds are checked explicitly
    because IfcOpenShell does not enforce GlobalId uniqueness.
    """
    path = Path(ifc_path)
    try:
        model = ifcopenshell.open(str(path))
    except Exception as exc:  # noqa: BLE001 - report the parse failure explicitly
        return IfcSchemaValidationReport(
            ifc_schema="unreadable",
            status=ValidationStatus.FAIL,
            issue_count=1,
            issues=(
                IfcSchemaIssue(level="error", message=f"IFC 无法解析: {exc}"),
            ),
        )
    issues: list[IfcSchemaIssue] = []
    try:
        logger = ifcvalidate.json_logger()
        ifcvalidate.validate(model, logger, express_rules=True)
        for item in logger.statements:
            issues.append(
                IfcSchemaIssue(
                    level=str(item.get("level") or "error"),
                    message=str(item.get("message") or "validation issue"),
                    instance=_schema_instance(item),
                )
            )
    except Exception as exc:  # noqa: BLE001 - validator failure is a failed check
        issues.append(
            IfcSchemaIssue(level="error", message=f"IfcOpenShell validate 执行失败: {exc}")
        )
    duplicate_global_ids, invalid_global_ids = _global_id_issues(model)
    failing = [
        item
        for item in issues
        if item.level.lower() in {"error", "fatal", "critical"}
    ]
    status = (
        ValidationStatus.FAIL
        if failing or duplicate_global_ids or invalid_global_ids
        else ValidationStatus.PASS
    )
    return IfcSchemaValidationReport(
        ifc_schema=model.schema,
        status=status,
        issue_count=len(failing),
        issues=tuple(issues[:200]),
        duplicate_global_ids=duplicate_global_ids,
        invalid_global_ids=invalid_global_ids,
    )


def _schema_instance(item: dict[str, Any]) -> str | None:
    for key in ("inst", "instance", "id"):
        value = item.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _global_id_issues(
    model: ifcopenshell.file,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    counts: dict[str, int] = {}
    invalid: set[str] = set()
    for entity in model.by_type("IfcRoot"):
        global_id = str(getattr(entity, "GlobalId", "") or "")
        if not global_id:
            invalid.add(f"#{entity.id()}:empty")
            continue
        error = ifcvalidate.validate_guid(global_id)
        if error:
            invalid.add(global_id)
        counts[global_id] = counts.get(global_id, 0) + 1
    duplicates = tuple(sorted(gid for gid, count in counts.items() if count > 1))
    return duplicates, tuple(sorted(invalid))


def _require_delivery_evidence_matches(
    actual: RuleDeliveryEvidence,
    expected: RuleDeliveryEvidence,
) -> None:
    for field in (
        "protocol_version",
        "identity_kind",
        "evidence_source",
        "rule_context_sha256",
        "source_ir_sha256",
        "evidence_aggregate_sha256",
        "decision_status",
        "production_verification",
        "checker_version",
        "checked_rule_count",
        "candidate_sha256",
        "validation_sha256",
    ):
        if getattr(actual, field) != getattr(expected, field):
            raise ValueError(f"IFC 交付规则证据与当前确认结果不一致: {field}")


def _duplicate_identity_findings(model: ifcopenshell.file) -> list[IfcIdsFinding]:
    counts: dict[str, int] = {}
    for entity in model.by_type("IfcPipeSegment"):
        common = ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {}
        stable_id = str(common.get("StableObjectID") or "")
        if stable_id:
            counts[stable_id] = counts.get(stable_id, 0) + 1
    findings: list[IfcIdsFinding] = []
    for stable_id, count in sorted(counts.items()):
        if count > 1:
            findings.append(
                IfcIdsFinding(
                    rule_id="IFC-IDEMPOTENT-IDS",
                    status=ValidationStatus.FAIL,
                    stable_id=stable_id,
                    ifc_class="IfcPipeSegment",
                    field_path="@StableObjectID",
                    expected=1,
                    actual=count,
                    source_ir_path="@delivery",
                    detail=f"StableObjectID {stable_id!r} 出现 {count} 次（重复身份）",
                )
            )
    return findings


def _geometry_evidence(
    report: IfcGeometryValidationReport | None,
) -> tuple[RuleEvidence, ...]:
    if report is None:
        return ()
    evidence: list[RuleEvidence] = []
    for index, finding in enumerate(report.findings):
        subject = (
            EvidenceSubjectType.SYSTEM
            if finding.stable_id == "@project"
            else EvidenceSubjectType.SEGMENT
        )
        evidence.append(
            RuleEvidence(
                evidence_id=f"ifc-geometry-{index:04d}-{finding.stable_id}",
                rule_id="IFC-GEOMETRY-READBACK",
                check_name="ifc_geometry_verified",
                status=(
                    EvidenceStatus.PASS
                    if finding.status is ValidationStatus.PASS
                    else EvidenceStatus.FAIL
                ),
                subject_type=subject,
                subject_id=finding.stable_id,
                detail=finding.detail,
                measured_value=finding.actual,
                limit_value=finding.expected,
                source_clause=f"ifcopenshell.geom {finding.check}; file readback",
            )
        )
    return tuple(evidence)


def validate_ifc_geometry(
    ifc_path: Path,
    ir: CompiledUtilityIR | dict[str, Any],
) -> IfcGeometryValidationReport:
    """Reopen the saved IFC and measure its actual tessellated geometry.

    No generator in-memory object or input parameter is used as geometry
    evidence: the report comes from ifcopenshell.geom on the saved file.
    """
    compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
    model = ifcopenshell.open(str(Path(ifc_path)))
    if not model.schema.startswith("IFC4X3"):
        raise ValueError(f"IFC Schema 必须为 IFC4X3，实际 {model.schema}")
    project = next(iter(model.by_type("IfcProject")), None)
    if project is None:
        raise ValueError("IFC 缺少 IfcProject")
    findings: list[IfcGeometryFinding] = [_unit_finding(project)]
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    measured: list[IfcMeasuredSegment] = []
    for segment in compiled.segments:
        segment_findings, metrics = _validate_segment_geometry(model, settings, segment)
        findings.extend(segment_findings)
        if metrics is not None:
            measured.append(metrics)
    status = (
        ValidationStatus.FAIL
        if any(item.status is ValidationStatus.FAIL for item in findings)
        else ValidationStatus.PASS
    )
    return IfcGeometryValidationReport(
        ifc_schema=model.schema,
        source_ir_id=compiled.ir_id,
        source_ir_sha256=compiled.canonical_sha256(),
        status=status,
        checked_segment_count=len(measured),
        findings=tuple(findings),
        segments=tuple(measured),
    )


def _unit_finding(project: Any) -> IfcGeometryFinding:
    assignment = getattr(project, "UnitsInContext", None)
    length_units = (
        [unit for unit in assignment.Units if getattr(unit, "UnitType", None) == "LENGTHUNIT"]
        if assignment is not None
        else []
    )
    if not length_units:
        return _geometry_finding(
            "@project", "length_unit", False, "IfcSIUnit(LENGTHUNIT, METRE)", None,
            "IFC 缺少长度单位声明",
        )
    for unit in length_units:
        if unit.is_a("IfcSIUnit") and unit.Name == "METRE" and unit.Prefix in (None, ""):
            return _geometry_finding(
                "@project", "length_unit", True, "IfcSIUnit(LENGTHUNIT, METRE)",
                "IfcSIUnit(LENGTHUNIT, METRE)", "项目长度单位为米",
            )
    actual = ",".join(
        f"{unit.is_a()}:{getattr(unit, 'Name', None)}:{getattr(unit, 'Prefix', None)}"
        for unit in length_units
    )
    return _geometry_finding(
        "@project", "length_unit", False, "IfcSIUnit(LENGTHUNIT, METRE)", actual,
        "项目长度单位不是米（不支持隐含单位换算）",
    )


def _validate_segment_geometry(
    model: ifcopenshell.file,
    settings: Any,
    segment: Any,
) -> tuple[list[IfcGeometryFinding], IfcMeasuredSegment | None]:
    stable_id = segment.segment_id
    findings: list[IfcGeometryFinding] = []
    expected_axis, outer_mm = execution_geometry(segment)
    inner_mm, _ = resolve_dimensions(
        segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
    )
    if inner_mm is None:
        raise ValueError(f"segment {stable_id!r} 缺少显式内径")
    expected_start = (expected_axis[0].x_m, expected_axis[0].y_m, expected_axis[0].z_m)
    expected_end = (expected_axis[1].x_m, expected_axis[1].y_m, expected_axis[1].z_m)
    expected_vector = tuple(b - a for a, b in zip(expected_start, expected_end, strict=True))
    expected_length = math.sqrt(sum(value * value for value in expected_vector))
    expected_unit = tuple(value / expected_length for value in expected_vector)

    entity = _find_pipe_entity(model, stable_id)
    if entity is None:
        findings.append(
            _geometry_finding(stable_id, "entity_present", False, "IfcPipeSegment", None,
                              "Saved IFC 中找不到该管段实体")
        )
        return findings, None
    findings.append(
        _geometry_finding(stable_id, "entity_present", True, "IfcPipeSegment", "IfcPipeSegment",
                          "实体存在")
    )
    structure_ok, structure_detail = _body_structure(entity)
    findings.append(
        _geometry_finding(stable_id, "body_representation", structure_ok,
                          "Body/SweptSolid IfcExtrudedAreaSolid(IfcCircleHollowProfileDef)",
                          structure_detail, "检查实体是否具有空心拉伸 Body 表示")
    )
    try:
        vertices, faces = _tessellate(settings, entity)
    except Exception as exc:  # noqa: BLE001 - geometry failure is recorded, not hidden
        findings.append(
            _geometry_finding(stable_id, "tessellation", False, "non-empty tessellated mesh", None,
                              f"三角化失败: {exc}")
        )
        return findings, None
    if len(vertices) < 4 or len(faces) < 4:
        findings.append(
            _geometry_finding(stable_id, "tessellation", False, "non-empty tessellated mesh",
                              f"verts={len(vertices)} triangles={len(faces)}", "三角化结果为空")
        )
        return findings, None
    try:
        metrics = _measure_tube(vertices, faces, expected_unit)
    except Exception as exc:  # noqa: BLE001
        findings.append(
            _geometry_finding(stable_id, "measurement", False, "measurable tube mesh", None,
                              f"几何测量失败: {exc}")
        )
        return findings, None

    tolerance = IFC_GEOMETRY_TOLERANCE_M
    hollow = metrics["inner_ring_count"] >= 3 and metrics["inner_radius_m"] > 0.0
    findings.append(
        _geometry_finding(stable_id, "hollow_profile", hollow, "inner surface radius > 0",
                          f"inner_radius_m={metrics['inner_radius_m']:.9f}", "检查空腔是否存在")
    )
    findings.append(
        _geometry_finding(
            stable_id, "inner_diameter", abs(metrics["inner_diameter_mm"] - inner_mm) <= tolerance * 1000.0,
            inner_mm, round(metrics["inner_diameter_mm"], 6), "读回内径（毫米）",
        )
    )
    findings.append(
        _geometry_finding(
            stable_id, "outer_diameter", abs(metrics["outer_diameter_mm"] - outer_mm) <= tolerance * 1000.0,
            outer_mm, round(metrics["outer_diameter_mm"], 6), "读回外径（毫米）",
        )
    )
    findings.append(
        _geometry_finding(
            stable_id, "axis_length", abs(metrics["length_m"] - expected_length) <= tolerance,
            round(expected_length, 9), round(metrics["length_m"], 9), "读回真实三维轴线长度（米）",
        )
    )
    endpoint_error = min(
        max(_distance(metrics["axis_start"], expected_start), _distance(metrics["axis_end"], expected_end)),
        max(_distance(metrics["axis_start"], expected_end), _distance(metrics["axis_end"], expected_start)),
    )
    findings.append(
        _geometry_finding(
            stable_id, "axis_position", endpoint_error <= tolerance,
            f"{expected_start}->{expected_end}",
            f"{metrics['axis_start']}->{metrics['axis_end']}",
            f"轴线端点最大偏差={endpoint_error:.9f} m",
        )
    )
    direction_error = 1.0 - abs(_dot(metrics["axis_direction"], expected_unit))
    findings.append(
        _geometry_finding(
            stable_id, "axis_direction", direction_error <= 1e-9,
            expected_unit, metrics["axis_direction"],
            f"方向余弦偏差={direction_error:.3e}",
        )
    )
    volume_ok = (
        abs(metrics["volume_consistency_relative_error"]) <= IFC_VOLUME_RELATIVE_TOLERANCE
    )
    findings.append(
        _geometry_finding(
            stable_id, "volume_consistency", volume_ok, "mesh volume == polygon-prism volume",
            round(metrics["mesh_volume_m3"], 9),
            f"网格体积与多边形棱柱体积相对差={metrics['volume_consistency_relative_error']:.3e}",
        )
    )
    measured = IfcMeasuredSegment(
        stable_id=stable_id,
        axis_start=metrics["axis_start"],
        axis_end=metrics["axis_end"],
        length_m=round(metrics["length_m"], 9),
        inner_diameter_mm=round(metrics["inner_diameter_mm"], 6),
        outer_diameter_mm=round(metrics["outer_diameter_mm"], 6),
        mesh_volume_m3=round(metrics["mesh_volume_m3"], 12),
        polygon_volume_m3=round(metrics["polygon_volume_m3"], 12),
        analytic_volume_m3=round(metrics["analytic_volume_m3"], 12),
        volume_consistency_relative_error=metrics["volume_consistency_relative_error"],
        discretization_relative_error=metrics["discretization_relative_error"],
        outer_ring_vertex_count=metrics["outer_ring_count"],
        inner_ring_vertex_count=metrics["inner_ring_count"],
    )
    return findings, measured


def _find_pipe_entity(model: ifcopenshell.file, stable_id: str) -> Any:
    for entity in model.by_type("IfcPipeSegment"):
        common = ifcopenshell.util.element.get_psets(entity).get(PSET_NAME) or {}
        if str(common.get("StableObjectID") or "") == stable_id:
            return entity
    return None


def _body_structure(entity: Any) -> tuple[bool, str]:
    representation = getattr(entity, "Representation", None)
    if representation is None:
        return False, "缺少 IfcProductDefinitionShape"
    shapes = [
        item
        for item in representation.Representations
        if item.RepresentationIdentifier == "Body"
    ]
    if not shapes:
        return False, "缺少 Body representation"
    shape = shapes[0]
    if shape.RepresentationType != "SweptSolid":
        return False, f"RepresentationType={shape.RepresentationType!r}"
    items = list(shape.Items or ())
    if len(items) != 1 or not items[0].is_a("IfcExtrudedAreaSolid"):
        return False, "Body 不是单个 IfcExtrudedAreaSolid"
    profile = items[0].SweptArea
    if not profile.is_a("IfcCircleHollowProfileDef"):
        return False, f"SweptArea={profile.is_a()}"
    return True, "IfcCircleHollowProfileDef + IfcExtrudedAreaSolid"


def _tessellate(settings: Any, entity: Any) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    shape = ifcopenshell.geom.create_shape(settings, entity)
    geometry = shape.geometry
    vertices = [
        tuple(geometry.verts[index : index + 3])
        for index in range(0, len(geometry.verts), 3)
    ]
    faces = [
        tuple(geometry.faces[index : index + 3])
        for index in range(0, len(geometry.faces), 3)
    ]
    return vertices, faces


def _measure_tube(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    expected_unit: tuple[float, float, float],
) -> dict[str, Any]:
    count = len(vertices)
    centroid = tuple(sum(point[axis] for point in vertices) / count for axis in range(3))
    covariance = [[0.0] * 3 for _ in range(3)]
    for point in vertices:
        relative = tuple(point[axis] - centroid[axis] for axis in range(3))
        for row in range(3):
            for column in range(3):
                covariance[row][column] += relative[row] * relative[column]
    direction = _normalize((1.0, 1.0, 1.0))
    for _ in range(200):
        direction = _normalize(
            tuple(_dot(covariance[row], direction) for row in range(3))
        )
    if _dot(direction, expected_unit) < 0:
        direction = tuple(-value for value in direction)
    up = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
    x_axis = _normalize(_cross(up, direction))
    y_axis = _cross(direction, x_axis)
    projected: list[tuple[float, float, float, float]] = []
    for point in vertices:
        relative = tuple(point[axis] - centroid[axis] for axis in range(3))
        along = _dot(relative, direction)
        x_value = _dot(relative, x_axis)
        y_value = _dot(relative, y_axis)
        projected.append((along, x_value, y_value, math.hypot(x_value, y_value)))
    along_values = [item[0] for item in projected]
    t_min, t_max = min(along_values), max(along_values)
    length = t_max - t_min
    inner_radius = min(item[3] for item in projected)
    outer_radius = max(item[3] for item in projected)

    def ring(t_value: float, radius: float) -> list[tuple[float, float]]:
        points = [
            (item[1], item[2])
            for item in projected
            if abs(item[0] - t_value) <= IFC_GEOMETRY_TOLERANCE_M
            and abs(item[3] - radius) <= IFC_GEOMETRY_TOLERANCE_M
        ]
        points.sort(key=lambda item: math.atan2(item[1], item[0]))
        return points

    outer_start = ring(t_min, outer_radius)
    outer_end = ring(t_max, outer_radius)
    inner_start = ring(t_min, inner_radius) if inner_radius > 0 else []
    inner_end = ring(t_max, inner_radius) if inner_radius > 0 else []
    if len(outer_start) >= 3 and len(outer_end) >= 3:
        outer_area = (_shoelace(outer_start) + _shoelace(outer_end)) / 2.0
    else:
        outer_area = 0.0
    if len(inner_start) >= 3 and len(inner_end) >= 3:
        inner_area = (_shoelace(inner_start) + _shoelace(inner_end)) / 2.0
    else:
        inner_area = 0.0
    polygon_volume = (outer_area - inner_area) * length
    mesh_volume = sum(
        _dot(vertices[first], _cross(vertices[second], vertices[third]))
        for first, second, third in faces
    ) / 6.0
    analytic_volume = math.pi * (outer_radius**2 - inner_radius**2) * length
    volume_error = (
        (mesh_volume - polygon_volume) / polygon_volume if polygon_volume > 0 else float("inf")
    )
    discretization_error = (
        (polygon_volume - analytic_volume) / analytic_volume if analytic_volume > 0 else 0.0
    )
    return {
        "axis_direction": direction,
        "axis_start": tuple(centroid[axis] + direction[axis] * t_min for axis in range(3)),
        "axis_end": tuple(centroid[axis] + direction[axis] * t_max for axis in range(3)),
        "length_m": length,
        "inner_radius_m": inner_radius,
        "outer_radius_m": outer_radius,
        "inner_diameter_mm": inner_radius * 2000.0,
        "outer_diameter_mm": outer_radius * 2000.0,
        "mesh_volume_m3": mesh_volume,
        "polygon_volume_m3": polygon_volume,
        "analytic_volume_m3": analytic_volume,
        "volume_consistency_relative_error": volume_error,
        "discretization_relative_error": discretization_error,
        "outer_ring_count": len(outer_start),
        "inner_ring_count": len(inner_start),
    }


def _shoelace(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x_value, y_value) in enumerate(points):
        next_x, next_y = points[(index + 1) % len(points)]
        area += x_value * next_y - next_x * y_value
    return abs(area) / 2.0


def _geometry_finding(
    stable_id: str,
    check: str,
    ok: bool,
    expected: Any,
    actual: Any,
    detail: str,
) -> IfcGeometryFinding:
    return IfcGeometryFinding(
        stable_id=stable_id,
        check=check,
        status=ValidationStatus.PASS if ok else ValidationStatus.FAIL,
        expected=expected if not isinstance(expected, tuple) else str(expected),
        actual=actual if not isinstance(actual, tuple) else str(actual),
        detail=detail,
    )


def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length <= 0:
        raise ValueError("无法从几何顶点确定轴线方向")
    return tuple(value / length for value in vector)


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))



def _tag(name: str) -> str:
    return f"{{{IDS_NS}}}{name}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


__all__ = [
    "IFC_GENERATOR_VERSION",
    "IFC_GEOMETRY_NOTE",
    "IFC_GEOMETRY_VALIDATION_VERSION",
    "IfcGeometryFinding",
    "IfcGeometryValidationReport",
    "IfcIdsFinding",
    "IfcIdsPackage",
    "IfcIdsValidationReport",
    "IfcMeasuredSegment",
    "IfcSchemaIssue",
    "IfcSchemaValidationReport",
    "ValidationStatus",
    "build_ifc_ids_package",
    "commit_ifc_ids_package",
    "validate_ifc_against_ids",
    "validate_ifc_geometry",
    "validate_ifc_schema",
]
