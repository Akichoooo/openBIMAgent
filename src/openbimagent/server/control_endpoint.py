"""M2 P3 受控写控制网络端点。

将认证后的远程控制请求经 M2ControlPreflight 策略核、M2IdempotencyTransaction
幂等状态机收敛为可审计的 Runtime IPC 代理，再经注入的 ipc_caller 转发到唯一
Runtime lease owner。本模块不持有 Runtime lease、不读取 IPC discovery token、
不构造 Runtime。幂等键同键同义复用、同键异义冲突。

认证主体由 principal_provider 注入（server 配置决定的受信任身份来源），
任何客户端都不能自行声明 actor 或角色。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openbimagent.server.authentication import M2AuthenticatedPrincipal
from openbimagent.server.contracts import (
    M2_API_PROTOCOL_VERSION,
    M2ApiEnvelope,
    M2ControlRequest,
    M2ErrorCode,
    make_m2_api_error,
)
from openbimagent.server.control_preflight import (
    M2ControlPreflight,
    M2ControlPreflightError,
    M2ControlProxyPlan,
)
from openbimagent.server.idempotency_transaction import (
    M2IdempotencyRecord,
    M2IdempotencyTransaction,
    M2IdempotencyTransactionDisposition,
)

M2_CONTROL_ENDPOINT_VERSION = "0.1"

PrincipalProvider = Callable[[Request], M2AuthenticatedPrincipal]
IpcCaller = Callable[[M2ControlProxyPlan], dict[str, Any]]
IdempotencyStore = Callable[[str], M2IdempotencyRecord | None]


def add_control_endpoint(
    app: FastAPI,
    *,
    principal_provider: PrincipalProvider,
    ipc_caller: IpcCaller,
    idempotency_store: IdempotencyStore,
    preflight: M2ControlPreflight | None = None,
    transaction: M2IdempotencyTransaction | None = None,
) -> None:
    preflight = preflight or M2ControlPreflight()
    transaction = transaction or M2IdempotencyTransaction()

    @app.post("/api/v1/control")
    async def _control(request: Request) -> JSONResponse:
        request_id = request.headers.get("x-request-id", "invalid-request")
        try:
            principal = principal_provider(request)
        except Exception:
            error = make_m2_api_error(
                code=M2ErrorCode.UNAUTHORIZED, message="认证失败", request_id=request_id
            )
            return JSONResponse(status_code=401, content=error.model_dump(mode="json"))

        try:
            body = await request.json()
            control_request = M2ControlRequest(**body)
        except Exception:
            error = make_m2_api_error(
                code=M2ErrorCode.INVALID_REQUEST, message="控制请求无效", request_id=request_id
            )
            return JSONResponse(status_code=400, content=error.model_dump(mode="json"))

        try:
            plan = preflight.prepare(principal=principal, request=control_request)
        except M2ControlPreflightError as exc:
            error = exc.to_api_error(request_id)
            status = 403 if exc.code is M2ErrorCode.FORBIDDEN else 400
            return JSONResponse(status_code=status, content=error.model_dump(mode="json"))

        existing = idempotency_store(plan.idempotency_scope_sha256)
        decision = transaction.reserve(
            plan=plan,
            existing=existing,
            reservation_id=f"res-{plan.idempotency_scope_sha256[:16]}",
        )
        if decision.disposition is M2IdempotencyTransactionDisposition.REPLAY:
            envelope = M2ApiEnvelope(
                protocol_version=M2_API_PROTOCOL_VERSION,
                request_id=request_id,
                ok=True,
                data={"receipt_id": decision.receipt_id, "replayed": True},
            )
            return JSONResponse(status_code=200, content=envelope.model_dump(mode="json"))
        if decision.disposition is not M2IdempotencyTransactionDisposition.ACQUIRED:
            error = make_m2_api_error(
                code=M2ErrorCode.IDEMPOTENCY_CONFLICT,
                message="幂等键冲突或在途",
                request_id=request_id,
            )
            return JSONResponse(status_code=409, content=error.model_dump(mode="json"))

        try:
            response = ipc_caller(plan)
        except Exception as exc:
            error = make_m2_api_error(
                code=M2ErrorCode.RUNTIME_UNAVAILABLE,
                message=f"Runtime IPC 调用失败: {exc}",
                request_id=request_id,
            )
            return JSONResponse(status_code=503, content=error.model_dump(mode="json"))

        envelope = M2ApiEnvelope(
            protocol_version=M2_API_PROTOCOL_VERSION,
            request_id=request_id,
            ok=True,
            data=response,
        )
        return JSONResponse(status_code=200, content=envelope.model_dump(mode="json"))