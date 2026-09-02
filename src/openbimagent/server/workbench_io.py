"""工作台 I/O 端点：LLM 设置读写 + 附件上传（功能打通，非演示）。

设计约束：
- API key **只写不回显**：GET 仅暴露 ``key_set`` 布尔；``llm_baseline.local.toml`` 与 ``.env`` 均已被 gitignore。
- 管道角色 provider key 写入进程环境（即时生效）并持久化到 ``.env``（供 CLI/重启加载）。
- 路径可用环境变量覆盖以便测试隔离：``OPENBIMAGENT_LLM_BASELINE`` / ``OPENBIMAGENT_UPLOADS_DIR``。
- 上传为原始字节流（``?name=`` 或 ``X-Filename`` 头），不引入 python-multipart 依赖。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BASELINE = _REPO_ROOT / "config" / "llm_baseline.local.toml"
_UPLOAD_INDEX = "index.json"
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: 管道角色（models.toml [providers.*].api_key_env）允许经设置页写入的环境变量白名单
_PROVIDER_ENV_KEYS = (
    "GLM_API_KEY",
    "GEMINI_API_KEY",
    "AGENTROUTER_API_KEY",
    "FREETOKENFAUCET_API_KEY",
)
_PLACEHOLDER_PREFIX = "sk-replace-me"


def _baseline_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_LLM_BASELINE")
    return Path(override) if override else _DEFAULT_BASELINE


def _env_file() -> Path:
    override = os.environ.get("OPENBIMAGENT_ENV_FILE")
    return Path(override) if override else _REPO_ROOT / ".env"


def _uploads_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_UPLOADS_DIR")
    return Path(override) if override else _REPO_ROOT / "out" / "uploads"


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_toml_flat(path: Path, data: dict[str, Any]) -> None:
    """扁平 key=value TOML 序列化（llm_baseline.local.toml 为扁平结构）。"""
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f"{key} = {_toml_quote(str(value))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_baseline_raw() -> dict[str, Any]:
    path = _baseline_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _settings_payload() -> dict[str, Any]:
    raw = _read_baseline_raw()
    key = str(raw.get("api_key", "")).strip()
    key_set = bool(key) and not key.startswith(_PLACEHOLDER_PREFIX)
    return {
        "status": "success",
        "baseline": {
            "configured": key_set,
            "model": raw.get("model"),
            "base_url": raw.get("base_url"),
            "api_key_set": key_set,
            "path": str(_baseline_path()),
        },
        "provider_keys": [{"env": name, "key_set": bool(os.environ.get(name))} for name in _PROVIDER_ENV_KEYS],
    }


def _update_env_file(updates: dict[str, str]) -> None:
    """把 key 写入 .env（同名键替换行，其余行保留）。"""
    existing: list[str] = []
    env_file = _env_file()
    if env_file.is_file():
        existing = env_file.read_text(encoding="utf-8").splitlines()
    remaining = [ln for ln in existing if not any(ln.startswith(f"{k}=") for k in updates)]
    remaining.extend(f"{k}={v}" for k, v in updates.items())
    env_file.write_text("\n".join(remaining) + "\n", encoding="utf-8")


def _safe_filename(name: str) -> str:
    base = Path(name).name.strip().replace(" ", "_")
    return re.sub(r"[^\w.\-一-鿿]", "", base) or "file.bin"


def _read_upload_index(uploads: Path) -> list[dict[str, Any]]:
    index = uploads / _UPLOAD_INDEX
    if not index.is_file():
        return []
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def add_workbench_io(app: FastAPI) -> None:
    """注册设置与上传端点（由 build_m2_readonly_app 调用）。"""

    @app.get(
        "/api/v1/settings/llm",
        summary="读取 LLM 设置（基线模型/端点 + provider key 状态；永不含 key 本体）",
        tags=["Workbench"],
    )
    async def get_llm_settings() -> dict:
        return _settings_payload()

    @app.put(
        "/api/v1/settings/llm",
        summary="保存 LLM 设置：写 llm_baseline.local.toml；provider key 入环境并持久化 .env",
        tags=["Workbench"],
    )
    async def put_llm_settings(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — json() 对非法 body 抛多种异常
            return JSONResponse(status_code=400, content={"status": "error", "error": "body 必须是 JSON 对象"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"status": "error", "error": "body 必须是 JSON 对象"})

        raw = _read_baseline_raw()
        for field in ("model", "base_url", "api_key"):
            value = body.get(field)
            if isinstance(value, str) and value.strip():
                raw[field] = value.strip()
        for key, default in {"max_scenarios": 10, "repetitions": 3, "temperature": 0.0, "request_timeout_s": 60}.items():
            raw.setdefault(key, default)
        try:
            _write_toml_flat(_baseline_path(), raw)
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"配置写入失败: {exc}"})

        provider_keys = body.get("provider_keys")
        env_updates: dict[str, str] = {}
        if isinstance(provider_keys, dict):
            for env_name, value in provider_keys.items():
                if env_name in _PROVIDER_ENV_KEYS and isinstance(value, str) and value.strip():
                    os.environ[env_name] = value.strip()
                    env_updates[env_name] = value.strip()
        if env_updates:
            try:
                _update_env_file(env_updates)
            except OSError:
                pass  # .env 持久化失败不影响进程内即时生效
        return JSONResponse(content=_settings_payload())

    @app.get("/api/v1/uploads", summary="上传附件清单（manifest 索引）", tags=["Workbench"])
    async def list_uploads() -> dict:
        return {"status": "success", "items": _read_upload_index(_uploads_dir())}

    @app.post("/api/v1/uploads", summary="上传附件（原始字节流；sha256 manifest 落盘）", tags=["Workbench"])
    async def upload_file(request: Request) -> JSONResponse:
        data = await request.body()
        if not data:
            return JSONResponse(status_code=400, content={"status": "error", "error": "空 body"})
        if len(data) > _MAX_UPLOAD_BYTES:
            return JSONResponse(status_code=413, content={"status": "error", "error": "超过 64MB 上限"})
        original = request.query_params.get("name") or request.headers.get("x-filename") or "file.bin"
        uploads = _uploads_dir()
        uploads.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stored = f"{stamp}_{_safe_filename(original)}"
        try:
            (uploads / stored).write_bytes(data)
            item = {
                "id": stored,
                "name": Path(original).name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "uploaded_at": stamp,
            }
            index = _read_upload_index(uploads)
            index.append(item)
            (uploads / _UPLOAD_INDEX).write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"落盘失败: {exc}"})
        return JSONResponse(content={"status": "success", "item": item})
