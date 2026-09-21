"""providers dialects 单测:default_headers 合并、reasoning_content 兼容(全程无网络)。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
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


def test_google_messages_convert_multimodal_and_tools() -> None:
    """OpenAI 消息中的 system/text/data-URI/tool_calls 可转换为 Gemini Content。"""
    from google.genai import types

    png = base64.b64encode(b"fake-png").decode("ascii")
    messages = [
        {"role": "system", "content": "你是 BIM critic"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ]
    system, contents = dialects._messages_to_google_contents(messages, types)
    assert system == "你是 BIM critic"
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "看图"
    assert contents[0].parts[1].inline_data.mime_type == "image/png"
    assert contents[0].parts[1].inline_data.data == b"fake-png"
    assert contents[1].parts[0].function_call.name == "read"
    assert contents[2].parts[0].function_response.response == {"result": "done"}


def test_google_tools_and_response_normalized() -> None:
    """OpenAI tools 转 Gemini declaration，Gemini 响应再归一化为 chat.completion。"""
    from google.genai import types

    tools = [{
        "type": "function",
        "function": {
            "name": "read",
            "description": "读文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }]
    converted = dialects._tools_to_google(tools, types)
    declaration = converted[0].function_declarations[0]
    assert declaration.name == "read"
    assert declaration.parameters_json_schema["required"] == ["path"]

    candidate = SimpleNamespace(
        finish_reason="STOP",
        content=SimpleNamespace(parts=[
            SimpleNamespace(text="思考", thought=True, function_call=None),
            SimpleNamespace(text="完成", thought=False, function_call=None),
            SimpleNamespace(
                text=None,
                thought=False,
                function_call=SimpleNamespace(id="fc1", name="read", args={"path": "a.txt"}),
            ),
        ]),
    )
    response = SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=4,
            thoughts_token_count=2,
            total_token_count=16,
        ),
    )
    result = dialects._google_response_to_completion(response)
    message = result["choices"][0]["message"]
    assert message["content"] == "完成"
    assert message["reasoning"] == "思考"
    assert message["tool_calls"][0]["function"]["name"] == "read"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}


def test_google_cancel_before_call_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """调用前 cancel_event 已置位时不初始化 SDK client，直接返回 aborted completion。"""
    import threading

    cancel = threading.Event()
    cancel.set()
    result = dialects.chat(
        Dialect.GOOGLE_GENAI,
        model="gemini-test",
        messages=[{"role": "user", "content": "hi"}],
        api_key="dummy",
        cancel_event=cancel,
    )
    assert result["aborted"] is True
    assert result["choices"][0]["finish_reason"] == "cancelled"


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


def test_stream_openai_completions_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """stream_openai_completions 契约(delta/reasoning/usage 事件)+ extra_params 并入请求体。"""
    _FakeClient.lines = [
        'data: {"choices": [{"delta": {"reasoning_content": "想"}}]}',
        'data: {"choices": [{"delta": {"content": "答"}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        'data: {"usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}',
        "data: [DONE]",
    ]
    _FakeClient.response_headers = {}
    monkeypatch.setattr(dialects.httpx, "Client", _FakeClient)
    events = list(
        dialects.stream_openai_completions(
            model="glm-5.2",
            messages=[{"role": "user", "content": "hi"}],
            base_url="http://127.0.0.1:9",
            api_key="k",
            extra_params={"reasoning_effort": "high"},
        )
    )
    assert events == [
        {"type": "reasoning", "text": "想"},
        {"type": "delta", "text": "答"},
        {"type": "usage", "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
    ]
    assert _FakeClient.captured["json"]["reasoning_effort"] == "high"
    assert _FakeClient.captured["json"]["stream_options"] == {"include_usage": True}
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer k"


# ---------- anthropic 方言 ----------


def _chat_anthropic_via_fake_client(
    monkeypatch: pytest.MonkeyPatch, lines: list[str], **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用 _FakeClient 跑一次 anthropic chat,返回 (结果, 捕获的请求)。"""
    _FakeClient.lines = lines
    _FakeClient.response_headers = {}
    monkeypatch.setattr(dialects.httpx, "Client", _FakeClient)
    result = dialects.chat(
        Dialect.ANTHROPIC,
        model="claude-opus-4-8-direct",
        messages=kwargs.pop("messages", [{"role": "user", "content": "hi"}]),
        base_url="http://127.0.0.1:9",
        api_key="sk-ant-secret",
        **kwargs,
    )
    return result, _FakeClient.captured


def test_anthropic_request_shaping(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求整形:URL/鉴权头/system 提升/tools 映射/max_tokens 缺省;default_headers 盖不住 x-api-key。"""
    tools = [{
        "type": "function",
        "function": {
            "name": "read",
            "description": "读文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }]
    _, captured = _chat_anthropic_via_fake_client(
        monkeypatch,
        ['data: {"type": "message_stop"}'],
        messages=[{"role": "system", "content": "你是 critic"}, {"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="required",
        default_headers={"x-api-key": "should-not-win", "User-Agent": AGENTROUTER_UA},
    )
    assert captured["url"] == "http://127.0.0.1:9/v1/messages"
    headers = captured["headers"]
    assert headers["x-api-key"] == "sk-ant-secret"  # default_headers 覆盖不了鉴权
    assert headers["anthropic-version"] == dialects.ANTHROPIC_VERSION
    assert headers["User-Agent"] == AGENTROUTER_UA
    body = captured["json"]
    assert body["model"] == "claude-opus-4-8-direct"
    assert body["stream"] is True
    assert body["max_tokens"] == dialects._ANTHROPIC_DEFAULT_MAX_TOKENS
    assert body["system"] == "你是 critic"  # system 提升为顶层参数
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["tools"][0]["input_schema"]["required"] == ["path"]
    assert body["tool_choice"] == {"type": "any"}
    # extra_params 可覆盖 max_tokens(reasoning_payload 等并入请求体)
    _, captured2 = _chat_anthropic_via_fake_client(
        monkeypatch, ['data: {"type": "message_stop"}'], extra_params={"max_tokens": 64}
    )
    assert captured2["json"]["max_tokens"] == 64


def test_anthropic_streaming_text_tool_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE 增量解析:text/thinking/tool_use(input_json_delta)/usage 归一化为 chat.completion。"""
    lines = [
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1}}}',
        'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}',
        'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "想"}}',
        'data: {"type": "content_block_stop", "index": 0}',
        'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}',
        'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "答"}}',
        'data: {"type": "content_block_stop", "index": 1}',
        'data: {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {}}}',
        'data: {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\\"path\\":"}}',
        'data: {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": " \\"a\\"}"}}',
        'data: {"type": "content_block_stop", "index": 2}',
        'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}',
        'data: {"type": "message_stop"}',
    ]
    result, _ = _chat_anthropic_via_fake_client(monkeypatch, lines)
    message = result["choices"][0]["message"]
    assert message["content"] == "答"
    assert message["reasoning"] == "想"
    assert message["tool_calls"] == [
        {"id": "toolu_1", "type": "function", "function": {"name": "read", "arguments": '{"path": "a"}'}}
    ]
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    assert result["aborted"] is False


def test_anthropic_error_event_surfaces_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE error 事件 → DialectError;错误信息只含服务端 type/message,不泄露 api_key。"""
    lines = ['data: {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}']
    with pytest.raises(dialects.DialectError) as exc_info:
        _chat_anthropic_via_fake_client(monkeypatch, lines)
    assert "overloaded_error" in str(exc_info.value)
    assert "sk-ant-secret" not in str(exc_info.value)


def test_anthropic_messages_convert_tools_and_images() -> None:
    """消息转换:assistant.tool_calls → tool_use 块;连续 tool 结果合并进一个 user 消息;data-URI 图片。"""
    png = base64.b64encode(b"fake-png").decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "done"},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "done2"},
    ]
    system, converted = dialects._messages_to_anthropic(messages)
    assert system is None
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    assert converted[0]["content"][1]["source"]["media_type"] == "image/png"
    assert converted[1]["content"][0] == {
        "type": "tool_use", "id": "toolu_1", "name": "read", "input": {"path": "a"},
    }
    tool_results = converted[2]["content"]
    assert [b["tool_use_id"] for b in tool_results] == ["toolu_1", "toolu_2"]  # 交替角色约束:合并


# ---------- openai-responses 方言 ----------


def _chat_responses_via_fake_client(
    monkeypatch: pytest.MonkeyPatch, lines: list[str], **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用 _FakeClient 跑一次 openai-responses chat,返回 (结果, 捕获的请求)。"""
    _FakeClient.lines = lines
    _FakeClient.response_headers = {}
    monkeypatch.setattr(dialects.httpx, "Client", _FakeClient)
    result = dialects.chat(
        Dialect.OPENAI_RESPONSES,
        model="gpt-5.5-direct",
        messages=kwargs.pop("messages", [{"role": "user", "content": "hi"}]),
        base_url="http://127.0.0.1:9",
        api_key="sk-openai-secret",
        **kwargs,
    )
    return result, _FakeClient.captured


def test_responses_request_shaping(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求整形:URL/Bearer/instructions 提升/function_call 历史条目/tools 展平/tool_choice 指定函数。"""
    tools = [{
        "type": "function",
        "function": {
            "name": "read",
            "description": "读文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "system", "content": "你是 critic"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "好",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ]
    _, captured = _chat_responses_via_fake_client(
        monkeypatch,
        ['data: {"type": "response.completed", "response": {}}'],
        messages=messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "read"}},
        default_headers={"Authorization": "Bearer should-not-win"},
    )
    assert captured["url"] == "http://127.0.0.1:9/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer sk-openai-secret"  # default_headers 盖不住
    body = captured["json"]
    assert body["model"] == "gpt-5.5-direct"
    assert body["stream"] is True
    assert body["instructions"] == "你是 critic"
    item_types = [item.get("type", "message") for item in body["input"]]
    assert item_types == ["message", "message", "function_call", "function_call_output"]
    assert body["input"][1]["content"] == [{"type": "output_text", "text": "好"}]
    assert body["input"][2] == {
        "type": "function_call", "call_id": "call_1", "name": "read", "arguments": '{"path":"a"}',
    }
    assert body["input"][3] == {"type": "function_call_output", "call_id": "call_1", "output": "done"}
    assert body["tools"][0] == {
        "type": "function", "name": "read", "description": "读文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    assert body["tool_choice"] == {"type": "function", "name": "read"}


def test_responses_streaming_text_tool_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE 增量解析:output_text/reasoning delta、function_call 参数分片、response.completed usage。"""
    lines = [
        'data: {"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read", "arguments": ""}}',
        'data: {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{\\"path\\": \\"a\\"}"}',
        'data: {"type": "response.reasoning_summary_text.delta", "delta": "想"}',
        'data: {"type": "response.output_text.delta", "delta": "答"}',
        'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}}',
    ]
    result, _ = _chat_responses_via_fake_client(monkeypatch, lines)
    message = result["choices"][0]["message"]
    assert message["content"] == "答"
    assert message["reasoning"] == "想"
    assert message["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path": "a"}'}}
    ]
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_responses_plain_text_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯文本回复:无 tool_calls 时 finish_reason=stop,usage 正常提取。"""
    lines = [
        'data: {"type": "response.output_text.delta", "delta": "你"}',
        'data: {"type": "response.output_text.delta", "delta": "好"}',
        'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}}',
    ]
    result, _ = _chat_responses_via_fake_client(monkeypatch, lines)
    assert result["choices"][0]["message"]["content"] == "你好"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 3


def test_responses_error_event_surfaces_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """response.failed 事件 → DialectError;错误信息只含服务端 code/message,不泄露 api_key。"""
    lines = [
        'data: {"type": "response.failed", "response": {"error": {"code": "server_error", "message": "boom"}}}'
    ]
    with pytest.raises(dialects.DialectError) as exc_info:
        _chat_responses_via_fake_client(monkeypatch, lines)
    assert "server_error" in str(exc_info.value)
    assert "sk-openai-secret" not in str(exc_info.value)
