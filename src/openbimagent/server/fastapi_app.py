"""M2 P2 只读 FastAPI 网络服务。

将既有框架无关的 M2ReadonlyHttpAdapter 封装为 FastAPI 应用。
该层只负责 HTTP 生命周期：接收请求、构造最小 M2ReadonlyHttpRequest 视图、
调用 adapter.dispatch()、按 M2ReadonlyHttpResponse 返回。不持有 Runtime lease，
不读取 IPC token，不构造 Runtime，不触碰文件系统。

认证、授权、限流、分页与隐私门禁均由底层 service/adapter 的协议层决定，
本模块不越过这些边界。
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response

from openbimagent.server.readonly_http import (
    M2HttpHeader,
    M2ReadonlyHttpAdapter,
    M2ReadonlyHttpRequest,
    M2ReadonlyHttpResponse,
)

M2_FASTAPI_APP_TITLE = "openBIMAgent M2 Read-Only API"
M2_FASTAPI_APP_VERSION = "0.1"


def _request_headers_to_m2(request: Request) -> tuple[M2HttpHeader, ...]:
    """将 Starlette 请求头收敛为受控 M2HttpHeader 元组（白名单 + 预算受限）。"""
    headers: list[M2HttpHeader] = []
    for name, value in request.headers.items():
        if len(headers) >= 64:
            break
        headers.append(M2HttpHeader(name=name, value=value))
    return tuple(headers)


def _body_size(request: Request) -> int:
    try:
        return int(request.headers.get("content-length", "0") or "0")
    except (TypeError, ValueError):
        return 0


def build_m2_readonly_app(adapter: M2ReadonlyHttpAdapter) -> FastAPI:
    """构建只读 FastAPI 应用；adapter 由调用方注入（持有注入的 service）。"""
    app = FastAPI(
        title=M2_FASTAPI_APP_TITLE,
        version=M2_FASTAPI_APP_VERSION,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def _readonly_gateway(request: Request) -> Response:
        m2_request = M2ReadonlyHttpRequest(
            method=request.method,
            target=request.url.path + (f"?{request.url.query}" if request.url.query else ""),
            headers=_request_headers_to_m2(request),
            body_size=_body_size(request),
        )
        m2_response: M2ReadonlyHttpResponse = adapter.dispatch(m2_request)
        return Response(
            content=m2_response.envelope.model_dump_json(),
            status_code=m2_response.status_code,
            media_type="application/json",
        )

    return app