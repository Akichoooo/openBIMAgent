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
import threading
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BASELINE = _REPO_ROOT / "config" / "llm_baseline.local.toml"
_CUSTOM_PROVIDERS_FILE = _REPO_ROOT / "config" / "custom_providers.local.json"
_UPLOAD_INDEX = "index.json"
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024

_providers_lock = threading.Lock()

_PRESET_PROVIDERS: list[dict[str, Any]] = []

_DEFAULT_CUSTOM_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "prov_sensenova_jy",
        "name": "商汤-jy",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://token.sensenova.cn/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "glm-5.2", "context_window": 1000000, "max_tokens": 128000, "capabilities": ["tools"]},
            {"name": "deepseek-v4-flash", "context_window": 1000000, "max_tokens": 128000, "capabilities": ["tools"]},
            {"name": "sensenova-6.8-flash-lite", "context_window": 256000, "max_tokens": 32000, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_sensenova_zy",
        "name": "商汤-zy",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://token.sensenova.cn/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "sensenova-6.8-flash-lite", "context_window": 256000, "max_tokens": 32000, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_sensenova_zy2",
        "name": "商汤-zy2",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://token.sensenova.cn/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "sensenova-6.8-flash-lite", "context_window": 256000, "max_tokens": 32000, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_sensenova_zy3",
        "name": "商汤-zy3",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://token.sensenova.cn/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "sensenova-6.8-flash-lite", "context_window": 256000, "max_tokens": 32000, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_sensenova_zjp",
        "name": "商汤-zjp",
        "group": "自定义供应商",
        "enabled": False,
        "base_url": "https://token.sensenova.cn/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "sensenova-6.8-flash-lite", "context_window": 256000, "max_tokens": 32000, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_ali",
        "name": "阿里",
        "group": "自定义供应商",
        "enabled": False,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "qwen-plus", "context_window": 128000, "max_tokens": 8192, "capabilities": ["tools"]},
            {"name": "qwen-max", "context_window": 32000, "max_tokens": 8192, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_volc",
        "name": "火山agent",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "doubao-pro-32k", "context_window": 32000, "max_tokens": 4096, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_jiyan",
        "name": "基元律动",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://api.jiyan.com/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "deepseek-chat", "context_window": 128000, "max_tokens": 8192, "capabilities": ["tools"]},
        ],
    },
    {
        "id": "prov_openrouter",
        "name": "openrouter",
        "group": "自定义供应商",
        "enabled": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "anthropic/claude-3.5-sonnet", "context_window": 200000, "max_tokens": 8192, "capabilities": ["tools", "vision"]},
        ],
    },
    {
        "id": "prov_peezy",
        "name": "peezy",
        "group": "自定义供应商",
        "enabled": False,
        "base_url": "https://api.peezy.ai/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "gpt-4o", "context_window": 128000, "max_tokens": 4096, "capabilities": ["tools", "vision"]},
        ],
    },
    {
        "id": "prov_deepseek",
        "name": "DeepSeek-gzm",
        "group": "自定义供应商",
        "enabled": True,
        "base_url": "https://api.deepseek.com/v1",
        "api_format": "Chat Completions (/chat/completions)",
        "api_key": "",
        "models": [
            {"name": "deepseek-chat", "context_window": 128000, "max_tokens": 8192, "capabilities": ["tools"]},
        ],
    },
]


def _read_custom_providers() -> list[dict[str, Any]]:
    path = _CUSTOM_PROVIDERS_FILE
    if not path.is_file():
        try:
            _write_custom_providers(_DEFAULT_CUSTOM_PROVIDERS)
            return [dict(p) for p in _DEFAULT_CUSTOM_PROVIDERS]
        except Exception:
            return [dict(p) for p in _DEFAULT_CUSTOM_PROVIDERS]
    try:
        with _providers_lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return [dict(p) for p in _DEFAULT_CUSTOM_PROVIDERS]


def _write_custom_providers(providers: list[dict[str, Any]]) -> None:
    path = _CUSTOM_PROVIDERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(providers, ensure_ascii=False, indent=2)
    with _providers_lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)


def _find_provider(provider_id: str) -> tuple[dict[str, Any] | None, bool]:
    """返回 (provider, is_preset)。"""
    for p in _PRESET_PROVIDERS:
        if p.get("id") == provider_id:
            return p, True
    for p in _read_custom_providers():
        if p.get("id") == provider_id:
            return p, False
    return None, False


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
        warning: str | None = None
        for field in ("model", "base_url", "api_key"):
            value = body.get(field)
            if isinstance(value, str) and value.strip():
                raw[field] = value.strip()
        # provider 联动：切模型时，自动同步 provider base_url 与 api_key
        model_name = str(raw.get("model", "")).strip()
        if model_name and "base_url" not in body:
            # 优先匹配自定义供应商与预置供应商
            custom_provs = _read_custom_providers()
            found_custom = False
            for cp in custom_provs:
                for m in cp.get("models", []):
                    if m.get("name") == model_name:
                        raw["base_url"] = str(cp.get("base_url", "")).rstrip("/")
                        if cp.get("api_key"):
                            raw["api_key"] = cp.get("api_key")
                        found_custom = True
                        break
                if found_custom:
                    break
            if not found_custom:
                for pp in _PRESET_PROVIDERS:
                    for m in pp.get("models", []):
                        if m.get("name") == model_name:
                            raw["base_url"] = str(pp.get("base_url", "")).rstrip("/")
                            key_env = pp.get("api_key_env", "")
                            env_key = os.environ.get(key_env, "") if key_env else ""
                            if env_key:
                                raw["api_key"] = env_key
                            found_custom = True
                            break
                    if found_custom:
                        break
            if not found_custom:
                try:
                    from openbimagent.providers.registry import DEFAULT_CONFIG_PATH, ModelRegistry

                    registry = ModelRegistry.load(DEFAULT_CONFIG_PATH)
                    declared = registry.models.get(model_name)
                    if declared is not None:
                        provider = registry.providers.get(declared.provider)
                        if provider is not None and getattr(provider, "base_url", ""):
                            raw["base_url"] = str(provider.base_url).rstrip("/")
                        key_env = getattr(provider, "api_key_env", "") if provider else ""
                        env_key = os.environ.get(key_env, "") if key_env else ""
                        if env_key:
                            raw["api_key"] = env_key
                        else:
                            warning = f"provider {declared.provider} 的 {key_env or 'API key'} 未配置，切换后请在设置页补 key"
                except Exception:  # noqa: BLE001 — models.toml 不可用时不阻断保存
                    pass
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
        payload = _settings_payload()
        if warning:
            payload["warning"] = warning
        return JSONResponse(content=payload)

    @app.get(
        "/api/v1/settings/models",
        summary="按 provider 分组的模型清单（包含预置与自定义供应商）",
        tags=["Workbench"],
    )
    async def list_switchable_models() -> dict:
        preset_list: list[dict[str, Any]] = []
        raw_base = _read_baseline_raw()
        glm_key = os.environ.get("GLM_API_KEY", "") or (
            raw_base.get("api_key") if raw_base.get("base_url", "").startswith("https://open.bigmodel.cn") else ""
        )
        for p in _PRESET_PROVIDERS:
            pc = dict(p)
            pc["api_key"] = glm_key or ""
            pc["key_set"] = bool(glm_key)
            preset_list.append(pc)

        custom_list = _read_custom_providers()

        # 合并出 providers 供前端兼容使用
        merged_providers = []
        for p in preset_list:
            merged_providers.append({
                "id": p["id"],
                "name": p["name"],
                "group": p.get("group", "智谱"),
                "type": p.get("api_format", "openai-compatible"),
                "base_url": p.get("base_url", ""),
                "api_format": p.get("api_format", "Chat Completions (/chat/completions)"),
                "api_key": p.get("api_key", ""),
                "key_set": p.get("key_set", False),
                "enabled": p.get("enabled", True),
                "is_preset": True,
                "models": p.get("models", []),
            })
        for p in custom_list:
            merged_providers.append({
                "id": p["id"],
                "name": p["name"],
                "group": p.get("group", "自定义供应商"),
                "type": p.get("api_format", "openai-compatible"),
                "base_url": p.get("base_url", ""),
                "api_format": p.get("api_format", "Chat Completions (/chat/completions)"),
                "api_key": p.get("api_key", ""),
                "key_set": bool(p.get("api_key")),
                "enabled": p.get("enabled", True),
                "is_preset": False,
                "models": p.get("models", []),
            })

        return {
            "status": "success",
            "presets": preset_list,
            "custom": custom_list,
            "providers": merged_providers,
            "current": raw_base.get("model"),
            "error": None,
        }

    @app.post("/api/v1/settings/providers", summary="添加自定义供应商", tags=["Workbench"])
    async def create_provider(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON"})
        name = str(body.get("name") or "自定义供应商").strip()
        base_url = str(body.get("base_url") or "").strip()
        api_format = str(body.get("api_format") or "Chat Completions (/chat/completions)").strip()
        api_key = str(body.get("api_key") or "").strip()
        enabled = bool(body.get("enabled", True))
        raw_models = body.get("models") or []
        models = []
        for m in raw_models:
            if isinstance(m, dict) and m.get("name"):
                models.append({
                    "name": str(m["name"]).strip(),
                    "context_window": int(m.get("context_window", 1000000)),
                    "max_tokens": int(m.get("max_tokens", 128000)),
                    "capabilities": list(m.get("capabilities") or ["tools"]),
                })
        new_provider = {
            "id": "prov_" + uuid4().hex[:8],
            "name": name,
            "group": "自定义供应商",
            "enabled": enabled,
            "base_url": base_url,
            "api_format": api_format,
            "api_key": api_key,
            "models": models,
        }
        custom = _read_custom_providers()
        custom.append(new_provider)
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "provider": new_provider})

    @app.patch("/api/v1/settings/providers/{provider_id}", summary="更新自定义供应商", tags=["Workbench"])
    async def update_provider(provider_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON"})
        custom = _read_custom_providers()
        target = next((p for p in custom if p.get("id") == provider_id), None)
        if not target:
            # 检查预置 BigModel
            for p in _PRESET_PROVIDERS:
                if p.get("id") == provider_id:
                    if "api_key" in body:
                        key_val = str(body["api_key"]).strip()
                        os.environ["GLM_API_KEY"] = key_val
                        _update_env_file({"GLM_API_KEY": key_val})
                        raw = _read_baseline_raw()
                        if raw.get("base_url", "").startswith("https://open.bigmodel.cn"):
                            raw["api_key"] = key_val
                            _write_toml_flat(_baseline_path(), raw)
                    return JSONResponse(content={"status": "success", "provider": p})
            return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})

        if "name" in body and str(body["name"]).strip():
            target["name"] = str(body["name"]).strip()
        if "base_url" in body:
            target["base_url"] = str(body["base_url"]).strip()
        if "api_format" in body:
            target["api_format"] = str(body["api_format"]).strip()
        if "api_key" in body:
            target["api_key"] = str(body["api_key"]).strip()
        if "enabled" in body:
            target["enabled"] = bool(body["enabled"])
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "provider": target})

    @app.delete("/api/v1/settings/providers/{provider_id}", summary="删除自定义供应商", tags=["Workbench"])
    async def delete_provider(provider_id: str) -> JSONResponse:
        custom = _read_custom_providers()
        initial_len = len(custom)
        custom = [p for p in custom if p.get("id") != provider_id]
        if len(custom) == initial_len:
            return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在或不可删除"})
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "deleted": provider_id})

    @app.post("/api/v1/settings/providers/{provider_id}/models", summary="为供应商添加模型", tags=["Workbench"])
    async def add_provider_model(provider_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON"})
        mname = str(body.get("name") or "").strip()
        if not mname:
            return JSONResponse(status_code=400, content={"status": "error", "error": "模型名称不能为空"})
        ctx = int(body.get("context_window", 1000000))
        max_tok = int(body.get("max_tokens", 128000))
        caps = list(body.get("capabilities") or ["tools"])

        custom = _read_custom_providers()
        target = next((p for p in custom if p.get("id") == provider_id), None)
        if not target:
            target = next((p for p in _PRESET_PROVIDERS if p.get("id") == provider_id), None)
            if not target:
                return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})
        models = target.setdefault("models", [])
        existing = next((m for m in models if m.get("name") == mname), None)
        if existing:
            existing["context_window"] = ctx
            existing["max_tokens"] = max_tok
            existing["capabilities"] = caps
        else:
            models.append({
                "name": mname,
                "context_window": ctx,
                "max_tokens": max_tok,
                "capabilities": caps,
            })
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "provider": target})

    @app.patch("/api/v1/settings/providers/{provider_id}/models/{model_name}", summary="修改模型", tags=["Workbench"])
    async def update_provider_model(provider_id: str, model_name: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON"})
        custom = _read_custom_providers()
        target = next((p for p in custom if p.get("id") == provider_id), None)
        if not target:
            target = next((p for p in _PRESET_PROVIDERS if p.get("id") == provider_id), None)
            if not target:
                return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})
        models = target.get("models", [])
        model_obj = next((m for m in models if m.get("name") == model_name), None)
        if not model_obj:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"模型 {model_name} 不存在"})
        if "new_name" in body and str(body["new_name"]).strip():
            model_obj["name"] = str(body["new_name"]).strip()
        if "context_window" in body:
            model_obj["context_window"] = int(body["context_window"])
        if "max_tokens" in body:
            model_obj["max_tokens"] = int(body["max_tokens"])
        if "capabilities" in body:
            model_obj["capabilities"] = list(body["capabilities"])
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "provider": target})

    @app.delete("/api/v1/settings/providers/{provider_id}/models/{model_name}", summary="删除模型", tags=["Workbench"])
    async def delete_provider_model(provider_id: str, model_name: str) -> JSONResponse:
        custom = _read_custom_providers()
        target = next((p for p in custom if p.get("id") == provider_id), None)
        if not target:
            target = next((p for p in _PRESET_PROVIDERS if p.get("id") == provider_id), None)
            if not target:
                return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})
        models = target.get("models", [])
        target["models"] = [m for m in models if m.get("name") != model_name]
        _write_custom_providers(custom)
        return JSONResponse(content={"status": "success", "provider": target})

    @app.post("/api/v1/settings/providers/{provider_id}/probe", summary="连通性测速探针", tags=["Workbench"])
    async def probe_provider(provider_id: str, request: Request) -> JSONResponse:
        target, is_preset = _find_provider(provider_id)
        if not target:
            return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})
        base_url = str(target.get("base_url") or "").rstrip("/")
        if not base_url:
            return JSONResponse(content={"status": "error", "error": "未配置 Base URL", "latency_ms": 0})
        api_key = str(target.get("api_key") or "").strip()
        if is_preset and not api_key:
            api_key = os.environ.get("GLM_API_KEY", "") or _read_baseline_raw().get("api_key", "")
        headers = {"User-Agent": "openBIMAgent/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=4.0, verify=False) as client:
                probe_url = base_url + "/models"
                try:
                    resp = await client.get(probe_url, headers=headers)
                    elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                    if resp.status_code in (200, 201):
                        return JSONResponse(content={"status": "success", "latency_ms": elapsed_ms, "message": f"连接正常 ({elapsed_ms}ms)"})
                    elif resp.status_code in (401, 403):
                        return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"鉴权未通过 (HTTP {resp.status_code})"})
                    else:
                        return JSONResponse(content={"status": "success", "latency_ms": elapsed_ms, "message": f"响应正常 (HTTP {resp.status_code} · {elapsed_ms}ms)"})
                except httpx.HTTPStatusError as e:
                    elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                    return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"HTTP 错误: {e.response.status_code}"})
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            err_msg = str(exc)
            if "ConnectTimeout" in err_msg or "ReadTimeout" in err_msg:
                return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": "请求超时 (4s)"})
            return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"网络不可达: {exc.__class__.__name__}"})

    @app.get("/api/v1/uploads", summary="上传附件清单（manifest 索引）", tags=["Workbench"])
    async def list_uploads() -> dict:
        return {"status": "success", "items": _read_upload_index(_uploads_dir())}

    @app.get("/api/v1/usage", summary="LLM 用量汇总（out/usage_summary.json；P3 成本面板数据源）", tags=["Workbench"])
    async def usage_summary() -> dict:
        path = _REPO_ROOT / "out" / "usage_summary.json"
        if not path.is_file():
            return {"status": "success", "usage": None}
        try:
            return {"status": "success", "usage": json.loads(path.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, OSError):
            return {"status": "success", "usage": None}

    @app.get("/api/v1/skills", summary="技能目录（渐进披露：仅 name/description 元数据 + 候选区 + 拒载清单）", tags=["Workbench"])
    async def list_skills() -> dict:
        from openbimagent.skills.registry import candidates_dir, default_skill_registry

        registry = default_skill_registry()
        candidates = sorted(p.name for p in candidates_dir().glob("*.md")) if candidates_dir().is_dir() else []
        return {
            "status": "success",
            "skills": registry.catalog(),
            "candidates": candidates,
            "rejected": registry.rejected,
        }

    @app.post("/api/v1/skills/invoke", summary="调用技能（返回完整正文；渐进披露的付费点）", tags=["Workbench"])
    async def invoke_skill(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.skills.registry import default_skill_registry

        name = str(request.get("name", "")).strip()
        skill = default_skill_registry().get(name)
        if skill is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"技能不存在: {name}"})
        return JSONResponse(content={"status": "success", "skill": {**skill.catalog_row(), "body": skill.body}})

    @app.post("/api/v1/skills/candidates/approve", summary="批准自蒸馏候选转正（fail-closed：人工门）", tags=["Workbench"])
    async def approve_candidate(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.skills.registry import builtin_skills_root, candidates_dir, load_skill, reload_skills

        filename = str(request.get("file", "")).strip()
        if not re.fullmatch(r"[\w.\-一-鿿]+\.md", filename):
            return JSONResponse(status_code=400, content={"status": "error", "error": "候选文件名非法"})
        src = candidates_dir() / filename
        if not src.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"候选不存在: {filename}"})
        try:
            skill = load_skill(src, source="distilled")
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"status": "error", "error": f"候选校验失败（不予转正）: {exc}"})
        dest_dir = builtin_skills_root() / skill.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        src.unlink()
        reload_skills()
        return JSONResponse(content={"status": "success", "approved": skill.name})

    @app.post("/api/v1/skills/candidates/discard", summary="丢弃自蒸馏候选（删除候选文件；永不自动生效的另一侧门）", tags=["Workbench"])
    async def discard_candidate(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.skills.registry import candidates_dir

        filename = str(request.get("file", "")).strip()
        if not re.fullmatch(r"[\w.\-一-鿿]+\.md", filename):
            return JSONResponse(status_code=400, content={"status": "error", "error": "候选文件名非法"})
        src = candidates_dir() / filename
        if not src.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"候选不存在: {filename}"})
        src.unlink()
        return JSONResponse(content={"status": "success", "discarded": filename})

    @app.get("/api/v1/hosts", summary="CAD 宿主状态（HostSupervisor：up/down/restarting/external；VW 恒 external 不伪探测）", tags=["Workbench"])
    async def hosts_status() -> dict:
        from openbimagent.mcp_clients.supervisor import STATE_EXTERNAL, STATE_UP, default_host_supervisor

        hosts = []
        for h in default_host_supervisor().status():
            hosts.append(
                {
                    **h,
                    # 前端兼容字段：up→True；external（不探测）→None；其余→False
                    "connected": True if h["state"] == STATE_UP else (None if h["state"] == STATE_EXTERNAL else False),
                }
            )
        return {"status": "success", "hosts": hosts}

    @app.post("/api/v1/hosts/{host_id}/restart", summary="有界退避重启宿主（仅 Blender 且配置 exe；超限/未配置/VW 如实拒绝）", tags=["Workbench"])
    async def host_restart(host_id: str) -> JSONResponse:
        from openbimagent.mcp_clients.supervisor import default_host_supervisor

        try:
            state = default_host_supervisor().restart(host_id)
        except KeyError as exc:
            return JSONResponse(status_code=404, content={"status": "error", "error": str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        return JSONResponse(content={"status": "success", "host": state.to_dict()})

    @app.get("/api/v1/toolset", summary="当前工具集预设与可选项（能力面收敛）", tags=["Workbench"])
    async def get_toolset() -> dict:
        from openbimagent.core.toolset import TOOLSET_PRESETS, current_toolset

        return {
            "status": "success",
            "current": current_toolset(),
            "presets": {k: (list(v) if v else ["*"]) for k, v in TOOLSET_PRESETS.items()},
        }

    @app.put("/api/v1/toolset", summary="切换工具集预设（minimal/modeling/full；未知名 fail-closed 400）", tags=["Workbench"])
    async def put_toolset(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.core.toolset import set_toolset

        name = str(request.get("name", "")).strip()
        try:
            set_toolset(name)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        return JSONResponse(content={"status": "success", "current": name})

    @app.get("/api/v1/memory", summary="读取长期记忆（MEMORY.md/USER.md 带行号条目；读取免费）", tags=["Workbench"])
    async def get_memory(n: int = 100) -> dict:
        from openbimagent.core.memory import default_memory_store

        store = default_memory_store()
        return {
            "status": "success",
            "memory": store.read_entries("memory", n),
            "user": store.read_entries("user", n),
            "root": str(store.root),
        }

    @app.post("/api/v1/memory/record", summary="写入长期记忆（prompt 策略门：confirm=true 人工确认语义）", tags=["Workbench"])
    async def record_memory(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.core.plugin import PluginPolicyPromptRequiredError, default_plugin_registry

        entry = str(request.get("entry", "")).strip()
        file_key = str(request.get("file", "memory")).strip()
        confirm = bool(request.get("confirm", False))
        try:
            result = default_plugin_registry.invoke("memory:record", file=file_key, entry=entry, confirm=confirm)
        except PluginPolicyPromptRequiredError as exc:
            return JSONResponse(status_code=409, content={"status": "error", "error": str(exc), "need_confirm": True})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        return JSONResponse(content={"status": "success", "recorded": result})

    @app.post("/api/v1/memory/delete", summary="删除长期记忆条目（prompt 策略门；按物理行号）", tags=["Workbench"])
    async def delete_memory(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.core.plugin import PluginPolicyPromptRequiredError, default_plugin_registry

        file_key = str(request.get("file", "memory")).strip()
        confirm = bool(request.get("confirm", False))
        try:
            line = int(request.get("line", 0))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"status": "error", "error": "line 必须是正整数"})
        try:
            result = default_plugin_registry.invoke("memory:delete", file=file_key, line=line, confirm=confirm)
        except PluginPolicyPromptRequiredError as exc:
            return JSONResponse(status_code=409, content={"status": "error", "error": str(exc), "need_confirm": True})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        if not result.get("deleted"):
            return JSONResponse(status_code=404, content={"status": "error", "error": f"行号不存在: {file_key}#{line}"})
        return JSONResponse(content={"status": "success", "deleted": result})

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

    @app.delete("/api/v1/uploads/{item_id}", summary="删除上传附件（manifest 移除 + 文件删除；id 即落盘文件名）", tags=["Workbench"])
    async def delete_upload(item_id: str) -> JSONResponse:
        uploads = _uploads_dir()
        safe = Path(item_id).name  # 防路径穿越（id 本就是服务端生成的落盘名，再兜一层）
        if safe != item_id or not item_id:
            return JSONResponse(status_code=400, content={"status": "error", "error": "非法附件 id"})
        index = _read_upload_index(uploads)
        item = next((it for it in index if it.get("id") == item_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"附件不存在: {item_id}"})
        target = uploads / safe
        try:
            if target.is_file():
                target.unlink()
            remaining = [it for it in index if it.get("id") != item_id]
            (uploads / _UPLOAD_INDEX).write_text(json.dumps(remaining, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"删除失败: {exc}"})
        return JSONResponse(content={"status": "success", "deleted": item_id})
