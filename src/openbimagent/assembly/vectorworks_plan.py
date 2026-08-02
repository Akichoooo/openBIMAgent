"""CompiledUtilityIR -> typed Vectorworks execution plan v1.

The plan is the deterministic host boundary for G1. It contains no natural-language
commands and no free-form script. A host adapter may translate these typed operations
to its own API only after validating the plan and its capability declaration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.utility.contracts import CompiledUtilityIR

VECTORWORKS_PLAN_VERSION = "1.0"
VECTORWORKS_PROTOCOL_VERSION = "1.0"
VECTORWORKS_HOST_API_VERSION = "2024"
VECTORWORKS_LAYER = "M1-Municipal-Utility"
VECTORWORKS_UNIT = "m"


class VectorworksPlanError(ValueError):
    """Typed plan, capability, or execution validation failed closed."""


class VectorworksObjectType(StrEnum):
    UTILITY_SYSTEM = "utility_system"
    MANHOLE = "manhole"
    INLET = "inlet"
    OUTLET = "outlet"
    JUNCTION = "junction"
    VALVE = "valve"
    EQUIPMENT = "equipment"
    TERMINAL = "terminal"
    DISTRIBUTION_PORT = "distribution_port"
    PIPE_SEGMENT = "pipe_segment"


class VectorworksOperationKind(StrEnum):
    CREATE_OBJECT = "create_object"
    SET_RECORD = "set_record"
    CONNECT_TOPOLOGY = "connect_topology"


class ReceiptStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    x_m: float
    y_m: float
    z_m: float


class RecordField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: str | float | int | bool
    unit: str | None = Field(default=None, max_length=32)


class VectorworksOperation(BaseModel):
    """One allowlisted typed operation; operation-specific fields are validated below."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    operation: VectorworksOperationKind
    operation_id: str = Field(min_length=1, max_length=256)
    object_id: str = Field(min_length=1, max_length=256)
    object_type: VectorworksObjectType
    name: str | None = Field(default=None, min_length=1, max_length=256)
    layer_name: str | None = Field(default=None, min_length=1, max_length=256)
    class_name: str | None = Field(default=None, min_length=1, max_length=256)
    units: str = Field(default=VECTORWORKS_UNIT, pattern=r"^m$")
    position: Coordinate | None = None
    centerline: tuple[Coordinate, ...] = ()
    diameter_mm: float | None = Field(default=None, gt=0)
    material: str | None = Field(default=None, min_length=1, max_length=256)
    slope: float | None = None
    ifc_class: str | None = Field(default=None, pattern=r"^Ifc[A-Za-z0-9]+$")
    ifc_predefined_type: str | None = Field(default=None, min_length=1, max_length=128)
    record_name: str | None = Field(default=None, min_length=1, max_length=128)
    record_fields: tuple[RecordField, ...] = ()
    references: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_operation_shape(self) -> "VectorworksOperation":
        if self.operation is VectorworksOperationKind.CREATE_OBJECT:
            required = {
                "name": self.name,
                "layer_name": self.layer_name,
                "class_name": self.class_name,
            }
            missing = [key for key, value in required.items() if value is None]
            if missing:
                raise ValueError(f"create_object 缺少字段: {missing}")
            if self.record_name is not None or self.record_fields or self.references:
                raise ValueError("create_object 不能携带 record/topology 字段")
            if self.object_type is VectorworksObjectType.PIPE_SEGMENT:
                if len(self.centerline) < 2 or self.diameter_mm is None or self.material is None:
                    raise ValueError("pipe_segment 必须携带 centerline/diameter_mm/material")
            elif self.object_type is VectorworksObjectType.UTILITY_SYSTEM:
                if self.position is not None or self.centerline:
                    raise ValueError("utility_system 不应携带几何坐标")
            elif self.position is None:
                raise ValueError(f"{self.object_type.value} 必须携带 position")
        elif self.operation is VectorworksOperationKind.SET_RECORD:
            if self.record_name is None or not self.record_fields:
                raise ValueError("set_record 必须携带 record_name 和 record_fields")
            if any(value is not None for value in (self.name, self.layer_name, self.class_name, self.position)):
                raise ValueError("set_record 不得携带创建字段")
            if self.centerline or self.references:
                raise ValueError("set_record 不得携带 geometry/topology 字段")
        elif self.operation is VectorworksOperationKind.CONNECT_TOPOLOGY:
            if self.object_type is not VectorworksObjectType.PIPE_SEGMENT:
                raise ValueError("connect_topology 只能作用于 pipe_segment")
            if len(self.references) != 2 or any(not item for item in self.references):
                raise ValueError("connect_topology 必须引用两个端口")
            if any(value is not None for value in (self.name, self.layer_name, self.class_name, self.position)):
                raise ValueError("connect_topology 不得携带创建字段")
            if self.record_name is not None or self.record_fields:
                raise ValueError("connect_topology 不得携带 record 字段")
        return self


class VectorworksExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    plan_version: str = Field(default=VECTORWORKS_PLAN_VERSION, pattern=r"^1(?:\.\d+)?$")
    protocol_version: str = Field(default=VECTORWORKS_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    host_api_version: str = Field(default=VECTORWORKS_HOST_API_VERSION, pattern=r"^2024$")
    plan_id: str = Field(min_length=1, max_length=256)
    ir_id: str = Field(min_length=1, max_length=256)
    source_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_ir_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: str = Field(default=VECTORWORKS_UNIT, pattern=r"^m$")
    operations: tuple[VectorworksOperation, ...] = Field(min_length=1)
    canonical_sha256: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")
    idempotency_key: str = Field(default="", pattern=r"^(|vw-plan:[0-9a-f]{64})$")

    @model_validator(mode="after")
    def _validate_plan(self) -> "VectorworksExecutionPlan":
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("execution plan operation_id 必须唯一")
        if any(
            item.operation is VectorworksOperationKind.CREATE_OBJECT
            and item.layer_name != VECTORWORKS_LAYER
            for item in self.operations
        ):
            raise ValueError("create_object layer_name 必须匹配固定 Vectorworks 范围锁")
        if self.canonical_sha256:
            expected = self.compute_canonical_sha256()
            if self.canonical_sha256 != expected:
                raise ValueError("execution plan canonical_sha256 不匹配")
            if self.idempotency_key != f"vw-plan:{expected}":
                raise ValueError("execution plan idempotency_key 不匹配 canonical hash")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("plan_id", None)
        payload.pop("canonical_sha256", None)
        payload.pop("idempotency_key", None)
        payload["operations"] = sorted(payload["operations"], key=lambda item: item["operation_id"])
        return payload

    def canonical_json(self) -> bytes:
        return json.dumps(
            self.canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

    def compute_canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def finalized(self) -> "VectorworksExecutionPlan":
        digest = self.compute_canonical_sha256()
        return self.model_copy(
            update={
                "plan_id": f"vw-plan-{digest[:24]}",
                "canonical_sha256": digest,
                "idempotency_key": f"vw-plan:{digest}",
            }
        )


class VectorworksCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=VECTORWORKS_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    host_api_version: str = Field(default=VECTORWORKS_HOST_API_VERSION, pattern=r"^2024$")
    units: tuple[str, ...] = (VECTORWORKS_UNIT, "mm")
    operations: tuple[VectorworksOperationKind, ...] = tuple(VectorworksOperationKind)
    object_types: tuple[VectorworksObjectType, ...] = tuple(VectorworksObjectType)
    controlled_save: bool = True
    idempotent_receipts: bool = True
    semantic_snapshot: bool = True


class OperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: ReceiptStatus
    object_id: str
    host_handle: str | None = None


class VectorworksExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    plan_id: str
    idempotency_key: str
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ReceiptStatus
    output_path: str
    state_path: str
    applied_operations: tuple[OperationReceipt, ...] = ()
    confirmed_object_ids: tuple[str, ...] = ()
    semantic_snapshot: dict[str, Any] | None = None
    compensations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class VectorworksPlanExecutor(Protocol):
    def describe_capabilities(self) -> VectorworksCapabilities: ...

    def execute_plan(
        self,
        plan: VectorworksExecutionPlan,
        *,
        capabilities: VectorworksCapabilities | None = None,
        approved: bool = False,
    ) -> VectorworksExecutionReceipt: ...


class VectorworksBuilder:
    """Deterministically compile a validated CompiledUtilityIR into typed operations."""

    def build(
        self,
        ir: CompiledUtilityIR | dict[str, Any],
        *,
        asset_ids: list[str] | tuple[str, ...] | None = None,
    ) -> VectorworksExecutionPlan:
        compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
        selected = set(asset_ids or ())
        operations: list[VectorworksOperation] = []
        systems = sorted(compiled.systems, key=lambda item: item.system_id)
        nodes = sorted(compiled.nodes, key=lambda item: item.node_id)
        segments = sorted(compiled.segments, key=lambda item: item.segment_id)
        for system_index, system in enumerate(systems):
            if selected and system.system_id not in selected:
                continue
            operations.extend(_create_and_record_system(system, f"/systems/{system_index}"))
        for node_index, node in enumerate(nodes):
            if selected and node.node_id not in selected:
                continue
            operations.extend(_create_and_record_node(node, f"/nodes/{node_index}"))
            for port_index, port in enumerate(sorted(node.ports, key=lambda item: item.port_id)):
                if selected and port.port_id not in selected and node.node_id not in selected:
                    continue
                operations.extend(
                    _create_and_record_port(
                        port,
                        node.system_id,
                        f"/nodes/{node_index}/ports/{port_index}",
                    )
                )
        for segment_index, segment in enumerate(segments):
            if selected and segment.segment_id not in selected:
                continue
            operations.extend(_create_and_record_segment(segment, f"/segments/{segment_index}"))
            operations.append(
                VectorworksOperation(
                    operation=VectorworksOperationKind.CONNECT_TOPOLOGY,
                    operation_id=f"connect:{segment.segment_id}",
                    object_id=segment.segment_id,
                    object_type=VectorworksObjectType.PIPE_SEGMENT,
                    references=(segment.start_port_id, segment.end_port_id),
                )
            )
        if not operations:
            raise VectorworksPlanError("CompiledUtilityIR 过滤后没有可编译对象")
        plan = VectorworksExecutionPlan(
            plan_id="pending",
            ir_id=compiled.ir_id,
            source_ir_sha256=compiled.source_ir_sha256,
            compiled_ir_sha256=compiled.canonical_sha256(),
            operations=tuple(operations),
        )
        return plan.finalized()

    def __call__(
        self,
        ir: CompiledUtilityIR | dict[str, Any],
        *,
        asset_ids: list[str] | tuple[str, ...] | None = None,
    ) -> VectorworksExecutionPlan:
        return self.build(ir, asset_ids=asset_ids)


def validate_plan_capabilities(
    plan: VectorworksExecutionPlan, capabilities: VectorworksCapabilities
) -> None:
    if plan.protocol_version != capabilities.protocol_version:
        raise VectorworksPlanError("Vectorworks execution plan protocol version 不匹配")
    if plan.host_api_version != capabilities.host_api_version:
        raise VectorworksPlanError("Vectorworks host API version 不匹配")
    if plan.units not in capabilities.units:
        raise VectorworksPlanError(f"Vectorworks 不支持计划单位: {plan.units!r}")
    if (
        not capabilities.controlled_save
        or not capabilities.idempotent_receipts
        or not capabilities.semantic_snapshot
    ):
        raise VectorworksPlanError(
            "Vectorworks capability 缺少 controlled_save/idempotent_receipts/semantic_snapshot"
        )
    allowed_ops = set(capabilities.operations)
    allowed_objects = set(capabilities.object_types)
    known_objects = {op.object_id for op in plan.operations if op.operation is VectorworksOperationKind.CREATE_OBJECT}
    for op in plan.operations:
        if op.operation not in allowed_ops:
            raise VectorworksPlanError(f"Vectorworks 能力不允许操作: {op.operation.value}")
        if op.object_type not in allowed_objects:
            raise VectorworksPlanError(f"Vectorworks 能力不允许对象类型: {op.object_type.value}")
        if op.operation is VectorworksOperationKind.SET_RECORD and op.object_id not in known_objects:
            raise VectorworksPlanError(f"set_record 引用未知对象: {op.object_id}")
        if op.operation is VectorworksOperationKind.CONNECT_TOPOLOGY:
            missing = [ref for ref in op.references if ref not in known_objects]
            if missing:
                raise VectorworksPlanError(f"connect_topology 引用未知端口: {missing}")


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not clean:
        raise VectorworksPlanError(f"对象 ID 无法形成稳定名称: {value!r}")
    return f"VW_M1_{clean}"


def _class_name(system_type: str) -> str:
    return f"M1-Utility-{system_type.upper()}"


def _record(
    operation_id: str,
    object_id: str,
    object_type: VectorworksObjectType,
    object_kind: str,
    ifc_class: str,
    ifc_type: str | None,
    system_id: str,
    source_ir_path: str,
    domain_properties: dict[str, str | float | int | bool | None] | None = None,
    geometry_properties: dict[str, tuple[float, str | None]] | None = None,
    material: str | None = None,
) -> VectorworksOperation:
    fields = [
        RecordField(field_name="StableObjectID", value=object_id),
        RecordField(field_name="IRObjectID", value=object_id),
        RecordField(field_name="ObjectType", value=object_type.value),
        RecordField(field_name="ObjectKind", value=object_kind),
        RecordField(field_name="SystemID", value=system_id),
        RecordField(field_name="IFCClass", value=ifc_class),
        RecordField(field_name="SourceIRPath", value=source_ir_path),
    ]
    if ifc_type is not None:
        fields.append(RecordField(field_name="IFCPredefinedType", value=ifc_type))
    if material is not None:
        fields.append(RecordField(field_name="Material", value=material))
    for name, value in sorted((domain_properties or {}).items()):
        if value is not None:
            fields.append(RecordField(field_name=f"Domain_{name}", value=value))
    for name, (value, unit) in sorted((geometry_properties or {}).items()):
        fields.append(RecordField(field_name=name, value=value, unit=unit))
    return VectorworksOperation(
        operation=VectorworksOperationKind.SET_RECORD,
        operation_id=operation_id,
        object_id=object_id,
        object_type=object_type,
        record_name="OpenBIMAgent_MunicipalUtility",
        record_fields=tuple(fields),
    )


def _create_and_record_system(system: Any, source_ir_path: str) -> list[VectorworksOperation]:
    obj_type = VectorworksObjectType.UTILITY_SYSTEM
    return [
        VectorworksOperation(
            operation=VectorworksOperationKind.CREATE_OBJECT,
            operation_id=f"create:{system.system_id}",
            object_id=system.system_id,
            object_type=obj_type,
            name=_safe_name(system.system_id),
            layer_name=VECTORWORKS_LAYER,
            class_name=_class_name(system.system_type.value),
            ifc_class=system.ifc_class,
            ifc_predefined_type=system.ifc_predefined_type,
        ),
        _record(
            f"record:{system.system_id}",
            system.system_id,
            obj_type,
            "system",
            system.ifc_class,
            system.ifc_predefined_type,
            system.system_id,
            source_ir_path,
            {"flow_regime": system.flow_regime.value, "system_type": system.system_type.value},
        ),
    ]


def _create_and_record_node(node: Any, source_ir_path: str) -> list[VectorworksOperation]:
    obj_type = VectorworksObjectType(node.node_type.value)
    return [
        VectorworksOperation(
            operation=VectorworksOperationKind.CREATE_OBJECT,
            operation_id=f"create:{node.node_id}",
            object_id=node.node_id,
            object_type=obj_type,
            name=_safe_name(node.node_id),
            layer_name=VECTORWORKS_LAYER,
            class_name=_class_name(node.system_id),
            position=Coordinate(**node.position.model_dump()),
            ifc_class=node.ifc_class,
            ifc_predefined_type=node.ifc_predefined_type,
        ),
        _record(
            f"record:{node.node_id}",
            node.node_id,
            obj_type,
            "node",
            node.ifc_class,
            node.ifc_predefined_type,
            node.system_id,
            source_ir_path,
            {"ground_elevation_m": node.ground_elevation_m, "node_type": node.node_type.value},
        ),
    ]


def _create_and_record_port(port: Any, system_id: str, source_ir_path: str) -> list[VectorworksOperation]:
    obj_type = VectorworksObjectType.DISTRIBUTION_PORT
    return [
        VectorworksOperation(
            operation=VectorworksOperationKind.CREATE_OBJECT,
            operation_id=f"create:{port.port_id}",
            object_id=port.port_id,
            object_type=obj_type,
            name=_safe_name(port.port_id),
            layer_name=VECTORWORKS_LAYER,
            class_name=_class_name(system_id),
            position=Coordinate(**port.position.model_dump()),
            ifc_class=port.ifc_class,
        ),
        _record(
            f"record:{port.port_id}",
            port.port_id,
            obj_type,
            "port",
            port.ifc_class,
            None,
            system_id,
            source_ir_path,
            {"direction": port.direction.value},
        ),
    ]


def _create_and_record_segment(segment: Any, source_ir_path: str) -> list[VectorworksOperation]:
    obj_type = VectorworksObjectType.PIPE_SEGMENT
    return [
        VectorworksOperation(
            operation=VectorworksOperationKind.CREATE_OBJECT,
            operation_id=f"create:{segment.segment_id}",
            object_id=segment.segment_id,
            object_type=obj_type,
            name=_safe_name(segment.segment_id),
            layer_name=VECTORWORKS_LAYER,
            class_name=_class_name(segment.system_id),
            units=VECTORWORKS_UNIT,
            centerline=tuple(Coordinate(**point.model_dump()) for point in segment.centerline),
            diameter_mm=segment.diameter_mm,
            material=segment.material,
            slope=segment.slope,
            ifc_class=segment.ifc_class,
            ifc_predefined_type=segment.ifc_predefined_type,
        ),
        _record(
            f"record:{segment.segment_id}",
            segment.segment_id,
            obj_type,
            "segment",
            segment.ifc_class,
            segment.ifc_predefined_type,
            segment.system_id,
            source_ir_path,
            {"min_cover_depth_m": segment.min_cover_depth_m},
            {
                "DiameterMM": (segment.diameter_mm, "mm"),
                "EndInvertM": (segment.end_invert_m, "m"),
                "HorizontalLengthM": (segment.horizontal_length_m, "m"),
                "Slope": (segment.slope, None),
                "StartInvertM": (segment.start_invert_m, "m"),
            },
            material=segment.material,
        ),
    ]


class FakeVectorworksExecutor:
    """Offline executor with optional durable host facts for restart recovery tests."""

    def __init__(
        self,
        *,
        fail_after_operations: int | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.capabilities = VectorworksCapabilities()
        self.fail_after_operations = fail_after_operations
        self.state_path = Path(state_path).resolve() if state_path is not None else None
        self.objects: dict[str, VectorworksOperation] = {}
        self.records: dict[str, tuple[RecordField, ...]] = {}
        self.connections: dict[str, tuple[str, ...]] = {}
        self._applied: dict[str, set[str]] = {}
        self._plan_hashes: dict[str, str] = {}
        self._receipts: dict[str, VectorworksExecutionReceipt] = {}
        self.execute_calls = 0
        self.apply_calls = 0
        if self.state_path is not None and self.state_path.is_file():
            self._load_state()

    def describe_capabilities(self) -> VectorworksCapabilities:
        return self.capabilities

    def execute_plan(
        self,
        plan: VectorworksExecutionPlan,
        *,
        capabilities: VectorworksCapabilities | None = None,
        approved: bool = False,
    ) -> VectorworksExecutionReceipt:
        del approved  # 离线 fake 无外部副作用；真实 adapter 仍强制传递审批状态。
        self.execute_calls += 1
        plan = VectorworksExecutionPlan.model_validate(plan.model_dump(mode="json"))
        validate_plan_capabilities(plan, capabilities or self.capabilities)
        known_hash = self._plan_hashes.get(plan.idempotency_key)
        if known_hash is not None and known_hash != plan.canonical_sha256:
            raise VectorworksPlanError("同一幂等键对应不同 execution plan，拒绝执行")
        self._plan_hashes[plan.idempotency_key] = plan.canonical_sha256
        previous = self._receipts.get(plan.idempotency_key)
        if previous is not None:
            return previous
        applied = self._applied.setdefault(plan.idempotency_key, set())
        operation_receipts: list[OperationReceipt] = []
        errors: list[str] = []
        for op in plan.operations:
            if op.operation_id in applied:
                operation_receipts.append(OperationReceipt(operation_id=op.operation_id, status=ReceiptStatus.COMPLETED, object_id=op.object_id))
                continue
            if self.fail_after_operations is not None and len(applied) >= self.fail_after_operations:
                errors.append(f"注入中断: operation={op.operation_id}")
                break
            self._apply(op)
            applied.add(op.operation_id)
            self._persist_state()
            operation_receipts.append(OperationReceipt(operation_id=op.operation_id, status=ReceiptStatus.COMPLETED, object_id=op.object_id))
        complete = len(applied) == len(plan.operations)
        status = ReceiptStatus.COMPLETED if complete else ReceiptStatus.PARTIAL
        receipt = VectorworksExecutionReceipt(
            receipt_id=f"vw-receipt-{plan.canonical_sha256[:24]}",
            plan_id=plan.plan_id,
            idempotency_key=plan.idempotency_key,
            canonical_sha256=plan.canonical_sha256,
            status=status,
            output_path="vectorworks://fake",
            state_path=str(self.state_path or "vectorworks://fake-state"),
            applied_operations=tuple(operation_receipts),
            confirmed_object_ids=tuple(sorted(self.objects)),
            compensations=tuple(f"restore:{op.object_id}" for op in plan.operations if op.object_id in self.objects),
            errors=tuple(errors),
        )
        if complete:
            self._receipts[plan.idempotency_key] = receipt
        self._persist_state()
        return receipt

    def _load_state(self) -> None:
        if self.state_path is None:
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if payload.get("state_version") != "1.0":
                raise ValueError("unsupported state_version")
            self.objects = {
                key: VectorworksOperation.model_validate(value)
                for key, value in payload.get("objects", {}).items()
            }
            self.records = {
                key: tuple(RecordField.model_validate(value) for value in values)
                for key, values in payload.get("records", {}).items()
            }
            self.connections = {
                key: tuple(str(value) for value in values)
                for key, values in payload.get("connections", {}).items()
            }
            self._applied = {
                key: {str(value) for value in values}
                for key, values in payload.get("applied", {}).items()
            }
            self._plan_hashes = {
                str(key): str(value)
                for key, value in payload.get("plan_hashes", {}).items()
            }
            self._receipts = {
                key: VectorworksExecutionReceipt.model_validate(value)
                for key, value in payload.get("receipts", {}).items()
            }
        except Exception as exc:
            raise VectorworksPlanError(
                f"Vectorworks fake host 状态损坏: {self.state_path}: {exc}"
            ) from exc

    def _persist_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state_version": "1.0",
            "objects": {
                key: value.model_dump(mode="json")
                for key, value in sorted(self.objects.items())
            },
            "records": {
                key: [value.model_dump(mode="json") for value in values]
                for key, values in sorted(self.records.items())
            },
            "connections": {
                key: list(values)
                for key, values in sorted(self.connections.items())
            },
            "applied": {
                key: sorted(values)
                for key, values in sorted(self._applied.items())
            },
            "plan_hashes": dict(sorted(self._plan_hashes.items())),
            "receipts": {
                key: value.model_dump(mode="json")
                for key, value in sorted(self._receipts.items())
            },
        }
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as file:
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _apply(self, op: VectorworksOperation) -> None:
        self.apply_calls += 1
        if op.operation is VectorworksOperationKind.CREATE_OBJECT:
            existing = self.objects.get(op.object_id)
            if existing is not None and existing != op:
                raise VectorworksPlanError(f"对象 {op.object_id!r} 已存在但语义不同")
            self.objects[op.object_id] = op
        elif op.operation is VectorworksOperationKind.SET_RECORD:
            if op.object_id not in self.objects:
                raise VectorworksPlanError(f"set_record 对象不存在: {op.object_id}")
            self.records[op.object_id] = op.record_fields
        elif op.operation is VectorworksOperationKind.CONNECT_TOPOLOGY:
            if op.object_id not in self.objects or any(ref not in self.objects for ref in op.references):
                raise VectorworksPlanError(f"connect_topology 引用对象不存在: {op.object_id}")
            self.connections[op.object_id] = op.references


__all__ = [
    "FakeVectorworksExecutor",
    "OperationReceipt",
    "ReceiptStatus",
    "RecordField",
    "VectorworksBuilder",
    "VectorworksCapabilities",
    "VectorworksExecutionPlan",
    "VectorworksExecutionReceipt",
    "VectorworksObjectType",
    "VectorworksOperation",
    "VectorworksOperationKind",
    "VectorworksPlanError",
    "VECTORWORKS_HOST_API_VERSION",
    "VECTORWORKS_LAYER",
    "VECTORWORKS_PLAN_VERSION",
    "VECTORWORKS_PROTOCOL_VERSION",
    "validate_plan_capabilities",
]
