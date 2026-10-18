"""Vectorworks 真机受控执行编排器 (Real-Host Controlled Executor)。

职责：把 typed ``VectorworksExecutionPlan`` 经自研 vectorworks-mcp（stdio）+
文件 IPC 交给 VW 宿主 runner 受控执行。与 Blender 编排器的关键差异：
VW 是 GUI 宿主、无法 headless 拉起——MCP server 子进程由客户端自动启动，
但 **VW 应用必须已运行且已加载 runner.py 并轮询同一 jobs 目录**。

配置契约（三项均须显式提供，缺一即快速失败并给出指引）：
- ``OPENBIMAGENT_VW_JOBS_DIR``     与 VW runner 轮询一致的 jobs 目录
- ``OPENBIMAGENT_VW_RESULTS_DIR``  runner 写回结果的目录
- ``OPENBIMAGENT_VW_AUTHORIZED_ROOT`` 受控输出授权根（.vwx 落盘范围）

安全边界：输出必须在授权根内且以 .vwx 结尾；``approved=True`` 走服务端
handoff/hash/approval 门禁；超时（默认 60s）视为 VW 宿主未运行/未加载 runner。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

VECTORWORKS_HOST_EXECUTOR_VERSION = "0.1"
_CLIENT_TIMEOUT_S = 60.0
_HEARTBEAT_MAX_AGE_S = 120.0


class VectorworksHostExecutionError(RuntimeError):
    """真机执行失败（配置缺失、宿主未运行、门禁拒绝或回执身份不一致）。"""


def _required_env(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise VectorworksHostExecutionError(
            f"缺少环境变量 {name}；Vectorworks 真机执行需同时配置 "
            "OPENBIMAGENT_VW_JOBS_DIR / OPENBIMAGENT_VW_RESULTS_DIR / "
            "OPENBIMAGENT_VW_AUTHORIZED_ROOT（jobs 目录必须与 VW 宿主 runner 轮询一致）"
        )
    return Path(value)


def _validate_ipc_contract(jobs_dir: Path, results_dir: Path) -> None:
    """验证客户端目录与 VW runner 的目录约定一致——不能只设置客户端一侧。

    runner 固定轮询 ``<IPC_ROOT>/jobs`` 并把结果写回 ``<IPC_ROOT>/results``
    （见 mcp_servers/vectorworks_mcp/runner.py 的 resolve_ipc_dirs）。若两端
    指向不同目录，runner 永远不会消费 job，客户端却只能看到 60s 超时。
    """
    if jobs_dir.name != "jobs" or results_dir.name != "results":
        raise VectorworksHostExecutionError(
            "Vectorworks IPC 目录契约不符: OPENBIMAGENT_VW_JOBS_DIR 必须以 jobs 结尾、"
            "OPENBIMAGENT_VW_RESULTS_DIR 必须以 results 结尾（runner 固定轮询 <IPC_ROOT>/jobs）；"
            f"jobs={jobs_dir} results={results_dir}"
        )
    if jobs_dir.parent.resolve() != results_dir.parent.resolve():
        raise VectorworksHostExecutionError(
            "Vectorworks IPC 目录契约不符: jobs 与 results 必须位于同一 IPC 根下；"
            f"jobs={jobs_dir} results={results_dir}"
        )
    env_root = os.environ.get("OPENBIMAGENT_VW_IPC_ROOT", "").strip()
    if env_root and Path(env_root).resolve() != jobs_dir.parent.resolve():
        raise VectorworksHostExecutionError(
            f"OPENBIMAGENT_VW_IPC_ROOT={env_root} 与 jobs/results 的父目录 "
            f"{jobs_dir.parent.resolve()} 不一致；runner 轮询的是 <IPC_ROOT>/jobs，"
            "两端必须使用同一 IPC 根（进程级配置，不要只设置客户端一侧）"
        )


def _check_runner_heartbeat(jobs_dir: Path) -> None:
    """预检 runner 心跳：ipc_root 必须与客户端目录一致、时间戳必须新鲜。

    心跳缺失时不报错（runner 可能正在启动，ping 仍是最终真值）；心跳存在但
    声明了不同的 ipc_root，或长时间未更新，则在等待 60s 超时前快速失败。
    """
    heartbeat = jobs_dir.parent / "runner_heartbeat.json"
    if not heartbeat.is_file():
        return
    try:
        payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VectorworksHostExecutionError(f"runner 心跳文件损坏: {heartbeat}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VectorworksHostExecutionError(f"runner 心跳格式非法: {heartbeat}")
    declared_root = payload.get("ipc_root")
    if declared_root and Path(str(declared_root)).resolve() != jobs_dir.parent.resolve():
        raise VectorworksHostExecutionError(
            f"runner 心跳声明的 ipc_root={declared_root} 与客户端目录 "
            f"{jobs_dir.parent.resolve()} 不一致；VW 中加载的 runner 与客户端配置指向"
            "不同 IPC 根，job 永远不会被消费"
        )
    timestamp = payload.get("ts")
    age = time.time() - float(timestamp) if isinstance(timestamp, (int, float)) else None
    if age is not None and age > _HEARTBEAT_MAX_AGE_S:
        raise VectorworksHostExecutionError(
            f"runner 心跳已过期 ({age:.0f}s > {_HEARTBEAT_MAX_AGE_S:.0f}s): {heartbeat}；"
            "VW 宿主可能未运行或未加载 runner.py（心跳仅在 VW 内运行 runner 后更新）"
        )


def execute_vectorworks_export(
    ir: Any,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """编译 IR → typed plan → vectorworks-mcp → VW 宿主受控执行，返回结构化回执。"""
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder

    jobs_dir = _required_env("OPENBIMAGENT_VW_JOBS_DIR")
    results_dir = _required_env("OPENBIMAGENT_VW_RESULTS_DIR")
    root = _required_env("OPENBIMAGENT_VW_AUTHORIZED_ROOT")
    _validate_ipc_contract(jobs_dir, results_dir)
    _check_runner_heartbeat(jobs_dir)
    root.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    target = Path(output_path) if output_path else root / "openbimagent_vectorworks_export.vwx"
    if not str(target).lower().endswith(".vwx"):
        raise VectorworksHostExecutionError(f"输出必须以 .vwx 结尾: {target}")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise VectorworksHostExecutionError(f"输出路径越界授权根 {root}: {target}") from exc

    plan = VectorworksBuilder().build(ir)
    started = time.monotonic()

    from openbimagent.mcp_clients.vectorworks import (
        VectorworksClientError,
        VectorworksMCPClient,
    )

    async def _run() -> Any:
        client = VectorworksMCPClient(
            jobs_dir=jobs_dir,
            results_dir=results_dir,
            authorized_root=root,
            default_output_path=target,
            timeout=_CLIENT_TIMEOUT_S,
        )
        await client.connect()
        try:
            caps = await client.describe_capabilities()
            return await client.execute_plan(plan, approved=True, capabilities=caps)
        finally:
            await client.close()

    try:
        receipt = asyncio.run(_run())
    except (VectorworksClientError, TimeoutError, OSError) as exc:
        raise VectorworksHostExecutionError(
            f"Vectorworks 执行失败（VW 宿主未运行/runner 未加载/目录不匹配? 超时 "
            f"{_CLIENT_TIMEOUT_S:.0f}s）: {exc}"
        ) from exc

    from openbimagent.deliver.host_delivery import HostDeliveryVerificationError, verify_host_delivery

    try:
        verified = verify_host_delivery(
            ir, receipt, host="vectorworks", output_path=target,
            plan_sha256=plan.canonical_sha256,
        )
    except HostDeliveryVerificationError as exc:
        if exc.kind not in {"semantic", "geometry"} or not (
            receipt.semantic_snapshot or receipt.geometry_snapshot
        ):
            raise VectorworksHostExecutionError(str(exc)) from exc
        snapshot = receipt.geometry_snapshot or {}
        return {
            "status": "partial",
            "host_status": receipt.status.value,
            "output_path": str(receipt.output_path),
            "state_path": str(receipt.state_path),
            "applied_operations": len(receipt.applied_operations),
            "confirmed_objects": len(receipt.confirmed_object_ids),
            "plan_id": receipt.plan_id,
            "plan_sha256": receipt.canonical_sha256,
            "errors": [str(exc), *receipt.errors],
            "semantic_snapshot": receipt.semantic_snapshot,
            "semantic_verified": exc.kind != "semantic",
            "semantic_comparison_report": exc.comparison_report,
            "geometry_snapshot": receipt.geometry_snapshot,
            "geometry_status": receipt.geometry_status or snapshot.get("status", "unverified"),
            "geometry_verified": False,
            "dimension_verified": None,
            "topology_status": snapshot.get("topology_status", "unverified"),
            "geometry_caveats": list(receipt.geometry_caveats),
            "geometry_failures": exc.geometry_failures or [
                item.get("failures")
                for item in snapshot.get("objects", [])
                if item.get("status") != "verified"
            ],
            "geometry_scope": (
                "vectorworks mesh vertices: semantic or geometry verification did not pass; "
                "no pass is claimed"
            ),
            "readback_scope": "host_reported_semantics + attempted mesh vertex measurement",
            "limitations": [
                "The host file and objects may exist, but this delivery is partial.",
                "No IFC, hydraulic or engineering compliance claim is made.",
            ],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    return {
        "status": receipt.status.value,
        "host_status": receipt.status.value,
        "output_path": str(receipt.output_path),
        "state_path": str(receipt.state_path),
        "applied_operations": len(receipt.applied_operations),
        "confirmed_objects": len(receipt.confirmed_object_ids),
        "plan_id": receipt.plan_id,
        "plan_sha256": receipt.canonical_sha256,
        "errors": list(receipt.errors),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        **verified,
    }
