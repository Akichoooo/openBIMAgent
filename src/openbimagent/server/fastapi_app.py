"""M2 P2 只读 FastAPI 网络服务。

将既有框架无关的 M2ReadonlyHttpAdapter 封装为 FastAPI 应用。
该层只负责 HTTP 生命周期：接收请求、构造最小 M2ReadonlyHttpRequest 视图、
调用 adapter.dispatch()、按 M2ReadonlyHttpResponse 返回。不持有 Runtime lease，
不读取 IPC token，不构造 Runtime，不触碰文件系统。

认证、授权、限流、分页与隐私门禁均由底层 service/adapter 的协议层决定，
本模块不越过这些边界。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response

from openbimagent.server.readonly_http import (
    M2HttpHeader,
    M2ReadonlyHttpAdapter,
    M2ReadonlyHttpRequest,
    M2ReadonlyHttpResponse,
)
from openbimagent.server.sse_endpoint import M2SseStreamBudget, add_sse_endpoint
from openbimagent.server.web_ui import add_web_ui

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


def build_m2_readonly_app(
    adapter: M2ReadonlyHttpAdapter,
    *,
    sessions_dir: Path | None = None,
    sse_budget: M2SseStreamBudget | None = None,
) -> FastAPI:
    """构建只读 FastAPI 应用；adapter 由调用方注入（持有注入的 service）。

    ``sessions_dir`` 是可选的 SSE 端点目录；未提供时跳过 SSE 端点注册。
    """
    app = FastAPI(
        title=M2_FASTAPI_APP_TITLE,
        version=M2_FASTAPI_APP_VERSION,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    if sessions_dir is not None:
        add_sse_endpoint(app, sessions_dir=sessions_dir, budget=sse_budget)
    add_web_ui(app)

    @app.get("/api/v1/plugins", summary="获取已加载插件清单与 Profile 列表", tags=["Plugins"])
    async def get_plugins_inventory() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        return default_plugin_registry.export_inventory()

    @app.get("/api/v1/ui/slots", summary="获取声明式 UI 插槽注册表", tags=["Plugins"])
    async def get_ui_slots() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        inv = default_plugin_registry.export_inventory()
        return {"slots": inv["ui_slots"], "total_slots": len(inv["ui_slots"])}

    @app.post("/api/v1/plugins/invoke", summary="通过微内核调度执行插件能力", tags=["Plugins"])
    async def invoke_plugin_capability(request: Request) -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        body = await request.json()
        capability = body.get("capability")
        if not capability:
            return {"status": "error", "error": "缺少 capability 参数"}
        payload = body.get("payload", {})
        try:
            res = default_plugin_registry.invoke(capability, **payload)
            if hasattr(res, "model_dump"):
                result_data = res.model_dump()
            elif hasattr(res, "to_dict"):
                result_data = res.to_dict()
            elif hasattr(res, "_asdict"):
                result_data = res._asdict()
            elif isinstance(res, (dict, list, int, float, bool, str)) or res is None:
                result_data = res
            else:
                result_data = str(res)
            return {"status": "success", "capability": capability, "result": result_data}
        except Exception as exc:
            return {"status": "error", "capability": capability, "error": str(exc)}

    @app.api_route(
        "/api/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
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