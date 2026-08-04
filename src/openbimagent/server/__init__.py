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
from openbimagent.server.openapi import (
    M2_OPENAPI_INFO_VERSION,
    M2_OPENAPI_VERSION,
    build_m2_readonly_openapi,
    canonical_openapi_bytes,
    canonical_openapi_sha256,
)
from openbimagent.server.service import M2_READONLY_SERVICE_VERSION, M2ReadOnlyService
from openbimagent.server.sse_projection import (
    M2_SSE_PROJECTION_VERSION,
    M2SseProjector,
    SseProjectionError,
)

__all__ = [
    "M2_API_PROTOCOL_VERSION",
    "M2_OPENAPI_INFO_VERSION",
    "M2_OPENAPI_VERSION",
    "M2_READONLY_SERVICE_VERSION",
    "M2_SSE_PROJECTION_VERSION",
    "M2_SSE_PROTOCOL_VERSION",
    "M2ApiEnvelope",
    "M2ApiError",
    "M2ArtifactMetadata",
    "M2ControlOperation",
    "M2ControlRequest",
    "M2ErrorCode",
    "M2SseCursor",
    "M2ReadOnlyService",
    "M2SseEvent",
    "M2SseProjector",
    "M2SseEventType",
    "SseProjectionError",
    "build_m2_readonly_openapi",
    "canonical_openapi_bytes",
    "canonical_openapi_sha256",
]
