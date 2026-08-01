"""Subagent Runtime v1 P1e 单机 IPC 控制通道。

服务只绑定 IPv4 loopback，由持有 Runtime lease 的进程内嵌启动。发现文件保存端口、实例身份和
令牌哈希；原始 bearer token 只存在受限权限的独立 token 文件中。协议使用一行一个 JSON，
设置严格的消息上限、超时和版本校验，不提供远程监听或 Runtime 构造能力。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.schema_gate.gate import gate_or_fix
from openbimagent.session.schema import uuid7

if TYPE_CHECKING:
    from openbimagent.orchestrator.runtime import LocalSubagentRuntime

IPC_PROTOCOL_VERSION = "1.0"
IPC_HOST = "127.0.0.1"
IPC_MAX_MESSAGE_BYTES = 128 * 1024
IPC_DEFAULT_TIMEOUT_S = 5.0
IPC_MAX_CONCURRENT_CLIENTS = 16
IPC_DISCOVERY_NAME = "control-ipc.json"
IPC_TOKEN_NAME = "control-ipc.token"
IpcOperation = Literal["ping", "approval.decide", "attempt.resume", "attempt.steer", "attempt.cancel"]


class IpcError(RuntimeError):
    """IPC 不可用、协议无效或远端控制操作失败。"""


class IpcRequest(BaseModel):
    """单个受认证写控制请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=IPC_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    message_id: str = Field(min_length=1, max_length=128)
    operation: IpcOperation
    actor: ActorRef
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")
    payload: dict[str, Any] = Field(default_factory=dict)
    bearer_token: str = Field(min_length=32, max_length=256)

    @model_validator(mode="after")
    def _payload_matches_operation(self) -> "IpcRequest":
        if self.actor.actor_type in {ActorType.RUNTIME, ActorType.LEGACY}:
            raise ValueError("外部 IPC 调用方不能声明 runtime/legacy actor_type")
        required = {
            "ping": set(),
            "approval.decide": {"approval_id", "approved"},
            "attempt.resume": {"source_request_id", "instruction"},
            "attempt.steer": {"request_id", "instruction"},
            "attempt.cancel": {"request_id"},
        }[self.operation]
        allowed = {
            "ping": set(),
            "approval.decide": {"approval_id", "approved", "reason"},
            "attempt.resume": {"source_request_id", "instruction"},
            "attempt.steer": {"request_id", "instruction"},
            "attempt.cancel": {"request_id"},
        }[self.operation]
        missing = required - set(self.payload)
        if missing:
            raise ValueError(f"{self.operation} 缺少 payload 字段: {sorted(missing)}")
        extra = set(self.payload) - allowed
        if extra:
            raise ValueError(f"{self.operation} 含未知 payload 字段: {sorted(extra)}")
        for key in {"approval_id", "source_request_id", "request_id", "instruction", "reason"} & set(self.payload):
            value = self.payload[key]
            if not isinstance(value, str):
                raise ValueError(f"payload.{key} 必须是字符串")
            if key != "reason" and not value.strip():
                raise ValueError(f"payload.{key} 不能为空")
            limit = 20_000 if key == "instruction" else 1_000 if key == "reason" else 200
            if len(value) > limit:
                raise ValueError(f"payload.{key} 超过长度上限 {limit}")
        if "approved" in self.payload and type(self.payload["approved"]) is not bool:
            raise ValueError("payload.approved 必须是布尔值")
        return self


class IpcResponse(BaseModel):
    """不包含 bearer token 的稳定响应信封。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=IPC_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    message_id: str = Field(min_length=1)
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _response_is_consistent(self) -> "IpcResponse":
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("成功响应不能携带 error")
        if not self.ok and not self.error_code:
            raise ValueError("失败响应必须携带 error_code")
        return self


class IpcDiscovery(BaseModel):
    """客户端发现信息；只保存 token 哈希，不保存认证秘密。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=IPC_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    runtime_instance_id: str = Field(min_length=1)
    host: str = Field(pattern=r"^127\.0\.0\.1$")
    port: int = Field(ge=1, le=65535)
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pid: int = Field(ge=1)
    started_at: datetime


class _ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], service: "RuntimeIpcServer") -> None:
        self.service = service
        super().__init__(server_address, _ControlHandler, bind_and_activate=True)


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service = self.server.service  # type: ignore[attr-defined]
        if not service._client_slots.acquire(blocking=False):
            response = service._error("unknown", "ServerBusy", "Runtime IPC 并发连接已达上限")
        else:
            try:
                self.connection.settimeout(service.timeout_s)
                response = service._handle_stream(self.rfile)
            finally:
                service._client_slots.release()
        response_payload = response.model_dump(mode="json")
        gate_or_fix("ipc_response", response_payload)
        encoded = json.dumps(response_payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.wfile.write(encoded + b"\n")


class RuntimeIpcServer:
    """绑定一个活跃 LocalSubagentRuntime 的 loopback-only 写控制服务。"""

    def __init__(
        self,
        runtime: "LocalSubagentRuntime",
        *,
        root: Path | None = None,
        timeout_s: float = IPC_DEFAULT_TIMEOUT_S,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        self.runtime = runtime
        self.root = Path(root or runtime.state_store.root).resolve()
        self.timeout_s = timeout_s
        self.runtime_instance_id = str(uuid7())
        self._token = secrets.token_urlsafe(32)
        self._server: _ControlServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._client_slots = threading.BoundedSemaphore(IPC_MAX_CONCURRENT_CLIENTS)
        self.discovery_path = self.root / IPC_DISCOVERY_NAME
        self.token_path = self.root / IPC_TOKEN_NAME
        self._idempotent: dict[tuple[str, str], tuple[str, IpcResponse]] = {}

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> IpcDiscovery:
        with self._lock:
            if self._server is not None:
                return self.discovery()
            server = _ControlServer((IPC_HOST, 0), self)
            self._server = server
            discovery = IpcDiscovery(
                runtime_instance_id=self.runtime_instance_id,
                host=IPC_HOST,
                port=int(server.server_address[1]),
                token_sha256=hashlib.sha256(self._token.encode()).hexdigest(),
                pid=os.getpid(),
                started_at=datetime.now(timezone.utc),
            )
            gate_or_fix("ipc_discovery", discovery.model_dump(mode="json"))
            try:
                self._write_private(self.token_path, self._token.encode("utf-8"))
                self._write_private(
                    self.discovery_path,
                    json.dumps(discovery.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8"),
                )
            except Exception:
                server.server_close()
                self._server = None
                raise
            self._thread = threading.Thread(target=server.serve_forever, name="openbim-runtime-ipc", daemon=True)
            self._thread.start()
            return discovery

    def discovery(self) -> IpcDiscovery:
        if self._server is None:
            raise IpcError("Runtime IPC 服务尚未启动")
        return IpcDiscovery.model_validate_json(self.discovery_path.read_text(encoding="utf-8"))

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            if server is None:
                return
            self._server = None
            self._thread = None
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.timeout_s))
        # Discovery 先失效，避免新客户端继续连接；若沙箱/安全软件拒绝删除，写入不可连接的
        # tombstone 取代旧端点。token 同样旋转为随机废弃值，不能继续认证已关闭实例。
        try:
            tombstone_token = secrets.token_urlsafe(32)
            self._write_private(
                self.discovery_path,
                json.dumps(
                    {
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "runtime_instance_id": self.runtime_instance_id,
                        "host": IPC_HOST,
                        "port": 1,
                        "token_sha256": hashlib.sha256(tombstone_token.encode()).hexdigest(),
                        "pid": os.getpid(),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )
            self._write_private(self.token_path, tombstone_token.encode("utf-8"))
        except OSError:
            pass

    def _handle_stream(self, stream: Any) -> IpcResponse:
        raw = stream.readline(IPC_MAX_MESSAGE_BYTES + 1)
        if not raw:
            return self._error("unknown", "EmptyRequest", "IPC 请求为空")
        if len(raw) > IPC_MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            return self._error("unknown", "MessageTooLarge", "IPC 请求超过消息上限或缺少换行终止符")
        try:
            request = IpcRequest.model_validate_json(raw)
        except Exception:
            # Pydantic 错误可能带 input_value；认证前不能回显任何请求内容或 bearer token。
            return self._error("unknown", "InvalidRequest", "IPC 请求不满足协议契约")
        gate_or_fix("ipc_request", request.model_dump(mode="json"))
        if not hmac.compare_digest(request.bearer_token, self._token):
            return self._error(request.message_id, "Unauthorized", "IPC bearer token 无效")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "operation": request.operation,
                    "actor": request.actor.model_dump(mode="json"),
                    "payload": request.payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        key = (request.actor.actor_id, request.idempotency_key)
        with self._lock:
            existing = self._idempotent.get(key)
            if existing is not None:
                if existing[0] != fingerprint:
                    return self._error(
                        request.message_id,
                        "IdempotencyConflict",
                        "actor_id + idempotency_key 已用于不同 IPC 控制语义",
                    )
                return existing[1].model_copy(update={"message_id": request.message_id})
            response = self._execute(request)
            if response.ok:
                self._idempotent[key] = (fingerprint, response)
            return response

    def _execute(self, request: IpcRequest) -> IpcResponse:
        try:
            payload = request.payload
            if request.operation == "ping":
                result = {"runtime_instance_id": self.runtime_instance_id, "pid": os.getpid()}
            elif request.operation == "approval.decide":
                receipt = self.runtime.decide_approval(
                    str(payload["approval_id"]),
                    approved=bool(payload["approved"]),
                    decided_by=request.actor,
                    reason=str(payload.get("reason") or ""),
                )
                result = {"decision_receipt": receipt.model_dump(mode="json")}
            elif request.operation == "attempt.resume":
                handle, receipt = self.runtime.resume(
                    str(payload["source_request_id"]),
                    instruction=str(payload["instruction"]),
                    idempotency_key=request.idempotency_key,
                    requested_by=request.actor,
                )
                result = {
                    "handle": handle.model_dump(mode="json"),
                    "resume_receipt": receipt.model_dump(mode="json"),
                }
            elif request.operation == "attempt.steer":
                receipt = self.runtime.steer(
                    str(payload["request_id"]),
                    instruction=str(payload["instruction"]),
                    requested_by=request.actor,
                )
                result = {"steer_receipt": receipt.model_dump(mode="json")}
            else:
                accepted = self.runtime.cancel(str(payload["request_id"]))
                handle = self.runtime.status(str(payload["request_id"]))
                result = {"cancel_accepted": accepted, "handle": handle.model_dump(mode="json")}
            return IpcResponse(message_id=request.message_id, ok=True, result=result)
        except Exception as exc:
            return self._error(request.message_id, type(exc).__name__, str(exc) or repr(exc))

    @staticmethod
    def _error(message_id: str, code: str, message: str) -> IpcResponse:
        return IpcResponse(message_id=message_id, ok=False, error_code=code, error_message=message[:2000])

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid7()}.tmp")
        try:
            with temp.open("xb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


class RuntimeIpcClient:
    """从 sessions/_runtime 发现并调用活跃 Runtime IPC 服务。"""

    def __init__(self, sessions_dir: Path, *, timeout_s: float = IPC_DEFAULT_TIMEOUT_S) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        self.root = Path(sessions_dir).resolve() / "_runtime"
        self.timeout_s = timeout_s

    def call(
        self,
        operation: IpcOperation,
        *,
        actor: ActorRef,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        discovery_path = self.root / IPC_DISCOVERY_NAME
        token_path = self.root / IPC_TOKEN_NAME
        try:
            discovery = IpcDiscovery.model_validate_json(discovery_path.read_text(encoding="utf-8"))
            token = token_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            raise IpcError(f"Runtime IPC discovery 不可用: {self.root}: {exc}") from exc
        if hashlib.sha256(token.encode()).hexdigest() != discovery.token_sha256:
            raise IpcError("Runtime IPC token 与 discovery 哈希不一致")
        request = IpcRequest(
            message_id=str(uuid7()),
            operation=operation,
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload or {},
            bearer_token=token,
        )
        encoded = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode()
        try:
            with socket.create_connection((discovery.host, discovery.port), timeout=self.timeout_s) as connection:
                connection.settimeout(self.timeout_s)
                connection.sendall(encoded + b"\n")
                stream = connection.makefile("rb")
                raw = stream.readline(IPC_MAX_MESSAGE_BYTES + 1)
        except OSError as exc:
            raise IpcError(f"Runtime IPC 连接失败: {exc}") from exc
        if not raw or len(raw) > IPC_MAX_MESSAGE_BYTES:
            raise IpcError("Runtime IPC 响应为空或超过消息上限")
        try:
            response = IpcResponse.model_validate_json(raw)
        except Exception as exc:
            raise IpcError(f"Runtime IPC 响应无效: {exc}") from exc
        if response.message_id != request.message_id:
            raise IpcError("Runtime IPC 响应 message_id 不匹配")
        if not response.ok:
            raise IpcError(f"{response.error_code}: {response.error_message}")
        return response.result or {}


__all__ = [
    "IPC_DEFAULT_TIMEOUT_S",
    "IPC_DISCOVERY_NAME",
    "IPC_HOST",
    "IPC_MAX_CONCURRENT_CLIENTS",
    "IPC_MAX_MESSAGE_BYTES",
    "IPC_PROTOCOL_VERSION",
    "IPC_TOKEN_NAME",
    "IpcDiscovery",
    "IpcError",
    "IpcRequest",
    "IpcResponse",
    "RuntimeIpcClient",
    "RuntimeIpcServer",
]
