"""providers dialects 单测:default_headers 合并、reasoning_content 兼容(全程无网络)。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openbimagent.providers import dialects
from openbimagent.providers.dialects import Dialect
from openbimagent.providers.registry import ModelRegistry

AGENTROUTER_UA = "claude-cli/2.0.0 (external, cli)"


class _FakeResponse:
    """httpx.Response 最小替身:stream 上下文 + iter_lines。"""

    def __init__(self, lines: list[str], headers: dict[str, str] | None = None) -> None:
        self._lines = lines
        self.headers = headers or {}  # httpx.Response 契约:headers 总存在(空 dict = 非 WAF,不触发拦截)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


class _FakeClient:
    """httpx.Client 最小替身:捕获最后一次 stream() 的请求参数,回放预设 SSE 行。"""

    captured: dict[str, Any] = {}
    lines: list[str] = []
    response_headers: dict[str, str] = {}

    def __init__(self, timeout: Any = None) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def stream(self, method: str, url: str, json: Any = None, headers: dict | None = None) -> _FakeResponse:
        type(self).captured = {"method": method, "url": url, "json": json, "headers": headers}
        return _FakeResponse(type(self).lines, type(self).response_headers)

    def close(self) -> None:
        pass


def _chat_via_fake_client(
    monkeypatch: pytest.MonkeyPatch, lines: list[str], **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用 _FakeClient 跑一次 openai-completions chat,返回 (结果, 捕获的请求)。"""
    _FakeClient.lines = lines
    monkeypatch.setattr(dialects.httpx, "Client", _FakeClient)
    result = dialects.chat(
        Dialect.OPENAI_COMPLETIONS,
        model="glm-5.2",
        messages=[{"role": "user", "content": "hi"}],
        base_url="http://127.0.0.1:9",
        api_key="real-key",
        **kwargs,
    )
    return result, _FakeClient.captured


def test_default_headers_merged_into_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 的 default_headers 合入请求头;Authorization 仍按 api_key 注入,不被覆盖。"""
    _, captured = _chat_via_fake_client(
        monkeypatch,
        ["data: [DONE]"],
        default_headers={"User-Agent": AGENTROUTER_UA, "Authorization": "Bearer should-not-win"},
    )
    headers = captured["headers"]
    assert headers["User-Agent"] == AGENTROUTER_UA
    assert headers["Authorization"] == "Bearer real-key"  # default_headers 覆盖不了鉴权
    assert headers["Content-Type"] == "application/json"


def test_default_headers_absent_behavior_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 default_headers 时行为不变:无 User-Agent,仅 Authorization + Content-Type。"""
    _, captured = _chat_via_fake_client(monkeypatch, ["data: [DONE]"])
    headers = captured["headers"]
    assert "User-Agent" not in headers
    assert headers == {"Authorization": "Bearer real-key", "Content-Type": "application/json"}


def test_reasoning_content_stream_assembled(monkeypatch: pytest.MonkeyPatch) -> None:
    """reasoning 模型:content 为空不算失败;reasoning_content/reasoning 分片拼进 message.reasoning。"""
    lines = [
        'data: {"choices": [{"delta": {"reasoning_content": "先想"}}]}',
        'data: {"choices": [{"delta": {"reasoning": "再想"}, "finish_reason": "stop"}]}',
        'data: {"usage": {"completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 5}}}',
        "data: [DONE]",
    ]
    result, _ = _chat_via_fake_client(monkeypatch, lines)
    message = result["choices"][0]["message"]
    assert message["content"] == ""  # 空 content 不视为错误
    assert message["reasoning"] == "先想再想"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 5


def _mini_registry(default_headers: dict[str, str] | None = None) -> ModelRegistry:
    provider: dict[str, Any] = {
        "type": "openai-compatible",
        "base_url": "http://127.0.0.1:9",
        "api_key_env": "DUMMY_KEY",
    }
    if default_headers is not None:
        provider["default_headers"] = default_headers
    return ModelRegistry(
        providers={"p1": provider},
        models={"m1": {"provider": "p1"}},
        profiles={"official": {"orchestrator": "m1"}},
        resilience={
            "retry": {"max": 1, "base_ms": 1},
            "timeout_s": 1,
            "circuit_breaker": {"failures": 5, "cooldown_s": 300},
        },
        active_profile="official",
    )


def test_registry_passes_default_headers_to_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    """registry → dialect 透传:配了 default_headers 原样下传,缺省下传 None(行为不变)。"""
    monkeypatch.setenv("DUMMY_KEY", "dummy")
    captured: dict[str, Any] = {}

    def fake_chat(dialect, *, model, messages, **kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr("openbimagent.providers.dialects.chat", fake_chat)
    _mini_registry({"User-Agent": AGENTROUTER_UA}).chat("orchestrator", [{"role": "user", "content": "hi"}])
    assert captured["default_headers"] == {"User-Agent": AGENTROUTER_UA}

    captured.clear()
    _mini_registry().chat("orchestrator", [{"role": "user", "content": "hi"}])
    assert captured["default_headers"] is None


def test_sse_line_roundtrip_pure() -> None:
    """纯函数路径:_consume_sse_line 累积 reasoning_content,_assemble_completion 透出 reasoning。"""
    acc: dict[str, Any] = {
        "content_parts": [],
        "reasoning_parts": [],
        "tool_calls": {},
        "finish_reason": None,
        "usage": None,
    }
    chunk = {"choices": [{"delta": {"reasoning_content": "思考片段"}, "finish_reason": "stop"}]}
    assert dialects._consume_sse_line(f"data: {json.dumps(chunk)}", acc) is True
    assert dialects._consume_sse_line("data: [DONE]", acc) is False
    result = dialects._assemble_completion(acc, aborted=False)
    message = result["choices"][0]["message"]
    assert message["content"] == ""
    assert message["reasoning"] == "思考片段"
    assert "reasoning" not in dialects._assemble_completion(
        {"content_parts": ["正"], "reasoning_parts": [], "tool_calls": {}, "finish_reason": "stop", "usage": None},
        aborted=False,
    )["choices"][0]["message"]  # 无 reasoning 时不透出该字段


def test_waf_html_response_raises_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """WAF 挑战页(200 + text/html)→ WAFChallengeError(TransportError 子类,registry 可重试)。

    不检测会被 _consume_sse_line 静默吞掉(只认 "data:" 行)→ 空响应假成功。
    """
    import httpx

    _FakeClient.lines = ["<!doctypehtml><meta name='aliyun_waf_aa'>..."]
    _FakeClient.response_headers = {"content-type": "text/html; charset=utf-8"}
    monkeypatch.setattr(dialects.httpx, "Client", _FakeClient)
    with pytest.raises(dialects.WAFChallengeError):
        dialects.chat(
            Dialect.OPENAI_COMPLETIONS,
            model="glm-5.2-ar",
            messages=[{"role": "user", "content": "hi"}],
            base_url="http://127.0.0.1:9",
            api_key="k",
        )
    _FakeClient.response_headers = {}  # reset
    # 必须是 TransportError 子类,否则 registry._is_retryable 不判为瞬时故障、不退避重试
    assert issubclass(dialects.WAFChallengeError, httpx.TransportError)
