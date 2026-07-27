"""M0 fork acceptance orchestrator.

Launches Blender 5.2 headless with the forked addon, waits for the socket,
runs the socket test client, and writes relay_workspace/m0_spikes/fork_test_report.md.

Usage (from project root):
    uv run python mcp_servers/blender_mcp/tests/run_fork_tests.py

Retries: if Blender fails to start / accept connections / hangs on the first
probe, the process is killed and relaunched, at most 2 retries.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))            # mcp_servers/blender_mcp/tests
FORK_DIR = os.path.dirname(HERE)                             # mcp_servers/blender_mcp
PROJECT_ROOT = os.path.dirname(os.path.dirname(FORK_DIR))    # openBIMAgent/

BLENDER_EXE = os.environ.get("OPENBIMAGENT_BLENDER_EXE", r"D:\devloop\blender\blender.exe")
ADDON = os.path.join(FORK_DIR, "addon.py")
OUT_DIR = os.path.join(PROJECT_ROOT, "relay_workspace", "m0_spikes", "fork_test_out")
REPORT = os.path.join(PROJECT_ROOT, "relay_workspace", "m0_spikes", "fork_test_report.md")
SNAPSHOT_DIR = os.path.join(OUT_DIR, "snapshots")
BLENDER_LOG = os.path.join(OUT_DIR, "blender_stdout.log")

HOST = "127.0.0.1"
PORT = int(os.environ.get("OPENBIMAGENT_BLENDER_PORT", "9887"))
LAUNCH_WAIT_S = 120      # max wait for the socket to come up
MAX_RETRIES = 2          # spec: kill-and-retry hung Blender at most twice

sys.path.insert(0, HERE)
import socket_test_client as stc  # noqa: E402


def log(msg):
    print(f"[run_fork_tests] {msg}", flush=True)


def wait_for_port(proc, timeout_s):
    """Poll until the addon accepts TCP connections (or Blender dies/timeout)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, f"Blender exited early (code {proc.returncode})"
        try:
            with socket.create_connection((HOST, PORT), timeout=1.0):
                return True, "port open"
        except OSError:
            time.sleep(1.0)
    return False, f"port {PORT} not open after {timeout_s}s"


def kill_blender(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def launch_blender(log_file):
    env = dict(os.environ)
    env["OPENBIMAGENT_BLENDER_PORT"] = str(PORT)
    env["OPENBIMAGENT_SNAPSHOT_DIR"] = SNAPSHOT_DIR
    cmd = [BLENDER_EXE, "--background", "--factory-startup", "--python", ADDON]
    log("launch: " + " ".join(cmd))
    return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)


def one_attempt(attempt_no, log_file):
    """Launch + run the suite once. Returns (results, infra_error)."""
    proc = launch_blender(log_file)
    try:
        ok, why = wait_for_port(proc, LAUNCH_WAIT_S)
        if not ok:
            log(f"infra failure: {why}")
            return None, why
        log(f"port open after probe: {why}; running cases")
        client = stc.BlenderSocketClient(HOST, PORT, timeout=300.0)
        try:
            client.connect()
            # first probe: if the addon accepts TCP but never answers, treat as infra
            pong = client.send("ping")
            log(f"first ping ok: blender={pong.get('blender_version')}")
            results = stc.run_all_cases(client, OUT_DIR)
            return results, None
        except (OSError, socket.timeout) as e:
            return None, f"socket-level failure: {e}"
        finally:
            client.close()
    finally:
        kill_blender(proc)
        log("blender process stopped")


def write_report(results, attempts, blender_log_tail, wall_s):
    passed = [r for r in results if r[2]]
    failed = [r for r in results if not r[2]]
    lines = []
    lines.append("# M0 fork 验收测试报告：blender-mcp 八项改造")
    lines.append("")
    lines.append(f"- 日期：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Blender:{BLENDER_EXE}(5.2.0 LTS,`--background --factory-startup`)")
    lines.append(f"- addon:{os.path.relpath(ADDON, PROJECT_ROOT)}(fork v1.0.0-m0, upstream ahujasid/blender-mcp@da4e16d v1.6.0)")
    lines.append(f"- 端口:{HOST}:{PORT} · 启动尝试:{attempts} 次 · 总耗时:{wall_s:.1f}s")
    lines.append(f"- 产物目录:{os.path.relpath(OUT_DIR, PROJECT_ROOT)}(截图/批量渲染/turntable/path/快照)")
    lines.append("")
    lines.append(f"## 汇总:**{len(passed)}/{len(results)} PASS**")
    lines.append("")
    lines.append("| 用例 | 改造项 | 结果 | 耗时 | 明细 |")
    lines.append("|---|---|---|---|---|")
    for case_id, name, ok, detail, dt in results:
        lines.append(f"| {case_id} | {name} | {'PASS' if ok else '**FAIL**'} | {dt:.1f}s | {detail} |")
    lines.append("")
    lines.append("## Blender stdout 尾部(40 行)")
    lines.append("")
    lines.append("```")
    lines.extend(blender_log_tail)
    lines.append("```")
    lines.append("")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"report written: {REPORT}")
    return len(failed)


def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    if not os.path.exists(BLENDER_EXE):
        print(f"FATAL: blender not found at {BLENDER_EXE}", flush=True)
        sys.exit(2)

    # refuse to run against an occupied port (would test someone else's server)
    try:
        with socket.create_connection((HOST, PORT), timeout=1.0):
            print(f"FATAL: port {PORT} already occupied; refusing to run", flush=True)
            sys.exit(2)
    except OSError:
        pass  # free, good

    results, infra_err, attempts = None, None, 0
    with open(BLENDER_LOG, "w", encoding="utf-8", errors="replace") as log_file:
        for attempt in range(1, MAX_RETRIES + 2):  # 1 try + up to 2 retries
            attempts = attempt
            log(f"--- attempt {attempt}/{MAX_RETRIES + 1} ---")
            results, infra_err = one_attempt(attempt, log_file)
            if results is not None:
                break
            log(f"attempt {attempt} failed at infra level: {infra_err}")

    if results is None:
        results = [("INFRA", "launch headless Blender + socket connect", False,
                    f"all {attempts} attempts failed: {infra_err}", 0.0)]

    try:
        with open(BLENDER_LOG, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read().splitlines()[-40:]
    except OSError:
        tail = ["<no blender log>"]

    n_failed = write_report(results, attempts, tail, time.time() - t0)
    passed = sum(1 for r in results if r[2])
    print(f"\n=== {passed}/{len(results)} PASS, report: {REPORT} ===", flush=True)
    sys.exit(1 if n_failed else 0)


if __name__ == "__main__":
    main()
