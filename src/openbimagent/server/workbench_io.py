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

def _sanitize_no_proxy() -> None:
    for k in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(k)
        if val:
            cleaned = ",".join(p.strip() for p in val.split(",") if not p.strip().startswith("::"))
            os.environ[k] = cleaned

_sanitize_no_proxy()

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
]


def _custom_providers_file() -> Path:
    override = os.environ.get("OPENBIMAGENT_CUSTOM_PROVIDERS")
    return Path(override) if override else _CUSTOM_PROVIDERS_FILE


def _read_custom_providers() -> list[dict[str, Any]]:
    path = _custom_providers_file()
    if not path.is_file():
        try:
            _write_custom_providers(_DEFAULT_CUSTOM_PROVIDERS)
            return [dict(p) for p in _DEFAULT_CUSTOM_PROVIDERS]
        except Exception:
            return [dict(p) for p in _DEFAULT_CUSTOM_PROVIDERS]
    try:
        with _providers_lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_custom_providers(providers: list[dict[str, Any]]) -> None:
    path = _custom_providers_file()
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


#: 第三方 MCP server 配置持久化文件（gitignore 的 .local.json，与 custom_providers 同模式）
_MCP_SERVERS_FILE = _REPO_ROOT / "config" / "mcp_servers.local.json"
_mcp_lock = threading.Lock()


def _mcp_config_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_MCP_CONFIG_FILE")
    return Path(override) if override else _MCP_SERVERS_FILE


def _read_mcp_config() -> dict[str, Any]:
    """持久化文件优先；回退进程环境 OPENBIMAGENT_MCP_SERVERS（条目含元数据键）。"""
    from openbimagent.mcp_clients.external import load_mcp_servers_config

    return load_mcp_servers_config()


def _apply_mcp_env(config: dict[str, Any]) -> None:
    """把保存的配置同步到进程环境（重启后由 external.attach_external_servers_from_env 挂载）。"""
    os.environ["OPENBIMAGENT_MCP_SERVERS"] = json.dumps(config, ensure_ascii=False)


def _mcp_tools_payload() -> dict[str, list[str]]:
    """已挂载 server 的工具名清单（读全局 default_plugin_registry；只读不探测，失败降级为空）。"""
    tools: dict[str, list[str]] = {}
    try:
        from openbimagent.core.plugin import default_plugin_registry

        inv = default_plugin_registry.export_inventory()
        for cap, pid in (inv.get("capabilities_map") or {}).items():
            if str(pid).startswith("plugin.external.mcp."):
                tools.setdefault(str(pid).removeprefix("plugin.external.mcp."), []).append(str(cap))
    except Exception:  # noqa: BLE001 — registry 不可用时降级为空（前端显示"未挂载"）
        return {}
    return tools


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
        summary="按 provider 分组的模型清单（key 只回 key_set 布尔，绝不回显本体）",
        tags=["Workbench"],
    )
    async def list_switchable_models() -> dict:
        preset_list: list[dict[str, Any]] = []
        raw_base = _read_baseline_raw()
        glm_key = os.environ.get("GLM_API_KEY", "") or (
            raw_base.get("api_key") if raw_base.get("base_url", "").startswith("https://open.bigmodel.cn") else ""
        )
        glm_key_set = bool(glm_key)
        for p in _PRESET_PROVIDERS:
            pc = dict(p)
            pc.pop("api_key", None)  # key 本体绝不出现在响应（与 settings/llm 同口径）
            pc["key_set"] = glm_key_set
            preset_list.append(pc)

        custom_list = _read_custom_providers()

        # 合并出 providers 供前端兼容使用（同样只带 key_set，不带 key）
        merged_providers = []
        for p in preset_list:
            merged_providers.append({
                "id": p["id"],
                "name": p["name"],
                "group": p.get("group", "智谱"),
                "type": p.get("api_format", "openai-compatible"),
                "base_url": p.get("base_url", ""),
                "api_format": p.get("api_format", "Chat Completions (/chat/completions)"),
                "key_set": p.get("key_set", False),
                "enabled": p.get("enabled", True),
                "is_preset": True,
                "models": p.get("models", []),
            })
        for p in custom_list:
            key_set = bool(p.get("api_key"))
            entry = dict(p)
            entry.pop("api_key", None)
            entry["key_set"] = key_set
            merged_providers.append({
                "id": p["id"],
                "name": p["name"],
                "group": p.get("group", "自定义供应商"),
                "type": p.get("api_format", "openai-compatible"),
                "base_url": p.get("base_url", ""),
                "api_format": p.get("api_format", "Chat Completions (/chat/completions)"),
                "key_set": key_set,
                "enabled": p.get("enabled", True),
                "is_preset": False,
                "models": p.get("models", []),
            })

        # custom 同样剥 key 本体（前端只看 key_set）
        safe_custom = []
        for p in custom_list:
            q = dict(p)
            key_set = bool(q.pop("api_key", None))
            q["key_set"] = key_set
            safe_custom.append(q)
        has_models = any(p.get("models") for p in merged_providers if p.get("enabled", True))
        active_current = raw_base.get("model") if has_models else ""
        return {
            "status": "success",
            "presets": preset_list,
            "custom": safe_custom,
            "providers": merged_providers,
            "current": active_current,
            "error": None,
        }

    @app.put(
        "/api/v1/settings/models",
        summary="全量保存模型与供应商设置",
        tags=["Workbench"],
    )
    async def save_models_settings(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"status": "error", "error": "请求体必须为 JSON 对象"})

        # 1. 保存当前基线模型 (current)
        curr = body.get("current")
        if curr and isinstance(curr, str) and curr.strip():
            raw_base = _read_baseline_raw()
            raw_base["model"] = curr.strip()
            # 寻找 provider 对应的 base_url / key
            custom_provs = _read_custom_providers()
            found_p = False
            for cp in custom_provs:
                for m in cp.get("models", []):
                    if m.get("name") == curr.strip() or m.get("id") == curr.strip():
                        raw_base["base_url"] = str(cp.get("base_url", "")).rstrip("/")
                        if cp.get("api_key"):
                            raw_base["api_key"] = cp.get("api_key")
                        found_p = True
                        break
                if found_p:
                    break
            if not found_p:
                for pp in _PRESET_PROVIDERS:
                    for m in pp.get("models", []):
                        if m.get("name") == curr.strip() or m.get("id") == curr.strip():
                            raw_base["base_url"] = str(pp.get("base_url", "")).rstrip("/")
                            key_env = pp.get("api_key_env", "")
                            if key_env and os.environ.get(key_env):
                                raw_base["api_key"] = os.environ[key_env]
                            found_p = True
                            break
                    if found_p:
                        break
            try:
                _write_toml_flat(_baseline_path(), raw_base)
            except OSError:
                pass

        # 2. 保存供应商列表 (providers)
        prov_list = body.get("providers")
        if isinstance(prov_list, list):
            preset_ids = {p["id"] for p in _PRESET_PROVIDERS}
            existing_custom = _read_custom_providers()
            existing_map = {cp.get("id"): cp for cp in existing_custom if cp.get("id")}
            new_custom = []
            for p in prov_list:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("id") or "").strip()
                if pid in preset_ids:
                    # 预置供应商仅同步 API Key
                    if p.get("api_key") and str(p["api_key"]).strip():
                        key_val = str(p["api_key"]).strip()
                        os.environ["GLM_API_KEY"] = key_val
                        _update_env_file({"GLM_API_KEY": key_val})
                        raw_base = _read_baseline_raw()
                        if raw_base.get("base_url", "").startswith("https://open.bigmodel.cn"):
                            raw_base["api_key"] = key_val
                            try:
                                _write_toml_flat(_baseline_path(), raw_base)
                            except OSError:
                                pass
                else:
                    # 自定义供应商
                    old = existing_map.get(pid, {})
                    old_key = old.get("api_key", "")
                    key_val = str(p.get("api_key", "")).strip() or old_key
                    raw_models = p.get("models") or []
                    models = []
                    for m in raw_models:
                        if isinstance(m, dict):
                            mname = str(m.get("name") or m.get("id") or "").strip()
                            if mname:
                                models.append({
                                    "name": mname,
                                    "context_window": int(m.get("context_window") or m.get("context_length", 128000)),
                                    "max_tokens": int(m.get("max_tokens") or m.get("max_output_tokens", 128000)),
                                    "capabilities": list(m.get("capabilities") or ["tools"]),
                                    "input_types": list(m.get("input_types") or ["text"]),
                                    "output_types": list(m.get("output_types") or ["text"]),
                                })
                    new_custom.append({
                        "id": pid or ("prov_" + uuid4().hex[:8]),
                        "name": str(p.get("name") or "自定义供应商").strip(),
                        "group": str(p.get("group") or "自定义供应商"),
                        "enabled": bool(p.get("enabled", True)),
                        "base_url": str(p.get("base_url") or "").strip(),
                        "api_format": str(p.get("api_format") or "Chat Completions (/chat/completions)").strip(),
                        "api_key": key_val,
                        "models": models,
                    })
            _write_custom_providers(new_custom)

        return JSONResponse(content={"status": "success", "message": "配置已保存"})


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
        safe = dict(new_provider)
        safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
        safe["key_set"] = bool(api_key)
        return JSONResponse(content={"status": "success", "provider": safe})

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
                        if key_val:  # 空 key = 保持已存值
                            os.environ["GLM_API_KEY"] = key_val
                            _update_env_file({"GLM_API_KEY": key_val})
                            raw = _read_baseline_raw()
                            if raw.get("base_url", "").startswith("https://open.bigmodel.cn"):
                                raw["api_key"] = key_val
                                _write_toml_flat(_baseline_path(), raw)
                    safe = dict(p)
                    safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
                    safe["key_set"] = bool(os.environ.get("GLM_API_KEY"))
                    return JSONResponse(content={"status": "success", "provider": safe})
            return JSONResponse(status_code=404, content={"status": "error", "error": "供应商不存在"})

        if "name" in body and str(body["name"]).strip():
            target["name"] = str(body["name"]).strip()
        if "base_url" in body:
            target["base_url"] = str(body["base_url"]).strip()
        if "api_format" in body:
            target["api_format"] = str(body["api_format"]).strip()
        if "api_key" in body:
            key_val = str(body["api_key"]).strip()
            if key_val:  # 空 key = 保持已存值（前端不回显 key 本体，留空仅表示不变）
                target["api_key"] = key_val
        if "enabled" in body:
            target["enabled"] = bool(body["enabled"])
        _write_custom_providers(custom)
        safe = dict(target)
        safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
        safe["key_set"] = bool(target.get("api_key"))
        return JSONResponse(content={"status": "success", "provider": safe})

    @app.delete("/api/v1/settings/providers/{provider_id}", summary="删除自定义供应商", tags=["Workbench"])
    @app.delete("/api/v1/settings/models/providers/{provider_id}", include_in_schema=False)
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
        ctx = int(body.get("context_window") or body.get("context_length", 1000000))
        max_tok = int(body.get("max_tokens") or body.get("max_output_tokens", 128000))
        caps = list(body.get("capabilities") or ["tools"])
        in_types = list(body.get("input_types") or ["text"])
        out_types = list(body.get("output_types") or ["text"])

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
            existing["input_types"] = in_types
            existing["output_types"] = out_types
        else:
            models.append({
                "name": mname,
                "context_window": ctx,
                "max_tokens": max_tok,
                "capabilities": caps,
                "input_types": in_types,
                "output_types": out_types,
            })
        _write_custom_providers(custom)
        safe = dict(target)
        safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
        safe["key_set"] = bool(target.get("api_key"))
        return JSONResponse(content={"status": "success", "provider": safe})

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
        elif "context_length" in body:
            model_obj["context_window"] = int(body["context_length"])
        if "max_tokens" in body:
            model_obj["max_tokens"] = int(body["max_tokens"])
        elif "max_output_tokens" in body:
            model_obj["max_tokens"] = int(body["max_output_tokens"])
        if "capabilities" in body:
            model_obj["capabilities"] = list(body["capabilities"])
        if "input_types" in body:
            model_obj["input_types"] = list(body["input_types"])
        if "output_types" in body:
            model_obj["output_types"] = list(body["output_types"])
        _write_custom_providers(custom)
        safe = dict(target)
        safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
        safe["key_set"] = bool(target.get("api_key"))
        return JSONResponse(content={"status": "success", "provider": safe})

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
        safe = dict(target)
        safe.pop("api_key", None)  # 响应只带 key_set，不带 key 本体
        safe["key_set"] = bool(target.get("api_key"))
        return JSONResponse(content={"status": "success", "provider": safe})

    async def _execute_provider_probe(request: Request, provider_id: str | None = None) -> JSONResponse:
        _sanitize_no_proxy()
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        target = None
        is_preset = False
        if provider_id and provider_id != "undefined" and provider_id != "null":
            target, is_preset = _find_provider(provider_id)
        
        base_url = str(body.get("base_url") or (target.get("base_url") if target else "") or "").rstrip("/")
        if not base_url:
            return JSONResponse(content={"status": "error", "error": "未配置 Base URL", "latency_ms": 0})
        api_key = str(body.get("api_key") or (target.get("api_key") if target else "") or "").strip()
        if is_preset and not api_key:
            api_key = os.environ.get("GLM_API_KEY", "") or _read_baseline_raw().get("api_key", "")
        if not api_key:
            return JSONResponse(content={"status": "error", "error": "未填写 API Key，请先配置有效 Key 后测速", "latency_ms": 0})
        headers = {"User-Agent": "openBIMAgent/1.0", "Authorization": f"Bearer {api_key}"}

        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=6.0, verify=False, trust_env=False) as client:
                probe_url = base_url + "/models"
                try:
                    resp = await client.get(probe_url, headers=headers)
                    elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                    if resp.status_code in (200, 201):
                        models_data = []
                        try:
                            rj = resp.json()
                            raw_list = rj.get("data") if isinstance(rj.get("data"), list) else rj.get("models")
                            if isinstance(raw_list, list):
                                models_data = [
                                    m.get("id") or m.get("name")
                                    for m in raw_list
                                    if isinstance(m, dict) and (m.get("id") or m.get("name"))
                                ]
                        except Exception:
                            pass
                        return JSONResponse(content={
                            "status": "success",
                            "latency_ms": elapsed_ms,
                            "message": f"连接正常 ({elapsed_ms}ms)",
                            "models": models_data,
                        })
                    elif resp.status_code in (401, 403):
                        return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"鉴权未通过 (HTTP {resp.status_code})：请检查 API Key"})
                    else:
                        return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"端点异常 (HTTP {resp.status_code})"})
                except httpx.HTTPStatusError as e:
                    elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                    return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"HTTP 错误: {e.response.status_code}"})
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            err_msg = str(exc)
            if "ConnectTimeout" in err_msg or "ReadTimeout" in err_msg:
                return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": "请求超时 (6s)"})
            return JSONResponse(content={"status": "error", "latency_ms": elapsed_ms, "error": f"网络不可达: {exc.__class__.__name__}"})

    @app.post("/api/v1/settings/providers/probe", summary="连通性测速探针 (临时配置)", tags=["Workbench"])
    async def probe_provider_adhoc(request: Request) -> JSONResponse:
        return await _execute_provider_probe(request, None)

    @app.post("/api/v1/settings/providers/{provider_id}/probe", summary="连通性测速探针", tags=["Workbench"])
    async def probe_provider(request: Request, provider_id: str) -> JSONResponse:
        return await _execute_provider_probe(request, provider_id)

    async def _execute_fetch_models(request: Request, provider_id: str | None = None) -> JSONResponse:
        _sanitize_no_proxy()
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        target = None
        if provider_id and provider_id != "undefined" and provider_id != "null":
            target, _ = _find_provider(provider_id)
        base_url = str(body.get("base_url") or (target.get("base_url") if target else "") or "").rstrip("/")
        api_key = str(body.get("api_key") or (target.get("api_key") if target else "") or "").strip()
        if not base_url:
            return JSONResponse(content={"status": "error", "error": "未配置 Base URL", "models": []})
        if not api_key:
            return JSONResponse(content={"status": "error", "error": "未填写 API Key", "models": []})
        
        headers = {"User-Agent": "openBIMAgent/1.0", "Authorization": f"Bearer {api_key}"}
        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=8.0, verify=False, trust_env=False) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
                elapsed_ms = int((time.perf_counter() - start_t) * 1000)
                if resp.status_code in (200, 201):
                    rj = resp.json()
                    raw_list = rj.get("data") if isinstance(rj.get("data"), list) else rj.get("models")
                    if isinstance(raw_list, list):
                        models_data = []
                        for m in raw_list:
                            if isinstance(m, dict):
                                mid = m.get("id") or m.get("name")
                                if mid:
                                    models_data.append({
                                        "id": str(mid),
                                        "name": str(mid),
                                        "input_types": m.get("input_modalities") or ["text"],
                                    })
                        return JSONResponse(content={
                            "status": "success",
                            "latency_ms": elapsed_ms,
                            "models": models_data,
                            "count": len(models_data),
                        })
                    return JSONResponse(content={"status": "error", "error": "接口未返回预期的模型数组", "models": []})
                elif resp.status_code in (401, 403):
                    return JSONResponse(content={"status": "error", "error": f"鉴权失败 (HTTP {resp.status_code})，请检查 API Key", "models": []})
                else:
                    return JSONResponse(content={"status": "error", "error": f"端点返回 HTTP {resp.status_code}", "models": []})
        except Exception as exc:
            return JSONResponse(content={"status": "error", "error": f"拉取失败: {str(exc)}", "models": []})

    @app.post("/api/v1/settings/providers/fetch_models", summary="拉取可用模型列表 (临时配置)", tags=["Workbench"])
    async def fetch_provider_models_adhoc(request: Request) -> JSONResponse:
        return await _execute_fetch_models(request, None)

    @app.post("/api/v1/settings/providers/{provider_id}/fetch_models", summary="拉取可用模型列表", tags=["Workbench"])
    async def fetch_provider_models(request: Request, provider_id: str) -> JSONResponse:
        return await _execute_fetch_models(request, provider_id)

    @app.post("/api/v1/settings/models/probe", summary="连通性测速探针 (按模型别名)", tags=["Workbench"], include_in_schema=False)
    async def probe_model_alias(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            pid = str(body.get("provider_id") or "").strip()
            return await probe_provider(request, pid)
        except Exception as e:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(e)})

    @app.get("/api/v1/uploads", summary="上传附件清单（manifest 索引）", tags=["Workbench"])
    async def list_uploads() -> dict:
        return {"status": "success", "items": _read_upload_index(_uploads_dir())}

    @app.get(
        "/api/v1/usage",
        summary="LLM 用量仪表盘（真实调用流水账 out/usage_log.jsonl 聚合：总量/按日/按模型/最近）",
        tags=["Workbench"],
    )
    async def usage_summary() -> dict:
        from openbimagent.server.usage_ledger import aggregate, _iter_entries

        if not _iter_entries():
            # 流水账为空时回退旧 CLI 快照（usage_summary.json），避免历史数据一夜消失
            path = _REPO_ROOT / "out" / "usage_summary.json"
            if path.is_file():
                try:
                    legacy = json.loads(path.read_text(encoding="utf-8"))
                    if legacy.get("total", {}).get("calls"):
                        return {"status": "success", "usage": legacy, "source": "legacy"}
                except (json.JSONDecodeError, OSError):
                    pass
            return {"status": "success", "usage": None}
        return {"status": "success", "usage": aggregate(), "source": "ledger"}

    @app.get("/api/v1/skills", summary="技能目录（渐进披露：仅 name/description 元数据 + 候选区 + 拒载清单）", tags=["Workbench"])
    async def list_skills() -> dict:
        from openbimagent.skills.registry import candidates_dir, default_skill_registry, load_skill

        registry = default_skill_registry()
        candidates = []
        if candidates_dir().is_dir():
            for p in sorted(candidates_dir().glob("*.md")):
                try:
                    sk = load_skill(p, source="candidate")
                    candidates.append({
                        "id": p.name,
                        "file": p.name,
                        "name": sk.name,
                        "description": sk.description,
                        "when_to_use": sk.when_to_use,
                    })
                except Exception:
                    candidates.append({
                        "id": p.name,
                        "file": p.name,
                        "name": p.stem,
                        "description": "自愈求解沉淀候选经验",
                    })
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

        filename = str(request.get("file") or request.get("id") or request.get("name") or "").strip()
        if filename and not filename.endswith(".md"):
            filename = f"{filename}.md"
        if not filename or not re.fullmatch(r"[\w.\-一-鿿]+\.md", filename):
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

        filename = str(request.get("file") or request.get("id") or request.get("name") or "").strip()
        if filename and not filename.endswith(".md"):
            filename = f"{filename}.md"
        if not re.fullmatch(r"[\w.\-一-鿿]+\.md", filename):
            return JSONResponse(status_code=400, content={"status": "error", "error": "候选文件名非法"})
        src = candidates_dir() / filename
        if not src.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"候选不存在: {filename}"})
        src.unlink()
        return JSONResponse(content={"status": "success", "discarded": filename})

    @app.get(
        "/api/v1/settings/mcp",
        summary="读取 MCP server 配置（本地持久化文件优先，回读 env；tools 为已挂载 server 的能力清单）",
        tags=["Workbench"],
    )
    async def get_mcp_settings() -> dict:
        with _mcp_lock:
            config = _read_mcp_config()
        return {
            "status": "success",
            "config": config,
            "tools": _mcp_tools_payload(),
            "path": str(_mcp_config_path()),
            "env_configured": bool(os.environ.get("OPENBIMAGENT_MCP_SERVERS", "").strip()),
        }

    @app.put(
        "/api/v1/settings/mcp",
        summary="保存 MCP server 配置：写 mcp_servers.local.json + 同步进程环境（重启后挂载；支持 mcpServers 包装格式）",
        tags=["Workbench"],
    )
    async def put_mcp_settings(request: dict[str, Any]) -> JSONResponse:
        config = request.get("config")
        # 兼容 {"mcpServers": {...}} 包装格式（高级配置区直接粘贴的形态）
        if isinstance(config, dict) and "mcpServers" in config and isinstance(config.get("mcpServers"), dict):
            config = config["mcpServers"]
        if not isinstance(config, dict):
            return JSONResponse(status_code=400, content={"status": "error", "error": "config 必须是对象（server 名 → 连接配置）或含 mcpServers 键"})
        name_re = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,31}$")
        for name, entry in config.items():
            if not name_re.fullmatch(str(name)):
                return JSONResponse(status_code=400, content={"status": "error", "error": f"server 名非法（需小写 slug）: {name}"})
            if not isinstance(entry, dict):
                return JSONResponse(status_code=400, content={"status": "error", "error": f"server {name} 条目必须是对象"})
            if not entry.get("disabled"):
                if not entry.get("url") and not entry.get("command"):
                    return JSONResponse(status_code=400, content={"status": "error", "error": f"server {name} 缺少 url 或 command"})
        with _mcp_lock:
            path = _mcp_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
            _apply_mcp_env(config)
        return JSONResponse(content={"status": "success", "saved": len(config), "path": str(path)})

    @app.delete(
        "/api/v1/settings/mcp",
        summary="恢复默认：删除本地持久化文件并按 env 原值重置（即当前 env 配置即为默认来源）",
        tags=["Workbench"],
    )
    async def delete_mcp_settings() -> JSONResponse:
        env_raw = os.environ.get("OPENBIMAGENT_MCP_SERVERS", "").strip()
        default_cfg: dict[str, Any] = {}
        if env_raw:
            try:
                loaded = json.loads(env_raw)
                if isinstance(loaded, dict):
                    default_cfg = loaded
            except json.JSONDecodeError:
                pass
        path = _mcp_config_path()
        with _mcp_lock:
            if path.is_file():
                path.unlink()
            _apply_mcp_env(default_cfg)
        return JSONResponse(content={"status": "success", "config": default_cfg})

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

        name = str(request.get("name") or request.get("preset") or "").strip()
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

        entry = str(request.get("entry") or request.get("text") or "").strip()
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
