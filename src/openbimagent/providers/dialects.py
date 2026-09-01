"""API 方言层:4 种方言统一 chat 接口;重试/熔断集中在 providers 层。

对应文档:
- docs/architecture/COMPONENTS.md §4 providers([resilience]:韧性集中,业务代码不重复造)
- config/models.toml [providers.*] 的 type 字段

方言:openai-completions / google-genai 已实现;openai-responses / anthropic 保留为扩展点(未实现)。
重试/熔断集中在 providers 层,业务代码不重复造。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from enum import StrEnum
from typing import Any

import httpx

ABORT_POLL_INTERVAL_S = 0.5
"""cancel_event 轮询间隔(ARCH §6.5:全程可 abort 且返回部分结果)。"""


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
    **kwargs: Any,
) -> dict[str, Any]:
    """统一调用入口:按方言适配请求/响应;重试/熔断由 registry 套用(models.toml [resilience])。

    返回 OpenAI chat.completion 形态 dict;abort 时正常返回并带 ``aborted=True`` 与部分内容。
    default_headers:provider 级额外请求头(models.toml [providers.*].default_headers),
    与方言注入的 Authorization/Content-Type 合并,后者优先(鉴权不被覆盖)。
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
    raise NotImplementedError(f"{dialect} 方言未实现(保留扩展点)")


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
