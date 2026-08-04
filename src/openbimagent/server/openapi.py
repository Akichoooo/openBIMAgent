"""M2 P2 pre-G7 确定性 OpenAPI 3.1 静态基线。

只描述无副作用的只读端点；不创建 Web 应用、不绑定端口，也不声明写控制操作。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from openbimagent.server.contracts import M2ApiEnvelope, M2ArtifactMetadata
from openbimagent.server.payload_privacy import M2_REMOTE_PAYLOAD_POLICY_VERSION

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
            "/api/v1/sessions": _get_operation("sessions", "List sessions"),
            "/api/v1/sessions/{session_id}": _get_operation(
                "sessions", "Get session metadata", path_parameter="session_id"
            ),
            "/api/v1/attempts": _get_operation(
                "attempts",
                "List attempts",
                query_parameters=("lineage_id", "status", "parent_session_id"),
            ),
            "/api/v1/attempts/{request_id}": _get_operation(
                "attempts", "Get attempt", path_parameter="request_id"
            ),
            "/api/v1/lineages/{lineage_id}": _get_operation(
                "attempts", "Get lineage attempts", path_parameter="lineage_id"
            ),
            "/api/v1/approvals": _get_operation(
                "approvals",
                "List approvals",
                query_parameters=("request_id", "pending_only"),
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
            "remote_payload_policy_version": M2_REMOTE_PAYLOAD_POLICY_VERSION,
            "remote_payload_runtime_gate_required": True,
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
                "minLength": 1,
                "maxLength": 128,
                "pattern": "^[A-Za-z0-9_.:@/-]+$",
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
        "maxLength": 200,
        "pattern": "^[A-Za-z0-9_.@-]+$",
    }


__all__ = [
    "M2_OPENAPI_INFO_VERSION",
    "M2_OPENAPI_SCHEMA_ID",
    "M2_OPENAPI_VERSION",
    "build_m2_readonly_openapi",
    "canonical_openapi_bytes",
    "canonical_openapi_sha256",
]
