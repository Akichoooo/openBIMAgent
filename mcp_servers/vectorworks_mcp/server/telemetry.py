"""OPENBIMAGENT (a): telemetry hard-off stub.

参照 mcp_servers/blender_mcp/server/telemetry.py 的硬关 stub 风格。
vectorworks-mcp 本就无遥测,此 stub 仅保 API 兼容:标志位为常量,每个
公开函数为 no-op,无任何网络或文件 IO,无 env 后门。
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
    """API-compatible no-op replacement (参照 blender_mcp NullTelemetry)。"""

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
