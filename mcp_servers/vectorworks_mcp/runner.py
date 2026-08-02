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
            created.add(object_id)
        elif kind == "set_record" and object_id not in created:
            raise ValueError(f"set_record 引用未知对象: {object_id}")
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
    if state.get("state_version") != "1.0":
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


def _create_typed_object(vs: Any, operation: dict[str, Any]) -> Any:
    name = _object_name(operation)
    existing = vs.GetObject(name)
    if existing is not None:
        return existing
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
    return handle


def _set_typed_record(vs: Any, operation: dict[str, Any]) -> None:
    handle = vs.GetObject(_object_name(operation))
    if handle is None:
        raise ValueError(f"set_record 对象不存在: {operation['object_id']}")
    record_name = operation.get("record_name")
    fields = operation.get("record_fields") or []
    if not isinstance(record_name, str) or not record_name or not fields:
        raise ValueError("set_record 缺少 record_name/record_fields")
    if vs.GetObject(record_name) is None:
        for field in fields:
            vs.NewField(record_name, field["field_name"], "", 4, 0)
    vs.SetRecord(handle, record_name)
    for field in fields:
        vs.SetRField(handle, record_name, field["field_name"], str(field["value"]))


def _connect_typed_topology(vs: Any, operation: dict[str, Any]) -> None:
    handle = vs.GetObject(_object_name(operation))
    if handle is None:
        raise ValueError(f"connect_topology 对象不存在: {operation['object_id']}")
    record_name = "OpenBIMAgent_Topology"
    fields = (("StartPortID", operation["references"][0]), ("EndPortID", operation["references"][1]))
    if vs.GetObject(record_name) is None:
        for field_name, _ in fields:
            vs.NewField(record_name, field_name, "", 4, 0)
    vs.SetRecord(handle, record_name)
    for field_name, value in fields:
        vs.SetRField(handle, record_name, field_name, str(value))


def _apply_typed_operation(vs: Any, operation: dict[str, Any]) -> None:
    kind = operation["operation"]
    if kind == "create_object":
        _create_typed_object(vs, operation)
    elif kind == "set_record":
        _set_typed_record(vs, operation)
    elif kind == "connect_topology":
        _connect_typed_topology(vs, operation)
    else:  # pragma: no cover - _validate_typed_plan 已失败关闭
        raise ValueError(f"unsupported typed operation: {kind!r}")


def _receipt(plan: dict[str, Any], state: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    applied = set(state.get("applied_operation_ids") or [])
    complete = len(applied) == len(plan["operations"])
    status = "completed" if complete else "partial"
    return {
        "receipt_id": f"vw-receipt-{plan['canonical_sha256'][:24]}",
        "plan_id": plan["plan_id"],
        "idempotency_key": plan["idempotency_key"],
        "canonical_sha256": plan["canonical_sha256"],
        "status": status,
        "applied_operations": [
            {
                "operation_id": operation["operation_id"],
                "status": "completed",
                "object_id": operation["object_id"],
            }
            for operation in plan["operations"]
            if operation["operation_id"] in applied
        ],
        "confirmed_object_ids": sorted(state.get("confirmed_object_ids") or []),
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
            "state_version": "1.0",
            "plan_id": plan["plan_id"],
            "idempotency_key": plan["idempotency_key"],
            "canonical_sha256": plan["canonical_sha256"],
            "output_path": str(target),
            "applied_operation_ids": [],
            "confirmed_object_ids": [],
        }
        # 在首个宿主写入前先持久化恢复身份，关闭“工程已保存但 sidecar 尚未创建”的窗口。
        _write_execution_state(sidecar, state)
    elif (
        state.get("idempotency_key") != plan["idempotency_key"]
        or state.get("canonical_sha256") != plan["canonical_sha256"]
        or Path(str(state.get("output_path"))).resolve() != target
    ):
        raise ValueError("目标工程 sidecar 与 typed plan 身份冲突")
    if state.get("receipt") and state["receipt"].get("status") == "completed":
        return state["receipt"]

    try:
        import vs  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(f"vs module not available: {exc}") from exc
    applied = set(state["applied_operation_ids"])
    confirmed = set(state["confirmed_object_ids"])
    errors: list[str] = []
    for operation in plan["operations"]:
        if operation["operation_id"] in applied:
            continue
        try:
            _apply_typed_operation(vs, operation)
            target.parent.mkdir(parents=True, exist_ok=True)
            save_result = vs.SaveActiveDocument(str(target))
            if save_result not in (0, None, True):
                raise RuntimeError(f"Vectorworks SaveActiveDocument 失败: code={save_result}")
            applied.add(operation["operation_id"])
            if operation["operation"] == "create_object":
                confirmed.add(operation["object_id"])
            state["applied_operation_ids"] = sorted(applied)
            state["confirmed_object_ids"] = sorted(confirmed)
            _write_execution_state(sidecar, state)
        except Exception as exc:
            errors.append(f"operation={operation['operation_id']}: {exc}")
            break
    receipt = _receipt(plan, state, errors)
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
