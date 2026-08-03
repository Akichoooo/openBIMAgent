# OPENBIMAGENT vectorworks-mcp runner (M1 phase 1)
# VW 宿主侧 Python runner:轮询 jobs/ 目录,执行命令,写 results/。
# 等价于 blender_mcp/addon.py,但走文件 IPC 替代 socket (VW 不支持常驻 socket)。
#
# 从 openBIMForge vendor/vs_interface.py 提取说明:
#   vendor/vs_interface.py 是 proxy/bridge 模块(非直接 vs.* 封装),硬依赖
#   vs 模块(VW 内置)与 forge_core 包,无法在测试环境独立 import。因此
#   本 runner 自行实现 execute_vs_code (exec with vs in globals),测试 mock vs。

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sys
import time
import traceback
import uuid
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

# OPENBIMAGENT (c): 已知 VW API 坑(节选自 README.md AGENTS.md 坑清单)
KNOWN_ISSUES = [
    "ArcByCenter 在 VW2024 中已损坏,用 Oval 替代",
    "Arc 第六参数为 Sweep 角度,非终点角度",
]


def get_vw_version() -> str:
    """获取 VectorWorks 版本。

    OPENBIMAGENT (c): 避免"模型不清楚 VW 版本工具出 bug"。在 VW 宿主内
    尝试 import vs 并调用版本 API;无 VW 环境时返回 "unknown"。

    Returns:
        VW 版本字符串 (如 "VectorWorks 2024") 或 "unknown"
    """
    try:
        import vs  # type: ignore[import-not-found]  # VectorWorks Python API
    except Exception:
        return "unknown"
    try:
        # vs.GetVersion() 返回 (major, minor, maintenance, build) 元组
        # 参考 openBIMForge 版本探测思路
        major = 2024  # 默认值,vendor 文件路径含 openBIMForge2024
        try:
            version_info = vs.GetVersion()
            if isinstance(version_info, (tuple, list)) and len(version_info) >= 1:
                major = int(version_info[0])
        except Exception:
            pass
        return f"VectorWorks {major}"
    except Exception:
        return "unknown"


def execute_vs_code(code: str) -> dict[str, Any]:
    """执行 VectorScript 代码 (vs.* API 调用)。

    从 openBIMForge vs_interface.py 提取思路:在 VW 内嵌 Python 中 exec
    代码,vs 模块注入 globals。捕获 stdout,异常转 error/traceback。

    Args:
        code: VectorScript 代码字符串

    Returns:
        {"ok": True, "stdout": ..., "stderr": ...} 成功
        {"ok": False, "error": ..., "traceback": ...} 失败
    """
    try:
        import vs  # type: ignore[import-not-found]  # VectorWorks Python API
    except Exception as e:
        return {
            "ok": False,
            "error": f"vs module not available: {e}",
            "traceback": traceback.format_exc(),
        }
    try:
        stdout_buf = io.StringIO()
        exec_globals: dict[str, Any] = {"vs": vs, "math": math, "__name__": "__vw_exec__"}
        with redirect_stdout(stdout_buf):
            exec(code, exec_globals)  # noqa: S102  (VW 代码执行器,沙箱由 VW 宿主保证)
        return {
            "ok": True,
            "stdout": stdout_buf.getvalue(),
            "stderr": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


_PLAN_VERSION = "1.0"
_PROTOCOL_VERSION = "1.0"
_HOST_API_VERSION = "2024"
_STATE_VERSION = "1.0"
_SEMANTIC_SNAPSHOT_VERSION = "1.0"
_AUTHORIZED_LAYER = "M1-Municipal-Utility"
_SEMANTIC_DECIMAL_PLACES = 6
_VECTORWORKS_METERS_UNIT_STYLE = 9
_ALLOWED_OPERATIONS = {"create_object", "set_record", "connect_topology"}
_ALLOWED_OBJECT_TYPES = {
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


def _semantic_params_hash(params: dict[str, Any]) -> str:
    semantic = {key: value for key, value in params.items() if key != "_approved"}
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _validate_execute_plan_gate(params: dict[str, Any], gate: Any) -> None:
    if not isinstance(gate, dict):
        raise PermissionError("typed execute_plan 缺少 runner 审批审计字段")
    if gate.get("requires_approval") is not True or gate.get("approved") is not True:
        raise PermissionError("typed execute_plan 未获 runner 侧确认的审批")
    if gate.get("params_hash") != _semantic_params_hash(params):
        raise PermissionError("typed execute_plan runner params hash 不匹配")


def _canonical_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    payload.pop("plan_id", None)
    payload.pop("canonical_sha256", None)
    payload.pop("idempotency_key", None)
    operations = payload.get("operations")
    if isinstance(operations, list):
        payload["operations"] = sorted(operations, key=lambda item: item.get("operation_id", ""))
    return payload


def _canonical_plan_sha256(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_plan_payload(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_rule_identity(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    required = {
        "rule_evidence_bundle_sha256",
        "rule_evaluation_sha256",
        "rule_decision_status",
        "production_verification",
        "exception_approval_id",
        "exception_approval_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        fields = set(raw) if isinstance(raw, dict) else set()
        raise ValueError(
            f"rule_identity 字段不匹配: missing={sorted(required - fields)} "
            f"unknown={sorted(fields - required)}"
        )
    for field in ("rule_evidence_bundle_sha256", "rule_evaluation_sha256"):
        value = raw[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"rule_identity {field} 无效")
    if raw["rule_decision_status"] not in {"pass", "fail", "unknown", "review_required"}:
        raise ValueError("rule_identity rule_decision_status 无效")
    if raw["production_verification"] not in {"eligible", "review_required"}:
        raise ValueError("rule_identity production_verification 无效")
    approval_id = raw["exception_approval_id"]
    approval_sha = raw["exception_approval_sha256"]
    if (approval_id is None) is not (approval_sha is None):
        raise ValueError("rule_identity 例外审批 ID 与 SHA-256 必须同时存在")
    if approval_id is not None and (
        not isinstance(approval_id, str) or not approval_id or len(approval_id) > 256
    ):
        raise ValueError("rule_identity exception_approval_id 无效")
    if approval_sha is not None and (
        not isinstance(approval_sha, str) or re.fullmatch(r"[0-9a-f]{64}", approval_sha) is None
    ):
        raise ValueError("rule_identity exception_approval_sha256 无效")
    if (
        raw["production_verification"] == "review_required"
        and raw["rule_decision_status"] in {"pass", "fail"}
    ):
        raise ValueError("PASS/FAIL 只允许绑定 eligible production verification")
    return raw


def _rule_domain_fields(identity: dict[str, Any] | None) -> dict[str, str]:
    if identity is None:
        return {}
    fields = {
        "rule_evidence_bundle_sha256": identity["rule_evidence_bundle_sha256"],
        "rule_evaluation_sha256": identity["rule_evaluation_sha256"],
        "rule_decision_status": identity["rule_decision_status"],
        "production_verification": identity["production_verification"],
    }
    if identity["exception_approval_id"] is not None:
        fields["exception_approval_id"] = identity["exception_approval_id"]
        fields["exception_approval_sha256"] = identity["exception_approval_sha256"]
    return fields


def _validate_typed_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("execution plan 必须是对象")
    allowed_fields = {
        "plan_version",
        "protocol_version",
        "host_api_version",
        "plan_id",
        "ir_id",
        "source_ir_sha256",
        "compiled_ir_sha256",
        "rule_identity",
        "units",
        "operations",
        "canonical_sha256",
        "idempotency_key",
    }
    unknown = sorted(set(raw) - allowed_fields)
    if unknown:
        raise ValueError(f"execution plan 含未知字段: {unknown}")
    required = allowed_fields
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"execution plan 缺少字段: {missing}")
    if raw["plan_version"] != _PLAN_VERSION or raw["protocol_version"] != _PROTOCOL_VERSION:
        raise ValueError("execution plan protocol/version 不匹配")
    if raw["host_api_version"] != _HOST_API_VERSION or raw["units"] != "m":
        raise ValueError("execution plan host API/units 不匹配")
    for hash_field in ("source_ir_sha256", "compiled_ir_sha256"):
        value = raw[hash_field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"execution plan {hash_field} 无效")
    rule_identity = _validate_rule_identity(raw["rule_identity"])
    expected_rule_fields = {
        f"Domain_{name}": value
        for name, value in _rule_domain_fields(rule_identity).items()
    }
    operations = raw["operations"]
    if not isinstance(operations, list) or not operations:
        raise ValueError("execution plan operations 不能为空")
    operation_ids: set[str] = set()
    created: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("typed operation 必须是对象")
        kind = operation.get("operation")
        operation_id = operation.get("operation_id")
        object_id = operation.get("object_id")
        object_type = operation.get("object_type")
        if kind not in _ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported typed operation: {kind!r}")
        if object_type not in _ALLOWED_OBJECT_TYPES:
            raise ValueError(f"unsupported object_type: {object_type!r}")
        if not isinstance(operation_id, str) or not operation_id or operation_id in operation_ids:
            raise ValueError("operation_id 缺失或重复")
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("object_id 缺失")
        operation_ids.add(operation_id)
        if kind == "create_object":
            if operation.get("layer_name") != _AUTHORIZED_LAYER:
                raise ValueError("create_object escaped authorized Vectorworks layer")
            created.add(object_id)
        elif kind == "set_record":
            if object_id not in created:
                raise ValueError(f"set_record 引用未知对象: {object_id}")
            record_fields = operation.get("record_fields")
            if not isinstance(record_fields, list):
                raise ValueError(f"set_record 缺少 record_fields: {object_id}")
            projected = {
                item.get("field_name"): item.get("value")
                for item in record_fields
                if isinstance(item, dict)
                and (
                    str(item.get("field_name", "")).startswith("Domain_rule_")
                    or str(item.get("field_name", "")).startswith("Domain_production_")
                    or str(item.get("field_name", "")).startswith("Domain_exception_")
                )
            }
            if projected != expected_rule_fields:
                raise ValueError(f"rule_identity 与对象 {object_id!r} record 不一致")
        elif kind == "connect_topology":
            references = operation.get("references")
            if (
                not isinstance(references, list)
                or len(references) != 2
                or any(reference not in created for reference in references)
                or object_id not in created
            ):
                raise ValueError(f"connect_topology 引用未知对象: {object_id}")
    digest = _canonical_plan_sha256(raw)
    if raw["canonical_sha256"] != digest:
        raise ValueError("execution plan canonical_sha256 不匹配")
    if raw["idempotency_key"] != f"vw-plan:{digest}":
        raise ValueError("execution plan idempotency_key 不匹配")
    return raw


def _resolve_output_path(output_path: Any, authorized_root: Any) -> Path:
    if not isinstance(output_path, str) or not output_path:
        raise ValueError("output_path 不能为空")
    if not isinstance(authorized_root, str) or not authorized_root:
        raise ValueError("authorized_root 不能为空")
    root = Path(authorized_root).resolve()
    target = Path(output_path).resolve()
    if target.suffix.lower() != ".vwx":
        raise ValueError("output_path 必须是 .vwx 文件")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output_path 超出授权根目录: {target}") from exc
    return target


def _state_path(output_path: Path) -> Path:
    return output_path.with_suffix(f"{output_path.suffix}.openbimagent.json")


def _read_execution_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"typed execution sidecar 损坏: {path}: {exc}") from exc
    if state.get("state_version") != _STATE_VERSION:
        raise ValueError("typed execution sidecar version 不支持")
    return state


def _write_execution_state(path: Path, state: dict[str, Any]) -> None:
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


def _object_name(operation: dict[str, Any]) -> str:
    name = operation.get("name")
    if isinstance(name, str) and name:
        return name
    object_id = str(operation["object_id"])
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", object_id).strip("_")
    if clean:
        return f"VW_M1_{clean}"
    digest = hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:20]
    return f"VW_M1_{digest}"


def _object_layer_name(vs: Any, handle: Any) -> str:
    layer_handle = vs.GetLayer(handle)
    if layer_handle is None:
        raise RuntimeError("Vectorworks 对象没有可读取的设计图层")
    layer_name = vs.GetLName(layer_handle)
    if not isinstance(layer_name, str) or not layer_name:
        raise RuntimeError("Vectorworks 对象设计图层名称为空")
    return layer_name


def _assert_object_layer(vs: Any, handle: Any, expected: str) -> None:
    actual = _object_layer_name(vs, handle)
    if actual != expected:
        raise PermissionError(
            f"Vectorworks object escaped layer scope: expected={expected!r}, actual={actual!r}"
        )


def _ensure_document_units_meters(vs: Any) -> None:
    """Set and verify meters before interpreting typed plan coordinates."""
    unit_info = vs.GetPrimaryUnitInfo()
    if not isinstance(unit_info, (tuple, list)) or len(unit_info) != 7:
        raise RuntimeError(
            f"Vectorworks GetPrimaryUnitInfo returned invalid value: {unit_info!r}"
        )
    style, precision, dimension_precision, unit_format, angle_precision, show_mark, display_fraction = unit_info
    if int(style) != _VECTORWORKS_METERS_UNIT_STYLE:
        vs.PrimaryUnits(
            _VECTORWORKS_METERS_UNIT_STYLE,
            precision,
            dimension_precision,
            unit_format,
            angle_precision,
            show_mark,
            display_fraction,
        )
    verified = vs.GetPrimaryUnitInfo()
    if (
        not isinstance(verified, (tuple, list))
        or len(verified) != 7
        or int(verified[0]) != _VECTORWORKS_METERS_UNIT_STYLE
    ):
        raise RuntimeError(
            "Vectorworks document units must be meters before typed geometry execution: "
            f"actual={verified!r}"
        )


def _create_typed_object(vs: Any, operation: dict[str, Any]) -> Any:
    layer_name = operation.get("layer_name")
    if layer_name != _AUTHORIZED_LAYER:
        raise PermissionError(f"Vectorworks layer scope denied: {layer_name!r}")
    name = _object_name(operation)
    existing = vs.GetObject(name)
    if existing is not None:
        _assert_object_layer(vs, existing, layer_name)
        return existing
    vs.Layer(layer_name)
    object_type = operation["object_type"]
    if object_type == "pipe_segment":
        centerline = operation.get("centerline") or []
        if len(centerline) < 2:
            raise ValueError("pipe_segment 缺少 centerline")
        vs.BeginPoly3D()
        for point in centerline:
            vs.Add3DPt((point["x_m"], point["y_m"], point["z_m"]))
        vs.EndPoly3D()
    else:
        position = operation.get("position") or {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0}
        vs.Locus3D((position["x_m"], position["y_m"], position["z_m"]))
    handle = vs.LNewObj()
    if handle is None:
        raise RuntimeError(f"Vectorworks 未返回新对象 handle: {operation['object_id']}")
    class_name = operation.get("class_name")
    if class_name:
        vs.NameClass(class_name)
        vs.SetClass(handle, class_name)
    vs.SetName(handle, name)
    _assert_object_layer(vs, handle, layer_name)
    return handle


def _ensure_record_fields(
    vs: Any,
    record_name: str,
    fields: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> None:
    record_handle = vs.GetObject(record_name)
    existing: set[str] = set()
    if record_handle is not None:
        count = int(vs.NumFields(record_handle))
        existing = {
            str(vs.GetFldName(record_handle, index))
            for index in range(1, count + 1)
        }
    for field in fields:
        field_name = str(field["field_name"])
        if field_name not in existing:
            vs.NewField(record_name, field_name, "", 4, 0)
            existing.add(field_name)


def _set_typed_record(vs: Any, operation: dict[str, Any]) -> None:
    handle = vs.GetObject(_object_name(operation))
    if handle is None:
        raise ValueError(f"set_record 对象不存在: {operation['object_id']}")
    record_name = operation.get("record_name")
    fields = operation.get("record_fields") or []
    if not isinstance(record_name, str) or not record_name or not fields:
        raise ValueError("set_record 缺少 record_name/record_fields")
    _ensure_record_fields(vs, record_name, fields)
    vs.SetRecord(handle, record_name)
    for field in fields:
        vs.SetRField(handle, record_name, field["field_name"], str(field["value"]))


def _connect_typed_topology(vs: Any, operation: dict[str, Any]) -> None:
    handle = vs.GetObject(_object_name(operation))
    if handle is None:
        raise ValueError(f"connect_topology 对象不存在: {operation['object_id']}")
    record_name = "OpenBIMAgent_Topology"
    fields = (("StartPortID", operation["references"][0]), ("EndPortID", operation["references"][1]))
    _ensure_record_fields(
        vs,
        record_name,
        tuple({"field_name": field_name} for field_name, _ in fields),
    )
    vs.SetRecord(handle, record_name)
    for field_name, value in fields:
        vs.SetRField(handle, record_name, field_name, str(value))


def _save_controlled_document(vs: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    active_before = str(vs.GetFPathName() or "")
    if active_before:
        active_path = Path(active_before).resolve()
        if active_path != target:
            raise RuntimeError(
                "Vectorworks save refused because another document is active: "
                f"expected={target}, actual={active_path}"
            )
        vs.DoMenuTextByName("Save", 0)
    else:
        save_result = vs.SaveActiveDocument(str(target))
        if save_result != 0:
            raise RuntimeError(
                f"Vectorworks SaveActiveDocument failed: code={save_result}"
            )
    active_after = str(vs.GetFPathName() or "")
    if not active_after or Path(active_after).resolve() != target:
        raise RuntimeError(
            "Vectorworks save did not activate the controlled target document"
        )
    if not target.is_file():
        raise RuntimeError(
            f"Vectorworks save did not materialize the controlled document: {target}"
        )


def _apply_typed_operation(vs: Any, operation: dict[str, Any]) -> str:
    kind = operation["operation"]
    if kind == "create_object":
        handle = _create_typed_object(vs, operation)
    elif kind == "set_record":
        _set_typed_record(vs, operation)
        handle = vs.GetObject(_object_name(operation))
    elif kind == "connect_topology":
        _connect_typed_topology(vs, operation)
        handle = vs.GetObject(_object_name(operation))
    else:  # pragma: no cover - _validate_typed_plan 已失败关闭
        raise ValueError(f"unsupported typed operation: {kind!r}")
    if handle is None:
        raise RuntimeError(f"Vectorworks operation 未返回对象: {operation['object_id']}")
    _assert_object_layer(vs, handle, _AUTHORIZED_LAYER)
    host_name = vs.GetName(handle)
    if not isinstance(host_name, str) or not host_name:
        host_name = _object_name(operation)
    return host_name


def _semantic_number(value: Any) -> float:
    number = round(float(value), _SEMANTIC_DECIMAL_PLACES)
    return 0.0 if number == 0 else number


def _semantic_coordinate(value: Any) -> dict[str, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"Vectorworks returned invalid 3D coordinate: {value!r}")
    return {
        "x_m": _semantic_number(value[0]),
        "y_m": _semantic_number(value[1]),
        "z_m": _semantic_number(value[2]),
    }


def _read_3d_center(vs: Any, handle: Any) -> dict[str, float]:
    result = vs.Get3DCntr(handle)
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        raise ValueError(f"Vectorworks Get3DCntr returned invalid value: {result!r}")
    point, z_value = result
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise ValueError(f"Vectorworks Get3DCntr returned invalid XY point: {point!r}")
    return _semantic_coordinate((point[0], point[1], z_value))


def _parse_record_value(raw: Any, expected: Any) -> Any:
    if raw is None or str(raw) == "":
        raise ValueError("Vectorworks record value is missing")
    text = str(raw)
    if isinstance(expected, bool):
        lowered = text.strip().lower()
        if lowered not in {"true", "false", "1", "0"}:
            raise ValueError(f"invalid boolean record value: {text!r}")
        return lowered in {"true", "1"}
    if isinstance(expected, int) and not isinstance(expected, bool):
        return int(text)
    if isinstance(expected, float):
        return _semantic_number(text)
    return text


def _record_definitions(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for operation in plan["operations"]:
        if operation["operation"] != "set_record":
            continue
        definitions[operation["object_id"]] = {
            item["field_name"]: item["value"]
            for item in operation.get("record_fields") or []
        }
    return definitions


def _project_semantic_snapshot(plan: dict[str, Any], vs: Any) -> dict[str, Any]:
    record_name = "OpenBIMAgent_MunicipalUtility"
    definitions = _record_definitions(plan)
    create_operations = [
        operation
        for operation in plan["operations"]
        if operation["operation"] == "create_object"
    ]
    objects: list[dict[str, Any]] = []
    for operation in create_operations:
        expected_id = operation["object_id"]
        handle = vs.GetObject(_object_name(operation))
        if handle is None:
            raise ValueError(f"semantic projection missing object: {expected_id}")
        _assert_object_layer(vs, handle, _AUTHORIZED_LAYER)
        field_definitions = definitions.get(expected_id)
        if not field_definitions:
            raise ValueError(f"semantic projection missing record definition: {expected_id}")
        required = {"StableObjectID", "ObjectKind", "SystemID", "IFCClass", "SourceIRPath"}
        missing = sorted(required - set(field_definitions))
        if missing:
            raise ValueError(f"semantic projection missing required fields for {expected_id}: {missing}")
        records = {
            name: _parse_record_value(vs.GetRField(handle, record_name, name), expected)
            for name, expected in field_definitions.items()
        }
        stable_id = str(records["StableObjectID"])
        if stable_id != expected_id:
            raise ValueError(
                f"semantic projection stable identity mismatch: expected={expected_id!r}, actual={stable_id!r}"
            )
        object_kind = str(records["ObjectKind"])
        position = None
        centerline: list[dict[str, float]] = []
        if object_kind in {"node", "port"}:
            position = _read_3d_center(vs, handle)
        elif object_kind == "segment":
            vertex_count = int(vs.GetVertNum(handle))
            if vertex_count < 2:
                raise ValueError(f"semantic projection pipe has fewer than two vertices: {stable_id}")
            centerline = [
                _semantic_coordinate(vs.GetPolyPt3D(handle, index))
                for index in range(1, vertex_count + 1)
            ]
        topology: list[str] = []
        if object_kind == "segment":
            topology = [
                str(vs.GetRField(handle, "OpenBIMAgent_Topology", "StartPortID")),
                str(vs.GetRField(handle, "OpenBIMAgent_Topology", "EndPortID")),
            ]
            if any(not value for value in topology):
                raise ValueError(f"semantic projection missing topology record: {stable_id}")
        domain = {
            name.removeprefix("Domain_"): value
            for name, value in records.items()
            if name.startswith("Domain_")
        }
        host_name = vs.GetName(handle)
        if not isinstance(host_name, str) or not host_name:
            raise ValueError(f"semantic projection missing host name: {stable_id}")
        material = records.get("Material")
        objects.append(
            {
                "stable_id": stable_id,
                "object_kind": object_kind,
                "system_id": str(records["SystemID"]),
                "position": position,
                "centerline": centerline,
                "topology": topology,
                "diameter_mm": records.get("DiameterMM"),
                "horizontal_length_m": records.get("HorizontalLengthM"),
                "start_invert_m": records.get("StartInvertM"),
                "end_invert_m": records.get("EndInvertM"),
                "slope": records.get("Slope"),
                "material": str(material) if material is not None else None,
                "ifc_class": str(records["IFCClass"]),
                "ifc_predefined_type": (
                    str(records["IFCPredefinedType"])
                    if "IFCPredefinedType" in records
                    else None
                ),
                "domain_properties": domain,
                "source_ir_path": str(records["SourceIRPath"]),
                "host_handle": f"vectorworks:{host_name}",
                "presentation_material": (
                    f"vectorworks:{material}" if material is not None else None
                ),
            }
        )
    snapshot = {
        "snapshot_version": _SEMANTIC_SNAPSHOT_VERSION,
        "host": "vectorworks",
        "host_adapter": "vectorworks-typed-plan-1.0.0-m1",
        "source_ir_id": plan["ir_id"],
        "source_ir_sha256": plan["compiled_ir_sha256"],
        "rule_identity": plan["rule_identity"],
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


def _receipt(
    plan: dict[str, Any],
    state: dict[str, Any],
    errors: list[str],
    semantic_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    applied = set(state.get("applied_operation_ids") or [])
    complete = (
        len(applied) == len(plan["operations"])
        and semantic_snapshot is not None
        and not errors
    )
    status = "completed" if complete else "partial"
    operation_receipts = {
        item["operation_id"]: item
        for item in state.get("operation_receipts") or []
    }
    return {
        "receipt_id": f"vw-receipt-{plan['canonical_sha256'][:24]}",
        "plan_id": plan["plan_id"],
        "idempotency_key": plan["idempotency_key"],
        "canonical_sha256": plan["canonical_sha256"],
        "status": status,
        "output_path": state["output_path"],
        "state_path": state["state_path"],
        "applied_operations": [
            operation_receipts[operation["operation_id"]]
            for operation in plan["operations"]
            if operation["operation_id"] in operation_receipts
        ],
        "confirmed_object_ids": sorted(state.get("confirmed_object_ids") or []),
        "semantic_snapshot": semantic_snapshot,
        "compensations": [
            f"restore:{object_id}"
            for object_id in sorted(state.get("confirmed_object_ids") or [])
        ],
        "errors": errors,
    }


def execute_typed_plan(
    plan_payload: Any,
    *,
    output_path: Any,
    authorized_root: Any,
) -> dict[str, Any]:
    """直接解释 allowlisted operations，并以 sidecar 保存幂等恢复事实。"""
    plan = _validate_typed_plan(plan_payload)
    target = _resolve_output_path(output_path, authorized_root)
    sidecar = _state_path(target)
    state = _read_execution_state(sidecar)
    if state is None:
        if target.exists():
            raise FileExistsError(f"目标工程已存在且无匹配 sidecar，拒绝覆盖: {target}")
        state = {
            "state_version": _STATE_VERSION,
            "plan_id": plan["plan_id"],
            "idempotency_key": plan["idempotency_key"],
            "canonical_sha256": plan["canonical_sha256"],
            "output_path": str(target),
            "state_path": str(sidecar),
            "applied_operation_ids": [],
            "confirmed_object_ids": [],
            "operation_receipts": [],
            "receipt": None,
        }
        # 在首个宿主写入前先持久化恢复身份，关闭“工程已保存但 sidecar 尚未创建”的窗口。
        _write_execution_state(sidecar, state)
    elif (
        state.get("idempotency_key") != plan["idempotency_key"]
        or state.get("canonical_sha256") != plan["canonical_sha256"]
        or Path(str(state.get("output_path"))).resolve() != target
        or Path(str(state.get("state_path"))).resolve() != sidecar
    ):
        raise ValueError("目标工程 sidecar 与 typed plan 身份冲突")
    if state.get("receipt") and state["receipt"].get("status") == "completed":
        if not target.is_file():
            raise FileNotFoundError(
                f"Vectorworks completed receipt exists but controlled document is missing: {target}"
            )
        return state["receipt"]

    try:
        import vs  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(f"vs module not available: {exc}") from exc
    active_document = str(vs.GetFPathName() or "")
    if target.exists():
        if not active_document:
            raise RuntimeError("Vectorworks recovery cannot identify the active document")
        if Path(active_document).resolve() != target:
            raise RuntimeError(
                "Vectorworks recovery requires the controlled target document to be active: "
                f"expected={target}, actual={Path(active_document).resolve()}"
            )
    elif state.get("applied_operation_ids"):
        raise FileNotFoundError(
            f"Vectorworks partial state exists but controlled document is missing: {target}"
        )
    elif active_document:
        raise RuntimeError(
            "Vectorworks first execution requires an unnamed blank document before any "
            f"typed host side effect: active={Path(active_document).resolve()}"
        )

    # Typed plan coordinates are meters. Normalize the active document before any
    # host geometry is created, including recovery runs after a host restart.
    _ensure_document_units_meters(vs)

    applied = set(state["applied_operation_ids"])
    confirmed = set(state["confirmed_object_ids"])
    operation_receipts = {
        item["operation_id"]: item
        for item in state.get("operation_receipts") or []
    }
    errors: list[str] = []
    for operation in plan["operations"]:
        if operation["operation_id"] in applied:
            continue
        try:
            host_handle = _apply_typed_operation(vs, operation)
            _save_controlled_document(vs, target)
            applied.add(operation["operation_id"])
            if operation["operation"] == "create_object":
                confirmed.add(operation["object_id"])
            operation_receipts[operation["operation_id"]] = {
                "operation_id": operation["operation_id"],
                "status": "completed",
                "object_id": operation["object_id"],
                "host_handle": host_handle,
            }
            state["applied_operation_ids"] = sorted(applied)
            state["confirmed_object_ids"] = sorted(confirmed)
            state["operation_receipts"] = [
                operation_receipts[item["operation_id"]]
                for item in plan["operations"]
                if item["operation_id"] in operation_receipts
            ]
            _write_execution_state(sidecar, state)
        except Exception as exc:
            errors.append(f"operation={operation['operation_id']}: {exc}")
            break
    semantic_snapshot = None
    if len(applied) == len(plan["operations"]) and not errors:
        try:
            semantic_snapshot = _project_semantic_snapshot(plan, vs)
        except Exception as exc:
            errors.append(f"semantic_projection: {exc}")
    receipt = _receipt(plan, state, errors, semantic_snapshot)
    if receipt["status"] == "completed":
        state["receipt"] = receipt
    _write_execution_state(sidecar, state)
    return receipt


def execute_command(
    command: str,
    params: dict[str, Any],
    *,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """命令分发:按 command 名路由到对应处理函数。

    Args:
        command: 命令名 (ping/describe_capabilities/execute_code)
        params: 命令参数

    Returns:
        执行结果字典

    Raises:
        ValueError: 未知命令
    """
    if command == "ping":
        return {"message": "pong"}
    elif command == "describe_capabilities":
        return {
            "vw_version": get_vw_version(),
            "python_version": sys.version,
            "known_issues": list(KNOWN_ISSUES),
        }
    elif command == "execute_plan":
        _validate_execute_plan_gate(params, gate)
        return execute_typed_plan(
            params.get("plan"),
            output_path=params.get("output_path"),
            authorized_root=params.get("authorized_root") or os.getenv("VW_MCP_AUTHORIZED_ROOT", ""),
        )
    elif command == "execute_code":
        code = params.get("code", "")
        return execute_vs_code(code)
    else:
        raise ValueError(f"Unknown command: {command}")


def _archive_active_file(path: Path) -> Path | None:
    """将已消费文件原子移出活跃目录，保留可审计记录且不依赖 unlink。"""
    if not path.exists():
        return None
    archive_dir = path.parent / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    if target.exists():
        target = archive_dir / f"{path.stem}.{time.time_ns()}{path.suffix}"
    os.replace(path, target)
    return target


def poll_jobs_once(jobs_dir: Path, results_dir: Path) -> list[str]:
    """处理一轮 job(测试友好:不死循环,处理完当前所有 job 即返回)。

    OPENBIMAGENT (d): glob jobs/*.json,对每个 job:
      1. 写 results/<job_id>.running 标记(供客户端观测进行中状态)
      2. 读 job → execute_command()
      3. 成功写 results/<job_id>.json,失败写 results/<job_id>.failed
      4. 清理 jobs/<job_id>.json 与 .running 标记

    Args:
        jobs_dir: jobs 目录
        results_dir: results 目录

    Returns:
        本轮处理的 job_id 列表
    """
    processed: list[str] = []
    for job_path in sorted(jobs_dir.glob("*.json")):
        job_id = job_path.stem
        running_path = results_dir / f"{job_id}.running"
        result_path = results_dir / f"{job_id}.json"
        failed_path = results_dir / f"{job_id}.failed"

        # 标记为 running
        running_path.write_text(datetime.now().isoformat(), encoding="utf-8")

        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            result = execute_command(
                job["command"],
                job.get("params", {}),
                gate=job.get("gate"),
            )
            result_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            failed_path.write_text(str(e), encoding="utf-8")
        finally:
            _archive_active_file(job_path)
            _archive_active_file(running_path)
            processed.append(job_id)

    return processed


def main() -> None:
    """runner 主循环:死循环轮询 jobs/,100ms 间隔。

    在 VW 内嵌 Python 中运行;Ctrl-C 或 VW 退出时停止。
    """
    jobs_dir = Path("jobs")
    results_dir = Path("results")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    print("VW MCP runner started", flush=True)
    print(f"  jobs_dir:   {jobs_dir.resolve()}", flush=True)
    print(f"  results_dir:{results_dir.resolve()}", flush=True)
    try:
        while True:
            poll_jobs_once(jobs_dir, results_dir)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Runner stopped", flush=True)


if __name__ == "__main__":
    main()
