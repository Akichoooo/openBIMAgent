"""M2 产品协议模型。

当前仅包含 pre-G7 的协议与 Schema 准备，不启动 HTTP server，不获取 Runtime lease。
"""

from openbimagent.server.contracts import (
    M2_API_PROTOCOL_VERSION,
    M2_SSE_PROTOCOL_VERSION,
    M2ApiEnvelope,
    M2ApiError,
    M2ArtifactMetadata,
    M2ControlOperation,
    M2ControlRequest,
    M2ErrorCode,
    M2SseCursor,
    M2SseEvent,
    M2SseEventType,
)

__all__ = [
    "M2_API_PROTOCOL_VERSION",
    "M2_SSE_PROTOCOL_VERSION",
    "M2ApiEnvelope",
    "M2ApiError",
    "M2ArtifactMetadata",
    "M2ControlOperation",
    "M2ControlRequest",
    "M2ErrorCode",
    "M2SseCursor",
    "M2SseEvent",
    "M2SseEventType",
]
