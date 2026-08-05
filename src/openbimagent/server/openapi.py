"""M2 P2 pre-G7 确定性 OpenAPI 3.1 静态基线。

只描述无副作用的只读端点；不创建 Web 应用、不绑定端口，也不声明写控制操作。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from openbimagent.server.authentication import M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION
from openbimagent.server.artifact_path import (
    M2_ARTIFACT_RELATIVE_PATH_CHARS_MAX,
    M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION,
)
from openbimagent.server.contracts import (
    M2_ERROR_RETRY_POLICY_VERSION,
    M2ApiEnvelope,
    M2ArtifactMetadata,
)
from openbimagent.server.correlation_identity import (
    M2_CORRELATION_ID_PATTERN,
    M2_CORRELATION_ID_POLICY_VERSION,
)
from openbimagent.server.idempotency_transaction import M2_IDEMPOTENCY_STORE_PROTOCOL_VERSION
from openbimagent.server.pagination import (
    M2_PAGE_CURSOR_CHARS_MAX,
    M2_PAGE_LIMIT_DEFAULT,
    M2_PAGE_LIMIT_MAX,
    M2_PAGINATION_CURSOR_AUTHENTICATED,
    M2_PAGINATION_POLICY_VERSION,
)
from openbimagent.server.payload_privacy import M2_REMOTE_PAYLOAD_POLICY_VERSION
from openbimagent.server.readonly_http import M2_READONLY_REQUEST_METADATA_BUDGET
from openbimagent.server.resource_identity import (
    M2_RESOURCE_ID_PATTERN,
    M2_RESOURCE_ID_POLICY_VERSION,
)
from openbimagent.server.sse_identity import (
    M2_SSE_STREAM_ID_PATTERN,
    M2_SSE_STREAM_ID_POLICY_VERSION,
)

M2_OPENAPI_VERSION = "3.1.0"
M2_OPENAPI_INFO_VERSION = "0.1.0-pre-g7"
M2_OPENAPI_SCHEMA_ID = "https://openbimagent.local/openapi/m2-readonly-v1.json"

_PATH_PARAMETERS = {
    "session_id": "会话标识",
    "request_id": "Subagent attempt request 标识",
    "lineage_id": "逻辑任务 lineage 标识",
    "artifact_id": "不可变工件标识",
}


def build_m2_readonly_openapi() -> dict[str, Any]:
    """生成稳定排序语义的 OpenAPI 3.1 文档；调用不产生文件或网络副作用。"""

    envelope_schema = M2ApiEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
    defs = envelope_schema.pop("$defs", {})
    envelope_schema["allOf"] = [
        {
            "if": {"properties": {"ok": {"const": True}}},
            "then": {"properties": {"data": {"type": "object"}, "error": {"type": "null"}}},
            "else": {
                "properties": {
                    "data": {"type": "null"},
                    "error": {"$ref": "#/components/schemas/M2ApiError"},
                }
            },
        }
    ]
    artifact_schema = M2ArtifactMetadata.model_json_schema(ref_template="#/components/schemas/{model}")
    artifact_defs = artifact_schema.pop("$defs", {})
    artifact_schema["allOf"] = [
        {
            "if": {"properties": {"download_available": {"const": True}}},
            "then": {"properties": {"status": {"const": "completed"}}},
        }
    ]
    components = _rewrite_component_refs(
        {
            "M2ApiEnvelope": envelope_schema,
            "M2ArtifactMetadata": artifact_schema,
            **defs,
            **artifact_defs,
        }
    )
    return {
        "openapi": M2_OPENAPI_VERSION,
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "info": {
            "title": "openBIMAgent M2 Read-Only API",
            "version": M2_OPENAPI_INFO_VERSION,
            "description": (
                "Pre-G7 contract baseline only. No network listener, Runtime lease, "
                "write control, arbitrary path access, or artifact mutation is provided."
            ),
        },
        "servers": [],
        "tags": [
            {"name": "health"},
            {"name": "sessions"},
            {"name": "attempts"},
            {"name": "approvals"},
            {"name": "artifacts"},
        ],
        "paths": {
            "/api/v1/health": _get_operation("health", "Read-only contract health"),
            "/api/v1/sessions": _get_operation(
                "sessions", "List sessions", query_parameters=("limit", "cursor")
            ),
            "/api/v1/sessions/{session_id}": _get_operation(
                "sessions", "Get session metadata", path_parameter="session_id"
            ),
            "/api/v1/attempts": _get_operation(
                "attempts",
                "List attempts",
                query_parameters=("lineage_id", "status", "parent_session_id", "limit", "cursor"),
            ),
            "/api/v1/attempts/{request_id}": _get_operation(
                "attempts", "Get attempt", path_parameter="request_id"
            ),
            "/api/v1/lineages/{lineage_id}": _get_operation(
                "attempts",
                "Get lineage attempts",
                path_parameter="lineage_id",
                query_parameters=("limit", "cursor"),
            ),
            "/api/v1/approvals": _get_operation(
                "approvals",
                "List approvals",
                query_parameters=("request_id", "pending_only", "limit", "cursor"),
            ),
            "/api/v1/artifacts/{artifact_id}": _get_operation(
                "artifacts", "Get artifact metadata", path_parameter="artifact_id"
            ),
        },
        "components": {"schemas": components},
        "x-openbimagent-boundaries": {
            "stage": "pre-g7-preparation-only",
            "read_only": True,
            "network_listener_started": False,
            "runtime_lease_acquired": False,
            "runtime_created": False,
            "write_control_enabled": False,
            "arbitrary_path_parameters": False,
            "artifact_relative_path_policy_version": M2_ARTIFACT_RELATIVE_PATH_POLICY_VERSION,
            "artifact_relative_path_chars_max": M2_ARTIFACT_RELATIVE_PATH_CHARS_MAX,
            "artifact_relative_path_io_performed": False,
            "artifact_symlink_validation_deferred_to_p2": True,
            "artifact_metadata_remote_payload_policy_version": M2_REMOTE_PAYLOAD_POLICY_VERSION,
            "remote_payload_policy_version": M2_REMOTE_PAYLOAD_POLICY_VERSION,
            "remote_payload_runtime_gate_required": True,
            "idempotency_store_protocol_version": M2_IDEMPOTENCY_STORE_PROTOCOL_VERSION,
            "idempotency_store_implemented": False,
            "authenticated_principal_protocol_version": M2_AUTHENTICATED_PRINCIPAL_PROTOCOL_VERSION,
            "authenticated_principal_remote_payload_policy_version": M2_REMOTE_PAYLOAD_POLICY_VERSION,
            "authentication_mechanism_selected": False,
            "authentication_secrets_in_principal": False,
            "error_retry_policy_version": M2_ERROR_RETRY_POLICY_VERSION,
            "error_retryable_codes": ["RateLimited", "RuntimeUnavailable"],
            "resource_id_policy_version": M2_RESOURCE_ID_POLICY_VERSION,
            "resource_id_pattern": M2_RESOURCE_ID_PATTERN,
            "correlation_id_policy_version": M2_CORRELATION_ID_POLICY_VERSION,
            "correlation_id_pattern": M2_CORRELATION_ID_PATTERN,
            "sse_stream_id_policy_version": M2_SSE_STREAM_ID_POLICY_VERSION,
            "sse_stream_id_pattern": M2_SSE_STREAM_ID_PATTERN,
            "sse_stream_id_policy_distinct_from_attempt_identity": True,
            "readonly_request_metadata_budget": dict(M2_READONLY_REQUEST_METADATA_BUDGET),
            "readonly_pagination_policy": {
                "version": M2_PAGINATION_POLICY_VERSION,
                "default_limit": M2_PAGE_LIMIT_DEFAULT,
                "max_limit": M2_PAGE_LIMIT_MAX,
                "cursor_chars_max": M2_PAGE_CURSOR_CHARS_MAX,
                "snapshot_bound": True,
                "authenticated": M2_PAGINATION_CURSOR_AUTHENTICATED,
            },
        },
    }


def canonical_openapi_bytes(document: dict[str, Any] | None = None) -> bytes:
    payload = build_m2_readonly_openapi() if document is None else deepcopy(document)
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_openapi_sha256(document: dict[str, Any] | None = None) -> str:
    return hashlib.sha256(canonical_openapi_bytes(document)).hexdigest()


def _rewrite_component_refs(value: Any) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                rewritten[key] = item.replace("#/$defs/", "#/components/schemas/", 1)
            else:
                rewritten[key] = _rewrite_component_refs(item)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_component_refs(item) for item in value]
    return value


def _get_operation(
    tag: str,
    summary: str,
    *,
    path_parameter: str | None = None,
    query_parameters: tuple[str, ...] = (),
) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = [
        {
            "name": "X-Request-ID",
            "in": "header",
            "required": True,
            "description": "Client correlation ID echoed by the M2 response envelope",
            "schema": {
                "type": "string",
                "pattern": M2_CORRELATION_ID_PATTERN,
                "x-openbimagent-correlation-id-policy": M2_CORRELATION_ID_POLICY_VERSION,
            },
        }
    ]
    if path_parameter is not None:
        parameters.append(
            {
                "name": path_parameter,
                "in": "path",
                "required": True,
                "description": _PATH_PARAMETERS[path_parameter],
                "schema": _id_schema(),
            }
        )
    for name in query_parameters:
        if name == "pending_only":
            schema: dict[str, Any] = {"type": "boolean", "default": False}
        elif name == "status":
            schema = {
                "type": "string",
                "enum": ["pending", "running", "completed", "failed", "cancelled"],
            }
        elif name == "limit":
            schema = {
                "type": "integer",
                "minimum": 1,
                "maximum": M2_PAGE_LIMIT_MAX,
                "default": M2_PAGE_LIMIT_DEFAULT,
            }
        elif name == "cursor":
            schema = {
                "type": "string",
                "minLength": 1,
                "maxLength": M2_PAGE_CURSOR_CHARS_MAX,
                "pattern": "^[A-Za-z0-9_-]+$",
                "x-openbimagent-pagination-policy": M2_PAGINATION_POLICY_VERSION,
                "x-openbimagent-authenticated": M2_PAGINATION_CURSOR_AUTHENTICATED,
            }
        else:
            schema = _id_schema()
        parameters.append({"name": name, "in": "query", "required": False, "schema": schema})
    operation: dict[str, Any] = {
        "get": {
            "tags": [tag],
            "summary": summary,
            "operationId": "get_" + summary.lower().replace(" ", "_"),
            "parameters": parameters,
            "responses": {
                "200": _response("Successful read-only projection"),
                "400": _response("Invalid request"),
                "404": _response("Resource not found"),
                "405": {
                    **_response("Method not allowed"),
                    "headers": {
                        "Allow": {
                            "required": True,
                            "schema": {"type": "string", "const": "GET"},
                        }
                    },
                },
                "409": _response("Persistent fact conflict"),
                "500": _response("Safe internal error"),
            },
        }
    }
    return operation


def _response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/M2ApiEnvelope"},
            }
        },
    }


def _id_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "pattern": M2_RESOURCE_ID_PATTERN,
        "x-openbimagent-resource-id-policy": M2_RESOURCE_ID_POLICY_VERSION,
    }


__all__ = [
    "M2_OPENAPI_INFO_VERSION",
    "M2_OPENAPI_SCHEMA_ID",
    "M2_OPENAPI_VERSION",
    "build_m2_readonly_openapi",
    "canonical_openapi_bytes",
    "canonical_openapi_sha256",
]
