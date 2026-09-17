"""本地工作台 Bearer 认证与 Host/Origin 边界。

Token 来源：环境变量 OPENBIMAGENT_WORKBENCH_TOKEN 或 config/workbench.local.toml。
正式工作台 API 包括敏感读取均需认证；显式 M2 只读装配保留开放读取。
Token 不注入匿名 HTML；本机操作员自行配置客户端凭据。认证不等于审批或执行隔离。
"""

from __future__ import annotations

import os
import secrets
import tomllib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOKEN_FILE = _REPO_ROOT / "config" / "workbench.local.toml"


def load_or_create_token() -> str:
    """取工作台 token；环境变量优先，否则读/建 config/workbench.local.toml。"""
    env_token = os.environ.get("OPENBIMAGENT_WORKBENCH_TOKEN", "").strip()
    if env_token:
        return env_token
    if _TOKEN_FILE.is_file():
        try:
            token = str(tomllib.loads(_TOKEN_FILE.read_text(encoding="utf-8")).get("token", "")).strip()
            if token:
                return token
        except (tomllib.TOMLDecodeError, OSError):
            pass
    token = secrets.token_urlsafe(24)
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(f'# 工作台控制面 token（自动生成，gitignored；可用 OPENBIMAGENT_WORKBENCH_TOKEN 覆盖）\ntoken = "{token}"\n', encoding="utf-8")
    except OSError:
        pass  # 落盘失败仅影响持久化，本次进程内 token 仍有效
    return token


def add_auth(app: FastAPI, token: str, *, protect_reads: bool = True, local_only: bool = True) -> None:
    """本地单操作员认证。Host/Origin 边界不替代 Bearer 身份验证。"""
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    from openbimagent.server.correlation_identity import is_m2_correlation_id

    if not token.strip():
        raise ValueError("workbench token must not be empty")

    def loopback(host: str | None) -> bool:
        if host == "localhost":
            return True
        try:
            return ip_address(host or "").is_loopback
        except ValueError:
            return False

    @app.middleware("http")
    async def _workbench_auth_guard(request: Request, call_next):
        def reject(status: int, code: str, message: str) -> JSONResponse:
            return JSONResponse(status_code=status, content={"status": "error", "code": code, "error": message})

        if local_only:
            # testclient is an in-process ASGI transport, never a TCP peer address.
            peer = request.client.host if request.client else None
            in_process = peer == "testclient" and request.url.hostname == "testserver"
            if not in_process and (not loopback(request.url.hostname) or (peer != "testclient" and not loopback(peer))):
                return reject(403, "local_only", "仅允许本机访问")
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    valid = parsed.scheme in ("http", "https") and loopback(parsed.hostname)
                except ValueError:
                    valid = False
                if not valid:
                    return reject(403, "origin_denied", "Origin 不在本机边界内")
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        needs_auth = protect_reads or request.method not in ("GET", "HEAD", "OPTIONS")
        if needs_auth:
            values = request.headers.getlist("authorization")
            if len(values) != 1 or not secrets.compare_digest(values[0].encode(), f"Bearer {token}".encode()):
                return reject(401, "unauthorized", "需要 Authorization: Bearer <workbench token>")
            request.state.actor = "human:web-operator"
        ids = request.headers.getlist("x-request-id")
        if len(ids) != 1 or not is_m2_correlation_id(ids[0]):
            if not protect_reads:
                from openbimagent.server.contracts import M2ApiEnvelope, M2ErrorCode, make_m2_api_error
                error = make_m2_api_error(code=M2ErrorCode.INVALID_REQUEST,
                                          message="缺失、重复或非法 X-Request-ID", request_id="invalid-request")
                envelope = M2ApiEnvelope(request_id="invalid-request", ok=False, error=error)
                return JSONResponse(status_code=400, content=envelope.model_dump(mode="json"))
            return reject(400, "invalid_request", "缺失、重复或非法 X-Request-ID")
        response = await call_next(request)
        response.headers["X-Request-ID"] = ids[0]
        return response
