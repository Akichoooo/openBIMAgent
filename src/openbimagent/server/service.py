"""M2 P2 pre-G7 只读服务适配器。

该模块不绑定网络端口、不构造 Runtime、不获取 Runtime lease，也不接受任意文件路径。
所有事实源必须由调用方注入；返回值统一使用 M2ApiEnvelope，并仅投影远程可见白名单字段。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from openbimagent.orchestrator.contracts import ArtifactRecord
from openbimagent.orchestrator.control_plane import ControlPlaneError
from openbimagent.server.artifact_path import validate_m2_artifact_relative_path
from openbimagent.server.contracts import (
    M2_API_PROTOCOL_VERSION,
    M2ApiEnvelope,
    M2ArtifactMetadata,
    M2ErrorCode,
    make_m2_api_error,
)
from openbimagent.server.pagination import (
    M2_PAGE_LIMIT_DEFAULT,
    M2PageResource,
    M2PaginationError,
    paginate_m2_items,
)
from openbimagent.server.resource_identity import is_m2_resource_id

M2_READONLY_SERVICE_VERSION = "0.1"


class ControlPlaneReader(Protocol):
    def list_attempts(
        self,
        *,
        lineage_id: str | None = None,
        status: str | None = None,
        parent_session_id: str | None = None,
    ) -> tuple[Any, ...]: ...

    def get_attempt(self, request_id: str) -> Any: ...

    def get_lineage(self, lineage_id: str) -> tuple[Any, ...]: ...

    def list_approvals(
        self,
        *,
        request_id: str | None = None,
        pending_only: bool = False,
    ) -> tuple[Any, ...]: ...


SessionIndexReader = Callable[[], Sequence[Mapping[str, Any]]]
ArtifactLookup = Callable[[str], ArtifactRecord | None]


class M2ReadOnlyService:
    """无副作用的只读服务投影；HTTP 框架只能在后续 Gate 外层封装。"""

    def __init__(
        self,
        *,
        control_plane: ControlPlaneReader,
        session_index_reader: SessionIndexReader,
        artifact_lookup: ArtifactLookup,
    ) -> None:
        self._control_plane = control_plane
        self._session_index_reader = session_index_reader
        self._artifact_lookup = artifact_lookup

    def health(self, *, request_id: str) -> M2ApiEnvelope:
        return self._success(
            request_id,
            {
                "service": "openbimagent-m2-readonly",
                "service_version": M2_READONLY_SERVICE_VERSION,
                "api_protocol_version": M2_API_PROTOCOL_VERSION,
                "mode": "m2-read-only",
                "status": "active",
                "network_listener_started": True,
                "runtime_lease_acquired": False,
                "write_control_enabled": False,
            },
        )

    def list_sessions(
        self,
        *,
        request_id: str,
        limit: int = M2_PAGE_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> M2ApiEnvelope:
        try:
            items = [self._session_metadata(item) for item in self._session_index_reader()]
            items.sort(key=lambda item: (item["last_active"], item["session_id"]), reverse=True)
            return self._page_success(
                request_id,
                items,
                resource="sessions",
                scope={},
                limit=limit,
                cursor=cursor,
            )
        except M2PaginationError as exc:
            return self._error(request_id, exc.code, str(exc))
        except Exception:
            return self._internal_error(request_id, "会话索引不可用")

    def get_session(self, *, request_id: str, session_id: str) -> M2ApiEnvelope:
        invalid = self._validate_resource(request_id, session_id, "session_id")
        if invalid is not None:
            return invalid
        try:
            items = [self._session_metadata(item) for item in self._session_index_reader()]
        except Exception:
            return self._internal_error(request_id, "会话索引不可用")
        for item in items:
            if item["session_id"] == session_id:
                return self._success(request_id, {"session": item})
        return self._not_found(request_id, "session")

    def list_attempts(
        self,
        *,
        request_id: str,
        lineage_id: str | None = None,
        status: str | None = None,
        parent_session_id: str | None = None,
        limit: int = M2_PAGE_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> M2ApiEnvelope:
        for value, name in (
            (lineage_id, "lineage_id"),
            (parent_session_id, "parent_session_id"),
        ):
            if value is not None:
                invalid = self._validate_resource(request_id, value, name)
                if invalid is not None:
                    return invalid
        try:
            views = self._control_plane.list_attempts(
                lineage_id=lineage_id,
                status=status,
                parent_session_id=parent_session_id,
            )
            items = [self._attempt_metadata(view) for view in views]
            items.sort(key=lambda item: (item["lineage_id"], item["attempt_number"], item["request_id"]))
            return self._page_success(
                request_id,
                items,
                resource="attempts",
                scope={
                    "lineage_id": lineage_id,
                    "status": status,
                    "parent_session_id": parent_session_id,
                },
                limit=limit,
                cursor=cursor,
            )
        except M2PaginationError as exc:
            return self._error(request_id, exc.code, str(exc))
        except ValueError:
            return self._invalid_request(request_id, "非法 attempt 查询条件")
        except ControlPlaneError:
            return self._conflict(request_id, "只读 attempt 审计事实冲突")
        except Exception:
            return self._internal_error(request_id, "attempt 事实不可用")

    def get_attempt(self, *, request_id: str, attempt_request_id: str) -> M2ApiEnvelope:
        invalid = self._validate_resource(request_id, attempt_request_id, "request_id")
        if invalid is not None:
            return invalid
        try:
            view = self._control_plane.get_attempt(attempt_request_id)
            return self._success(request_id, {"attempt": self._attempt_metadata(view)})
        except ControlPlaneError:
            return self._not_found(request_id, "attempt")
        except Exception:
            return self._internal_error(request_id, "attempt 事实不可用")

    def get_lineage(
        self,
        *,
        request_id: str,
        lineage_id: str,
        limit: int = M2_PAGE_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> M2ApiEnvelope:
        invalid = self._validate_resource(request_id, lineage_id, "lineage_id")
        if invalid is not None:
            return invalid
        try:
            views = self._control_plane.get_lineage(lineage_id)
            items = [self._attempt_metadata(view) for view in views]
            items.sort(key=lambda item: (item["attempt_number"], item["request_id"]))
            return self._page_success(
                request_id,
                items,
                resource="lineages",
                scope={"lineage_id": lineage_id},
                limit=limit,
                cursor=cursor,
                extra={"lineage_id": lineage_id},
            )
        except M2PaginationError as exc:
            return self._error(request_id, exc.code, str(exc))
        except ControlPlaneError:
            return self._not_found(request_id, "lineage")
        except Exception:
            return self._internal_error(request_id, "lineage 事实不可用")

    def list_approvals(
        self,
        *,
        request_id: str,
        attempt_request_id: str | None = None,
        pending_only: bool = False,
        limit: int = M2_PAGE_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> M2ApiEnvelope:
        if attempt_request_id is not None:
            invalid = self._validate_resource(request_id, attempt_request_id, "request_id")
            if invalid is not None:
                return invalid
        try:
            views = self._control_plane.list_approvals(
                request_id=attempt_request_id,
                pending_only=pending_only,
            )
            items = [self._approval_metadata(view) for view in views]
            items.sort(key=lambda item: (item["requested_at"], item["approval_id"]))
            return self._page_success(
                request_id,
                items,
                resource="approvals",
                scope={"request_id": attempt_request_id, "pending_only": pending_only},
                limit=limit,
                cursor=cursor,
            )
        except M2PaginationError as exc:
            return self._error(request_id, exc.code, str(exc))
        except ControlPlaneError:
            return self._conflict(request_id, "只读 approval 审计事实冲突")
        except Exception:
            return self._internal_error(request_id, "approval 事实不可用")

    def get_artifact_metadata(self, *, request_id: str, artifact_id: str) -> M2ApiEnvelope:
        invalid = self._validate_resource(request_id, artifact_id, "artifact_id")
        if invalid is not None:
            return invalid
        try:
            record = self._artifact_lookup(artifact_id)
        except Exception:
            return self._internal_error(request_id, "artifact 索引不可用")
        if record is None:
            return self._not_found(request_id, "artifact")
        if record.artifact_id != artifact_id or not record.immutable:
            return self._conflict(request_id, "artifact 身份或不可变属性冲突")
        try:
            if record.relative_path is not None:
                validate_m2_artifact_relative_path(record.relative_path)
            metadata = M2ArtifactMetadata(
                artifact_id=record.artifact_id,
                kind=record.kind,
                media_type=record.media_type or "application/octet-stream",
                sha256=record.sha256,
                size_bytes=record.size_bytes,
                immutable=True,
                status=record.status.value,
                source_attempt_id=record.source_attempt_id,
                download_available=False,
            )
            return self._success(request_id, {"artifact": metadata.model_dump(mode="json")})
        except Exception:
            return self._conflict(request_id, "artifact 元数据不满足远程协议")

    @staticmethod
    def _session_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(entry.get("id", ""))
        if not is_m2_resource_id(session_id):
            raise ValueError("session index 包含非法 id")
        event_count = entry.get("event_count", 0)
        if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count < 0:
            raise ValueError("session index 包含非法 event_count")
        return {
            "session_id": session_id,
            "title": str(entry.get("title", "未命名会话"))[:500],
            "playbook": str(entry.get("playbook") or ""),  # 侧边栏文件夹分组数据源
            "created_at": str(entry.get("created_at", "")),
            "last_active": str(entry.get("last_active", "")),
            "event_count": event_count,
            "archived": bool(entry.get("archived", False)),
            "archived_at": str(entry.get("archived_at", "")),
        }

    @staticmethod
    def _attempt_metadata(view: Any) -> dict[str, Any]:
        data = view.model_dump(mode="json")
        return {
            key: data[key]
            for key in (
                "request_id",
                "agent_id",
                "parent_session_id",
                "child_session_id",
                "role",
                "lineage_id",
                "attempt_number",
                "resumed_from_request_id",
                "status",
                "phase",
                "updated_at",
                "error_code",
                "receipt_id",
                "artifact_count",
            )
        }

    @staticmethod
    def _approval_metadata(view: Any) -> dict[str, Any]:
        data = view.model_dump(mode="json")
        return {
            key: data[key]
            for key in (
                "approval_id",
                "request_id",
                "agent_id",
                "parent_session_id",
                "child_session_id",
                "tool_name",
                "permission_key",
                "args_sha256",
                "requested_at",
                "pending",
                "decision",
                "decided_at",
                "receipt_id",
            )
        }

    @staticmethod
    def _validate_resource(request_id: str, value: str, field: str) -> M2ApiEnvelope | None:
        if is_m2_resource_id(value):
            return None
        return M2ReadOnlyService._invalid_request(request_id, f"非法 {field}")

    @staticmethod
    def _success(request_id: str, data: dict[str, Any]) -> M2ApiEnvelope:
        return M2ApiEnvelope(request_id=request_id, ok=True, data=data)

    @staticmethod
    def _page_success(
        request_id: str,
        items: Sequence[Mapping[str, Any]],
        *,
        resource: M2PageResource,
        scope: Mapping[str, Any],
        limit: int,
        cursor: str | None,
        extra: Mapping[str, Any] | None = None,
    ) -> M2ApiEnvelope:
        page = paginate_m2_items(items, resource=resource, scope=scope, limit=limit, cursor=cursor)
        data = page.model_dump(mode="json")
        if extra:
            data = {**dict(extra), **data}
        return M2ReadOnlyService._success(request_id, data)

    @staticmethod
    def _error(
        request_id: str,
        code: M2ErrorCode,
        message: str,
    ) -> M2ApiEnvelope:
        error = make_m2_api_error(
            code=code,
            message=message,
            request_id=request_id,
        )
        return M2ApiEnvelope(request_id=request_id, ok=False, error=error)

    @staticmethod
    def _invalid_request(request_id: str, message: str) -> M2ApiEnvelope:
        return M2ReadOnlyService._error(request_id, M2ErrorCode.INVALID_REQUEST, message)

    @staticmethod
    def _not_found(request_id: str, resource: str) -> M2ApiEnvelope:
        return M2ReadOnlyService._error(request_id, M2ErrorCode.NOT_FOUND, f"{resource} 不存在")

    @staticmethod
    def _conflict(request_id: str, message: str) -> M2ApiEnvelope:
        return M2ReadOnlyService._error(request_id, M2ErrorCode.CONFLICT, message)

    @staticmethod
    def _internal_error(request_id: str, message: str) -> M2ApiEnvelope:
        return M2ReadOnlyService._error(
            request_id,
            M2ErrorCode.INTERNAL_ERROR,
            message,
        )


__all__ = [
    "ArtifactLookup",
    "ControlPlaneReader",
    "M2_READONLY_SERVICE_VERSION",
    "M2ReadOnlyService",
    "SessionIndexReader",
]
