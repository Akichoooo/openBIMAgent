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
from openbimagent.providers.dialects import PROVIDER_TYPE_MAP, CircuitBreaker, DialectError, reasoning_payload

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "models.toml"
"""仓库内置 models.toml(src/openbimagent/providers/registry.py → 上溯三级为仓库根)。"""

PROFILE_ENV_VAR = "OPENBIMAGENT_PROFILE"
DEFAULT_PROFILE = "official"

#: per-role 默认思考档位(统一 5 档)的**内置兜底**;质量咽喉(critic/modeler/planner)高档,杂活(clarify/deliver)低档。
#: models.toml ``[reasoning_defaults]`` 优先（旧版仅声称“与 toml 镜像”但从不读 toml，
#: 改 toml 完全不生效——两份手写副本退化成一份死配置）；chat(reasoning_level=...) 可显式覆盖。
_REASONING_DEFAULTS: dict[str, str] = {
    "critic_render": "high",
    "critic_scad": "high",
    "modeler": "high",
    "planner": "high",
    "lighter": "high",
    "materialist": "high",
    "orchestrator": "medium",
    "researcher": "medium",
    "clarify": "low",
    "deliver": "low",
}


def _provider_endpoint(provider: ProviderConfig) -> str | None:
    """Resolved endpoint for diagnostics; never includes credentials."""
    base_url = provider.base_url
    if not base_url:
        return None
    if provider.type in {"openai-compatible", "openai-completions"}:
        return f"{base_url.rstrip('/')}/chat/completions"
    return base_url


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
    """单个模型声明;capabilities 含 tools/vision 等。

    ``reasoning``/``cost_multiplier``/``description`` 对应 models.toml 的
    ``[models.<name>.reasoning]`` / ``cost_multiplier`` / ``description``。旧版未声明这三个
    字段，pydantic 默认忽略 extra → toml 里写了但运行时一律拿不到（死配置）。
    """

    provider: str
    context_window: int | None = None  # 调研值,部分模型待官方确认(config/models.toml 注)
    capabilities: list[str] = Field(default_factory=list)
    cost_per_mtoken: dict[str, float] | None = None
    cost_multiplier: float | None = None
    description: str | None = None
    reasoning: dict[str, Any] | None = None


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
    reasoning_defaults: dict[str, str] = Field(default_factory=dict)
    """models.toml ``[reasoning_defaults]``;优先于内置 ``_REASONING_DEFAULTS``。"""
    tracking: dict[str, Any] = Field(default_factory=dict)
    """models.toml ``[tracking]``（log_tokens/log_cost）;旧版从未加载，是纯装饰配置。"""
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
            reasoning_defaults={
                str(k): str(v) for k, v in (raw.get("reasoning_defaults") or {}).items()
            },
            tracking=dict(raw.get("tracking") or {}),
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

    def reasoning_default_for(self, role: str) -> str | None:
        """per-role 默认思考档位：models.toml ``[reasoning_defaults]`` 优先，内置表兜底。

        两边都没声明则返回 None（用模型自身默认）。toml 值必须在 REASONING_LEVELS 内，
        否则视为配置错误忽略并回退内置值（不静默把非法档位传给方言层）。
        """
        from openbimagent.providers.dialects import REASONING_LEVELS

        candidate = self.reasoning_defaults.get(role)
        if candidate in REASONING_LEVELS:
            return candidate
        return _REASONING_DEFAULTS.get(role)

    def tracking_enabled(self, key: str, default: bool = True) -> bool:
        """models.toml ``[tracking]`` 开关（log_tokens/log_cost）；未声明走 default。"""
        value = self.tracking.get(key)
        return default if value is None else bool(value)

    def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: threading.Event | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        reasoning_level: str | None = None,
        required_capabilities: list[str] | None = None,
        diagnostics: dict[str, Any] | None = None,
        cancel_reason: str | None = None,
    ) -> dict[str, Any]:
        """角色 → 模型 → provider 解析后统一调用;沿降级链尝试,跳过熔断冷却中的模型。

        reasoning_level:统一思考档位(off/low/medium/high/max);缺省用 per-role 默认(_REASONING_DEFAULTS)。
        required_capabilities:降级链能力位过滤(如 ["vision"]);缺省自动推断——
        带 tools 要求 "tools"、消息含图像要求 "vision";链上无满足候选即 fail-fast,
        不再把 vision/工具任务降级到能力缺失的模型上硬失败。
        diagnostics:可选调用事件汇;记录请求/尝试/计时/HTTP 状态/长度/usage 是否存在,
        不记录密钥、完整 base64 或完整 reasoning 正文。收到部分内容不等于业务成功。
        """
        level = reasoning_level or self.reasoning_default_for(role)
        chain = self.fallback_chain(self.model_name_for_role(role))
        required = set(required_capabilities) if required_capabilities is not None else self._infer_capabilities(messages, tools)
        if required:
            chain = [m for m in chain if required.issubset(set(self.models[m].capabilities))]
            if not chain:
                raise ProviderError(
                    f"角色 {role!r} 降级链无满足能力 {sorted(required)} 的候选模型;请在 models.toml 补充具备该能力的 fallback"
                )
        if diagnostics is not None:
            diagnostics.setdefault("role", role)
            diagnostics.setdefault("requested_model", self.model_name_for_role(role))
            diagnostics.setdefault("requested_chain", list(chain))
            diagnostics.setdefault("attempts", [])
            diagnostics.setdefault("skipped_models", [])
            diagnostics.setdefault("started_at", time.time())
        errors: list[str] = []
        for model_name in chain:
            model = self.models[model_name]
            provider = self.providers[model.provider]
            with self._breaker_lock:
                allowed = self._breaker.allow(model_name)
            if not allowed:
                errors.append(f"{model_name}: 熔断冷却中,跳过")
                if diagnostics is not None:
                    diagnostics["skipped_models"].append({"model": model_name, "reason": "circuit_breaker"})
                continue
            if cancel_event is not None and cancel_event.is_set():
                if diagnostics is not None:
                    diagnostics["cancel_before_model"] = model_name
                result = dialects.aborted_completion(
                    reason=cancel_reason or "pre_cancelled", diagnostics=diagnostics
                )
                result["model_resolved"] = model_name
                result["model_provider"] = model.provider
                result["model_endpoint"] = _provider_endpoint(provider)
                return result
            try:
                result = self._chat_with_retry(
                    model_name,
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    cancel_event=cancel_event,
                    cancel_reason=cancel_reason,
                    reasoning_level=level,
                    diagnostics=diagnostics,
                )
            except ProviderKeyError:
                raise  # 缺 key 是配置错误,不是模型故障:不走降级链,直接报清晰错误
            except Exception as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise  # 截止/取消后不再启动 fallback
                errors.append(f"{model_name}: {exc}")
                if diagnostics is not None:
                    diagnostics["skipped_models"].append({"model": model_name, "reason": str(exc)[:300]})
                continue
            result["model_resolved"] = model_name
            result["model_provider"] = model.provider
            result["model_endpoint"] = _provider_endpoint(provider)
            if diagnostics is not None:
                diagnostics["selected_model"] = model_name
                diagnostics["selected_provider"] = model.provider
                diagnostics["selected_endpoint"] = result["model_endpoint"]
                diagnostics["response_model"] = result.get("response_model")
                if result.get("aborted"):
                    diagnostics["aborted"] = True
                    diagnostics["cancel_reason"] = result.get("cancel_reason") or cancel_reason or "cancel_event"
            return result
        raise ProviderError(f"角色 {role!r} 降级链 {' → '.join(chain)} 全部失败: " + "; ".join(errors))

    @staticmethod
    def _infer_capabilities(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> set[str]:
        """按调用形态推断能力位:挂工具 → tools;消息含图像(多模态 parts)→ vision。"""
        required: set[str] = set()
        if tools:
            required.add("tools")
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("type") or "")
                if kind in ("image_url", "image", "input_image") or "inline_data" in part or "image_url" in part:
                    required.add("vision")
                    break
        return required

    def _chat_with_retry(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        cancel_event: threading.Event | None,
        cancel_reason: str | None = None,
        reasoning_level: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """单模型调用 + 指数退避重试(max 次尝试,base_ms × 2^n);成功/失败都喂熔断器。

        每次重试启动前检查 cancel_event。aborted/取消结果只记录失败,不当作模型成功。
        """
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
            attempt_diag: dict[str, Any] = {
                "model": model_name,
                "provider": model.provider,
                "endpoint": _provider_endpoint(provider),
                "retry_index": attempt,
                "started_at": time.time(),
                "request_sent": False,
            }
            if cancel_event is not None and cancel_event.is_set():
                attempt_diag.update(outcome="cancelled_before_request", duration_ms=0.0)
                if diagnostics is not None:
                    diagnostics.setdefault("attempts", []).append(attempt_diag)
                result = dialects.aborted_completion(
                    reason=cancel_reason or "pre_cancelled", diagnostics=attempt_diag
                )
                with self._breaker_lock:
                    self._breaker.record(model_name, False)
                return result
            attempt_started = time.perf_counter()
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
                    cancel_reason=cancel_reason,
                    default_headers=provider.default_headers or None,
                    extra_params=reasoning_payload(model_name, reasoning_level)
                    if reasoning_level
                    else None,
                    on_delta=get_delta_sink(),
                    diagnostics=attempt_diag,
                )
            except Exception as exc:
                last_exc = exc
                attempt_diag.update(
                    outcome="error",
                    duration_ms=round((time.perf_counter() - attempt_started) * 1000, 1),
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                    http_status=(
                        exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else attempt_diag.get("http_status")
                    ),
                )
                if diagnostics is not None:
                    diagnostics.setdefault("attempts", []).append(attempt_diag)
                with self._breaker_lock:
                    self._breaker.record(model_name, False)
                if cancel_event is not None and cancel_event.is_set():
                    raise  # abort 不重试,直接上抛
                if not _is_retryable(exc) or attempt == attempts - 1:
                    raise
                time.sleep(base_ms / 1000 * (2**attempt))
            else:
                attempt_diag["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 1)
                attempt_diag["outcome"] = "aborted" if result.get("aborted") else "completed"
                attempt_diag["aborted"] = bool(result.get("aborted"))
                attempt_diag["response_model"] = result.get("response_model")
                if diagnostics is not None:
                    diagnostics.setdefault("attempts", []).append(attempt_diag)
                with self._breaker_lock:
                    self._breaker.record(model_name, not bool(result.get("aborted")))
                if result.get("aborted"):
                    if diagnostics is not None:
                        diagnostics["aborted"] = True
                        diagnostics["cancel_reason"] = result.get("cancel_reason") or cancel_reason or "cancel_event"
                    return result  # caller decides; never treated as a valid report
                return result
        raise last_exc  # type: ignore[misc]


def _is_retryable(exc: Exception) -> bool:
    """瞬时故障才重试:网络层错误、429、5xx;4xx(鉴权/参数)与方言不支持不重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


_registry_cache: dict[tuple[str, str | None], ModelRegistry] = {}
_registry_lock = threading.Lock()

# 线程本地流式增量汇:run 工作线程经 set_delta_sink 绑定后,本线程内所有 registry.chat
# 调用(openai-completions 方言)边收边把文本分片推给 sink(delta|reasoning),供 SSE 实时回显;
# 持久累积仍在方言层 acc 内完成,回显旁路与落盘内容同源。线程隔离,并发 run 互不串流。
_delta_local = threading.local()


def set_delta_sink(sink: Any) -> None:
    """绑定/解绑当前线程的流式增量汇;sink 签名 (kind: str, text: str) -> None,None 解绑。"""
    _delta_local.sink = sink


def get_delta_sink() -> Any:
    """取当前线程的流式增量汇(缺省 None=不回显,行为与此前完全一致)。"""
    return getattr(_delta_local, "sink", None)


def get_default_registry(profile: str | None = None) -> ModelRegistry:
    """加载仓库内置 config/models.toml(按 (path, profile) 缓存)。"""
    key = (str(DEFAULT_CONFIG_PATH), profile)
    with _registry_lock:
        if key not in _registry_cache:
            _registry_cache[key] = ModelRegistry.load(DEFAULT_CONFIG_PATH, profile=profile)
        return _registry_cache[key]
