"""Blender 真机受控执行编排器 (Real-Host Controlled Executor)。

职责：把 typed ``BlenderExecutionPlan`` 交给真实 headless Blender 5.2 + fork addon
受控执行（启动 → 等端口 → execute_plan → 回执 → 清理进程）。供
``cad_host:blender.execute`` 能力与 ``tools/m3_real_e2e_blender.py`` 复用；
纯内存编排，不持有任何跨调用状态。

安全边界：
- 输出仅允许写入授权根（``OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT``）；
- 端口被占用即拒绝启动（不复用别人家的 server）；
- 无论成败必杀子进程树。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

BLENDER_HOST_EXECUTOR_VERSION = "0.1"
DEFAULT_BLENDER_EXE = Path(r"D:\devloop\blender\blender.exe")
DEFAULT_AUTHORIZED_ROOT = Path(r"D:\devloop\G6_Test")
DEFAULT_EXECUTE_PORT = 9889
_ADDON_PATH = (
    Path(__file__).resolve().parents[3] / "mcp_servers" / "blender_mcp" / "addon.py"
)
_PORT_WAIT_TIMEOUT_S = 120.0
_CLIENT_TIMEOUT_S = 180.0


class BlenderHostExecutionError(RuntimeError):
    """真机执行失败（宿主缺失、端口占用、超时或 addon 拒绝）。"""


def _wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _kill_tree(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


def execute_blender_export(
    ir: Any,
    *,
    output_path: str | Path | None = None,
    blender_exe: str | Path | None = None,
    port: int | None = None,
    authorized_root: str | Path | None = None,
) -> dict[str, Any]:
    """编译 IR → typed plan → headless Blender 5.2 受控执行，返回结构化回执。

    端到端约 10–30s（首帧着色器编译约 19s）；同输出路径重复执行幂等
    （受控保存协议返回与首次一致的 receipt）。
    """
    from openbimagent.assembly.blender_plan import BlenderBuilder

    exe = Path(blender_exe or os.environ.get("OPENBIMAGENT_BLENDER_EXE") or DEFAULT_BLENDER_EXE)
    if not exe.is_file():
        raise BlenderHostExecutionError(
            f"Blender 可执行文件不存在: {exe}（用 OPENBIMAGENT_BLENDER_EXE 指定）"
        )
    root = Path(authorized_root or os.environ.get("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT") or DEFAULT_AUTHORIZED_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    listen_port = int(port or os.environ.get("OPENBIMAGENT_BLENDER_EXECUTE_PORT") or DEFAULT_EXECUTE_PORT)
    target = Path(output_path) if output_path else root / "openbimagent_blender_export.blend"
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise BlenderHostExecutionError(
            f"输出路径越界授权根 {root}: {target}"
        ) from exc

    try:
        with socket.create_connection(("127.0.0.1", listen_port), timeout=1.0):
            raise BlenderHostExecutionError(f"端口 {listen_port} 已被占用，拒绝复用别人的 server")
    except OSError:
        pass

    plan = BlenderBuilder().build(ir)
    sidecar = target.with_suffix(target.suffix + ".openbimagent.json")
    started = time.monotonic()

    tmp = Path(tempfile.mkdtemp(prefix="obmcp_exec_"))
    env = dict(os.environ)
    env["OPENBIMAGENT_BLENDER_PORT"] = str(listen_port)
    env["OPENBIMAGENT_SNAPSHOT_DIR"] = str(tmp / "snapshots")
    env["OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT"] = str(root)
    with (tmp / "blender_stdout.log").open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [str(exe), "--background", "--factory-startup", "--python", str(_ADDON_PATH)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
    try:
        if not _wait_for_port("127.0.0.1", listen_port, _PORT_WAIT_TIMEOUT_S):
            tail = (tmp / "blender_stdout.log").read_text(encoding="utf-8", errors="replace")[-1500:]
            raise BlenderHostExecutionError(
                f"headless Blender 未在 {_PORT_WAIT_TIMEOUT_S:.0f}s 内监听端口 {listen_port}\n{tail}"
            )

        from openbimagent.assembly.semantic_snapshot import SemanticSnapshot
        from openbimagent.mcp_clients.blender import BlenderMCPClient

        async def _run() -> tuple[Any, Any]:
            client = BlenderMCPClient.transport_socket(
                host="127.0.0.1",
                port=listen_port,
                timeout=_CLIENT_TIMEOUT_S,
                authorized_root=root,
            )
            await client.connect()
            try:
                caps = await client.describe_capabilities()
                receipt = await client.execute_plan(
                    plan, output_path=target, approved=True, capabilities=caps
                )
                return receipt, client
            finally:
                await client.close()

        receipt, _ = asyncio.run(_run())
        snapshot = SemanticSnapshot.model_validate(receipt.semantic_snapshot)
        if not target.is_file() or target.stat().st_size == 0:
            raise BlenderHostExecutionError(f"回执 completed 但输出缺失: {target}")
        return {
            "status": receipt.status.value,
            "output_path": str(target),
            "sidecar_path": str(sidecar),
            "output_bytes": target.stat().st_size,
            "objects": len(snapshot.objects),
            "source_ir_sha256": snapshot.source_ir_sha256,
            "plan_sha256": plan.canonical_sha256,
            "blender_port": listen_port,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        _kill_tree(proc)
