"""Smoke test: the forked MCP server imports and exposes <=12 tools (item d).

No Blender needed. Run: uv run python mcp_servers/blender_mcp/tests/smoke_server_import.py
"""

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORK_DIR = os.path.dirname(HERE)
sys.path.insert(0, FORK_DIR)

EXPECTED = {
    "ping", "describe_capabilities", "get_scene_info", "get_object_info",
    "get_viewport_screenshot", "execute_blender_code", "set_editable_scope",
    "restore_snapshot", "batch_render", "camera_turntable", "camera_path_render",
}
BANNED_SUBSTRINGS = ("polyhaven", "sketchfab", "hyper3d", "rodin", "hunyuan", "texture")

failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} -- {detail}", flush=True)
    if not cond:
        failures.append(name)


from server.server import mcp, FORK_VERSION  # noqa: E402

tools = asyncio.run(mcp.list_tools())
names = {t.name for t in tools}

check("tool count <= 12", len(names) <= 12, f"count={len(names)}")
check("expected tool set present", EXPECTED <= names, f"missing={EXPECTED - names}")
check("no extra tools", names <= EXPECTED, f"extra={names - EXPECTED}")
for b in BANNED_SUBSTRINGS:
    check(f"no '{b}' tool exposed", not any(b in n for n in names))

print(f"tools ({len(names)}): {sorted(names)}")
print()
if failures:
    print(f"{len(failures)} checks FAILED", flush=True)
    sys.exit(1)
print(f"server import smoke passed (fork {FORK_VERSION})", flush=True)
