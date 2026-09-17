"""providers registry 单测:profile 解析、fallback 链、缺 key 报错、熔断器(全程无网络)。"""

from pathlib import Path

import httpx
import pytest

from openbimagent.providers.dialects import CircuitBreaker
from openbimagent.providers.registry import (
    PROFILE_ENV_VAR,
    ModelRegistry,
    ProviderKeyError,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "models.toml"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例默认official profile、且 GLM key 不存在(缺 key 用例依赖)。"""
    monkeypatch.delenv(PROFILE_ENV_VAR, raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)


def test_profile_default_official() -> None:
    """默认 official 通道:角色 → 模型 → provider 解析。"""
    reg = ModelRegistry.load(CONFIG_PATH)
    assert reg.active_profile == "official"
    assert reg.model_name_for_role("orchestrator") == "glm-5.2"
    assert reg.model_for_role("modeler").provider == "gemini"  # 质量咽喉不降 Flash(COMPONENTS §3)
    assert "vision" in reg.model_for_role("critic_render").capabilities


def test_profile_switch_via_env(monkeypatch) -> None:
    """OPENBIMAGENT_PROFILE=test 切 agentrouter 联调通道。"""
    monkeypatch.setenv(PROFILE_ENV_VAR, "test")
    reg = ModelRegistry.load(CONFIG_PATH)
    assert reg.active_profile == "test"
    assert reg.model_name_for_role("modeler") == "claude-opus-4-8"
    assert reg.model_name_for_role("critic_render") == "gpt-5.5"


def test_faucet_profile_uses_independent_generator_and_critic_models() -> None:
    """免费联调 profile 不覆盖官方配置，生成与评判模型分家。"""
    reg = ModelRegistry.load(CONFIG_PATH, profile="faucet")
    assert reg.providers["freetokenfaucet"].api_key_env == "FREETOKENFAUCET_API_KEY"
    assert reg.providers["freetokenfaucet"].base_url == "https://freetokenfaucet.com/v1"
    assert reg.model_name_for_role("planner") == "gpt-5.6-terra"
    assert reg.model_name_for_role("critic_render") == "mimo-v2.5-pro"
    assert reg.model_name_for_role("orchestrator") == "deepseek-v4-flash"
    assert reg.fallback_chain("gpt-5.6-terra") == [
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "deepseek-v4-flash",
    ]


def test_unknown_profile_and_role_raise() -> None:
    with pytest.raises(ValueError, match="未知 profile"):
        ModelRegistry.load(CONFIG_PATH, profile="不存在的通道")
    with pytest.raises(ValueError, match="未定义角色"):
        ModelRegistry.load(CONFIG_PATH).model_name_for_role("不存在的角色")


def test_fallback_chain() -> None:
    """降级链含主模型自身,依序替换。"""
    reg = ModelRegistry.load(CONFIG_PATH, profile="test")
    assert reg.fallback_chain("claude-opus-4-8") == ["claude-opus-4-8", "claude-opus-4-6", "glm-5.2-ar"]
    assert reg.fallback_chain("glm-5.2-ar") == ["glm-5.2-ar"]  # 无降级即自身
    with pytest.raises(ValueError, match="未声明的模型"):
        reg.fallback_chain("不存在的模型")


def test_missing_api_key_clear_error() -> None:
    """缺 key 报清晰错误:含 provider 与环境变量名,不含任何 key 值。"""
    reg = ModelRegistry.load(CONFIG_PATH)
    with pytest.raises(ProviderKeyError) as exc_info:
        reg.api_key_for("glm")
    msg = str(exc_info.value)
    assert "glm" in msg and "GLM_API_KEY" in msg


def test_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("GLM_API_KEY", "dummy-key-for-test")
    reg = ModelRegistry.load(CONFIG_PATH)
    assert reg.api_key_for("glm") == "dummy-key-for-test"


def test_agentrouter_default_headers_from_config() -> None:
    """agentrouter 按 User-Agent 过滤客户端(实测 401);models.toml 必须带 claude-cli UA。"""
    reg = ModelRegistry.load(CONFIG_PATH)
    headers = reg.providers["agentrouter"].default_headers
    assert headers.get("User-Agent") == "claude-cli/2.0.0 (external, cli)"
    assert reg.providers["glm"].default_headers == {}  # 未配置时为空,行为不变


def _mini_registry() -> ModelRegistry:
    """最小注册表:orchestrator=m1,fallback m1→m2;retry 1 次、base 1ms,测降级不发网络。"""
    return ModelRegistry(
        providers={"p1": {"type": "openai-compatible", "base_url": "http://127.0.0.1:9", "api_key_env": "DUMMY_KEY"}},
        models={"m1": {"provider": "p1"}, "m2": {"provider": "p1"}},
        profiles={"official": {"orchestrator": "m1"}},
        fallbacks={"m1": ["m2"]},
        resilience={"retry": {"max": 1, "base_ms": 1}, "timeout_s": 1, "circuit_breaker": {"failures": 5, "cooldown_s": 300}},
        active_profile="official",
    )


def test_chat_walks_fallback_chain(monkeypatch) -> None:
    """主模型瞬时失败 → 沿降级链换 m2 成功;monkeypatch 方言层,全程无网络。"""
    monkeypatch.setenv("DUMMY_KEY", "dummy")
    calls: list[str] = []

    def fake_chat(dialect, *, model, messages, **kwargs):
        calls.append(model)
        if model == "m1":
            raise httpx.ConnectError("模拟网络故障")
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr("openbimagent.providers.dialects.chat", fake_chat)
    reg = _mini_registry()
    result = reg.chat("orchestrator", [{"role": "user", "content": "hi"}])
    assert result["model_resolved"] == "m2"
    assert calls == ["m1", "m2"]


def test_role_reasoning_default_reaches_wire(monkeypatch) -> None:
    """critic_render 默认 high → 经 reasoning_payload 变成 wire 参数(本地 mock HTTP 全离线)。"""
    import json as _json
    import threading as _threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    monkeypatch.setenv("DUMMY_KEY", "dummy")
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            captured["body"] = _json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunk = _json.dumps({
                "id": "1", "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            })
            self.wfile.write(f"data: {chunk}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reg = ModelRegistry(
            providers={"p1": {"type": "openai-compatible", "base_url": f"http://127.0.0.1:{server.server_address[1]}", "api_key_env": "DUMMY_KEY"}},
            models={"glm-5.2": {"provider": "p1"}},
            profiles={"official": {"critic_render": "glm-5.2"}},
            fallbacks={},
            resilience={"retry": {"max": 1, "base_ms": 1}, "timeout_s": 10, "circuit_breaker": {"failures": 5, "cooldown_s": 300}},
            active_profile="official",
        )
        reg.chat("critic_render", [{"role": "user", "content": "hi"}])
    finally:
        server.shutdown()
    assert captured["body"]["reasoning_effort"] == "high"


def test_unknown_model_sends_no_reasoning_params(monkeypatch) -> None:
    """未知模型:角色默认档位不臆造 wire 参数(请求体不含 reasoning 字段)。"""
    monkeypatch.setenv("DUMMY_KEY", "dummy")
    captured: dict = {}

    def fake_chat(dialect, *, model, messages, **kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr("openbimagent.providers.dialects.chat", fake_chat)
    reg = ModelRegistry(
        providers={"p1": {"type": "openai-compatible", "base_url": "http://127.0.0.1:9", "api_key_env": "DUMMY_KEY"}},
        models={"mystery": {"provider": "p1"}},
        profiles={"official": {"deliver": "mystery"}},
        fallbacks={},
        resilience={"retry": {"max": 1, "base_ms": 1}, "timeout_s": 1, "circuit_breaker": {"failures": 5, "cooldown_s": 300}},
        active_profile="official",
    )
    reg.chat("deliver", [{"role": "user", "content": "hi"}])
    extra = captured.get("extra_params") or {}
    assert extra == {}


def test_chat_missing_key_raises_key_error(monkeypatch) -> None:
    """整条链第一步就缺 key:报 ProviderKeyError(不是网络错误)。"""
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    with pytest.raises(ProviderKeyError, match="DUMMY_KEY"):
        _mini_registry().chat("orchestrator", [])


def test_circuit_breaker() -> None:
    """5 次失败冷却(此处阈值 2):closed → open → 成功复位 closed。"""
    cb = CircuitBreaker(failures=2, cooldown_s=100)
    assert cb.allow("m")
    cb.record("m", False)
    assert cb.allow("m")
    cb.record("m", False)  # 达阈值 → open
    assert not cb.allow("m")
    cb.record("m", True)  # 成功复位
    assert cb.allow("m")
