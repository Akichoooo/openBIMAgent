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
from collections import Counter
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


_PLAN_VERSION = "1.1"
_PROTOCOL_VERSION = "1.0"
_HOST_API_VERSION = "2024"
_STATE_VERSION = "1.0"
_SEMANTIC_SNAPSHOT_VERSION = "1.0"
_GEOMETRY_SNAPSHOT_VERSION = "1.1"
_PIPE_GEOMETRY_TOLERANCE_M = 1e-5
_READBACK_TOLERANCE_BASIS = (
    "numeric readback resolution only: 1e-5 m (0.01 mm) bound for host vertex "
    "readback at the protocol's six-decimal-metre precision; not a surface "
    "accuracy, fabrication or engineering acceptance tolerance"
)
_MESH_TOPOLOGY_SCOPE = (
    "face count, duplicate faces, shared-edge connectivity and face orientation"
)
_MESH_TOPOLOGY_UNAVAILABLE_REASON = (
    "Vectorworks 2024 exposes no non-destructive mesh face/edge readback: the "
    "official vs.py API surface (2865 functions) contains only GetMeshVertsCnt, "
    "GetMeshVertex, SetMeshVertex and MeshToGroup for meshes, and MeshToGroup "
    "destructively converts the mesh to a group of 3D polygons"
)
_PIPE_MESH_OBJECT_TYPE = 40
_PIPE_GEOMETRY_KIND = "hollow_tube_mesh"
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


_OPERATION_FIELDS = {
    "operation",
    "operation_id",
    "object_id",
    "object_type",
    "name",
    "layer_name",
    "class_name",
    "units",
    "position",
    "centerline",
    "nominal_diameter_mm",
    "inner_diameter_mm",
    "outer_diameter_mm",
    "geometry_kind",
    "circumferential_segments",
    "material",
    "slope",
    "ifc_class",
    "ifc_predefined_type",
    "record_name",
    "record_fields",
    "references",
}


def _positive_finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数值")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} 必须是有限正数")
    return number


def _pipe_axis_points(
    operation: dict[str, Any],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    centerline = operation.get("centerline")
    if not isinstance(centerline, list) or len(centerline) != 2:
        raise ValueError("pipe_segment 必须是直管：centerline 必须恰好有两个轴线端点")
    points = []
    for point in centerline:
        if not isinstance(point, dict) or set(point) != {"x_m", "y_m", "z_m"}:
            raise ValueError("pipe_segment centerline 坐标必须是 {x_m,y_m,z_m}")
        values = []
        for axis in ("x_m", "y_m", "z_m"):
            value = point[axis]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"pipe_segment centerline.{axis} 必须是有限数值")
            values.append(float(value))
        points.append(tuple(values))
    if math.dist(points[0], points[1]) <= 1e-9:
        raise ValueError("pipe_segment 长度无效（轴线端点重合）")
    return points[0], points[1]


def _validate_pipe_operation(operation: dict[str, Any]) -> None:
    inner = _positive_finite(operation.get("inner_diameter_mm"), "inner_diameter_mm")
    outer = _positive_finite(operation.get("outer_diameter_mm"), "outer_diameter_mm")
    _positive_finite(operation.get("nominal_diameter_mm"), "nominal_diameter_mm")
    if inner >= outer:
        raise ValueError("pipe_segment inner_diameter_mm 必须小于 outer_diameter_mm")
    if operation.get("geometry_kind") != _PIPE_GEOMETRY_KIND:
        raise ValueError(
            f"pipe_segment 必须显式声明 geometry_kind={_PIPE_GEOMETRY_KIND!r}；不能静默退化为轴线"
        )
    segments = operation.get("circumferential_segments")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 8 <= segments <= 64:
        raise ValueError("pipe_segment circumferential_segments 必须是 8..64 的整数")
    _pipe_axis_points(operation)


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
        unknown_operation_fields = sorted(set(operation) - _OPERATION_FIELDS)
        if unknown_operation_fields:
            raise ValueError(f"typed operation 含未知字段: {unknown_operation_fields}")
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
            if object_type == "pipe_segment":
                _validate_pipe_operation(operation)
            else:
                if any(
                    operation.get(field) is not None
                    for field in (
                        "nominal_diameter_mm",
                        "inner_diameter_mm",
                        "outer_diameter_mm",
                        "geometry_kind",
                        "circumferential_segments",
                    )
                ):
                    raise ValueError(f"{object_type} 不得携带 pipe geometry 字段")
                if operation.get("centerline"):
                    raise ValueError(f"{object_type} 不得携带 centerline")
            created.add(object_id)
        elif kind == "set_record":
            if any(
                operation.get(field) is not None
                for field in (
                    "nominal_diameter_mm",
                    "inner_diameter_mm",
                    "outer_diameter_mm",
                    "geometry_kind",
                    "circumferential_segments",
                )
            ):
                raise ValueError("set_record 不得携带 pipe geometry 字段")
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
            if any(
                operation.get(field) is not None
                for field in (
                    "nominal_diameter_mm",
                    "inner_diameter_mm",
                    "outer_diameter_mm",
                    "geometry_kind",
                    "circumferential_segments",
                )
            ):
                raise ValueError("connect_topology 不得携带 pipe geometry 字段")
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


def _vectorworks_nil_handle(handle: Any) -> bool:
    """Return whether the embedded Vectorworks bridge returned a NIL handle.

    Depending on the Vectorworks Python bridge and call site, NIL can surface as
    ``None`` or numeric zero. Never pass such a value to an API requiring a
    concrete HANDLE: Vectorworks opens a native ``Handle variable is NIL``
    dialog before Python can enforce the failure-closed boundary.
    """
    return handle is None or handle is False or (
        isinstance(handle, (int, float)) and handle == 0
    )


def _layer_name(vs: Any, layer_handle: Any) -> str:
    if _vectorworks_nil_handle(layer_handle):
        raise RuntimeError("Vectorworks 设计图层 handle 为 NIL")
    layer_name = vs.GetLName(layer_handle)
    if not isinstance(layer_name, str) or not layer_name:
        raise RuntimeError("Vectorworks 设计图层名称为空")
    return layer_name


def _criteria_atom(value: str) -> str:
    """Return a narrowly validated Vectorworks criteria atom."""
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None:
        raise ValueError(f"Vectorworks criteria value is not allowlisted: {value!r}")
    return value


def _same_vectorworks_object(vs: Any, left: Any, right: Any) -> bool:
    if _vectorworks_nil_handle(left) or _vectorworks_nil_handle(right):
        return False
    if left == right:
        return True
    get_uuid = getattr(vs, "GetObjectUuid", None)
    if not callable(get_uuid):
        return False
    left_uuid = str(get_uuid(left) or "")
    right_uuid = str(get_uuid(right) or "")
    return bool(left_uuid and right_uuid and left_uuid == right_uuid)


def _object_matches_layer_criteria(vs: Any, handle: Any, expected: str, object_name: str | None = None) -> bool:
    """Prove layer scope with one bounded host query and no HANDLE traversal.

    Vectorworks 2024 can emit native ``Handle variable is NIL`` dialogs while
    Python traverses layer/object HANDLE chains. Avoid GetParent/GetLName,
    FLayer/NextLayer, and FInLayer/NextObj entirely. Query the already named
    object by exact layer and name criteria in one host call, then compare the
    returned object identity.

    ``object_name`` is the stable name from the typed plan, used directly
    to build the ForEachObject criteria.  This avoids calling vs.GetName on
    the handle, which may return inconsistent values in Vectorworks 2024.
    """
    if _vectorworks_nil_handle(handle):
        return False
    if object_name is None:
        object_name = vs.GetName(handle)
    if not isinstance(object_name, str) or not object_name:
        handle_type = int(vs.GetTypeN(handle)) if hasattr(vs, "GetTypeN") else -1
        handle_name = str(vs.GetName(handle)) if hasattr(vs, "GetName") else "N/A"
        raise RuntimeError(
            "Vectorworks 对象名称为空，无法验证图层范围 | "
            f"object_name_param={object_name!r} "
            f"handle_type={handle_type} "
            f"handle_name={handle_name!r} "
            f"expected_layer={expected!r}"
        )
    criteria = (
        f"((L='{_criteria_atom(expected)}') & "
        f"(N='{_criteria_atom(object_name)}'))"
    )
    for_each_object = getattr(vs, "ForEachObject", None)
    if not callable(for_each_object):
        raise RuntimeError("Vectorworks ForEachObject 不可用，无法验证图层范围")
    matched = False

    def _verify(candidate: Any) -> None:
        nonlocal matched
        if not matched and _same_vectorworks_object(vs, candidate, handle):
            matched = True

    for_each_object(_verify, criteria)
    return matched


def _assert_object_layer(vs: Any, handle: Any, expected: str, object_name: str | None = None) -> None:
    if not _object_matches_layer_criteria(vs, handle, expected, object_name=object_name):
        raise PermissionError(
            f"Vectorworks object is not contained by authorized layer: {expected!r}"
        )


def _assert_blank_document(vs: Any) -> None:
    """Reject any existing drawing object in any layer before first execution.

    ALL has no active-layer, visibility, selection, name or object-type filter.
    A group or symbol instance itself counts as a drawing object, so rejecting
    it also protects its contents without traversing native HANDLE chains.
    Resource definitions without drawing instances are not drawing objects.
    """
    for_each_object = getattr(vs, "ForEachObject", None)
    if not callable(for_each_object):
        raise RuntimeError("Vectorworks ForEachObject unavailable; cannot prove blank document")
    occupied = False

    def _found_object(handle: Any) -> None:
        nonlocal occupied
        occupied = True

    for_each_object(_found_object, "(ALL)")
    if occupied:
        raise RuntimeError(
            "Vectorworks first execution requires an empty unnamed document; "
            "existing drawing objects were found. Open a blank document explicitly."
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


def _require_vs_functions(vs: Any, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not hasattr(vs, name)]
    if missing:
        raise RuntimeError(
            "Vectorworks 宿主缺少直管实体所需的 API: "
            + ", ".join(missing)
            + "；在任何宿主写入前拒绝执行"
        )


def _validate_pipe_solid_host_capabilities(vs: Any, plan: dict[str, Any]) -> None:
    """Fail before any host side effect if the mesh API is unavailable."""
    pipe_operations = [
        operation
        for operation in plan["operations"]
        if operation.get("operation") == "create_object"
        and operation.get("object_type") == "pipe_segment"
    ]
    if not pipe_operations:
        return
    _require_vs_functions(
        vs,
        (
            "BeginMesh",
            "EndMesh",
            "BeginPoly3D",
            "Add3DPt",
            "EndPoly3D",
            "LNewObj",
            "GetTypeN",
            "GetMeshVertsCnt",
            "GetMeshVertex",
            "SetName",
            "SetClass",
            "Layer",
        ),
    )


def _unit_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1e-15:
        raise ValueError("cannot normalize a zero-length vector")
    return tuple(value / length for value in vector)


def _cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _zip_strict(left: Any, right: Any) -> Any:
    """Pairwise strict pairing helper; host Python 3.9 lacks the strict keyword."""
    left_values = tuple(left)
    right_values = tuple(right)
    if len(left_values) != len(right_values):
        raise ValueError(
            f"strict pairing length mismatch: {len(left_values)} != {len(right_values)}"
        )
    return ((left_values[index], right_values[index]) for index in range(len(left_values)))


def _dot(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> float:
    return sum(a * b for a, b in _zip_strict(left, right))


def _pipe_mesh_faces(operation: dict[str, Any]) -> list[tuple[tuple[float, float, float], ...]]:
    """Closed mesh: outer wall, inner wall, and annular caps at both ends.

    Coordinates are world metres from the typed plan axis. The mesh is a host
    geometry object, not a native pipe/boolean solid; the readback verifies the
    actual mesh vertices against these expected surfaces.
    """
    start, end = _pipe_axis_points(operation)
    inner = _positive_finite(operation.get("inner_diameter_mm"), "inner_diameter_mm") / 2000.0
    outer = _positive_finite(operation.get("outer_diameter_mm"), "outer_diameter_mm") / 2000.0
    segments = operation.get("circumferential_segments")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 8 <= segments <= 64:
        raise ValueError("circumferential_segments 必须是 8..64 的整数")
    direction = tuple(b - a for a, b in _zip_strict(start, end))
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 1e-9:
        raise ValueError("pipe segment has zero length")
    axis = tuple(value / length for value in direction)
    reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (1.0, 0.0, 0.0)
    normal_a = _unit_vector(_cross(axis, reference))
    normal_b = _cross(axis, normal_a)
    outer_start: list[tuple[float, float, float]] = []
    inner_start: list[tuple[float, float, float]] = []
    outer_end: list[tuple[float, float, float]] = []
    inner_end: list[tuple[float, float, float]] = []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        radial = tuple(
            math.cos(angle) * a + math.sin(angle) * b
            for a, b in _zip_strict(normal_a, normal_b)
        )
        outer_start.append(tuple(p + r * outer for p, r in _zip_strict(start, radial)))
        inner_start.append(tuple(p + r * inner for p, r in _zip_strict(start, radial)))
        outer_end.append(tuple(p + r * outer for p, r in _zip_strict(end, radial)))
        inner_end.append(tuple(p + r * inner for p, r in _zip_strict(end, radial)))
    faces: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(segments):
        following = (index + 1) % segments
        faces.append((outer_start[index], outer_start[following], outer_end[following], outer_end[index]))
        faces.append((inner_start[index], inner_end[index], inner_end[following], inner_start[following]))
        faces.append((outer_start[index], inner_start[index], inner_start[following], outer_start[following]))
        faces.append((outer_end[index], outer_end[following], inner_end[following], inner_end[index]))
    return faces


def _create_pipe_mesh(vs: Any, operation: dict[str, Any]) -> Any:
    _validate_pipe_solid_host_capabilities(vs, {"operations": [operation]})
    faces = _pipe_mesh_faces(operation)
    vs.BeginMesh()
    for face in faces:
        vs.BeginPoly3D()
        for point in face:
            vs.Add3DPt(point)
        vs.EndPoly3D()
    vs.EndMesh()
    handle = vs.LNewObj()
    if _vectorworks_nil_handle(handle):
        raise RuntimeError(
            f"Vectorworks 未返回 pipe mesh handle: object_id={operation['object_id']}"
        )
    handle_type = int(vs.GetTypeN(handle))
    if handle_type != _PIPE_MESH_OBJECT_TYPE:
        raise RuntimeError(
            f"Vectorworks pipe 对象不是 mesh: object_id={operation['object_id']} "
            f"GetTypeN={handle_type} expected={_PIPE_MESH_OBJECT_TYPE}"
        )
    return handle


def _create_typed_object(vs: Any, operation: dict[str, Any]) -> Any:
    layer_name = operation.get("layer_name")
    if layer_name != _AUTHORIZED_LAYER:
        raise PermissionError(f"Vectorworks layer scope denied: {layer_name!r}")
    name = _object_name(operation)
    existing = vs.GetObject(name)
    if existing is not None:
        existing_type = int(vs.GetTypeN(existing)) if hasattr(vs, "GetTypeN") else -1
        if existing_type > 0:
            _assert_object_layer(vs, existing, layer_name, object_name=name)
            return existing
        # Handle is invalid (type 0) — treat as no existing object
        existing = None
    vs.Layer(layer_name)
    object_type = operation["object_type"]
    if object_type == "pipe_segment":
        handle = _create_pipe_mesh(vs, operation)
    else:
        position = operation.get("position") or {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0}
        # Groups are proper named containers in Vectorworks. Locus3D alone
        # may not support SetName/GetName, causing the "Handle variable is
        # NIL" dialog on SetName and empty name on GetName.  Wrapping it
        # in a group gives us a handle that supports naming.
        vs.BeginGroup()
        vs.Locus3D((position["x_m"], position["y_m"], position["z_m"]))
        vs.EndGroup()
        handle = vs.LNewObj()
    if _vectorworks_nil_handle(handle):
        raise RuntimeError(
            f"Vectorworks 未返回新对象 handle: operation={operation['operation_id']} "
            f"object_type={object_type}"
        )
    handle_type = int(vs.GetTypeN(handle))
    if handle_type <= 0:
        raise RuntimeError(
            f"Vectorworks 创建对象 handle 类型无效: type={handle_type} "
            f"object_type={object_type} object_id={operation['object_id']}"
        )
    class_name = operation.get("class_name")
    if class_name:
        vs.NameClass(class_name)
        vs.SetClass(handle, class_name)
    vs.SetName(handle, name)
    _assert_object_layer(vs, handle, layer_name, object_name=name)
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


def _is_unnamed_document_reference(active_document: str) -> bool:
    """Return whether Vectorworks reports an unsaved blank-document placeholder.

    Vectorworks can report a new blank document as ``Untitled-1`` from
    ``GetFPathName`` even though it is not a filesystem path.  Only this
    narrow placeholder form is accepted; arbitrary unsaved document names
    remain fail-closed and cannot receive typed host side effects.
    """
    return re.fullmatch(r"Untitled(?:[- ]\d+)?", active_document.strip(), flags=re.IGNORECASE) is not None


def _save_controlled_document(vs: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    active_before = str(vs.GetFPathName() or "")
    if active_before and not _is_unnamed_document_reference(active_before):
        active_path = Path(active_before).resolve()
        if active_path != target:
            raise RuntimeError(
                "Vectorworks save refused because another document is active: "
                f"expected={target}, actual={active_path}"
            )
    # 宿主真机证据（2026-10-18，two-pipes 验收）：VW2024 嵌入 Python 里
    # DoMenuTextByName("Save", 0) 是静默 no-op（不写盘、不报错），导致已命名
    # 文档的 per-op 持久化边界不可观测。改为每次保存都走带返回码的
    # SaveActiveDocument，并以 mtime 前进验证落盘，静默未写盘即失败。
    before_mtime_ns = target.stat().st_mtime_ns if target.is_file() else None
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
    after_mtime_ns = target.stat().st_mtime_ns
    if before_mtime_ns is not None and after_mtime_ns == before_mtime_ns:
        raise RuntimeError(
            f"Vectorworks save did not write the controlled document: {target}"
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
    _assert_object_layer(vs, handle, _AUTHORIZED_LAYER, object_name=_object_name(operation))
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


def _read_mesh_vertices(vs: Any, handle: Any) -> tuple[list[tuple[float, float, float]], int]:
    count = int(vs.GetMeshVertsCnt(handle))
    if count <= 0:
        raise ValueError("Vectorworks mesh has no vertices")
    vertices: list[tuple[float, float, float]] = []
    index_base = 0
    try:
        first = vs.GetMeshVertex(handle, 0)
    except Exception:
        first = None
    if first is None:
        index_base = 1
        vertices = [
            _semantic_coordinate_tuple(vs.GetMeshVertex(handle, index))
            for index in range(1, count + 1)
        ]
    else:
        vertices.append(_semantic_coordinate_tuple(first))
        for index in range(1, count):
            vertices.append(_semantic_coordinate_tuple(vs.GetMeshVertex(handle, index)))
    return vertices, index_base


def _semantic_coordinate_tuple(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"Vectorworks returned invalid mesh vertex: {value!r}")
    point = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in point):
        raise ValueError(f"Vectorworks returned non-finite mesh vertex: {value!r}")
    return point  # type: ignore[return-value]


def _centroid(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    return tuple(sum(point[index] for point in points) / len(points) for index in range(3))  # type: ignore[return-value]


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.dist(left, right)


def _polygonal_radial_deviation_mm(segments: int, diameter_mm: float) -> float:
    """Mid-edge sagitta of a regular N-gon inscribed in the nominal circle.

    The generated mesh places every ring vertex exactly on the nominal inner or
    outer surface.  Between two adjacent vertices the wall is a straight chord
    inside the nominal circle; its maximum radial deviation (sagitta) is
    ``h = R * (1 - cos(pi / N))``.  This is a property of the chosen
    circumferential discretization, not a numeric readback tolerance and not an
    engineering acceptance threshold.
    """
    radius_mm = diameter_mm / 2.0
    return radius_mm * (1.0 - math.cos(math.pi / segments))


def _face_cycles(
    face: tuple[tuple[float, float, float], ...],
) -> tuple[
    list[tuple[float, float, float]],
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float, float], ...],
]:
    """Return (rounded original cycle, directed canonical cycle, undirected key)."""
    points = [
        (round(point[0], 6), round(point[1], 6), round(point[2], 6)) for point in face
    ]
    if not points:
        return [], (), ()
    rotations = [tuple(points[index:] + points[:index]) for index in range(len(points))]
    reversed_points = list(reversed(points))
    reversed_rotations = [
        tuple(reversed_points[index:] + reversed_points[:index])
        for index in range(len(reversed_points))
    ]
    return points, min(rotations), min([*rotations, *reversed_rotations])


def _verify_mesh_face_topology(
    host_faces: list[tuple[tuple[float, float, float], ...]],
    expected_faces: list[tuple[tuple[float, float, float], ...]],
) -> dict[str, Any]:
    """Check mesh closure, duplicate faces, edge manifoldness and orientation.

    Only usable when the host exposes a non-destructive face readback; the
    Vectorworks 2024 adapter never does (see _read_host_mesh_faces).  Faces are
    compared as directed vertex cycles after rounding to the protocol's
    six-decimal-metre precision, so deleting a face, duplicating a face or
    reversing a face's winding each fail with a distinct diagnostic instead of
    passing on the vertex count alone.  This verifies the geometry implied by
    the face loops, not just the vertex set.
    """
    host_undirected: Counter[tuple[tuple[float, float, float], ...]] = Counter()
    host_directed: Counter[tuple[tuple[float, float, float], ...]] = Counter()
    host_directed_key: dict[
        tuple[tuple[float, float, float], ...], tuple[tuple[float, float, float], ...]
    ] = {}
    host_edges: Counter[
        tuple[tuple[float, float, float], tuple[float, float, float]]
    ] = Counter()
    for face in host_faces:
        points, directed, undirected = _face_cycles(face)
        host_directed[directed] += 1
        host_undirected[undirected] += 1
        host_directed_key[directed] = undirected
        if len(points) < 3:
            continue
        for index, point in enumerate(points):
            following = points[(index + 1) % len(points)]
            if point != following:
                host_edges[(point, following)] += 1
    expected_undirected: Counter[tuple[tuple[float, float, float], ...]] = Counter()
    expected_directed: Counter[tuple[tuple[float, float, float], ...]] = Counter()
    expected_directed_key: dict[
        tuple[tuple[float, float, float], ...], tuple[tuple[float, float, float], ...]
    ] = {}
    for face in expected_faces:
        _points, directed, undirected = _face_cycles(face)
        expected_directed[directed] += 1
        expected_undirected[undirected] += 1
        expected_directed_key[directed] = undirected
    missing = sum((expected_undirected - host_undirected).values())
    duplicate_or_extra = sum((host_undirected - expected_undirected).values())
    reversed_faces = 0
    for undirected in expected_undirected:
        expected_for_key = {
            key for key, value in expected_directed_key.items() if value == undirected
        }
        for directed, count in host_directed.items():
            if host_directed_key[directed] != undirected:
                continue
            if directed not in expected_for_key:
                reversed_faces += count
    boundary_edges = 0
    nonmanifold_edges = 0
    inconsistent_edges = 0
    processed: set[
        tuple[tuple[float, float, float], tuple[float, float, float]]
    ] = set()
    for (point, following), forward_count in host_edges.items():
        key = (point, following) if point <= following else (following, point)
        if key in processed:
            continue
        processed.add(key)
        backward_count = host_edges.get((following, point), 0)
        total = forward_count + backward_count
        if total < 2:
            boundary_edges += 1
        elif total > 2:
            nonmanifold_edges += 1
        elif forward_count != 1 or backward_count != 1:
            inconsistent_edges += 1
    checks = {
        "face_count_matches": len(host_faces) == len(expected_faces),
        "no_missing_faces": missing == 0,
        "no_duplicate_or_extra_faces": duplicate_or_extra == 0,
        "face_orientation_consistent": reversed_faces == 0 and inconsistent_edges == 0,
        "edge_manifold": boundary_edges == 0 and nonmanifold_edges == 0,
    }
    failures: list[str] = []
    if missing:
        failures.append(f"{missing} expected mesh faces are missing from the host readback")
    if duplicate_or_extra:
        failures.append(
            f"{duplicate_or_extra} host mesh faces are duplicated or not part of the expected closed mesh"
        )
    if reversed_faces:
        failures.append(f"{reversed_faces} host mesh faces have reversed or inconsistent winding")
    if boundary_edges:
        failures.append(f"{boundary_edges} mesh edges are not shared by exactly two faces")
    if nonmanifold_edges:
        failures.append(f"{nonmanifold_edges} mesh edges are shared by more than two faces")
    if inconsistent_edges:
        failures.append(f"{inconsistent_edges} mesh edges are traversed twice in the same direction")
    return {
        "status": "verified" if all(checks.values()) else "failed",
        "method": "host_face_readback",
        "scope": _MESH_TOPOLOGY_SCOPE,
        "face_count": len(host_faces),
        "checks": checks,
        "failures": failures[:12],
    }


def _read_host_mesh_faces(
    vs: Any, handle: Any
) -> list[tuple[tuple[float, float, float], ...]] | None:
    """Return host mesh face loops when the host exposes a non-destructive API.

    Vectorworks 2024 does not.  The official vs.py API surface (2865 functions)
    contains only GetMeshVertsCnt, GetMeshVertex, SetMeshVertex and MeshToGroup
    for meshes; MeshToGroup destructively converts the mesh to a group of 3D
    polygons, so it cannot be used for readback.  A future host adapter must be
    added here explicitly together with its documented face semantics.
    """
    del vs, handle
    return None


def _topology_unverified(reason: str) -> dict[str, Any]:
    return {
        "status": "unverified",
        "method": "no_nondestructive_host_face_api",
        "scope": _MESH_TOPOLOGY_SCOPE,
        "checks": {},
        "face_count": None,
        "failures": [],
        "reason": reason,
    }


def _pipe_object_status(dimension_status: str, topology_status: str) -> str:
    if "failed" in {dimension_status, topology_status}:
        return "failed"
    if dimension_status == "verified" and topology_status == "verified":
        return "verified"
    if dimension_status == "verified" and topology_status == "unverified":
        return "dimension_verified_topology_unverified"
    return "unverified"


def _measure_pipe_mesh(vs: Any, handle: Any, operation: dict[str, Any]) -> dict[str, Any]:
    expected_start, expected_end = _pipe_axis_points(operation)
    expected_length = _distance(expected_start, expected_end)
    expected_inner_mm = _positive_finite(operation.get("inner_diameter_mm"), "inner_diameter_mm")
    expected_outer_mm = _positive_finite(operation.get("outer_diameter_mm"), "outer_diameter_mm")
    expected_nominal_mm = _positive_finite(operation.get("nominal_diameter_mm"), "nominal_diameter_mm")
    segments = int(operation["circumferential_segments"])
    tolerance_m = _PIPE_GEOMETRY_TOLERANCE_M
    tolerance_mm = tolerance_m * 1000.0
    expected = {
        "axis_start_m": {"x_m": expected_start[0], "y_m": expected_start[1], "z_m": expected_start[2]},
        "axis_end_m": {"x_m": expected_end[0], "y_m": expected_end[1], "z_m": expected_end[2]},
        "length_m": expected_length,
        "nominal_diameter_mm": expected_nominal_mm,
        "inner_diameter_mm": expected_inner_mm,
        "outer_diameter_mm": expected_outer_mm,
        "circumferential_segments": segments,
    }
    discretization = {
        "circumferential_segments": segments,
        "radial_deviation_mm": {
            "inner": round(_polygonal_radial_deviation_mm(segments, expected_inner_mm), 6),
            "outer": round(_polygonal_radial_deviation_mm(segments, expected_outer_mm), 6),
        },
        "basis": (
            "regular N-gon mid-edge sagitta h = R*(1-cos(pi/N)); ring vertices lie "
            "exactly on the nominal circle and the chordal wall deviates inward by at most h"
        ),
        "is_engineering_tolerance": False,
    }
    base = {
        "stable_id": operation["object_id"],
        "object_type": "pipe_segment",
        "geometry_kind": operation.get("geometry_kind"),
        "host_handle": vs.GetName(handle),
        "expected": expected,
        "discretization": discretization,
        "readback_tolerance_m": tolerance_m,
        "readback_tolerance_basis": _READBACK_TOLERANCE_BASIS,
    }

    def _early_failure(
        dimension_status: str, dimension_checks: dict[str, Any], failures: list[str]
    ) -> dict[str, Any]:
        topology = _topology_unverified(_MESH_TOPOLOGY_UNAVAILABLE_REASON)
        return {
            **base,
            "measured": None,
            "dimension_status": dimension_status,
            "dimension_checks": dimension_checks,
            "topology_status": topology["status"],
            "topology_checks": topology["checks"],
            "topology_method": topology["method"],
            "topology_scope": topology["scope"],
            "topology_reason": topology["reason"],
            "topology_face_count": topology["face_count"],
            "status": _pipe_object_status(dimension_status, topology["status"]),
            "failures": failures,
        }

    try:
        object_type = int(vs.GetTypeN(handle))
    except Exception as exc:
        return _early_failure(
            "unverified",
            {"object_type_mesh": False},
            [f"GetTypeN failed: {exc}"],
        )
    if object_type != _PIPE_MESH_OBJECT_TYPE:
        return _early_failure(
            "failed",
            {"object_type_mesh": False},
            [f"object type is {object_type}, expected mesh type {_PIPE_MESH_OBJECT_TYPE}"],
        )
    try:
        vertices, index_base = _read_mesh_vertices(vs, handle)
    except Exception as exc:
        return _early_failure(
            "unverified",
            {"object_type_mesh": True},
            [f"mesh vertex readback failed: {exc}"],
        )
    direction = tuple(b - a for a, b in _zip_strict(expected_start, expected_end))
    axis = tuple(value / expected_length for value in direction)
    inner_radius = expected_inner_mm / 2000.0
    outer_radius = expected_outer_mm / 2000.0
    groups: dict[str, list[tuple[float, float, float]]] = {
        "outer_start": [], "inner_start": [], "outer_end": [], "inner_end": []
    }
    failures: list[str] = []
    failure_count = 0
    max_end_error = 0.0
    max_radial_error = 0.0
    for vertex in vertices:
        offset = tuple(value - start for value, start in _zip_strict(vertex, expected_start))
        along = _dot(offset, axis)
        perpendicular = tuple(value - along * axis_value for value, axis_value in _zip_strict(offset, axis))
        radial = math.sqrt(sum(value * value for value in perpendicular))
        end_error = min(abs(along), abs(along - expected_length))
        radial_error = min(abs(radial - inner_radius), abs(radial - outer_radius))
        max_end_error = max(max_end_error, end_error)
        max_radial_error = max(max_radial_error, radial_error)
        if end_error > tolerance_m:
            failure_count += 1
            if len(failures) < 8:
                failures.append(
                    f"vertex off end planes: along={along:.9g} m error={end_error * 1000:.6g} mm"
                )
            continue
        if radial_error > tolerance_m:
            failure_count += 1
            if len(failures) < 8:
                failures.append(
                    f"vertex off inner/outer surfaces: radius={radial:.9g} m error={radial_error * 1000:.6g} mm"
                )
            continue
        station = "start" if abs(along) <= tolerance_m else "end"
        if abs(radial - outer_radius) <= tolerance_m:
            groups[f"outer_{station}"].append(vertex)
        else:
            groups[f"inner_{station}"].append(vertex)
    for name, points in groups.items():
        if len(points) < 3:
            failures.append(f"{name} ring has fewer than 3 measurable vertices: {len(points)}")
    outer_start_centroid = _centroid(groups["outer_start"]) if groups["outer_start"] else None
    outer_end_centroid = _centroid(groups["outer_end"]) if groups["outer_end"] else None
    inner_start_centroid = _centroid(groups["inner_start"]) if groups["inner_start"] else None
    inner_end_centroid = _centroid(groups["inner_end"]) if groups["inner_end"] else None
    measured: dict[str, Any] | None = None
    dimension_checks = {
        "object_type_mesh": True,
        "vertex_count_at_least_4_rings": len(vertices) >= 4 * segments,
        "both_ends_present": bool(groups["outer_start"] and groups["outer_end"]),
        "both_radii_present": bool(
            groups["inner_start"] and groups["inner_end"] and groups["outer_start"] and groups["outer_end"]
        ),
        "vertices_on_end_planes": max_end_error <= tolerance_m,
        "vertices_on_inner_outer_surfaces": max_radial_error <= tolerance_m,
    }
    if outer_start_centroid is not None and outer_end_centroid is not None and inner_start_centroid is not None and inner_end_centroid is not None:
        measured_length = _distance(outer_start_centroid, outer_end_centroid)
        measured_axis = tuple(
            (b - a) / measured_length
            for a, b in _zip_strict(outer_start_centroid, outer_end_centroid)
        )
        outer_radius_measured = (
            sum(_distance(vertex, outer_start_centroid) for vertex in groups["outer_start"])
            + sum(_distance(vertex, outer_end_centroid) for vertex in groups["outer_end"])
        ) / (len(groups["outer_start"]) + len(groups["outer_end"]))
        inner_radius_measured = (
            sum(_distance(vertex, inner_start_centroid) for vertex in groups["inner_start"])
            + sum(_distance(vertex, inner_end_centroid) for vertex in groups["inner_end"])
        ) / (len(groups["inner_start"]) + len(groups["inner_end"]))
        centroid_end_error = max(
            _distance(outer_start_centroid, expected_start),
            _distance(outer_end_centroid, expected_end),
            _distance(inner_start_centroid, expected_start),
            _distance(inner_end_centroid, expected_end),
        )
        dimension_checks.update(
            axis_start=centroid_end_error <= tolerance_m,
            axis_end=centroid_end_error <= tolerance_m,
            axis_direction=max(abs(a - b) for a, b in _zip_strict(measured_axis, axis)) <= 1e-6,
            length=abs(measured_length - expected_length) <= tolerance_m,
            inner_diameter=abs(inner_radius_measured * 2000.0 - expected_inner_mm) <= tolerance_mm,
            outer_diameter=abs(outer_radius_measured * 2000.0 - expected_outer_mm) <= tolerance_mm,
        )
        measured = {
            "vertex_count": len(vertices),
            "vertex_index_base": index_base,
            "ring_vertex_counts": {name: len(points) for name, points in groups.items()},
            "axis_start_m": {
                "x_m": _semantic_number(outer_start_centroid[0]),
                "y_m": _semantic_number(outer_start_centroid[1]),
                "z_m": _semantic_number(outer_start_centroid[2]),
            },
            "axis_end_m": {
                "x_m": _semantic_number(outer_end_centroid[0]),
                "y_m": _semantic_number(outer_end_centroid[1]),
                "z_m": _semantic_number(outer_end_centroid[2]),
            },
            "length_m": _semantic_number(measured_length),
            "axis_direction": {"x": measured_axis[0], "y": measured_axis[1], "z": measured_axis[2]},
            "inner_diameter_mm": _semantic_number(inner_radius_measured * 2000.0),
            "outer_diameter_mm": _semantic_number(outer_radius_measured * 2000.0),
            "max_end_error_mm": max_end_error * 1000.0,
            "max_radial_error_mm": max_radial_error * 1000.0,
            "max_centroid_error_mm": centroid_end_error * 1000.0,
            "vertex_sha256": hashlib.sha256(
                json.dumps(
                    sorted(tuple(round(value, 6) for value in vertex) for vertex in vertices),
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "sample_vertices": [
                {"x_m": vertex[0], "y_m": vertex[1], "z_m": vertex[2]}
                for vertex in vertices[:4]
            ],
        }
    else:
        dimension_checks.update(axis_start=False, axis_end=False, axis_direction=False, length=False, inner_diameter=False, outer_diameter=False)
    if failure_count:
        failures.insert(0, f"{failure_count} mesh vertices were outside the expected surfaces/end planes")
    dimension_status = "verified" if all(dimension_checks.values()) and not failures else "failed"
    host_faces = _read_host_mesh_faces(vs, handle)
    if host_faces is not None:
        topology = _verify_mesh_face_topology(host_faces, _pipe_mesh_faces(operation))
        topology_failures = [f"topology: {item}" for item in topology["failures"]]
    else:
        topology = _topology_unverified(_MESH_TOPOLOGY_UNAVAILABLE_REASON)
        topology_failures = []
    if topology["status"] == "failed":
        failures.extend(topology_failures)
    topology_status = topology["status"]
    return {
        **base,
        "measured": measured,
        "dimension_status": dimension_status,
        "dimension_checks": dimension_checks,
        "topology_status": topology_status,
        "topology_checks": topology["checks"],
        "topology_method": topology["method"],
        "topology_scope": topology["scope"],
        "topology_reason": topology.get("reason"),
        "topology_face_count": topology.get("face_count"),
        "status": _pipe_object_status(dimension_status, topology_status),
        "failures": failures[:12],
    }


def _project_geometry_snapshot(plan: dict[str, Any], vs: Any) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for operation in plan["operations"]:
        if operation.get("operation") != "create_object" or operation.get("object_type") != "pipe_segment":
            continue
        handle = vs.GetObject(_object_name(operation))
        if handle is None:
            objects.append(
                {
                    "stable_id": operation["object_id"],
                    "object_type": "pipe_segment",
                    "geometry_kind": operation.get("geometry_kind"),
                    "expected": {
                        "axis_start_m": operation["centerline"][0],
                        "axis_end_m": operation["centerline"][1],
                        "inner_diameter_mm": operation.get("inner_diameter_mm"),
                        "outer_diameter_mm": operation.get("outer_diameter_mm"),
                    },
                    "measured": None,
                    "dimension_status": "failed",
                    "dimension_checks": {"object_exists": False},
                    "topology_status": "unverified",
                    "topology_checks": {},
                    "topology_method": "no_nondestructive_host_face_api",
                    "topology_scope": _MESH_TOPOLOGY_SCOPE,
                    "topology_reason": _MESH_TOPOLOGY_UNAVAILABLE_REASON,
                    "topology_face_count": None,
                    "readback_tolerance_m": _PIPE_GEOMETRY_TOLERANCE_M,
                    "readback_tolerance_basis": _READBACK_TOLERANCE_BASIS,
                    "status": "failed",
                    "failures": ["pipe mesh object is missing from the host document"],
                }
            )
            continue
        objects.append(_measure_pipe_mesh(vs, handle, operation))
    statuses = {item["status"] for item in objects}
    if not objects:
        status = "not_applicable"
    elif statuses == {"verified"}:
        status = "verified"
    elif "failed" in statuses:
        status = "failed"
    elif statuses <= {"dimension_verified_topology_unverified"}:
        status = "dimension_verified_topology_unverified"
    else:
        status = "unverified"
    topology_statuses = {item["topology_status"] for item in objects}
    if not objects:
        topology_status = "not_applicable"
    elif topology_statuses == {"verified"}:
        topology_status = "verified"
    elif "failed" in topology_statuses:
        topology_status = "failed"
    else:
        topology_status = "unverified"
    return {
        "geometry_snapshot_version": _GEOMETRY_SNAPSHOT_VERSION,
        "host": "vectorworks",
        "host_adapter": "vectorworks-mesh-vertices-1.1.0",
        "source_ir_id": plan["ir_id"],
        "source_ir_sha256": plan["compiled_ir_sha256"],
        "units": "m",
        "readback_tolerance_m": _PIPE_GEOMETRY_TOLERANCE_M,
        "readback_tolerance_basis": _READBACK_TOLERANCE_BASIS,
        "topology_status": topology_status,
        "topology_scope": _MESH_TOPOLOGY_SCOPE,
        "objects": sorted(objects, key=lambda item: item["stable_id"]),
        "status": status,
        "limitations": [
            "Geometry is measured from actual host mesh vertices, not from plan values or records.",
            "Closed-solid topology (" + _MESH_TOPOLOGY_SCOPE + ") is not read back and stays unverified: " + _MESH_TOPOLOGY_UNAVAILABLE_REASON + ".",
            "Curved surfaces are polygonal meshes; the per-object discretization sagitta describes the chordal deviation from the nominal circle and is not an engineering acceptance threshold.",
            "The mesh has inner/outer surfaces and annular end caps; it is not a native pipe/boolean solid or a hydraulic model.",
            "Node/manhole objects remain schematic and are not part of the pipe solid measurement.",
        ],
    }


def _project_semantic_snapshot(
    plan: dict[str, Any],
    vs: Any,
    geometry_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        _assert_object_layer(vs, handle, _AUTHORIZED_LAYER, object_name=_object_name(operation))
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
            geometry_object = next(
                (
                    item
                    for item in (geometry_snapshot or {}).get("objects", [])
                    if item.get("stable_id") == stable_id
                ),
                None,
            )
            measured = (geometry_object or {}).get("measured") or {}
            if not measured.get("axis_start_m") or not measured.get("axis_end_m"):
                raise ValueError(
                    f"semantic projection cannot read measured pipe axis for {stable_id}; "
                    "geometry readback is unavailable"
                )
            centerline = [measured["axis_start_m"], measured["axis_end_m"]]
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
                "diameter_mm": records.get("NominalDiameterMM", records.get("DiameterMM")),
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


def _geometry_caveats(geometry_snapshot: dict[str, Any] | None) -> list[str]:
    if not geometry_snapshot:
        return []
    if geometry_snapshot.get("status") == "dimension_verified_topology_unverified":
        return [
            "mesh dimensions were verified against actual host vertices; closed-solid "
            "topology (" + _MESH_TOPOLOGY_SCOPE + ") is unverified: " + _MESH_TOPOLOGY_UNAVAILABLE_REASON
        ]
    return []


def _receipt(
    plan: dict[str, Any],
    state: dict[str, Any],
    errors: list[str],
    semantic_snapshot: dict[str, Any] | None,
    geometry_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied = set(state.get("applied_operation_ids") or [])
    geometry_status = (geometry_snapshot or {}).get("status", "unverified")
    geometry_ok = geometry_status in {"verified", "not_applicable"}
    complete = (
        len(applied) == len(plan["operations"])
        and semantic_snapshot is not None
        and geometry_snapshot is not None
        and geometry_ok
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
        "geometry_snapshot": geometry_snapshot,
        "geometry_status": geometry_status,
        "geometry_caveats": _geometry_caveats(geometry_snapshot),
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
    # Check host mesh APIs before blank-document/unit/layer side effects.
    _validate_pipe_solid_host_capabilities(vs, plan)
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
    elif active_document and not _is_unnamed_document_reference(active_document):
        raise RuntimeError(
            "Vectorworks first execution requires an unnamed blank document before any "
            f"typed host side effect: active={active_document}"
        )
    else:
        _assert_blank_document(vs)

    # Typed plan coordinates are meters. Normalize the active document before any
    # host geometry is created, including recovery runs after a host restart.
    _ensure_document_units_meters(vs)

    # Ensure the authorized layer exists BEFORE any operations, so that
    # _create_typed_object's vs.Layer() call switches to an existing layer
    # rather than creating one. If vs.Layer() creates a new layer, the
    # subsequent vs.LNewObj() would return the layer handle, not the
    # object handle, causing "Handle variable is NIL" on SetName.
    vs.Layer(_AUTHORIZED_LAYER)
    vs.LNewObj()  # consume the layer handle (if any)

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
    geometry_snapshot = None
    if len(applied) == len(plan["operations"]) and not errors:
        try:
            geometry_snapshot = _project_geometry_snapshot(plan, vs)
            if geometry_snapshot.get("status") not in {
                "verified",
                "not_applicable",
                "dimension_verified_topology_unverified",
            }:
                errors.append(
                    "geometry_readback: "
                    + json.dumps(geometry_snapshot.get("objects", []), ensure_ascii=False)[:1200]
                )
        except Exception as exc:
            geometry_snapshot = {
                "geometry_snapshot_version": _GEOMETRY_SNAPSHOT_VERSION,
                "host": "vectorworks",
                "host_adapter": "vectorworks-mesh-vertices-1.1.0",
                "source_ir_id": plan["ir_id"],
                "source_ir_sha256": plan["compiled_ir_sha256"],
                "units": "m",
                "readback_tolerance_m": _PIPE_GEOMETRY_TOLERANCE_M,
                "readback_tolerance_basis": _READBACK_TOLERANCE_BASIS,
                "topology_status": "unverified",
                "topology_scope": _MESH_TOPOLOGY_SCOPE,
                "objects": [],
                "status": "unverified",
                "errors": [f"geometry projection failed: {exc}"],
                "limitations": ["No host mesh geometry could be read; no measurement was fabricated."],
            }
            errors.append(f"geometry_projection: {exc}")
        try:
            semantic_snapshot = _project_semantic_snapshot(plan, vs, geometry_snapshot)
        except Exception as exc:
            errors.append(f"semantic_projection: {exc}")
    receipt = _receipt(plan, state, errors, semantic_snapshot, geometry_snapshot)
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


DEFAULT_IPC_ROOT = Path("D:/devloop/vw_ipc")  # forward slashes: no escaping, Windows-pathlib equivalent



def resolve_ipc_dirs(ipc_root: Path | str | None = None) -> tuple[Path, Path, Path, Path]:
    """IPC 目录约定:固定根目录(OPENBIMAGENT_VW_IPC_ROOT 可覆盖)下的 jobs/ + results/。

    不再依赖 VW 进程 CWD(相对路径在 Program Files 下不可写且路径不可见),
    并返回 heartbeat 路径供外部探测 runner 存活。
    """
    root = Path(ipc_root or os.environ.get("OPENBIMAGENT_VW_IPC_ROOT") or DEFAULT_IPC_ROOT)
    return root, root / "jobs", root / "results", root / "runner_heartbeat.json"


def write_heartbeat(
    heartbeat_path: Path,
    started_at: datetime,
    vw_version: str | None = None,
    *,
    ipc_root: Path | str | None = None,
    stopped_reason: str | None = None,
) -> None:
    """落盘心跳(每秒节流由调用方负责);外部用时间戳判断 runner 是否存活。

    vw_version 由 main 启动时探测一次并缓存,避免每秒 import vs。
    ipc_root 声明 runner 实际轮询的目录,客户端据此校验两端目录契约。
    stopped_reason 仅在受控停止后写入;正常轮询期间不出现该字段。
    """
    payload: dict[str, Any] = {
        "ts": time.time(),
        "started_at": started_at.isoformat(),
        "vw_version": vw_version if vw_version is not None else get_vw_version(),
    }
    if ipc_root is not None:
        payload["ipc_root"] = str(Path(ipc_root).resolve())
    if stopped_reason is not None:
        payload["stopped_reason"] = stopped_reason
    heartbeat_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


_LOG_PATH: Path | None = None


def log_to_file(message: str) -> None:
    """追加一行带时间戳的运行日志到 IPC 根下 runner_log.txt(可 tail -f 实时观测)。

    VW UI 被轮询循环占用时 print 不可见——文件日志是唯一可靠观测面;
    写失败静默跳过,绝不影响主循环。
    """
    if _LOG_PATH is None:
        return
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} [runner] {message}\n")
    except Exception:
        pass


def run_session(
    *,
    ipc_root: Path | str | None = None,
    max_seconds: float = 0.0,
    stop_file: Path | None = None,
    poll_interval: float = 0.1,
    heartbeat_interval: float = 1.0,
    alive_log_interval: float = 5.0,
    sleep: Any = time.sleep,
    clock: Any = time.time,
    max_iterations: int | None = None,
) -> str:
    """有界轮询会话;返回停止原因(写日志/心跳,便于外部判断与测试断言)。

    停止语义:stop 文件与时限只在两轮轮询之间检查——正在执行的宿主 API
    调用不会被中断。正常停止不得强杀或关闭 Vectorworks:
      - 停止等待:写入 stop 文件,runner 在当前轮询轮次结束后退出;
      - 有界会话:max_seconds 到时自动退出;
      - 取消执行:进行中的宿主 API 调用不可被取消,只能等它返回。
    """
    global _LOG_PATH
    root, jobs_dir, results_dir, heartbeat_path = resolve_ipc_dirs(ipc_root)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = root / "runner_log.txt"
    started_at = datetime.now()
    vw_version = get_vw_version()
    log_to_file(
        f"started vw_version={vw_version} ipc_root={root.resolve()} "
        f"max_seconds={max_seconds} stop_file={stop_file}"
    )
    session_started = clock()
    last_heartbeat = 0.0
    last_alive_log = 0.0
    iterations = 0
    total_consumed = 0
    stopped_reason = "unknown"
    write_heartbeat(heartbeat_path, started_at, vw_version, ipc_root=root)
    try:
        while True:
            if stop_file is not None and stop_file.exists():
                stopped_reason = f"stop file observed: {stop_file}"
                break
            if max_seconds > 0 and clock() - session_started >= max_seconds:
                stopped_reason = f"bounded session limit reached: {max_seconds:.1f}s"
                break
            if max_iterations is not None and iterations >= max_iterations:
                stopped_reason = "max_iterations reached"
                break
            try:
                processed = poll_jobs_once(jobs_dir, results_dir)
                if processed:
                    total_consumed += len(processed)
                    log_to_file(f"consumed jobs {processed} (total={total_consumed})")
            except Exception:
                # 单轮异常只记录不退出:坏 job 不能杀死整个 runner
                log_to_file(f"poll error (continuing): {traceback.format_exc(limit=3)}")
            iterations += 1
            now = clock()
            if now - last_heartbeat >= heartbeat_interval:
                write_heartbeat(heartbeat_path, started_at, vw_version, ipc_root=root)
                last_heartbeat = now
            if now - last_alive_log >= alive_log_interval:
                log_to_file(f"alive iter={iterations} consumed_total={total_consumed}")
                last_alive_log = now
            sleep(poll_interval)
    except KeyboardInterrupt:
        stopped_reason = "KeyboardInterrupt"
    write_heartbeat(
        heartbeat_path, started_at, vw_version, ipc_root=root, stopped_reason=stopped_reason
    )
    log_to_file(f"stopped reason={stopped_reason}")
    return stopped_reason


def main() -> None:
    """runner 主循环:轮询 jobs/,100ms 间隔;每 ~1s 心跳 + ~5s 存活日志。

    在 VW 内嵌 Python 中运行——UI 会显示"未响应"(脚本线程被轮询循环占用),
    这是预期形态:循环仍在消费 jobs 并写 results。
    停止方式(正常停止不得强杀/关闭 VW):
      - 停止等待:写入 <ipc_root>/stop_runner(或 OPENBIMAGENT_VW_RUNNER_STOP_FILE),
        runner 在下一轮轮询前退出并把 stopped_reason 写进心跳;
      - 有界会话:设置 OPENBIMAGENT_VW_RUNNER_MAX_SECONDS=N 秒,到时自动退出;
      - 取消执行:当前宿主 API 调用进行中时无法被中断,只能等它返回。
    观测:tail -f <ipc_root>/runner_log.txt(启动/存活/逐 job 消费/停止记录)。
    """
    root, _jobs_dir, _results_dir, _heartbeat_path = resolve_ipc_dirs()
    raw_max = os.environ.get("OPENBIMAGENT_VW_RUNNER_MAX_SECONDS", "").strip()
    try:
        max_seconds = float(raw_max) if raw_max else 0.0
    except ValueError:
        max_seconds = 0.0
    stop_file_env = os.environ.get("OPENBIMAGENT_VW_RUNNER_STOP_FILE", "").strip()
    stop_file = Path(stop_file_env) if stop_file_env else root / "stop_runner"
    print("VW MCP runner started", flush=True)
    print(f"  ipc_root:  {root.resolve()}", flush=True)
    print(f"  stop_file: {stop_file}", flush=True)
    print(
        "  bounded:   "
        + (
            f"{max_seconds:.0f}s (OPENBIMAGENT_VW_RUNNER_MAX_SECONDS)"
            if max_seconds > 0
            else "unbounded until stop file is written"
        ),
        flush=True,
    )
    stopped_reason = run_session(ipc_root=root, max_seconds=max_seconds, stop_file=stop_file)
    print(f"VW MCP runner stopped: {stopped_reason}", flush=True)


if __name__ == "__main__":
    main()
