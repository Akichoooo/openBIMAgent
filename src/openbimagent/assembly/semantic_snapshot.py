"""G3 host-independent semantic snapshots and deterministic cross-host comparison."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.assembly.rule_projection import RuleProjectionIdentity
from openbimagent.assembly.vectorworks_plan import (
    FakeVectorworksExecutor,
    ReceiptStatus,
    VectorworksBuilder,
    VectorworksExecutionPlan,
    VectorworksOperation,
)
from openbimagent.utility.contracts import CompiledUtilityIR

SEMANTIC_SNAPSHOT_VERSION = "1.0"
SEMANTIC_COMPARISON_VERSION = "1.0"


class SnapshotHost(StrEnum):
    BLENDER = "blender"
    VECTORWORKS = "vectorworks"


class SemanticObjectKind(StrEnum):
    SYSTEM = "system"
    NODE = "node"
    PORT = "port"
    SEGMENT = "segment"


class ComparisonStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    x_m: float
    y_m: float
    z_m: float


class SemanticObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    stable_id: str = Field(min_length=1, max_length=256)
    object_kind: SemanticObjectKind
    system_id: str = Field(min_length=1, max_length=256)
    position: Coordinate | None = None
    centerline: tuple[Coordinate, ...] = ()
    topology: tuple[str, ...] = ()
    diameter_mm: float | None = Field(default=None, gt=0)
    horizontal_length_m: float | None = Field(default=None, gt=0)
    start_invert_m: float | None = None
    end_invert_m: float | None = None
    slope: float | None = None
    material: str | None = Field(default=None, min_length=1, max_length=256)
    ifc_class: str = Field(pattern=r"^Ifc[A-Za-z0-9]+$")
    ifc_predefined_type: str | None = Field(default=None, min_length=1, max_length=128)
    domain_properties: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    source_ir_path: str = Field(pattern=r"^/(systems|nodes|segments)/[0-9]+(?:/ports/[0-9]+)?$")
    host_handle: str | None = Field(default=None, min_length=1, max_length=256)
    presentation_material: str | None = Field(default=None, min_length=1, max_length=256)

    def comparable_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("host_handle", None)
        payload.pop("presentation_material", None)
        return payload


class SemanticSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    snapshot_version: str = Field(default=SEMANTIC_SNAPSHOT_VERSION, pattern=r"^1(?:\.\d+)?$")
    host: SnapshotHost
    host_adapter: str = Field(min_length=1, max_length=128)
    source_ir_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_identity: RuleProjectionIdentity | None = None
    units: str = Field(default="m", pattern=r"^m$")
    objects: tuple[SemanticObject, ...] = Field(min_length=1)
    allowed_host_differences: tuple[str, ...] = ("host_handle", "presentation_material")
    canonical_sha256: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "SemanticSnapshot":
        ids = [item.stable_id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("semantic snapshot stable_id 必须唯一")
        if self.allowed_host_differences != ("host_handle", "presentation_material"):
            raise ValueError("G3 只允许 host_handle 和 presentation_material 作为宿主差异")
        expected_rule_properties = (
            self.rule_identity.domain_properties() if self.rule_identity is not None else {}
        )
        for item in self.objects:
            projected = {
                key: value
                for key, value in item.domain_properties.items()
                if key in {
                    "rule_evidence_bundle_sha256",
                    "rule_evaluation_sha256",
                    "rule_decision_status",
                    "production_verification",
                    "exception_approval_id",
                    "exception_approval_sha256",
                }
            }
            if projected != expected_rule_properties:
                raise ValueError(
                    f"semantic snapshot rule identity 与对象 {item.stable_id!r} 投影不一致"
                )
        if self.canonical_sha256 and self.canonical_sha256 != self.compute_canonical_sha256():
            raise ValueError("semantic snapshot canonical_sha256 不匹配")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("canonical_sha256", None)
        payload["objects"] = sorted(payload["objects"], key=lambda item: item["stable_id"])
        return payload

    def compute_canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def finalized(self) -> "SemanticSnapshot":
        return self.model_copy(update={"canonical_sha256": self.compute_canonical_sha256()})


class SemanticDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_id: str = Field(min_length=1, max_length=256)
    field_path: str = Field(min_length=1, max_length=512)
    left_value: Any = None
    right_value: Any = None
    left_source_ir_path: str | None = None
    right_source_ir_path: str | None = None


class SemanticComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_version: str = Field(default=SEMANTIC_COMPARISON_VERSION, pattern=r"^1(?:\.\d+)?$")
    left_host: SnapshotHost
    right_host: SnapshotHost
    source_ir_id: str
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ComparisonStatus
    compared_object_count: int = Field(ge=0)
    differences: tuple[SemanticDifference, ...] = ()

    @model_validator(mode="after")
    def _status_matches_differences(self) -> "SemanticComparisonReport":
        expected = ComparisonStatus.FAIL if self.differences else ComparisonStatus.PASS
        if self.status is not expected:
            raise ValueError("semantic comparison status 与 differences 不一致")
        return self

    @property
    def ok(self) -> bool:
        return self.status is ComparisonStatus.PASS


class FakeBlenderSemanticExecutor:
    """Offline Blender boundary: materialize validated IR into host-independent semantics."""

    def execute(
        self,
        ir: CompiledUtilityIR | dict[str, Any],
        *,
        rule_identity: RuleProjectionIdentity | None = None,
    ) -> SemanticSnapshot:
        compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
        objects = _objects_from_ir(
            compiled,
            host=SnapshotHost.BLENDER,
            rule_identity=rule_identity,
        )
        return SemanticSnapshot(
            host=SnapshotHost.BLENDER,
            host_adapter="fake-blender-semantic-v1",
            source_ir_id=compiled.ir_id,
            source_ir_sha256=compiled.canonical_sha256(),
            rule_identity=rule_identity,
            objects=objects,
        ).finalized()


class FakeVectorworksSemanticExecutor:
    """Run the typed Vectorworks plan, then project only actually materialized executor state."""

    def __init__(self) -> None:
        self.executor = FakeVectorworksExecutor()
        self.plan: VectorworksExecutionPlan | None = None

    def execute(
        self,
        ir: CompiledUtilityIR | dict[str, Any],
        *,
        rule_identity: RuleProjectionIdentity | None = None,
    ) -> SemanticSnapshot:
        compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
        plan = VectorworksBuilder().build(compiled, rule_identity=rule_identity)
        receipt = self.executor.execute_plan(plan)
        if receipt.status is not ReceiptStatus.COMPLETED:
            raise ValueError(f"Vectorworks 模拟执行未完成: {receipt.status.value}")
        self.plan = plan
        objects = tuple(
            _object_from_vectorworks_state(object_id, operation, self.executor.records.get(object_id, ()), self.executor.connections)
            for object_id, operation in sorted(self.executor.objects.items())
        )
        return SemanticSnapshot(
            host=SnapshotHost.VECTORWORKS,
            host_adapter="fake-vectorworks-plan-v1",
            source_ir_id=compiled.ir_id,
            source_ir_sha256=compiled.canonical_sha256(),
            rule_identity=rule_identity,
            objects=objects,
        ).finalized()


def compare_semantic_snapshots(
    left: SemanticSnapshot | dict[str, Any],
    right: SemanticSnapshot | dict[str, Any],
) -> SemanticComparisonReport:
    left_snapshot = left if isinstance(left, SemanticSnapshot) else SemanticSnapshot.model_validate(left)
    right_snapshot = right if isinstance(right, SemanticSnapshot) else SemanticSnapshot.model_validate(right)
    differences: list[SemanticDifference] = []
    if left_snapshot.source_ir_id != right_snapshot.source_ir_id:
        differences.append(SemanticDifference(object_id="@snapshot", field_path="source_ir_id", left_value=left_snapshot.source_ir_id, right_value=right_snapshot.source_ir_id))
    if left_snapshot.source_ir_sha256 != right_snapshot.source_ir_sha256:
        differences.append(SemanticDifference(object_id="@snapshot", field_path="source_ir_sha256", left_value=left_snapshot.source_ir_sha256, right_value=right_snapshot.source_ir_sha256))
    _diff_values(
        "@snapshot",
        "rule_identity",
        left_snapshot.rule_identity.model_dump(mode="json") if left_snapshot.rule_identity else None,
        right_snapshot.rule_identity.model_dump(mode="json") if right_snapshot.rule_identity else None,
        "@snapshot",
        "@snapshot",
        differences,
    )

    left_objects = {item.stable_id: item for item in left_snapshot.objects}
    right_objects = {item.stable_id: item for item in right_snapshot.objects}
    for object_id in sorted(set(left_objects) | set(right_objects)):
        left_object = left_objects.get(object_id)
        right_object = right_objects.get(object_id)
        if left_object is None or right_object is None:
            differences.append(SemanticDifference(
                object_id=object_id,
                field_path="@object",
                left_value=left_object.comparable_dict() if left_object else None,
                right_value=right_object.comparable_dict() if right_object else None,
                left_source_ir_path=left_object.source_ir_path if left_object else None,
                right_source_ir_path=right_object.source_ir_path if right_object else None,
            ))
            continue
        _diff_values(
            object_id,
            "",
            left_object.comparable_dict(),
            right_object.comparable_dict(),
            left_object.source_ir_path,
            right_object.source_ir_path,
            differences,
        )
    return SemanticComparisonReport(
        left_host=left_snapshot.host,
        right_host=right_snapshot.host,
        source_ir_id=left_snapshot.source_ir_id,
        source_ir_sha256=left_snapshot.source_ir_sha256,
        status=ComparisonStatus.FAIL if differences else ComparisonStatus.PASS,
        compared_object_count=len(set(left_objects) | set(right_objects)),
        differences=tuple(differences),
    )


def _objects_from_ir(
    compiled: CompiledUtilityIR,
    *,
    host: SnapshotHost,
    rule_identity: RuleProjectionIdentity | None = None,
) -> tuple[SemanticObject, ...]:
    rule_properties = rule_identity.domain_properties() if rule_identity is not None else {}
    objects: list[SemanticObject] = []
    for index, system in enumerate(sorted(compiled.systems, key=lambda item: item.system_id)):
        objects.append(SemanticObject(
            stable_id=system.system_id,
            object_kind=SemanticObjectKind.SYSTEM,
            system_id=system.system_id,
            ifc_class=system.ifc_class,
            ifc_predefined_type=system.ifc_predefined_type,
            domain_properties={
                "system_type": system.system_type.value,
                "flow_regime": system.flow_regime.value,
                **rule_properties,
            },
            source_ir_path=f"/systems/{index}",
            host_handle=f"{host.value}:{system.system_id}",
        ))
    for node_index, node in enumerate(sorted(compiled.nodes, key=lambda item: item.node_id)):
        objects.append(SemanticObject(
            stable_id=node.node_id,
            object_kind=SemanticObjectKind.NODE,
            system_id=node.system_id,
            position=Coordinate(**node.position.model_dump()),
            ifc_class=node.ifc_class,
            ifc_predefined_type=node.ifc_predefined_type,
            domain_properties={
                "node_type": node.node_type.value,
                "ground_elevation_m": node.ground_elevation_m,
                **rule_properties,
            },
            source_ir_path=f"/nodes/{node_index}",
            host_handle=f"{host.value}:{node.node_id}",
        ))
        for port_index, port in enumerate(sorted(node.ports, key=lambda item: item.port_id)):
            objects.append(SemanticObject(
                stable_id=port.port_id,
                object_kind=SemanticObjectKind.PORT,
                system_id=node.system_id,
                position=Coordinate(**port.position.model_dump()),
                ifc_class=port.ifc_class,
                domain_properties={"direction": port.direction.value, **rule_properties},
                source_ir_path=f"/nodes/{node_index}/ports/{port_index}",
                host_handle=f"{host.value}:{port.port_id}",
            ))
    for index, segment in enumerate(sorted(compiled.segments, key=lambda item: item.segment_id)):
        objects.append(SemanticObject(
            stable_id=segment.segment_id,
            object_kind=SemanticObjectKind.SEGMENT,
            system_id=segment.system_id,
            centerline=tuple(Coordinate(**point.model_dump()) for point in segment.centerline),
            topology=(segment.start_port_id, segment.end_port_id),
            diameter_mm=segment.diameter_mm,
            horizontal_length_m=segment.horizontal_length_m,
            start_invert_m=segment.start_invert_m,
            end_invert_m=segment.end_invert_m,
            slope=segment.slope,
            material=segment.material,
            ifc_class=segment.ifc_class,
            ifc_predefined_type=segment.ifc_predefined_type,
            domain_properties={"min_cover_depth_m": segment.min_cover_depth_m, **rule_properties},
            source_ir_path=f"/segments/{index}",
            host_handle=f"{host.value}:{segment.segment_id}",
            presentation_material=f"{host.value}:{segment.material}",
        ))
    return tuple(sorted(objects, key=lambda item: item.stable_id))


def _object_from_vectorworks_state(
    object_id: str,
    operation: VectorworksOperation,
    record_fields: tuple[Any, ...],
    connections: dict[str, tuple[str, ...]],
) -> SemanticObject:
    records = {item.field_name: item.value for item in record_fields}
    required = {"SystemID", "IFCClass", "ObjectKind", "SourceIRPath"}
    missing = sorted(required - set(records))
    if missing:
        raise ValueError(f"Vectorworks 对象 {object_id!r} 缺少语义记录: {missing}")
    kind = SemanticObjectKind(str(records["ObjectKind"]))
    domain_properties = {
        key.removeprefix("Domain_"): value
        for key, value in records.items()
        if key.startswith("Domain_")
    }
    return SemanticObject(
        stable_id=object_id,
        object_kind=kind,
        system_id=str(records["SystemID"]),
        position=Coordinate(**operation.position.model_dump()) if operation.position is not None else None,
        centerline=tuple(Coordinate(**point.model_dump()) for point in operation.centerline),
        topology=connections.get(object_id, ()),
        diameter_mm=operation.diameter_mm,
        horizontal_length_m=_optional_float(records.get("HorizontalLengthM")),
        start_invert_m=_optional_float(records.get("StartInvertM")),
        end_invert_m=_optional_float(records.get("EndInvertM")),
        slope=operation.slope,
        material=operation.material,
        ifc_class=str(records["IFCClass"]),
        ifc_predefined_type=str(records["IFCPredefinedType"]) if "IFCPredefinedType" in records else None,
        domain_properties=domain_properties,
        source_ir_path=str(records["SourceIRPath"]),
        host_handle=f"vectorworks:{operation.name or object_id}",
        presentation_material=f"vectorworks:{operation.material}" if operation.material else None,
    )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _diff_values(
    object_id: str,
    path: str,
    left: Any,
    right: Any,
    left_source: str,
    right_source: str,
    differences: list[SemanticDifference],
) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            _diff_values(object_id, f"{path}.{key}" if path else key, left.get(key), right.get(key), left_source, right_source, differences)
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append(SemanticDifference(object_id=object_id, field_path=path, left_value=left, right_value=right, left_source_ir_path=left_source, right_source_ir_path=right_source))
            return
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            _diff_values(object_id, f"{path}[{index}]", left_item, right_item, left_source, right_source, differences)
        return
    if left != right:
        differences.append(SemanticDifference(object_id=object_id, field_path=path, left_value=left, right_value=right, left_source_ir_path=left_source, right_source_ir_path=right_source))


__all__ = [
    "ComparisonStatus",
    "FakeBlenderSemanticExecutor",
    "FakeVectorworksSemanticExecutor",
    "RuleProjectionIdentity",
    "SemanticComparisonReport",
    "SemanticDifference",
    "SemanticObject",
    "SemanticObjectKind",
    "SemanticSnapshot",
    "SnapshotHost",
    "compare_semantic_snapshots",
]
