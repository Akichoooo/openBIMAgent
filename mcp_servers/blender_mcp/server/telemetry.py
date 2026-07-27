"""OPENBIMAGENT (a): telemetry hard-off stub.

Replaces upstream src/blender_mcp/telemetry.py (see vendor/telemetry.py).
The upstream collector phones home via httpx and (as shipped) crashes on a
missing `.config` module. In this fork telemetry is hard-disabled: the flag
is a constant, every public function is a no-op, and nothing here performs
network or file I/O. There is intentionally no environment-variable or
preference backdoor.
"""

from enum import Enum

TELEMETRY_ENABLED = False  # OPENBIMAGENT (a): single source of truth

MCP_VERSION = "unknown"


class EventType(str, Enum):
    STARTUP = "startup"
    TOOL_EXECUTION = "tool_execution"
    PROMPT_SENT = "prompt_sent"
    CONNECTION = "connection"
    ERROR = "error"


class NullTelemetry:
    """API-compatible no-op replacement for the upstream TelemetryCollector."""

    enabled = False

    def _check_user_consent(self) -> bool:
        return False

    def record_event(self, *args, **kwargs) -> None:
        return None

    def upload_screenshot(self, *args, **kwargs) -> None:
        return None

    def record_startup(self) -> None:
        return None

    def flush(self) -> None:
        return None


_NULL = NullTelemetry()


def get_telemetry() -> NullTelemetry:
    return _NULL


def record_startup() -> None:
    return None
