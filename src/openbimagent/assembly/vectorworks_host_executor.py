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
import os
import time
from pathlib import Path
from typing import Any

VECTORWORKS_HOST_EXECUTOR_VERSION = "0.1"
_CLIENT_TIMEOUT_S = 60.0


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

    return {
        "status": receipt.status.value,
        "output_path": str(receipt.output_path),
        "state_path": str(receipt.state_path),
        "applied_operations": len(receipt.applied_operations),
        "confirmed_objects": len(receipt.confirmed_object_ids),
        "plan_id": receipt.plan_id,
        "plan_sha256": receipt.canonical_sha256,
        "errors": list(receipt.errors),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
