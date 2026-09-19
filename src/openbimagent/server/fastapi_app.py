"""M2 P2 只读 FastAPI 网络服务。

将既有框架无关的 M2ReadonlyHttpAdapter 封装为 FastAPI 应用。
该层只负责 HTTP 生命周期：接收请求、构造最小 M2ReadonlyHttpRequest 视图、
调用 adapter.dispatch()、按 M2ReadonlyHttpResponse 返回。不持有 Runtime lease，
不读取 IPC token，不构造 Runtime，不触碰文件系统。

认证、授权、限流、分页与隐私门禁均由底层 service/adapter 的协议层决定，
本模块不越过这些边界。
"""

from __future__ import annotations

import asyncio
import threading
import os
import json
from dataclasses import dataclass
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

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

INVOKE_OVERLOADED_STATUS_CODE = 503
INVOKE_OVERLOADED_ERROR_CODE = -32001
INVOKE_OVERLOADED_MESSAGE = "Server overloaded; retry later."


class InvokeConcurrencyGuard:
    """invoke 端点有界并发背压（对标 Codex app-server 有界队列 + -32001 过载语义）。

    非阻塞获取：满载立即拒绝而不是排队堆积，由调用方返回 503 让客户端重试。
    """

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency 必须 ≥ 1: {max_concurrency}")
        self._semaphore = threading.Semaphore(max_concurrency)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


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
    invoke_max_concurrency: int = 4,
    settings: AppSettings | None = None,
) -> FastAPI:
    """构建只读 FastAPI 应用；adapter 由调用方注入（持有注入的 service）。

    ``sessions_dir`` 是可选的 SSE 端点目录；未提供时跳过 SSE 端点注册。
    ``invoke_max_concurrency`` 是 /api/v1/plugins/invoke 的有界并发上限，
    满载返回 503 + error code -32001（对标 Codex app-server 背压语义）。
    """
    app = FastAPI(
        title=M2_FASTAPI_APP_TITLE,
        version=M2_FASTAPI_APP_VERSION,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    from openbimagent.server.auth import add_auth, load_or_create_token

    if settings is not None:
        app.state.mode = settings.mode
        app.state.workspace_roots = settings.workspace_roots
        from openbimagent.server.chat import add_chat, add_chat_stream
        from openbimagent.server.runs import add_runs
        from openbimagent.server.approvals import add_approvals
        from openbimagent.server.workspaces import register_workspace_routes
        from openbimagent.server.workbench_io import add_workbench_io

        add_chat(app)
        add_chat_stream(app)
        add_runs(app)
        add_approvals(app)
        register_workspace_routes(app)
        add_workbench_io(app)
    elif sessions_dir is not None:
        add_sse_endpoint(app, sessions_dir=sessions_dir, budget=sse_budget)
    workbench_token = (settings.token if settings else None) or load_or_create_token()
    add_web_ui(app, token=workbench_token)

    @app.middleware("http")
    async def feature_availability(request: Request, call_next):
        path = request.url.path
        # These legacy exports have no parameter/plan-bound approval receipt.
        # Never turn a browser confirm flag into host execution authorization.
        if path in ("/api/v1/demo/export-blender", "/api/v1/demo/export-vectorworks"):
            # Authentication middleware must run first (registered last below).
            return JSONResponse(status_code=503, content={
                "status": "unavailable", "code": "approval_binding_required",
                "error": "Use the run approval workflow; direct demo exports are disabled",
            })
        if getattr(app.state, "control_plane_unavailable", False) and (
            path.startswith("/api/v1/attempts") or path.startswith("/api/v1/lineages")
        ):
            return JSONResponse(status_code=503, content={"status": "unavailable", "code": "control_plane_unavailable"})
        if settings and settings.mode != "demo" and path.startswith("/api/v1/demo/"):
            return JSONResponse(status_code=503, content={"status": "unavailable", "code": "demo_mode_required"})
        return await call_next(request)

    # Starlette executes last-added middleware first: auth before availability/handlers.
    add_auth(app, workbench_token, protect_reads=settings is not None)

    invoke_guard = InvokeConcurrencyGuard(invoke_max_concurrency)
    export_guard = InvokeConcurrencyGuard(1)  # 真机导出串行：Blender/VW 共用，防并发多宿主写盘

    @app.get("/api/v1/mcp/health", tags=["Workbench"])
    async def mcp_health() -> dict:
        """A9:MCP 健康面板。

        只读 app 不持有活 MCP client;此处返回工程声明的宿主清单与连接提示。
        活体探针(ping 三态:connected/needs-auth/failed)由 runtime-serve /
        operator-console 进程提供;AgentLoop 侧的 mcp_call ping 是真实探针入口。
        """
        from openbimagent.core.loop import TOOL_NAMES

        return {
            "status": "success",
            "declared_hosts": ["blender", "vectorworks"],
            "live_probe_endpoint": "runtime-serve (mcp_call ping)",
            "loop_mcp_governance": "execute_plan / ping / describe_capabilities / lookup_api",
            "tools": list(TOOL_NAMES),
        }

    @app.get("/api/v1/context", tags=["Workbench"])
    async def context_breakdown() -> dict:
        """C8:上下文构成可视化(claude /context),只读 app 报告可注入项的存量与预算。"""
        from openbimagent.core.loop import MAX_SYSTEM_PROMPT_TOKENS, TOOL_NAMES

        return {
            "status": "success",
            "system_prompt_token_budget": MAX_SYSTEM_PROMPT_TOKENS,
            "tools_catalog": list(TOOL_NAMES),
            "workspace_instructions_source": "WORKSPACE.md (root -> workdir, codex AGENTS.md style)",
            "compaction": {
                "keep_recent_groups": 8,
                "keep_recent_tokens": 20000,
                "context_budget_ratio": 0.8,
                "split_turn_elision": True,
            },
            "memory": "core/memory.py MEMORY.md + USER.md (approval-gated writes)",
            "skills": "progressive disclosure (catalog_fragment, body on invoke)",
            "note": "活会话的实际上下文构成需 runtime-serve;本端点只报可注入项的契约",
        }

    @app.get("/api/v1/runtime", tags=["Workbench"])
    async def runtime_mode() -> dict:
        return {"status": "success", "mode": settings.mode if settings else "readonly",
                "direct_host_exports": False, "direct_prompt_invocation": False}

    @app.get("/api/v1/hooks", tags=["Workbench"])
    async def hooks_unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "status": "unavailable", "code": "hooks_configuration_unavailable",
            "error": "Lifecycle hooks exist; HTTP configuration is not implemented",
        })

    @app.get("/healthz", include_in_schema=False, tags=["Health"])
    async def healthz() -> dict:
        """存活探针（对标 Codex app-server /healthz）：进程在即 200。"""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False, tags=["Health"])
    async def readyz() -> Response:
        """就绪探针：微内核必须已装载插件且具备核心求解能力。"""
        from openbimagent.core.plugin import default_plugin_registry

        inventory = default_plugin_registry.export_inventory()
        ready = inventory["active_plugins"] and inventory["capabilities_map"].get("solver:self_healing")
        if not ready:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "active_plugins": inventory["plugin_count"]},
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "plugin_count": inventory["plugin_count"],
                "total_capabilities": inventory["total_capabilities"],
            },
        )

    @app.get("/api/v1/plugins", summary="获取已加载插件清单与 Profile 列表", tags=["Plugins"])
    async def get_plugins_inventory() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        from openbimagent.core.toolset import current_toolset, filter_capabilities

        inventory = default_plugin_registry.export_inventory()
        inventory["capabilities_map"] = filter_capabilities(inventory["capabilities_map"])
        inventory["total_capabilities"] = len(inventory["capabilities_map"])
        inventory["toolset"] = current_toolset()
        return inventory

    @app.get("/api/v1/ui/slots", summary="获取声明式 UI 插槽注册表", tags=["Plugins"])
    async def get_ui_slots() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        inv = default_plugin_registry.export_inventory()
        return {"slots": inv["ui_slots"], "total_slots": len(inv["ui_slots"])}

    @app.post("/api/v1/plugins/invoke", summary="通过微内核调度执行插件能力", tags=["Plugins"])
    async def invoke_plugin_capability(request: Request) -> Response:
        from openbimagent.core.plugin import default_plugin_registry

        body = await request.json()
        capability = body.get("capability")
        if not capability:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": "缺少 capability 参数"},
            )
        from openbimagent.core.toolset import current_toolset, is_allowed

        if not is_allowed(capability):
            return JSONResponse(status_code=403, content={
                "status": "error", "code": "toolset_denied",
                "error": f"工具集 {current_toolset()} 不允许能力 {capability}",
            })
        payload = body.get("payload", {})
        from openbimagent.core.plugin import CapabilityPolicyDecision

        policy = default_plugin_registry.capability_policy_for(capability)
        if policy and policy.decision is CapabilityPolicyDecision.FORBIDDEN:
            return JSONResponse(status_code=403, content={"status": "error", "code": "policy_denied"})
        if policy and policy.decision is CapabilityPolicyDecision.PROMPT:
            return JSONResponse(status_code=503, content={
                "status": "unavailable", "code": "approval_binding_required",
                "error": "Direct invocation has no bound approval; use the run approval workflow",
            })
        confirm = False
        if not invoke_guard.try_acquire():
            return JSONResponse(
                status_code=INVOKE_OVERLOADED_STATUS_CODE,
                content={
                    "status": "error",
                    "capability": capability,
                    "error": {
                        "code": INVOKE_OVERLOADED_ERROR_CODE,
                        "message": INVOKE_OVERLOADED_MESSAGE,
                    },
                },
            )
        try:
            # 求解器为同步 CPU 密集实现：放线程池执行，避免占死事件循环
            res = await asyncio.to_thread(
                default_plugin_registry.invoke, capability, confirm=confirm, **payload
            )
        except Exception as exc:  # noqa: BLE001 — 端点返回结构化错误而非 500
            return JSONResponse(
                status_code=200,
                content={"status": "error", "capability": capability, "error": str(exc)},
            )
        finally:
            invoke_guard.release()
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

    @app.get(
        "/api/v1/demo/municipal-pipeline",
        summary="运行内置自愈演示场景，返回真实 Compiled IR 与自愈时间线",
        tags=["Plugins"],
    )
    async def demo_municipal_pipeline() -> dict:
        from openbimagent.benchmark.self_healing_ablation import build_demo_invocation
        from openbimagent.core.plugin import default_plugin_registry

        try:
            res = default_plugin_registry.invoke("solver:self_healing", **build_demo_invocation())
        except Exception as exc:  # noqa: BLE001 — 演示端点失败返回可读错误而非 500
            return {"status": "error", "error": f"自愈演示调度失败: {exc}"}

        if res.final_ir is None:
            return {"status": "error", "error": "自愈演示未收敛，无 final IR"}

        ir = res.final_ir
        nodes = [
            {
                "node_id": n.node_id,
                "type": getattr(n.node_type, "value", str(n.node_type)),
                "x": n.position.x_m,
                "y": n.position.y_m,
                "invert_z": n.position.z_m,
                "ground": n.ground_elevation_m,
            }
            for n in ir.nodes
        ]
        segments = [
            {
                "segment_id": s.segment_id,
                "points": [{"x": p.x_m, "y": p.y_m, "z": p.z_m} for p in s.centerline],
                "diameter_mm": s.diameter_mm,
                "slope": s.slope,
                "length_m": s.horizontal_length_m,
                "start_invert_m": s.start_invert_m,
                "end_invert_m": s.end_invert_m,
            }
            for s in ir.segments
        ]
        violations = [
            {
                "rule_id": v.rule_id,
                "target_id": v.target_id,
                "violation_type": v.violation_type,
                "location_xy": list(v.location_xy),
                "required": v.required_value,
                "actual": v.actual_value,
                "description": v.description,
            }
            for v in res.resolved_violations
        ]
        timeline = [
            {
                "iteration": it.iteration,
                "route_status": it.route_status,
                "rule_fail_count": it.rule_fail_count,
                "converged": it.converged,
            }
            for it in res.iteration_history
        ]
        return {
            "status": "success",
            "converged": res.converged,
            "iterations_spent": res.iterations_spent,
            "resolved_violations": violations,
            "timeline": timeline,
            "log": list(res.log),
            "nodes": nodes,
            "segments": segments,
        }

    @app.post(
        "/api/v1/demo/export-blender",
        summary="真实 Blender 5.2 受控导出（prompt 策略，body 须 confirm=true）",
        tags=["Plugins"],
    )
    async def export_blender(request: Request) -> Response:
        """微内核全链路真机导出：自愈求解 → 策略门 → headless Blender execute_plan。

        能力 ``cad_host:blender.execute`` 默认 prompt 策略；本端点把 body 的
        ``confirm`` 透传给 invoke，未确认时由策略门拒绝（人确认语义在调用方）。
        """
        from openbimagent.core.plugin import default_plugin_registry

        body = await request.json()
        confirm = bool(body.get("confirm", False))
        if not export_guard.try_acquire():
            return JSONResponse(
                status_code=INVOKE_OVERLOADED_STATUS_CODE,
                content={
                    "status": "error",
                    "error": {
                        "code": INVOKE_OVERLOADED_ERROR_CODE,
                        "message": INVOKE_OVERLOADED_MESSAGE,
                    },
                },
            )
        try:
            from openbimagent.benchmark.self_healing_ablation import build_demo_invocation

            solved = await asyncio.to_thread(
                default_plugin_registry.invoke, "solver:self_healing", **build_demo_invocation()
            )
            if not solved.converged or solved.final_ir is None:
                return {"status": "error", "error": "演示场景未收敛，无 IR 可导出"}
            receipt = await asyncio.to_thread(
                default_plugin_registry.invoke,
                "cad_host:blender.execute",
                ir=solved.final_ir,
                confirm=confirm,
            )
            return {"status": "success", "receipt": receipt}
        except Exception as exc:  # noqa: BLE001 — 结构化错误而非 500
            return JSONResponse(
                status_code=200,
                content={"status": "error", "error": str(exc)},
            )
        finally:
            export_guard.release()

    @app.post(
        "/api/v1/demo/export-vectorworks",
        summary="真实 Vectorworks 受控导出（prompt 策略，body 须 confirm=true）",
        tags=["Plugins"],
    )
    async def export_vectorworks(request: Request) -> Response:
        """微内核全链路真机导出：自愈求解 → 策略门 → VW 宿主 runner execute_plan。

        前置：VW 应用已运行且已加载 runner（jobs 目录经
        OPENBIMAGENT_VW_JOBS_DIR/RESULTS_DIR/AUTHORIZED_ROOT 配置一致）。
        """
        from openbimagent.core.plugin import default_plugin_registry

        body = await request.json()
        confirm = bool(body.get("confirm", False))
        if not export_guard.try_acquire():
            return JSONResponse(
                status_code=INVOKE_OVERLOADED_STATUS_CODE,
                content={
                    "status": "error",
                    "error": {
                        "code": INVOKE_OVERLOADED_ERROR_CODE,
                        "message": INVOKE_OVERLOADED_MESSAGE,
                    },
                },
            )
        try:
            from openbimagent.benchmark.self_healing_ablation import build_demo_invocation

            solved = await asyncio.to_thread(
                default_plugin_registry.invoke, "solver:self_healing", **build_demo_invocation()
            )
            if not solved.converged or solved.final_ir is None:
                return {"status": "error", "error": "演示场景未收敛，无 IR 可导出"}
            receipt = await asyncio.to_thread(
                default_plugin_registry.invoke,
                "cad_host:vectorworks.execute",
                ir=solved.final_ir,
                confirm=confirm,
            )
            return {"status": "success", "receipt": receipt}
        except Exception as exc:  # noqa: BLE001 — 结构化错误而非 500
            return JSONResponse(
                status_code=200,
                content={"status": "error", "error": str(exc)},
            )
        finally:
            export_guard.release()

    @app.get(
        "/api/v1/demo/rule-tree",
        summary="真实 MunicipalRuleSet v1.2 规则树（经微内核 rules:gb50289 编译）",
        tags=["Plugins"],
    )
    async def demo_rule_tree() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        try:
            rule_set = default_plugin_registry.invoke("rules:gb50289")
        except Exception as exc:  # noqa: BLE001 — 结构化错误而非 500
            return {"status": "error", "error": str(exc)}
        rules = [
            {
                "rule_key": rule.rule_key,
                "source_rule_id": rule.source_rule_id,
                "obstacle_kind": rule.obstacle_kind,
                "obstacle_category": rule.obstacle_category,
                "required_clearance_m": rule.required_clearance_m,
                "source_clause": rule.source_clause,
                "confidence": rule.confidence.value,
                "enforcement": rule.enforcement.value,
                "self_test_match": len(rule.self_tests.match),
                "self_test_not_match": len(rule.self_tests.not_match),
                "standard_id": rule.verification.standard_id,
                "clause": rule.verification.clause,
                "table": rule.verification.table,
                "production_eligible": rule.verification.production_eligible(),
            }
            for rule in rule_set.rules
        ]
        return {
            "status": "success",
            "protocol_version": rule_set.protocol_version,
            "compiler_version": rule_set.compiler_version,
            "canonical_sha256": rule_set.canonical_sha256,
            "total_rules": len(rules),
            "rules": rules,
        }

    @app.get(
        "/api/v1/demo/runtime-info",
        summary="运行时信息：LLM 基线模型（不含 key）+ 微内核统计",
        tags=["Plugins"],
    )
    async def demo_runtime_info() -> dict:
        from openbimagent.core.plugin import default_plugin_registry

        try:
            from openbimagent.benchmark.llm_direct_baseline import load_llm_baseline_config

            cfg = load_llm_baseline_config()
        except Exception:  # noqa: BLE001 — 配置缺失不致命, 前端显示未配置
            cfg = None
        inventory = default_plugin_registry.export_inventory()
        return {
            "status": "success",
            "llm": {
                "configured": cfg is not None,
                "model": cfg.model if cfg else None,
                "base_url": cfg.base_url if cfg else None,
            },
            "registry": {
                "plugins": inventory["plugin_count"],
                "capabilities": inventory["total_capabilities"],
                "policies": len(inventory["capability_policies"]),
            },
        }

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


@dataclass(frozen=True)
class AppSettings:
    mode: Literal["local-production", "demo", "test"] = "local-production"
    token: str | None = None
    workspace_roots: tuple[Path, ...] = (Path(__file__).resolve().parents[3],)


@dataclass(frozen=True)
class AppServices:
    readonly_adapter: M2ReadonlyHttpAdapter | None = None


def create_app(settings: AppSettings | None = None, services: AppServices | None = None) -> FastAPI:
    settings = settings or AppSettings()
    if settings.mode not in ("local-production", "demo", "test"):
        raise ValueError("unknown app mode")
    adapter = services.readonly_adapter if services else None
    if adapter is None:
        from openbimagent.server.service import M2ReadOnlyService
        from openbimagent.server.workspaces import _sessions_dir

        class UnavailableControlPlane:
            def __getattr__(self, name):
                def unavailable(*args, **kwargs):
                    raise RuntimeError("control plane is not configured")
                return unavailable

        def read_index():
            path = _sessions_dir() / "index.json"
            return json.loads(path.read_text(encoding="utf-8")).get("sessions", []) if path.is_file() else []

        class WorkbenchReadOnlyService(M2ReadOnlyService):
            @staticmethod
            def _session_metadata(entry):
                return {**M2ReadOnlyService._session_metadata(entry),
                        **{key: entry[key] for key in ("workspace", "playbook", "archived", "mode") if key in entry}}

        adapter = M2ReadonlyHttpAdapter(WorkbenchReadOnlyService(
            control_plane=UnavailableControlPlane(), session_index_reader=read_index,
            artifact_lookup=lambda _: None,
        ))
    app = build_m2_readonly_app(adapter, settings=settings)
    app.state.control_plane_unavailable = not (services and services.readonly_adapter)
    return app


def build_demo_app() -> FastAPI:
    """Explicit demo mode, with the same authentication and business routes."""
    return create_app(AppSettings(mode="demo"))


# Bind uvicorn to 127.0.0.1; middleware also rejects non-local peers/hosts.
app = create_app(AppSettings(mode=os.environ.get("OPENBIMAGENT_APP_MODE", "local-production")))