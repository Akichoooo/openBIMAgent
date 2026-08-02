"""真实 Blender 5.2 headless 集成测试(1 例;带 skipif 守卫)。

任务要求:headless 起 fork addon → connect → describe_capabilities → 建方块 → 截图非黑,
带 skipif 守卫。这是唯一允许起真实 Blender 进程的测试;其余 blender 相关单测全 mock。

跳过条件(任一即跳过,不报失败):
- 默认 Blender 路径 D:/devloop/blender/blender.exe 不存在;
- 环境变量 OPENBIMAGENT_RUN_REAL_BLENDER=1 未设置(默认不跑,避免 CI/常规 pytest 拖慢)。

跑法:
    OPENBIMAGENT_RUN_REAL_BLENDER=1 uv run pytest tests/test_blender_integration_real.py -v

实测耗时:Blender 5.2 background 首帧着色器编译 ~19s + ping/cube/screenshot ~5s,
总 ~30s;timeout 设 180s 与 fork server 默认对齐。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from openbimagent.assembly.blender_plan import BlenderBuilder
from openbimagent.assembly.semantic_snapshot import SemanticSnapshot
from openbimagent.mcp_clients.blender import BlenderMCPClient
from test_compiled_utility_ir import solved_payload

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = PROJECT_ROOT / "mcp_servers" / "blender_mcp" / "addon.py"
BLENDER_EXE = Path(os.environ.get("OPENBIMAGENT_BLENDER_EXE", r"D:\devloop\blender\blender.exe"))
BLENDER_PORT = int(os.environ.get("OPENBIMAGENT_BLENDER_PORT", "9887"))
BLENDER_HOST = "127.0.0.1"
LAUNCH_WAIT_S = 120  # 与 run_fork_tests.py 一致;首帧着色器编译 ~19s
RUN_REAL = os.environ.get("OPENBIMAGENT_RUN_REAL_BLENDER") == "1"
REAL_AUTHORIZED_ROOT = Path(
    os.environ.get("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", r"D:\devloop\G6_Test")
).resolve()

requires_real_blender = pytest.mark.skipif(
    not RUN_REAL or not BLENDER_EXE.is_file(),
    reason=(
        f"跳过真实 Blender 集成测试(OPENBIMAGENT_RUN_REAL_BLENDER={RUN_REAL}, "
        f"BLENDER_EXE 存在={BLENDER_EXE.is_file()});"
        "显式开启:OPENBIMAGENT_RUN_REAL_BLENDER=1 uv run pytest tests/test_blender_integration_real.py -v"
    ),
)


def _wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    """轮询 TCP 端口直到 addon 接受连接(或超时)。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(1.0)
    return False


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def headless_blender(tmp_path):
    """起 headless Blender + fork addon,等端口起来;teardown 时杀进程。

    snapshot 目录指向 tmp_path 避免污染 relay_workspace;stdout 落 tmp_path 便于排查。
    """
    if not RUN_REAL or not BLENDER_EXE.is_file():
        pytest.skip("跳过真实 Blender 集成测试")

    # 拒绝占用端口(避免测到别人的 server;与 run_fork_tests.py 同规则)
    try:
        with socket.create_connection((BLENDER_HOST, BLENDER_PORT), timeout=1.0):
            pytest.skip(f"端口 {BLENDER_PORT} 已被占用,拒绝测试别人家的 server")
    except OSError:
        pass

    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "blender_stdout.log"

    env = dict(os.environ)
    env["OPENBIMAGENT_BLENDER_PORT"] = str(BLENDER_PORT)
    env["OPENBIMAGENT_SNAPSHOT_DIR"] = str(snapshot_dir)
    env["OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT"] = str(REAL_AUTHORIZED_ROOT)

    cmd = [str(BLENDER_EXE), "--background", "--factory-startup", "--python", str(ADDON_PATH)]
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)

    try:
        if not _wait_for_port(BLENDER_HOST, BLENDER_PORT, LAUNCH_WAIT_S):
            _kill_proc(proc)
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            pytest.fail(f"Blender 端口 {BLENDER_PORT} 未在 {LAUNCH_WAIT_S}s 内起来\nstdout 尾部:\n{tail}")
        yield proc
    finally:
        _kill_proc(proc)


@requires_real_blender
def test_real_blender_connect_describe_cube_screenshot(headless_blender, tmp_path) -> None:
    """端到端:socket connect → ping → describe_capabilities → execute_code(建方块)→ 截图非黑。

    fork 改造 e(健康探针)、h(describe_capabilities)、c(快照+AST)、f(截图非黑)联合验证。
    不调 set_editable_scope/batch_render/turntable(那些在 socket 单测里已 mock 验证过协议)。
    """
    client = BlenderMCPClient.transport_socket(host=BLENDER_HOST, port=BLENDER_PORT, timeout=180.0)

    async def run() -> None:
        await client.connect()
        try:
            # 1. describe_capabilities:fork 改造 h,必须返回 server/host/tools/limits
            caps = await client.describe_capabilities()
            assert "host" in caps, f"describe_capabilities 缺 host: {caps}"
            assert "tools" in caps, f"describe_capabilities 缺 tools: {caps}"
            # host 段含合法渲染引擎枚举(5.2 应有 BLENDER_EEVEE)
            engines = caps.get("host", {}).get("render_engines_legal") or []
            assert "BLENDER_EEVEE" in engines, f"5.2 合法引擎应含 BLENDER_EEVEE,实收 {engines}"

            # 2. 范围锁:set_editable_scope 锁定 M0Cube(先解锁再上锁,防上批残留)
            await client.set_editable_scope(objects=["M0Cube"], enabled=True)

            # 3. execute_code 建方块(fork 改造 c:AST allowlist + 快照)
            code = (
                "import bpy\n"
                "bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))\n"
                "obj = bpy.context.active_object\n"
                "obj.name = 'M0Cube'\n"
                # 加个相机 + 灯,确保 render_fallback 能出非黑图
                "bpy.ops.object.camera_add(location=(5, -5, 5), rotation=(1.1, 0, 0.785))\n"
                "bpy.context.scene.camera = bpy.context.active_object\n"
                "bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))\n"
                "bpy.context.scene.render.engine = 'BLENDER_EEVEE'\n"
                "bpy.context.scene.render.resolution_x = 256\n"
                "bpy.context.scene.render.resolution_y = 256\n"
            )
            exec_result = await client.execute_code(code)
            assert exec_result.get("executed") is True, f"execute_code 未执行成功: {exec_result}"
            assert exec_result.get("snapshot"), "execute_code 应返回快照路径(fork 改造 c)"
            assert exec_result.get("scope_checked") is True, "execute_code 应通过范围锁校验"

            # 4. 截图非黑(fork 改造 f:brightness ≥ 0.01)
            shot_path = tmp_path / "real_cube_viewport.png"
            shot = await client.screenshot_or_render(filepath=str(shot_path), max_size=256)
            assert shot["brightness"] >= 0.01, f"截图黑图:brightness={shot['brightness']} < 0.01"
            assert shot_path.is_file() and shot_path.stat().st_size > 1000, "截图文件未落盘或过小"
            # method 应为 render_fallback(background 下无 View3D region,走 bpy.ops.render.render)
            assert shot["method"] == "render_fallback", f"background 下应走 render_fallback,实收 {shot['method']}"
        finally:
            await client.close()

    asyncio.run(run())


@requires_real_blender
def test_real_blender_typed_municipal_plan(headless_blender) -> None:
    """G6 typed path: approved plan -> controlled save -> receipt -> real scene projection."""
    del headless_blender
    output = REAL_AUTHORIZED_ROOT / "openbimagent_g6_typed.blend"
    sidecar = output.with_suffix(output.suffix + ".openbimagent.json")
    plan = BlenderBuilder().build(solved_payload())
    existing_receipt = None
    before_hashes = None
    if output.exists() or sidecar.exists():
        assert output.is_file() and sidecar.is_file(), "受控输出与 sidecar 必须同时存在"
        state = json.loads(sidecar.read_text(encoding="utf-8"))
        assert state["canonical_sha256"] == plan.canonical_sha256
        assert state["idempotency_key"] == plan.idempotency_key
        existing_receipt = state.get("receipt")
        assert existing_receipt is not None, "已有受控输出必须有 completed receipt"
        before_hashes = (_sha256(output), _sha256(sidecar))
    client = BlenderMCPClient.transport_socket(
        host=BLENDER_HOST,
        port=BLENDER_PORT,
        timeout=180.0,
        authorized_root=REAL_AUTHORIZED_ROOT,
    )

    async def run() -> None:
        await client.connect()
        try:
            caps = await client.describe_capabilities()
            typed = caps.get("typed_execution")
            assert typed and typed["controlled_save"] is True
            assert typed["idempotent_receipts"] is True
            receipt = await client.execute_plan(
                plan,
                output_path=output,
                approved=True,
                capabilities=caps,
            )
            assert receipt.status.value == "completed"
            assert output.is_file() and output.stat().st_size > 0
            assert sidecar.is_file()
            snapshot = SemanticSnapshot.model_validate(receipt.semantic_snapshot)
            assert snapshot.source_ir_sha256 == plan.compiled_ir_sha256
            assert len(snapshot.objects) == 6
            if existing_receipt is not None:
                assert receipt.model_dump(mode="json") == existing_receipt
                assert before_hashes is not None
                assert (_sha256(output), _sha256(sidecar)) == before_hashes
        finally:
            await client.close()

    asyncio.run(run())
