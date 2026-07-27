"""Plain-python checks for OPENBIMAGENT (a): telemetry hard-off.

No Blender needed. Run: uv run python mcp_servers/blender_mcp/tests/test_telemetry_off.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORK_DIR = os.path.dirname(HERE)
sys.path.insert(0, FORK_DIR)

failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} -- {detail}", flush=True)
    if not cond:
        failures.append(name)


# 1) stub semantics
from server import telemetry as t  # noqa: E402

check("TELEMETRY_ENABLED is False", t.TELEMETRY_ENABLED is False, f"value={t.TELEMETRY_ENABLED}")
check("get_telemetry()._check_user_consent() is False",
      t.get_telemetry()._check_user_consent() is False)
check("NullTelemetry.enabled is False", t.get_telemetry().enabled is False)
check("record_event returns None", t.get_telemetry().record_event(tool_name="x") is None)
check("upload_screenshot returns None", t.get_telemetry().upload_screenshot(b"", "x") is None)
check("record_startup returns None", t.record_startup() is None)

# 2) decorators are identity
from server import telemetry_decorator as td  # noqa: E402


@td.telemetry_tool("x")
def f(a, b=1):
    return a + b


@td.rich_telemetry_tool("y", capture_code=True)
def g(a):
    return a * 2


check("telemetry_tool is identity", f(1, b=2) == 3)
check("rich_telemetry_tool is identity", g(3) == 6)

# 3) stub performs no network imports
src = open(t.__file__, encoding="utf-8").read()
for banned in ("httpx", "requests", "urllib", "socket"):
    check(f"telemetry stub does not import {banned}", f"import {banned}" not in src)

# 4) forked server.py has no telemetry wiring at all
srv = open(os.path.join(FORK_DIR, "server", "server.py"), encoding="utf-8").read()
check("server.py does not import .telemetry", "from .telemetry import" not in srv)
check("server.py does not import .telemetry_decorator", "from .telemetry_decorator import" not in srv)
check("server.py never calls record_event", "record_event(" not in srv)
check("server.py never calls get_telemetry", "get_telemetry(" not in srv)

# 5) forked addon hard-codes consent off
addon = open(os.path.join(FORK_DIR, "addon.py"), encoding="utf-8").read()
check("addon defines TELEMETRY_ENABLED = False", "TELEMETRY_ENABLED = False" in addon)
check("addon get_telemetry_consent hard-codes consent False",
      '"consent": False' in addon)

print()
if failures:
    print(f"{len(failures)} checks FAILED", flush=True)
    sys.exit(1)
print("all telemetry hard-off checks passed", flush=True)
