"""API 方言层:4 种方言统一 chat 接口;重试/熔断集中在 providers 层。

对应文档:
- docs/architecture/COMPONENTS.md §4 providers([resilience]:韧性集中,业务代码不重复造)
- config/models.toml [providers.*] 的 type 字段

方言:openai-completions / openai-responses / anthropic / google-genai,均已实现:
- openai-completions:OpenAI 兼容端点(GLM/agentrouter/freetokenfaucet),httpx SSE。
- google-genai:Google Gen AI SDK(同步 generate_content)。
- anthropic:Anthropic Messages API(POST /v1/messages,x-api-key + anthropic-version,SSE)。
- openai-responses:OpenAI Responses API(POST /v1/responses,Bearer,SSE)。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from enum import StrEnum
from typing import Any

import httpx

ABORT_POLL_INTERVAL_S = 0.5
"""cancel_event 轮询间隔(ARCH §6.5:全程可 abort 且返回部分结果)。"""

REASONING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high", "max")
"""统一思考档位(chat stream 的 effort 参数);非法值一律回退 medium。"""

_DEFAULT_REASONING_LEVEL = "medium"


def _normalize_level(level: str | None) -> str:
    """非法/缺省档位回退 medium(不抛错:档位只是偏好,不能让请求失败)。"""
    return level if level in REASONING_LEVELS else _DEFAULT_REASONING_LEVEL


# 本地兼容映射不等同于供应商能力认证；部署前需按实际端点确认参数支持。
_THINKING_TYPE: dict[str, str] = {"off": "disabled", "low": "disabled", "medium": "adaptive", "high": "enabled", "max": "enabled"}
_QWEN_BUDGET: dict[str, int] = {"low": 4096, "medium": 32768, "high": 262144, "max": 262144}
_EFFORT: dict[str, str] = {"off": "none", "low": "low", "medium": "medium", "high": "high", "max": "max"}
def _payload_fn(fn: Any) -> Any:
    return fn


_effort_map = _payload_fn(lambda lv: {"reasoning_effort": _EFFORT[lv]})


_MODEL_REASONING_MAP: dict[str, Any] = {
    "kimi-k3": lambda level: {"reasoning_effort": "max"},
    "qwen3.8-max": lambda level: {"enable_thinking": False} if level == "off" else {
        "enable_thinking": True, "thinking_budget": _QWEN_BUDGET[level],
    },
    "mimo-v2.5": _payload_fn(lambda lv: {"thinking": {"type": _THINKING_TYPE[lv]}}),
    "mimo-v2.5-pro": _payload_fn(lambda lv: {"thinking": {"type": _THINKING_TYPE[lv]}}),
    "deepseek-v4-flash": _payload_fn(
        lambda lv: {"thinking": {"type": "disabled"}}
        if lv == "off"
        else {"thinking": {"type": "enabled"}, "reasoning_effort": _EFFORT[lv]}
    ),
    "claude-opus-4-8": _payload_fn(lambda lv: {"output_config": {"effort": _EFFORT[lv]}}),
    "claude-opus-4-6": _payload_fn(lambda lv: {"output_config": {"effort": _EFFORT[lv]}}),
    "gemini-3.1-pro": _payload_fn(lambda lv: {"thinking_level": _EFFORT[lv]}),
    "gemini-3.6-flash": _payload_fn(lambda lv: {"thinking_level": _EFFORT[lv]}),
    "gpt-5.5": _effort_map,
    "gpt-5.6-luna": _effort_map,
    "gpt-5.6-terra": _effort_map,
    "glm-5.2": _payload_fn(lambda lv: {"reasoning_effort": {"off": "none", "low": "low", "medium": "high", "high": "high", "max": "max"}[lv]}),
    "glm-5.2-ar": _payload_fn(lambda lv: {"reasoning_effort": {"off": "none", "low": "low", "medium": "high", "high": "high", "max": "max"}[lv]}),
}


def reasoning_payload(model: str, level: str | None) -> dict[str, Any]:
    """统一思考档位(off/low/medium/high/max)→ 各模型 wire 参数。

    使用本地兼容映射；未知模型不附加 reasoning 字段。非法档位回退 medium。
    返回值可并入 extra_params；实际端点是否支持这些参数需独立验证。
    """
    normalized = _normalize_level(level)
    mapper = _MODEL_REASONING_MAP.get(model)
    if mapper is None:
        return {}
    return mapper(normalized)


def stream_openai_completions(
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str,
    api_key: str,
    timeout_s: int = 120,
    cancel_event: threading.Event | None = None,
    extra_params: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """openai-compatible 流式生成器:逐 delta 产出事件,server/chat.py 逐帧转发 SSE。

    事件形态(与 chat.py 消费契约一致):
    - {"type": "delta", "text": ...}      正文分片
    - {"type": "reasoning", "text": ...}  思维链分片(reasoning_content / reasoning)
    - {"type": "usage", "usage": {...}}   收尾用量(include_usage)
    cancel_event 置位即关闭连接、停止产出(部分内容已由调用方消费,不回滚)。
    extra_params:reasoning_payload 等额外请求体字段,原样并入。
    """
    if not base_url:
        raise DialectError("openai-compatible provider 缺少 base_url")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **(extra_params or {}),
    }
    headers = {"Authorization": f"Bearer {api_key or ''}", "Content-Type": "application/json"}
    acc: dict[str, Any] = {
        "content_parts": [],
        "reasoning_parts": [],
        "tool_calls": {},
        "finish_reason": None,
        "usage": None,
    }
    with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            # WAF 挑战页(200 + text/html)不拦截会被 _consume_sse_line 吞掉成空响应假成功
            ct = resp.headers.get("content-type", "").lower()
            if "text/html" in ct:
                raise WAFChallengeError(
                    f"响应为 HTML(content-type={ct!r}),疑似 Aliyun WAF 挑战页(速率限流);退避后重试"
                )
            for line in resp.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    return
                # 只透传正文与 reasoning 增量;usage 收尾一帧;tool_calls 在 chat 端点不在契约内
                before_content = len(acc["content_parts"])
                before_reasoning = len(acc["reasoning_parts"])
                if not _consume_sse_line(line, acc):
                    break
                for piece in acc["reasoning_parts"][before_reasoning:]:
                    yield {"type": "reasoning", "text": piece}
                for piece in acc["content_parts"][before_content:]:
                    yield {"type": "delta", "text": piece}
    if acc["usage"]:
        yield {"type": "usage", "usage": acc["usage"]}


class Dialect(StrEnum):
    """4 种 API 方言(COMPONENTS §1 providers 技术行)。"""

    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC = "anthropic"
    GOOGLE_GENAI = "google-genai"


PROVIDER_TYPE_MAP: dict[str, Dialect] = {
    "openai-compatible": Dialect.OPENAI_COMPLETIONS,  # glm 等 OpenAI 兼容端点
    "openai-completions": Dialect.OPENAI_COMPLETIONS,
    "openai-responses": Dialect.OPENAI_RESPONSES,
    "anthropic": Dialect.ANTHROPIC,
    "google-genai": Dialect.GOOGLE_GENAI,
}
"""models.toml provider.type → 方言映射。"""


class DialectError(RuntimeError):
    """方言层调用失败(连接/协议/参数);熔断与降级的判定输入。"""


class WAFChallengeError(httpx.TransportError):
    """Aliyun WAF/CDN 返回 HTML 挑战页(非 SSE/JSON),通常因速率限流触发。

    继承 httpx.TransportError → registry._is_retryable 判为瞬时故障,走指数退避重试。
    不检测会被 _consume_sse_line 静默吞掉(只处理 "data:" 行)→ 空响应假成功。
    """


def chat(
    dialect: Dialect,
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None = None,
    api_key: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    timeout_s: int = 120,
    cancel_event: threading.Event | None = None,
    default_headers: dict[str, str] | None = None,
    extra_params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """统一调用入口:按方言适配请求/响应;重试/熔断由 registry 套用(models.toml [resilience])。

    返回 OpenAI chat.completion 形态 dict;abort 时正常返回并带 ``aborted=True`` 与部分内容。
    default_headers:provider 级额外请求头(models.toml [providers.*].default_headers),
    与方言注入的 Authorization/Content-Type 合并,后者优先(鉴权不被覆盖)。
    extra_params:reasoning_payload 等额外请求体字段,并入 HTTP 方言请求体
    (google-genai 方言走 SDK,忽略 extra_params)。
    """
    if dialect is Dialect.OPENAI_COMPLETIONS:
        return _chat_openai_completions(
            model=model,
            messages=messages,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            tool_choice=tool_choice,
            timeout_s=timeout_s,
            cancel_event=cancel_event,
            default_headers=default_headers,
            extra_params=extra_params,
        )
    if dialect is Dialect.GOOGLE_GENAI:
        return _chat_google_genai(
            model=model,
            messages=messages,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            tool_choice=tool_choice,
            timeout_s=timeout_s,
            cancel_event=cancel_event,
            default_headers=default_headers,
        )
    if dialect is Dialect.ANTHROPIC:
        return _chat_anthropic(
            model=model,
            messages=messages,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            tool_choice=tool_choice,
            timeout_s=timeout_s,
            cancel_event=cancel_event,
            default_headers=default_headers,
            extra_params=extra_params,
        )
    if dialect is Dialect.OPENAI_RESPONSES:
        return _chat_openai_responses(
            model=model,
            messages=messages,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            tool_choice=tool_choice,
            timeout_s=timeout_s,
            cancel_event=cancel_event,
            default_headers=default_headers,
            extra_params=extra_params,
        )
    raise NotImplementedError(f"未实现的方言: {dialect}")


def _chat_google_genai(
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None,
    api_key: str | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    timeout_s: int,
    cancel_event: threading.Event | None,
    default_headers: dict[str, str] | None,
) -> dict[str, Any]:
    """Google Gen AI SDK 方言:消息/图片/函数调用双向适配为统一 chat.completion 形态。

    使用 SDK 的同步 ``models.generate_content``。调用前和返回后检查 cancel_event;
    SDK 当前不提供同步请求的跨线程安全强制中断,所以置位后以 aborted=True 丢弃完整响应,
    保持上层 checkpoint 语义。base_url/default_headers 仅在显式配置时经 HttpOptions 透传。
    """
    if not api_key:
        raise DialectError("google-genai provider 缺少 api_key")
    if cancel_event is not None and cancel_event.is_set():
        return _empty_aborted_completion()
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - pyproject 已声明依赖
        raise DialectError("google-genai 方言需要 google-genai 依赖") from exc

    system_instruction, contents = _messages_to_google_contents(messages, types)
    config_kwargs: dict[str, Any] = {}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    google_tools = _tools_to_google(tools, types)
    if google_tools:
        config_kwargs["tools"] = google_tools
        tool_config = _tool_config_to_google(tool_choice, google_tools, types)
        if tool_config is not None:
            config_kwargs["tool_config"] = tool_config

    http_options_kwargs: dict[str, Any] = {"timeout": int(timeout_s * 1000)}
    if base_url:
        http_options_kwargs["base_url"] = base_url
    if default_headers:
        http_options_kwargs["headers"] = dict(default_headers)
    http_options = types.HttpOptions(**http_options_kwargs)
    client = genai.Client(api_key=api_key, http_options=http_options)
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    finally:
        client.close()
    if cancel_event is not None and cancel_event.is_set():
        return _empty_aborted_completion()
    return _google_response_to_completion(response)


def _messages_to_google_contents(messages: list[dict[str, Any]], types: Any) -> tuple[str | None, list[Any]]:
    """OpenAI messages → (system_instruction, Google Content[])。"""
    system_parts: list[str] = []
    contents: list[Any] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            text = _content_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            name = str(message.get("name") or message.get("tool_call_id") or "tool")
            payload = _json_object_or_text(content)
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=name, response=payload)],
                )
            )
            continue
        parts = _content_to_google_parts(content, types)
        tool_calls = message.get("tool_calls") or []
        if tool_calls and len(parts) == 1 and getattr(parts[0], "text", None) == "":
            parts.clear()
        for tool_call in tool_calls:
            fn = tool_call.get("function") or {}
            args = _json_object_or_text(fn.get("arguments", {}))
            parts.append(types.Part.from_function_call(name=str(fn.get("name") or "tool"), args=args))
        if not parts:
            parts = [types.Part.from_text(text="")]
        contents.append(types.Content(role="model" if role == "assistant" else "user", parts=parts))
    if not contents:
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="")])]
    return ("\n\n".join(system_parts) or None), contents


def _content_to_google_parts(content: Any, types: Any) -> list[Any]:
    """OpenAI 字符串/content-parts → Google Part[]，支持 base64 data-URI 图片。"""
    if isinstance(content, str):
        return [types.Part.from_text(text=content)]
    if not isinstance(content, list):
        return [types.Part.from_text(text=str(content))]
    parts: list[Any] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(types.Part.from_text(text=str(item)))
            continue
        if item.get("type") == "text":
            parts.append(types.Part.from_text(text=str(item.get("text") or "")))
            continue
        if item.get("type") == "image_url":
            image = item.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url.startswith("data:"):
                raise DialectError("google-genai 当前只支持 base64 data-URI image_url")
            mime_type, data = _decode_data_uri(url)
            parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))
            continue
        parts.append(types.Part.from_text(text=json.dumps(item, ensure_ascii=False)))
    return parts


def _decode_data_uri(uri: str) -> tuple[str, bytes]:
    """解码 ``data:<mime>;base64,<payload>``。"""
    try:
        header, encoded = uri.split(",", 1)
        mime_type = header[5:].split(";", 1)[0]
        if ";base64" not in header or not mime_type:
            raise ValueError
        return mime_type, base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise DialectError("image_url 不是合法 base64 data-URI") from exc


def _tools_to_google(tools: list[dict[str, Any]] | None, types: Any) -> list[Any]:
    """OpenAI function tools → Google Tool(function_declarations)。"""
    declarations: list[Any] = []
    for item in tools or []:
        if item.get("type") != "function" or not isinstance(item.get("function"), dict):
            continue
        fn = item["function"]
        declarations.append(
            types.FunctionDeclaration(
                name=str(fn.get("name") or "tool"),
                description=str(fn.get("description") or ""),
                parameters_json_schema=fn.get("parameters") or {"type": "object", "properties": {}},
            )
        )
    return [types.Tool(function_declarations=declarations)] if declarations else []


def _tool_config_to_google(tool_choice: Any, google_tools: list[Any], types: Any) -> Any | None:
    """OpenAI tool_choice → Google FunctionCallingConfig。"""
    if not google_tools or tool_choice is None or tool_choice == "auto":
        return None
    mode = "AUTO"
    allowed: list[str] | None = None
    if tool_choice == "none":
        mode = "NONE"
    elif tool_choice == "required":
        mode = "ANY"
    elif isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        if fn.get("name"):
            mode, allowed = "ANY", [str(fn["name"])]
    return types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode=mode, allowed_function_names=allowed)
    )


def _google_response_to_completion(response: Any) -> dict[str, Any]:
    """GenerateContentResponse → OpenAI chat.completion 形态。"""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise DialectError("google-genai 响应缺 candidates(可能被安全策略拦截)")
    candidate = candidates[0]
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for index, part in enumerate(getattr(getattr(candidate, "content", None), "parts", None) or []):
        text = getattr(part, "text", None)
        if isinstance(text, str):
            (reasoning_parts if getattr(part, "thought", False) else content_parts).append(text)
        fn = getattr(part, "function_call", None)
        if fn is not None and getattr(fn, "name", None):
            call_id = str(getattr(fn, "id", None) or f"call_google_{index}")
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(fn.name),
                        "arguments": json.dumps(getattr(fn, "args", None) or {}, ensure_ascii=False),
                    },
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish = str(getattr(candidate, "finish_reason", None) or "stop").split(".")[-1].lower()
    result: dict[str, Any] = {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else finish}],
        "aborted": False,
    }
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
        completion = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        result["usage"] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(getattr(usage, "total_token_count", 0) or prompt + completion),
        }
    return result


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content) if content is not None else ""


def _json_object_or_text(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"result": value}
    return {"result": value}


def _empty_aborted_completion() -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "cancelled"}],
        "aborted": True,
    }


def _chat_openai_completions(
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None,
    api_key: str | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    timeout_s: int,
    cancel_event: threading.Event | None,
    default_headers: dict[str, str] | None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """openai-compatible 方言:POST {base_url}/chat/completions,Bearer 鉴权。

    用流式(SSE)以便 abort 时能返回已收到的部分内容;worker 线程消费流,
    主线程每 0.5s 轮询 cancel_event,置位即关闭连接、拼装部分结果返回。
    reasoning 模型(如 agentrouter 的 glm-5.2)的 reasoning_content 分片同样累积,
    content 允许为空,不视为失败(见 _assemble_completion)。
    """
    if not base_url:
        raise DialectError("openai-compatible provider 缺少 base_url")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **(extra_params or {}),
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    # default_headers 在前:Authorization/Content-Type 始终由方言按 api_key 注入,不被覆盖
    headers = {
        **(default_headers or {}),
        "Authorization": f"Bearer {api_key or ''}",
        "Content-Type": "application/json",
    }

    acc: dict[str, Any] = {
        "content_parts": [],
        "reasoning_parts": [],
        "tool_calls": {},
        "finish_reason": None,
        "usage": None,
    }
    holder: dict[str, Any] = {}  # 主线程 abort 时要关闭的 client/response
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
                holder["client"] = client
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    holder["response"] = resp
                    resp.raise_for_status()
                    # WAF 检测:agentrouter 速率限流时返回 200 + text/html 挑战页,
                    # 若不拦截会被 _consume_sse_line 静默吞掉(只认 "data:" 行)→ 空响应假成功。
                    ct = resp.headers.get("content-type", "").lower()
                    if "text/html" in ct:
                        raise WAFChallengeError(
                            f"响应为 HTML(content-type={ct!r}),疑似 Aliyun WAF 挑战页(速率限流);退避后重试"
                        )
                    for line in resp.iter_lines():
                        if not _consume_sse_line(line, acc):
                            break
        except BaseException as exc:  # abort 关闭连接时 worker 也会在此落地
            errors.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    aborted = False
    while thread.is_alive():
        thread.join(timeout=ABORT_POLL_INTERVAL_S)
        if cancel_event is not None and cancel_event.is_set():
            aborted = True
            resp = holder.get("response")
            if resp is not None:
                resp.close()
            elif holder.get("client") is not None:
                holder["client"].close()
            thread.join(timeout=5)
            break
    if errors and not aborted:
        raise errors[0]
    return _assemble_completion(acc, aborted=aborted)


def _consume_sse_line(line: str, acc: dict[str, Any]) -> bool:
    """解析一行 SSE 增量并入 acc;返回 False 表示流结束(``data: [DONE]``)。纯函数,可单测。"""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return True
    data_str = line[len("data:") :].strip()
    if data_str == "[DONE]":
        return False
    _apply_delta(acc, json.loads(data_str))
    return True


def _apply_delta(acc: dict[str, Any], data: dict[str, Any]) -> None:
    """把一个 stream chunk 的 delta 累积进 acc(content/reasoning 拼接 / tool_calls 分片合并 / usage)。"""
    # agentrouter 对 claude 系模型偶发 `data: null` 行(json.loads→None),非 dict 直接跳过不崩。
    if not isinstance(data, dict):
        return
    for choice in data.get("choices") or []:
        delta = choice.get("delta") or {}
        if delta.get("content"):
            acc["content_parts"].append(delta["content"])
        # reasoning 模型(如 glm-5.2):思维链在 reasoning_content(部分实现用 reasoning)分片下发
        reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning_piece:
            acc["reasoning_parts"].append(reasoning_piece)
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = acc["tool_calls"].setdefault(
                idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
        if choice.get("finish_reason"):
            acc["finish_reason"] = choice["finish_reason"]
    if data.get("usage"):
        acc["usage"] = data["usage"]


def _assemble_completion(acc: dict[str, Any], *, aborted: bool) -> dict[str, Any]:
    """把流式累积拼回非流式 chat.completion 形态;abort 时携带部分内容与 aborted=True。

    reasoning 模型兼容:content 允许为空(思维链计在 reasoning_tokens),不视为错误;
    收到的 reasoning_content 分片拼好后透出在 message.reasoning。
    """
    content = "".join(acc["content_parts"])
    reasoning = "".join(acc["reasoning_parts"])
    tool_calls = [acc["tool_calls"][i] for i in sorted(acc["tool_calls"])] or None
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    result: dict[str, Any] = {
        "choices": [
            {"message": message, "finish_reason": acc["finish_reason"] or ("cancelled" if aborted else "stop")}
        ],
        "aborted": aborted,
    }
    if acc["usage"]:
        result["usage"] = acc["usage"]
    return result


def _stream_sse_with_abort(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: int,
    cancel_event: threading.Event | None,
    consume_line: Any,
) -> bool:
    """SSE 流消费 + 全程可 abort(与 _chat_openai_completions 同语义,供 HTTP 方言复用)。

    worker 线程逐行交给 consume_line(返回 False 即收尾),主线程每 0.5s 轮询 cancel_event,
    置位即关闭连接。返回是否 aborted;非 abort 的异常原样上抛(HTTPStatusError/TransportError
    由 registry 判定重试,WAF HTML 挑战页统一转 WAFChallengeError)。
    """
    holder: dict[str, Any] = {}
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
                holder["client"] = client
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    holder["response"] = resp
                    resp.raise_for_status()
                    ct = resp.headers.get("content-type", "").lower()
                    if "text/html" in ct:
                        raise WAFChallengeError(
                            f"响应为 HTML(content-type={ct!r}),疑似 WAF 挑战页(速率限流);退避后重试"
                        )
                    for line in resp.iter_lines():
                        if not consume_line(line):
                            break
        except BaseException as exc:  # abort 关闭连接时 worker 也会在此落地
            errors.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    aborted = False
    while thread.is_alive():
        thread.join(timeout=ABORT_POLL_INTERVAL_S)
        if cancel_event is not None and cancel_event.is_set():
            aborted = True
            resp = holder.get("response")
            if resp is not None:
                resp.close()
            elif holder.get("client") is not None:
                holder["client"].close()
            thread.join(timeout=5)
            break
    if errors and not aborted:
        raise errors[0]
    return aborted


def _new_stream_acc() -> dict[str, Any]:
    return {
        "content_parts": [],
        "reasoning_parts": [],
        "tool_calls": {},
        "finish_reason": None,
        "usage": None,
    }


# ---------- anthropic(Anthropic Messages API) ----------

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_DEFAULT_MAX_TOKENS = 8192
_ANTHROPIC_STOP_MAP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "refusal": "content_filter",
}


def _chat_anthropic(
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None,
    api_key: str | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    timeout_s: int,
    cancel_event: threading.Event | None,
    default_headers: dict[str, str] | None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Anthropic Messages 方言:POST {base_url}/v1/messages,x-api-key + anthropic-version 鉴权。

    用流式(SSE)以支持 abort 返回部分内容。system 消息提升为顶层 system 参数;
    OpenAI function tools → Anthropic tools(name/description/input_schema);
    tool_calls/tool 角色消息 → tool_use/tool_result 内容块(连续同角色消息合并,
    满足 Anthropic 交替角色约束)。max_tokens 为 API 必填,缺省 8192,可由 extra_params 覆盖。
    """
    base = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/v1/messages"
    system, anthropic_messages = _messages_to_anthropic(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": _ANTHROPIC_DEFAULT_MAX_TOKENS,
        "stream": True,
        **(extra_params or {}),
    }
    if system:
        payload["system"] = system
    anthropic_tools = _tools_to_anthropic(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
        choice = _tool_choice_to_anthropic(tool_choice)
        if choice is not None:
            payload["tool_choice"] = choice
    # default_headers 在前:x-api-key/anthropic-version 始终由方言注入,不被覆盖
    headers = {
        **(default_headers or {}),
        "x-api-key": api_key or "",
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }
    acc = _new_stream_acc()
    aborted = _stream_sse_with_abort(
        url=url,
        payload=payload,
        headers=headers,
        timeout_s=timeout_s,
        cancel_event=cancel_event,
        consume_line=lambda line: _consume_anthropic_sse_line(line, acc),
    )
    return _assemble_completion(acc, aborted=aborted)


def _messages_to_anthropic(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI messages → (system, Anthropic messages);连续同角色合并,满足交替角色约束。"""
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    def _append(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if anthropic_messages and anthropic_messages[-1]["role"] == role:
            anthropic_messages[-1]["content"].extend(blocks)
        else:
            anthropic_messages.append({"role": role, "content": blocks})

    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            text = _content_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            _append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or ""),
                        "content": _content_text(content),
                    }
                ],
            )
            continue
        anthropic_role = "assistant" if role == "assistant" else "user"
        blocks = _content_to_anthropic_blocks(content)
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(tool_call.get("id") or ""),
                    "name": str(fn.get("name") or "tool"),
                    "input": _json_object_or_text(fn.get("arguments", {})),
                }
            )
        _append(anthropic_role, blocks or [{"type": "text", "text": ""}])
    if not anthropic_messages:
        anthropic_messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
    return ("\n\n".join(system_parts) or None), anthropic_messages


def _content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    """OpenAI 字符串/content-parts → Anthropic 内容块,支持 data-URI 与 http(s) 图片。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]
    blocks: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            blocks.append({"type": "text", "text": str(item)})
            continue
        if item.get("type") == "text":
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
            continue
        if item.get("type") == "image_url":
            image = item.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str) and url.startswith("data:"):
                mime_type, data = _decode_data_uri(url)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    }
                )
            elif isinstance(url, str) and url.startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                raise DialectError("anthropic 方言只支持 base64 data-URI 或 http(s) image_url")
            continue
        blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
    return blocks


def _tools_to_anthropic(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI function tools → Anthropic tools(name/description/input_schema)。"""
    anthropic_tools: list[dict[str, Any]] = []
    for item in tools or []:
        if item.get("type") != "function" or not isinstance(item.get("function"), dict):
            continue
        fn = item["function"]
        anthropic_tools.append(
            {
                "name": str(fn.get("name") or "tool"),
                "description": str(fn.get("description") or ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return anthropic_tools


def _tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    """OpenAI tool_choice → Anthropic tool_choice(auto/none/any/tool)。"""
    if tool_choice is None or tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        if fn.get("name"):
            return {"type": "tool", "name": str(fn["name"])}
    return None


def _consume_anthropic_sse_line(line: str, acc: dict[str, Any]) -> bool:
    """解析一行 Anthropic SSE 并入 acc;message_stop 时返回 False。纯函数,可单测。"""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return True
    data = json.loads(line[len("data:") :].strip())
    if not isinstance(data, dict):
        return True
    event_type = data.get("type")
    if event_type == "message_start":
        usage = (data.get("message") or {}).get("usage") or {}
        _accumulate_anthropic_usage(acc, usage)
    elif event_type == "content_block_start":
        block = data.get("content_block") or {}
        if block.get("type") == "tool_use":
            acc["tool_calls"][data.get("index", 0)] = {
                "id": str(block.get("id") or ""),
                "type": "function",
                "function": {"name": str(block.get("name") or ""), "arguments": ""},
            }
    elif event_type == "content_block_delta":
        delta = data.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta" and delta.get("text"):
            acc["content_parts"].append(delta["text"])
        elif delta_type == "thinking_delta" and delta.get("thinking"):
            acc["reasoning_parts"].append(delta["thinking"])
        elif delta_type == "input_json_delta" and delta.get("partial_json"):
            slot = acc["tool_calls"].get(data.get("index", 0))
            if slot is not None:
                slot["function"]["arguments"] += delta["partial_json"]
    elif event_type == "message_delta":
        stop_reason = (data.get("delta") or {}).get("stop_reason")
        if stop_reason:
            acc["finish_reason"] = _ANTHROPIC_STOP_MAP.get(str(stop_reason), "stop")
        _accumulate_anthropic_usage(acc, data.get("usage") or {})
    elif event_type == "message_stop":
        return False
    elif event_type == "error":
        # 只透传服务端错误类型/消息,不含请求头/鉴权信息(api_key 不外泄)
        error = data.get("error") or {}
        raise DialectError(f"anthropic SSE error: {error.get('type')}: {error.get('message')}")
    return True


def _accumulate_anthropic_usage(acc: dict[str, Any], usage: dict[str, Any]) -> None:
    """Anthropic usage(input_tokens/output_tokens)累积为 OpenAI usage 形态。"""
    if not usage:
        return
    current = acc["usage"] or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    current["prompt_tokens"] += int(usage.get("input_tokens") or 0)
    current["completion_tokens"] += int(usage.get("output_tokens") or 0)
    current["total_tokens"] = current["prompt_tokens"] + current["completion_tokens"]
    acc["usage"] = current


# ---------- openai-responses(OpenAI Responses API) ----------

OPENAI_RESPONSES_DEFAULT_BASE_URL = "https://api.openai.com"


def _chat_openai_responses(
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None,
    api_key: str | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    timeout_s: int,
    cancel_event: threading.Event | None,
    default_headers: dict[str, str] | None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """OpenAI Responses 方言:POST {base_url}/v1/responses,Bearer 鉴权,SSE 流式。

    system 消息提升为 instructions;assistant.tool_calls → function_call 条目,
    tool 角色 → function_call_output 条目;tools 展平为 Responses 形态
    (type/name/description/parameters 顶层)。响应归一化为 chat.completion。
    """
    base = (base_url or OPENAI_RESPONSES_DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/v1/responses"
    instructions, input_items = _messages_to_responses_input(messages)
    payload: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": True,
        **(extra_params or {}),
    }
    if instructions:
        payload["instructions"] = instructions
    responses_tools = _tools_to_responses(tools)
    if responses_tools:
        payload["tools"] = responses_tools
        choice = _tool_choice_to_responses(tool_choice)
        if choice is not None:
            payload["tool_choice"] = choice
    # default_headers 在前:Authorization/Content-Type 始终由方言按 api_key 注入,不被覆盖
    headers = {
        **(default_headers or {}),
        "Authorization": f"Bearer {api_key or ''}",
        "Content-Type": "application/json",
    }
    acc = _new_stream_acc()
    aborted = _stream_sse_with_abort(
        url=url,
        payload=payload,
        headers=headers,
        timeout_s=timeout_s,
        cancel_event=cancel_event,
        consume_line=lambda line: _consume_responses_sse_line(line, acc),
    )
    return _assemble_completion(acc, aborted=aborted)


def _messages_to_responses_input(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI messages → (instructions, Responses input 条目列表)。"""
    system_parts: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            text = _content_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": _content_text(content),
                }
            )
            continue
        if role == "assistant":
            text = _content_text(content)
            if text:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for tool_call in message.get("tool_calls") or []:
                fn = tool_call.get("function") or {}
                arguments = fn.get("arguments", "")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id") or ""),
                        "name": str(fn.get("name") or "tool"),
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False),
                    }
                )
            continue
        items.append(
            {
                "type": "message",
                "role": "user",
                "content": _content_to_responses_parts(content),
            }
        )
    if not items:
        items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": ""}]}]
    return ("\n\n".join(system_parts) or None), items


def _content_to_responses_parts(content: Any) -> list[dict[str, Any]]:
    """OpenAI 字符串/content-parts → Responses input_text/input_image 条目。"""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "input_text", "text": str(content)}]
    parts: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append({"type": "input_text", "text": str(item)})
            continue
        if item.get("type") == "text":
            parts.append({"type": "input_text", "text": str(item.get("text") or "")})
            continue
        if item.get("type") == "image_url":
            image = item.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url:
                raise DialectError("openai-responses 方言的 image_url 缺少 url")
            parts.append({"type": "input_image", "image_url": url})
            continue
        parts.append({"type": "input_text", "text": json.dumps(item, ensure_ascii=False)})
    return parts or [{"type": "input_text", "text": ""}]


def _tools_to_responses(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI function tools → Responses 展平形态(name/description/parameters 顶层)。"""
    responses_tools: list[dict[str, Any]] = []
    for item in tools or []:
        if item.get("type") != "function" or not isinstance(item.get("function"), dict):
            continue
        fn = item["function"]
        responses_tools.append(
            {
                "type": "function",
                "name": str(fn.get("name") or "tool"),
                "description": str(fn.get("description") or ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return responses_tools


def _tool_choice_to_responses(tool_choice: Any) -> Any | None:
    """OpenAI tool_choice → Responses tool_choice(字符串直通;指定函数转展平 dict)。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        if fn.get("name"):
            return {"type": "function", "name": str(fn["name"])}
    return None


def _consume_responses_sse_line(line: str, acc: dict[str, Any]) -> bool:
    """解析一行 Responses SSE 并入 acc;response.completed 时返回 False。纯函数,可单测。"""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return True
    data = json.loads(line[len("data:") :].strip())
    if not isinstance(data, dict):
        return True
    event_type = data.get("type")
    if event_type == "response.output_text.delta" and data.get("delta"):
        acc["content_parts"].append(data["delta"])
    elif event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
        if data.get("delta"):
            acc["reasoning_parts"].append(data["delta"])
    elif event_type == "response.output_item.added":
        item = data.get("item") or {}
        if item.get("type") == "function_call":
            key = str(item.get("id") or data.get("output_index") or len(acc["tool_calls"]))
            acc["tool_calls"][key] = {
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {"name": str(item.get("name") or ""), "arguments": ""},
            }
    elif event_type == "response.function_call_arguments.delta" and data.get("delta"):
        slot = acc["tool_calls"].get(str(data.get("item_id") or ""))
        if slot is not None:
            slot["function"]["arguments"] += data["delta"]
    elif event_type == "response.function_call_arguments.done":
        item = data.get("item_id")
        slot = acc["tool_calls"].get(str(item or ""))
        if slot is not None and not slot["function"]["arguments"] and data.get("arguments"):
            slot["function"]["arguments"] = data["arguments"]
    elif event_type in ("response.completed", "response.incomplete"):
        response = data.get("response") or {}
        usage = response.get("usage") or {}
        if usage:
            prompt = int(usage.get("input_tokens") or 0)
            completion = int(usage.get("output_tokens") or 0)
            acc["usage"] = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": int(usage.get("total_tokens") or prompt + completion),
            }
        if acc["finish_reason"] is None:
            if acc["tool_calls"]:
                acc["finish_reason"] = "tool_calls"
            elif event_type == "response.incomplete":
                acc["finish_reason"] = "length"
        return False
    elif event_type in ("response.failed", "error"):
        # 只透传服务端错误 code/message,不含请求头/鉴权信息(api_key 不外泄)
        error = data.get("response", {}).get("error") or data.get("error") or {}
        raise DialectError(f"openai-responses SSE error: {error.get('code')}: {error.get('message')}")
    return True


class CircuitBreaker:
    """熔断器:连续失败达阈值进入冷却,冷却期直接短路走降级链(registry.fallback_chain)。

    状态机:closed → (失败 ≥ failures) open → (冷却 cooldown_s 后) half-open(放行试一次,
    成功回 closed,失败重新 open)。参数来自 models.toml [resilience.circuit_breaker]。
    """

    def __init__(self, failures: int = 5, cooldown_s: int = 300, clock: Any = None) -> None:
        self.failures = failures
        self.cooldown_s = cooldown_s
        self._clock = clock or time.monotonic
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def allow(self, model: str) -> bool:
        """该模型当前是否可调用;open 且冷却未满 → False(走降级链),冷却满 → half-open 放行。"""
        opened_at = self._opened_at.get(model)
        if opened_at is None:
            return True
        return (self._clock() - opened_at) >= self.cooldown_s

    def record(self, model: str, ok: bool) -> None:
        """记录一次调用结果;成功复位 closed,失败累计,达阈值(或 half-open 再败)进入 open。"""
        if ok:
            self._fails.pop(model, None)
            self._opened_at.pop(model, None)
            return
        self._fails[model] = self._fails.get(model, 0) + 1
        if model in self._opened_at or self._fails[model] >= self.failures:
            self._opened_at[model] = self._clock()
