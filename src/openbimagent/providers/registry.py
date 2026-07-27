"""模型注册表:加载 config/models.toml、profile 角色-模型绑定、降级链、集中式韧性。

对应文档:
- docs/architecture/COMPONENTS.md §4 多厂家模型统一配置(定稿)
- config/models.toml(本仓库)

官方搭档(Aider Architect/Editor 模式):Pro 出规划与建模,Flash 跑高频杂活,GLM 当调度。
profile 由环境变量 OPENBIMAGENT_PROFILE 选择(默认 official);api_key 一律从环境变量读,
读不到报清晰错误,任何日志/异常信息都不打印 key 本身。
"""

from __future__ import annotations

import os
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from openbimagent.providers import dialects
from openbimagent.providers.dialects import PROVIDER_TYPE_MAP, CircuitBreaker, DialectError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "models.toml"
"""仓库内置 models.toml(src/openbimagent/providers/registry.py → 上溯三级为仓库根)。"""

PROFILE_ENV_VAR = "OPENBIMAGENT_PROFILE"
DEFAULT_PROFILE = "official"


class ProviderKeyError(RuntimeError):
    """api_key 环境变量未设置;报错只含变量名,绝不打印 key。"""


class ProviderError(RuntimeError):
    """降级链全部失败后的汇总错误。"""


class ProviderConfig(BaseModel):
    """单个厂家连接配置;type 即 providers.dialects 的方言标识。

    default_headers:随每次请求合并的额外头(如 agentrouter 按 User-Agent 过滤客户端);
    Authorization 仍由 api_key_env 从环境变量读取后注入,不被 default_headers 覆盖。
    """

    type: str
    base_url: str | None = None
    api_key_env: str
    default_headers: dict[str, str] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    """单个模型声明;capabilities 含 tools/vision 等。"""

    provider: str
    context_window: int | None = None  # 调研值,部分模型待官方确认(config/models.toml 注)
    capabilities: list[str] = Field(default_factory=list)
    cost_per_mtoken: dict[str, float] | None = None


class ResilienceConfig(BaseModel):
    """重试/超时/熔断参数;韧性集中在 providers 层,业务代码不重复造(COMPONENTS §4)。"""

    retry: dict[str, Any] = Field(default_factory=lambda: {"max": 3, "backoff": "exponential", "base_ms": 1000})
    timeout_s: int = 120
    circuit_breaker: dict[str, Any] = Field(default_factory=lambda: {"failures": 5, "cooldown_s": 300})


class ModelRegistry(BaseModel):
    """models.toml 的运行时视图:providers / models / profiles / fallbacks / resilience。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    profiles: dict[str, dict[str, str]] = Field(default_factory=dict)
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    active_profile: str = DEFAULT_PROFILE

    _breaker: CircuitBreaker = PrivateAttr()
    _breaker_lock: threading.Lock = PrivateAttr()

    def model_post_init(self, __context: Any, /) -> None:
        cb = self.resilience.circuit_breaker
        self._breaker = CircuitBreaker(
            failures=int(cb.get("failures", 5)),
            cooldown_s=int(cb.get("cooldown_s", 300)),
        )
        self._breaker_lock = threading.Lock()

    # ---------- 加载与解析 ----------

    @classmethod
    def load(cls, path: Path, profile: str | None = None) -> "ModelRegistry":
        """加载并校验 models.toml(tomllib);profile 取参数 > OPENBIMAGENT_PROFILE > official。"""
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        active = profile or os.environ.get(PROFILE_ENV_VAR) or DEFAULT_PROFILE
        profiles = raw.get("profiles", {})
        if active not in profiles:
            raise ValueError(f"未知 profile {active!r};可选: {sorted(profiles)}(环境变量 {PROFILE_ENV_VAR} 切换)")
        registry = cls(
            providers=raw.get("providers", {}),
            models=raw.get("models", {}),
            profiles=profiles,
            fallbacks=raw.get("fallbacks", {}),
            resilience=raw.get("resilience", {}),
            active_profile=active,
        )
        registry._validate_refs()
        return registry

    def _validate_refs(self) -> None:
        """引用完整性:profiles/fallbacks 引用的模型、模型引用的 provider 必须已声明。"""
        for name, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(f"模型 {name!r} 引用了未声明的 provider {model.provider!r}")
        for profile_name, role_map in self.profiles.items():
            for role, model_name in role_map.items():
                if model_name not in self.models:
                    raise ValueError(f"profile {profile_name!r} 的角色 {role!r} 绑定了未声明的模型 {model_name!r}")
        for model_name, chain in self.fallbacks.items():
            for entry in [model_name, *chain]:
                if entry not in self.models:
                    raise ValueError(f"fallbacks 引用了未声明的模型 {entry!r}")

    def model_name_for_role(self, role: str) -> str:
        """角色 → 模型名(当前 profile);modeler 硬性保持 gemini-3.1-pro 不降 Flash 由 profile 数据保证。"""
        role_map = self.profiles.get(self.active_profile, {})
        if role not in role_map:
            raise ValueError(f"profile {self.active_profile!r} 未定义角色 {role!r};已有角色: {sorted(role_map)}")
        return role_map[role]

    def model_for_role(self, role: str) -> ModelConfig:
        """角色 → 模型配置(model_name_for_role 的 ModelConfig 形态)。"""
        return self.models[self.model_name_for_role(role)]

    def fallback_chain(self, model: str) -> list[str]:
        """降级链(含主模型自身,依序尝试):如 gemini-3.1-pro → [gemini-3.1-pro, glm-5.2]。"""
        if model not in self.models:
            raise ValueError(f"未声明的模型 {model!r};已有模型: {sorted(self.models)}")
        return [model, *self.fallbacks.get(model, [])]

    def api_key_for(self, provider_name: str) -> str:
        """从环境变量读 api_key;读不到报清晰错误(只含变量名,不打印 key)。"""
        if provider_name not in self.providers:
            raise ValueError(f"未声明的 provider {provider_name!r};已有: {sorted(self.providers)}")
        env_var = self.providers[provider_name].api_key_env
        key = os.environ.get(env_var)
        if not key:
            raise ProviderKeyError(f"provider {provider_name!r} 需要环境变量 {env_var} 提供 API key,但它未设置或为空")
        return key

    # ---------- 统一调用入口 ----------

    def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: threading.Event | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """角色 → 模型 → provider 解析后统一调用;沿降级链尝试,跳过熔断冷却中的模型。"""
        chain = self.fallback_chain(self.model_name_for_role(role))
        errors: list[str] = []
        for model_name in chain:
            with self._breaker_lock:
                allowed = self._breaker.allow(model_name)
            if not allowed:
                errors.append(f"{model_name}: 熔断冷却中,跳过")
                continue
            try:
                result = self._chat_with_retry(
                    model_name, messages, tools=tools, tool_choice=tool_choice, cancel_event=cancel_event
                )
            except ProviderKeyError:
                raise  # 缺 key 是配置错误,不是模型故障:不走降级链,直接报清晰错误
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")
                continue
            result["model_resolved"] = model_name
            return result
        raise ProviderError(f"角色 {role!r} 降级链 {' → '.join(chain)} 全部失败: " + "; ".join(errors))

    def _chat_with_retry(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        """单模型调用 + 指数退避重试(max 次尝试,base_ms × 2^n);成功/失败都喂熔断器。"""
        model = self.models[model_name]
        provider = self.providers[model.provider]
        dialect = PROVIDER_TYPE_MAP.get(provider.type)
        if dialect is None:
            raise DialectError(f"provider {model.provider!r} 的 type {provider.type!r} 无方言映射")
        api_key = self.api_key_for(model.provider)
        attempts = int(self.resilience.retry.get("max", 3))
        base_ms = int(self.resilience.retry.get("base_ms", 1000))
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                result = dialects.chat(
                    dialect,
                    model=model_name,
                    messages=messages,
                    base_url=provider.base_url,
                    api_key=api_key,
                    tools=tools,
                    tool_choice=tool_choice,
                    timeout_s=self.resilience.timeout_s,
                    cancel_event=cancel_event,
                    default_headers=provider.default_headers or None,
                )
            except Exception as exc:
                last_exc = exc
                with self._breaker_lock:
                    self._breaker.record(model_name, False)
                if cancel_event is not None and cancel_event.is_set():
                    raise  # abort 不重试,直接上抛
                if not _is_retryable(exc) or attempt == attempts - 1:
                    raise
                time.sleep(base_ms / 1000 * (2**attempt))
            else:
                with self._breaker_lock:
                    self._breaker.record(model_name, True)
                return result
        raise last_exc  # type: ignore[misc]


def _is_retryable(exc: Exception) -> bool:
    """瞬时故障才重试:网络层错误、429、5xx;4xx(鉴权/参数)与方言不支持不重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


_registry_cache: dict[tuple[str, str | None], ModelRegistry] = {}
_registry_lock = threading.Lock()


def get_default_registry(profile: str | None = None) -> ModelRegistry:
    """加载仓库内置 config/models.toml(按 (path, profile) 缓存)。"""
    key = (str(DEFAULT_CONFIG_PATH), profile)
    with _registry_lock:
        if key not in _registry_cache:
            _registry_cache[key] = ModelRegistry.load(DEFAULT_CONFIG_PATH, profile=profile)
        return _registry_cache[key]
