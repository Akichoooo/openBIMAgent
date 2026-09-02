"""工作台控制面鉴权（审核 🔴 修复）：本地 Bearer token 守卫。

威胁模型：本地桌面工作台——防的是局域网/同机其他进程对**变更端点**的任意调用
（改 LLM 配置、刷后台任务、审批放行、上传写盘、真机导出）。
设计：
- token 来源优先级：``OPENBIMAGENT_WORKBENCH_TOKEN`` 环境变量 → ``config/workbench.local.toml``
  （首次启动自动生成并落盘；``config/*.local.toml`` 已 gitignore，绝不入库）。
- 守卫范围：``/api/v1/**`` 的**非 GET/HEAD/OPTIONS** 请求；只读端点保持开放（M2 只读网关语义不变）。
- token 经 ``add_web_ui(token=...)`` 注入所伺服页面（同源使用，不出现在任何 API 响应体中）。
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


def add_auth(app: FastAPI, token: str) -> None:
    """注册 Bearer token 守卫中间件（仅拦截 /api/v1/** 的变更方法）。"""

    @app.middleware("http")
    async def _workbench_auth_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        if request.headers.get("authorization", "") == f"Bearer {token}":
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "error": "未授权：变更端点需要 Authorization: Bearer <workbench token>（token 见 config/workbench.local.toml 或所伺服页面注入）",
            },
        )
