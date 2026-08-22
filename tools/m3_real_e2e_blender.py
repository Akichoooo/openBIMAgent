"""M3 真机 E2E 预演：全链路经 registry.invoke 调度 + 策略门治理 + 真实 Blender 5.2 执行。

链路（每步都过微内核，不绕过 registry）：
  1. invoke("solver:self_healing", **build_demo_invocation())  → 真实自愈求解 → CompiledUtilityIR
  2. 策略门演示：cad_host:blender 设为 prompt → 无 confirm 拒绝；confirm=True 放行
  3. invoke("cad_host:blender", ir=...)  → BlenderBuilder → typed BlenderExecutionPlan
  4. headless Blender 5.2 + fork addon → execute_plan 受控落盘 → receipt + semantic snapshot

跑法：.venv/Scripts/python.exe tools/m3_real_e2e_blender.py
前置：D:/devloop/blender/blender.exe 存在；端口 9887 空闲；输出写入授权根 D:/devloop/G6_Test。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbimagent.core.plugin import (  # noqa: E402
    CapabilityPolicyDecision,
    CapabilityPolicyRule,
    PluginPolicyPromptRequiredError,
    create_default_plugin_registry,
)

BLENDER_EXE = Path(os.environ.get("OPENBIMAGENT_BLENDER_EXE", r"D:\devloop\blender\blender.exe"))
ADDON_PATH = PROJECT_ROOT / "mcp_servers" / "blender_mcp" / "addon.py"
BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = int(os.environ.get("OPENBIMAGENT_BLENDER_PORT", "9887"))
AUTHORIZED_ROOT = Path(os.environ.get("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", r"D:\devloop\G6_Test"))
OUTPUT = AUTHORIZED_ROOT / "m3_invoke_e2e.blend"


def _wait_for_port(timeout_s: float = 120.0) -> bool:
    import time

    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            with socket.create_connection((BLENDER_HOST, BLENDER_PORT), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _kill(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


def main() -> None:
    if not BLENDER_EXE.is_file():
        raise SystemExit(f"Blender 不存在: {BLENDER_EXE}")
    try:
        with socket.create_connection((BLENDER_HOST, BLENDER_PORT), timeout=1.0):
            raise SystemExit(f"端口 {BLENDER_PORT} 已被占用，拒绝复用别人的 server")
    except OSError:
        pass

    registry = create_default_plugin_registry()
    print("[1/5] 微内核就绪:", registry.export_inventory()["plugin_count"], "plugins")

    # 2. 真实自愈求解经微内核调度（含规则自检样例验证的 v1.2 规则集）
    from openbimagent.benchmark.self_healing_ablation import build_demo_invocation

    solved = registry.invoke("solver:self_healing", **build_demo_invocation("SH-2"))
    assert solved.converged and solved.final_ir is not None
    ir = solved.final_ir
    print(
        f"[2/5] solver:self_healing → converged in {solved.iterations_spent} iters, "
        f"{len(ir.segments)} segments, resolved {len(solved.resolved_violations)} violations"
    )

    # 3. 策略门治理演示：CAD 宿主写操作需人工确认（Codex execpolicy prompt 语义）
    registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="cad_host:*",
            decision=CapabilityPolicyDecision.PROMPT,
            justification="CAD 宿主写操作产生外部可见 .blend 工件，需人工确认",
        ),
    ])
    try:
        registry.invoke("cad_host:blender", ir=ir)
        raise AssertionError("prompt 策略应拦截无确认调用")
    except PluginPolicyPromptRequiredError as exc:
        print("[3/5] 策略门拦截 ✓:", str(exc)[:80], "...")
    plan = registry.invoke("cad_host:blender", ir=ir, confirm=True)
    print(f"[3/5] cad_host:blender (confirm=True) → typed plan {plan.canonical_sha256[:12]}…")

    # 4. headless Blender 5.2 + fork addon
    AUTHORIZED_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="m3_e2e_"))
    env = dict(os.environ)
    env["OPENBIMAGENT_BLENDER_PORT"] = str(BLENDER_PORT)
    env["OPENBIMAGENT_SNAPSHOT_DIR"] = str(tmp / "snapshots")
    env["OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT"] = str(AUTHORIZED_ROOT)
    proc = subprocess.Popen(
        [str(BLENDER_EXE), "--background", "--factory-startup", "--python", str(ADDON_PATH)],
        stdout=open(tmp / "blender_stdout.log", "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        if not _wait_for_port():
            tail = (tmp / "blender_stdout.log").read_text(encoding="utf-8", errors="replace")[-1500:]
            raise SystemExit(f"Blender 未起来\n{tail}")

        # 5. 受控执行：execute_plan → receipt + semantic snapshot
        from openbimagent.mcp_clients.blender import BlenderMCPClient

        async def execute() -> None:
            client = BlenderMCPClient.transport_socket(
                host=BLENDER_HOST,
                port=BLENDER_PORT,
                timeout=180.0,
                authorized_root=AUTHORIZED_ROOT,
            )
            await client.connect()
            try:
                caps = await client.describe_capabilities()
                receipt = await client.execute_plan(
                    plan, output_path=OUTPUT, approved=True, capabilities=caps
                )
                from openbimagent.assembly.semantic_snapshot import SemanticSnapshot

                snap = SemanticSnapshot.model_validate(receipt.semantic_snapshot)
                print(
                    f"[4/5] execute_plan → {receipt.status.value} | "
                    f"objects={len(snap.objects)} | ir_sha={snap.source_ir_sha256[:12]}…"
                )
                assert snap.source_ir_sha256 == plan.compiled_ir_sha256
                assert OUTPUT.is_file() and OUTPUT.stat().st_size > 0
                sidecar = OUTPUT.with_suffix(OUTPUT.suffix + ".openbimagent.json")
                assert sidecar.is_file()
                print(f"[5/5] 受控落盘 ✓ {OUTPUT} ({OUTPUT.stat().st_size} bytes) + sidecar")
            finally:
                await client.close()

        asyncio.run(execute())
    finally:
        _kill(proc)
        print("Blender 进程已清理")


if __name__ == "__main__":
    main()
