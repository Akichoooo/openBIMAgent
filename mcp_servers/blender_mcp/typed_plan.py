"""Self-contained typed Blender host adapter used inside Blender's embedded Python.

The module deliberately has no dependency on openBIMAgent or Pydantic. It validates
wire dictionaries again inside the host, dispatches fixed bpy operations directly,
and persists operation receipts atomically for idempotent restart recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

PLAN_VERSION = "1.0"
PROTOCOL_VERSION = "1.0"
HOST_API_VERSION = "5.2"
STATE_VERSION = "1.0"
SEMANTIC_SNAPSHOT_VERSION = "1.0"
SEMANTIC_DECIMAL_PLACES = 6
OPERATIONS = {"create_object", "set_properties", "connect_topology"}
OBJECT_TYPES = {
    "utility_system",
    "manhole",
    "inlet",
    "outlet",
    "junction",
    "valve",
    "equipment",
    "terminal",
    "distribution_port",
    "pipe_segment",
}
PRIMITIVES = {"empty", "cylinder", "uv_sphere", "polyline_curve"}
REQUIRED_PLAN_FIELDS = {
    "plan_version",
    "protocol_version",
    "host_api_version",
    "plan_id",
    "ir_id",
    "source_ir_sha256",
    "compiled_ir_sha256",
    "units",
    "collection_name",
    "operations",
    "canonical_sha256",
    "idempotency_key",
}
REQUIRED_OPERATION_FIELDS = {
    "operation",
    "operation_id",
    "object_id",
    "object_type",
    "object_name",
    "collection_name",
    "primitive",
    "units",
    "position",
    "centerline",
    "diameter_mm",
    "material",
    "properties",
    "references",
}


class TypedPlanError(ValueError):
    pass


def canonical_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(plan, ensure_ascii=False, allow_nan=False))
    payload.pop("plan_id", None)
    payload.pop("canonical_sha256", None)
    payload.pop("idempotency_key", None)
    payload["operations"] = sorted(payload.get("operations", []), key=lambda item: item["operation_id"])
    return payload


def canonical_plan_sha256(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_plan_payload(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise TypedPlanError("plan must be an object")
    fields = set(plan)
    if fields != REQUIRED_PLAN_FIELDS:
        raise TypedPlanError(
            f"plan fields mismatch: missing={sorted(REQUIRED_PLAN_FIELDS - fields)} "
            f"unknown={sorted(fields - REQUIRED_PLAN_FIELDS)}"
        )
    if plan["plan_version"] != PLAN_VERSION or plan["protocol_version"] != PROTOCOL_VERSION:
        raise TypedPlanError("unsupported plan/protocol version")
    if plan["host_api_version"] != HOST_API_VERSION:
        raise TypedPlanError("unsupported Blender host API version")
    if plan["units"] != "m":
        raise TypedPlanError("unsupported plan units")
    if not isinstance(plan["operations"], list) or not plan["operations"]:
        raise TypedPlanError("operations must be a non-empty array")
    digest = canonical_plan_sha256(plan)
    if plan["canonical_sha256"] != digest:
        raise TypedPlanError("canonical_sha256 mismatch")
    if plan["idempotency_key"] != f"blender-plan:{digest}":
        raise TypedPlanError("idempotency_key mismatch")
    operation_ids: set[str] = set()
    created_ids: set[str] = set()
    for operation in plan["operations"]:
        _validate_operation(operation, plan["collection_name"])
        operation_id = operation["operation_id"]
        if operation_id in operation_ids:
            raise TypedPlanError(f"duplicate operation_id: {operation_id}")
        operation_ids.add(operation_id)
        if operation["operation"] == "create_object":
            if operation["object_id"] in created_ids:
                raise TypedPlanError(f"duplicate object_id: {operation['object_id']}")
            created_ids.add(operation["object_id"])
    for operation in plan["operations"]:
        if operation["operation"] == "set_properties" and operation["object_id"] not in created_ids:
            raise TypedPlanError(f"set_properties references unknown object: {operation['object_id']}")
        if operation["operation"] == "connect_topology":
            missing = [value for value in operation["references"] if value not in created_ids]
            if missing:
                raise TypedPlanError(f"connect_topology references unknown objects: {missing}")
    return plan


def _validate_operation(operation: Any, collection_name: str) -> None:
    if not isinstance(operation, dict) or set(operation) != REQUIRED_OPERATION_FIELDS:
        fields = set(operation) if isinstance(operation, dict) else set()
        raise TypedPlanError(
            f"operation fields mismatch: missing={sorted(REQUIRED_OPERATION_FIELDS - fields)} "
            f"unknown={sorted(fields - REQUIRED_OPERATION_FIELDS)}"
        )
    if operation["operation"] not in OPERATIONS:
        raise TypedPlanError(f"unsupported operation: {operation['operation']}")
    if operation["object_type"] not in OBJECT_TYPES:
        raise TypedPlanError(f"unsupported object_type: {operation['object_type']}")
    if operation["units"] != "m":
        raise TypedPlanError("unsupported operation units")
    kind = operation["operation"]
    if kind == "create_object":
        if operation["collection_name"] != collection_name:
            raise TypedPlanError("create_object escaped collection scope")
        if operation["primitive"] not in PRIMITIVES or not operation["object_name"]:
            raise TypedPlanError("create_object missing allowlisted primitive/object_name")
        if operation["properties"] or operation["references"]:
            raise TypedPlanError("create_object carries forbidden properties/references")
        if operation["object_type"] == "pipe_segment":
            if operation["primitive"] != "polyline_curve":
                raise TypedPlanError("pipe_segment requires polyline_curve")
            if len(operation["centerline"]) < 2 or not operation["diameter_mm"] or not operation["material"]:
                raise TypedPlanError("pipe_segment missing geometry")
        elif operation["object_type"] == "utility_system":
            if operation["primitive"] != "empty" or operation["position"] is not None:
                raise TypedPlanError("utility_system requires positionless empty")
        elif operation["position"] is None:
            raise TypedPlanError("located object missing position")
    elif kind == "set_properties":
        if not operation["properties"]:
            raise TypedPlanError("set_properties requires properties")
        names: set[str] = set()
        for item in operation["properties"]:
            if set(item) != {"property_name", "value"}:
                raise TypedPlanError("custom property fields mismatch")
            name = item["property_name"]
            if not isinstance(name, str) or not name.startswith("openbim_") or name in names:
                raise TypedPlanError(f"invalid or duplicate custom property: {name!r}")
            if not isinstance(item["value"], (str, int, float, bool)):
                raise TypedPlanError(f"unsupported custom property value: {name}")
            names.add(name)
    elif operation["object_type"] != "pipe_segment" or len(operation["references"]) != 2:
        raise TypedPlanError("connect_topology requires pipe_segment and two references")


def resolve_output_path(output_path: str, authorized_root: str) -> Path:
    if not authorized_root:
        raise TypedPlanError("authorized_root is required")
    root = Path(authorized_root).resolve()
    target = Path(output_path).resolve()
    if target.suffix.lower() != ".blend":
        raise TypedPlanError("output_path must end with .blend")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise TypedPlanError(f"output_path escaped authorized root: {target}") from exc
    return target


def state_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".openbimagent.json")


def read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TypedPlanError(f"execution state is unreadable: {path}: {exc}") from exc
    if state.get("state_version") != STATE_VERSION:
        raise TypedPlanError("unsupported execution state version")
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def execute_typed_plan(
    *,
    plan: dict[str, Any],
    output_path: str,
    authorized_root: str,
    approved: bool,
    bpy_module: Any,
    snapshot_fn: Any,
    fork_version: str,
) -> dict[str, Any]:
    if approved is not True:
        raise TypedPlanError("typed execute_plan requires explicit approval")
    plan = validate_plan(plan)
    target = resolve_output_path(output_path, authorized_root)
    sidecar = state_path(target)
    state = read_state(sidecar)
    if target.exists() and state is None:
        raise TypedPlanError(f"refusing to overwrite existing Blender file without matching state: {target}")
    if state is not None:
        if state.get("idempotency_key") != plan["idempotency_key"]:
            raise TypedPlanError("existing state idempotency_key mismatch")
        if state.get("canonical_sha256") != plan["canonical_sha256"]:
            raise TypedPlanError("same output/state has different canonical semantics")
        if state.get("receipt") is not None:
            return state["receipt"]
        if target.is_file():
            # The .blend save can become durable immediately before the sidecar
            # acknowledgement. Always reopen a matching controlled file so a
            # restart safely replays that operation against host state instead
            # of continuing from an unrelated empty scene.
            open_result = bpy_module.ops.wm.open_mainfile(filepath=str(target))
            if isinstance(open_result, set) and "FINISHED" not in open_result:
                raise TypedPlanError(f"Blender recovery open failed: {open_result}")
        elif state.get("applied_operation_ids"):
            raise TypedPlanError("partial state exists but controlled Blender file is missing")
    else:
        snapshot = snapshot_fn(tag="pre_typed_plan")
        state = {
            "state_version": STATE_VERSION,
            "plan_id": plan["plan_id"],
            "idempotency_key": plan["idempotency_key"],
            "canonical_sha256": plan["canonical_sha256"],
            "output_path": str(target),
            "snapshot_path": snapshot,
            "applied_operation_ids": [],
            "operation_receipts": [],
            "receipt": None,
        }
        write_state(sidecar, state)
    applied = set(state["applied_operation_ids"])
    errors: list[str] = []
    for operation in plan["operations"]:
        if operation["operation_id"] in applied:
            continue
        try:
            host_handle = apply_operation(operation, plan["collection_name"], bpy_module)
            _save_active_document(bpy_module, target)
            applied.add(operation["operation_id"])
            state["applied_operation_ids"] = sorted(applied)
            state["operation_receipts"].append(
                {
                    "operation_id": operation["operation_id"],
                    "status": "completed",
                    "object_id": operation["object_id"],
                    "host_handle": host_handle,
                }
            )
            write_state(sidecar, state)
        except Exception as exc:
            errors.append(f"operation={operation['operation_id']}: {exc}")
            break
    complete = len(applied) == len(plan["operations"])
    semantic_snapshot = project_semantic_snapshot(plan, bpy_module, fork_version) if complete else None
    receipt = {
        "receipt_id": f"blender-receipt-{plan['canonical_sha256'][:24]}",
        "plan_id": plan["plan_id"],
        "idempotency_key": plan["idempotency_key"],
        "canonical_sha256": plan["canonical_sha256"],
        "status": "completed" if complete else "partial",
        "output_path": str(target),
        "snapshot_path": state["snapshot_path"],
        "state_path": str(sidecar),
        "applied_operations": state["operation_receipts"],
        "confirmed_object_ids": sorted(
            operation["object_id"]
            for operation in plan["operations"]
            if operation["operation"] == "create_object"
            and operation["operation_id"] in applied
        ),
        "semantic_snapshot": semantic_snapshot,
        "errors": errors,
    }
    if complete:
        state["receipt"] = receipt
    write_state(sidecar, state)
    return receipt


def _save_active_document(bpy_module: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = bpy_module.ops.wm.save_as_mainfile(filepath=str(target))
    if isinstance(result, set) and "FINISHED" not in result:
        raise TypedPlanError(f"Blender save failed: {result}")


def apply_operation(operation: dict[str, Any], collection_name: str, bpy_module: Any) -> str | None:
    kind = operation["operation"]
    if kind == "create_object":
        return _create_object(operation, collection_name, bpy_module)
    obj = _object_by_stable_id(bpy_module, operation["object_id"])
    if obj is None:
        raise TypedPlanError(f"object not found by stable id: {operation['object_id']}")
    if kind == "set_properties":
        for item in operation["properties"]:
            obj[item["property_name"]] = item["value"]
    elif kind == "connect_topology":
        for reference in operation["references"]:
            if _object_by_stable_id(bpy_module, reference) is None:
                raise TypedPlanError(f"topology reference not found: {reference}")
        obj["openbim_topology_json"] = json.dumps(operation["references"], separators=(",", ":"))
    else:
        raise TypedPlanError(f"unsupported operation: {kind}")
    return obj.name


def _ensure_collection(bpy_module: Any, collection_name: str) -> Any:
    collection = bpy_module.data.collections.get(collection_name)
    if collection is None:
        collection = bpy_module.data.collections.new(collection_name)
        bpy_module.context.scene.collection.children.link(collection)
    return collection


def _move_to_collection(obj: Any, collection: Any) -> None:
    if collection not in obj.users_collection:
        collection.objects.link(obj)
    for current in list(obj.users_collection):
        if current != collection:
            current.objects.unlink(obj)


def _create_object(operation: dict[str, Any], collection_name: str, bpy_module: Any) -> str:
    existing = _object_by_stable_id(bpy_module, operation["object_id"])
    if existing is not None:
        if existing.name != operation["object_name"]:
            raise TypedPlanError(f"stable object exists with different name: {operation['object_id']}")
        return existing.name
    if bpy_module.data.objects.get(operation["object_name"]) is not None:
        raise TypedPlanError(f"object name already exists without stable identity: {operation['object_name']}")
    primitive = operation["primitive"]
    position = operation["position"]
    if primitive == "empty":
        obj = bpy_module.data.objects.new(operation["object_name"], None)
        _ensure_collection(bpy_module, collection_name).objects.link(obj)
    elif primitive == "cylinder":
        bpy_module.ops.mesh.primitive_cylinder_add(
            vertices=24,
            radius=0.5,
            depth=1.0,
            location=_coordinate(position),
        )
        obj = bpy_module.context.object
        obj.name = operation["object_name"]
    elif primitive == "uv_sphere":
        bpy_module.ops.mesh.primitive_uv_sphere_add(
            segments=16,
            ring_count=8,
            radius=0.15,
            location=_coordinate(position),
        )
        obj = bpy_module.context.object
        obj.name = operation["object_name"]
    elif primitive == "polyline_curve":
        curve = bpy_module.data.curves.new(operation["object_name"] + "_Curve", type="CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 1
        curve.bevel_depth = float(operation["diameter_mm"]) / 2000.0
        curve.bevel_resolution = 3
        spline = curve.splines.new("POLY")
        spline.points.add(len(operation["centerline"]) - 1)
        for point, coordinate in zip(spline.points, operation["centerline"]):
            point.co = (*_coordinate(coordinate), 1.0)
        obj = bpy_module.data.objects.new(operation["object_name"], curve)
        _ensure_collection(bpy_module, collection_name).objects.link(obj)
        obj["openbim_material"] = operation["material"]
    else:
        raise TypedPlanError(f"unsupported primitive: {primitive}")
    _move_to_collection(obj, _ensure_collection(bpy_module, collection_name))
    obj["openbim_stable_id"] = operation["object_id"]
    obj["openbim_object_type"] = operation["object_type"]
    return obj.name


def _coordinate(value: dict[str, Any]) -> tuple[float, float, float]:
    if not isinstance(value, dict) or set(value) != {"x_m", "y_m", "z_m"}:
        raise TypedPlanError("invalid coordinate")
    return (float(value["x_m"]), float(value["y_m"]), float(value["z_m"]))


def _object_by_stable_id(bpy_module: Any, stable_id: str) -> Any | None:
    matches = [obj for obj in bpy_module.data.objects if obj.get("openbim_stable_id") == stable_id]
    if len(matches) > 1:
        raise TypedPlanError(f"duplicate stable id in Blender scene: {stable_id}")
    return matches[0] if matches else None


def project_semantic_snapshot(
    plan: dict[str, Any],
    bpy_module: Any,
    fork_version: str,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for operation in plan["operations"]:
        if operation["operation"] != "create_object":
            continue
        obj = _object_by_stable_id(bpy_module, operation["object_id"])
        if obj is None:
            raise TypedPlanError(f"semantic projection missing object: {operation['object_id']}")
        required = {
            "openbim_object_kind",
            "openbim_system_id",
            "openbim_ifc_class",
            "openbim_source_ir_path",
        }
        missing = sorted(name for name in required if name not in obj)
        if missing:
            raise TypedPlanError(f"semantic projection missing properties for {obj.name}: {missing}")
        domain = {
            key.removeprefix("openbim_domain_"): obj[key]
            for key in obj.keys()
            if key.startswith("openbim_domain_")
        }
        centerline: list[dict[str, float]] = []
        if operation["primitive"] == "polyline_curve":
            spline = obj.data.splines[0]
            centerline = [
                {
                    "x_m": _semantic_number(point.co.x),
                    "y_m": _semantic_number(point.co.y),
                    "z_m": _semantic_number(point.co.z),
                }
                for point in spline.points
            ]
        position = None
        if operation["position"] is not None:
            position = {
                "x_m": _semantic_number(obj.location.x),
                "y_m": _semantic_number(obj.location.y),
                "z_m": _semantic_number(obj.location.z),
            }
        objects.append(
            {
                "stable_id": operation["object_id"],
                "object_kind": obj["openbim_object_kind"],
                "system_id": obj["openbim_system_id"],
                "position": position,
                "centerline": centerline,
                "topology": json.loads(obj.get("openbim_topology_json", "[]")),
                "diameter_mm": _optional_float(obj.get("openbim_geometry_diameter_mm")),
                "horizontal_length_m": _optional_float(obj.get("openbim_geometry_horizontal_length_m")),
                "start_invert_m": _optional_float(obj.get("openbim_geometry_start_invert_m")),
                "end_invert_m": _optional_float(obj.get("openbim_geometry_end_invert_m")),
                "slope": _optional_float(obj.get("openbim_geometry_slope")),
                "material": obj.get("openbim_material"),
                "ifc_class": obj["openbim_ifc_class"],
                "ifc_predefined_type": obj.get("openbim_ifc_predefined_type"),
                "domain_properties": domain,
                "source_ir_path": obj["openbim_source_ir_path"],
                "host_handle": f"blender:{obj.name}",
                "presentation_material": (
                    f"blender:{obj.get('openbim_material')}"
                    if obj.get("openbim_material") is not None
                    else None
                ),
            }
        )
    snapshot = {
        "snapshot_version": SEMANTIC_SNAPSHOT_VERSION,
        "host": "blender",
        "host_adapter": f"blender-typed-plan-{fork_version}",
        "source_ir_id": plan["ir_id"],
        "source_ir_sha256": plan["compiled_ir_sha256"],
        "units": "m",
        "objects": sorted(objects, key=lambda item: item["stable_id"]),
        "allowed_host_differences": ["host_handle", "presentation_material"],
        "canonical_sha256": "",
    }
    encoded = json.dumps(
        {key: value for key, value in snapshot.items() if key != "canonical_sha256"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    snapshot["canonical_sha256"] = hashlib.sha256(encoded).hexdigest()
    return snapshot


def _semantic_number(value: Any) -> float:
    number = round(float(value), SEMANTIC_DECIMAL_PLACES)
    return 0.0 if number == 0 else number


def _optional_float(value: Any) -> float | None:
    return _semantic_number(value) if value is not None else None


__all__ = [
    "TypedPlanError",
    "apply_operation",
    "canonical_plan_sha256",
    "execute_typed_plan",
    "project_semantic_snapshot",
    "read_state",
    "resolve_output_path",
    "state_path",
    "validate_plan",
    "write_state",
]
