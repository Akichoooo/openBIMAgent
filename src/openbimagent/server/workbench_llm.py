"""工作台 LLM registry 适配器：把设置页模型配置投影成 pipeline 需要的 registry.chat 接口。

Web「新建任务」此前不传 registry,planner 只能走确定性占位模板(runs.py 离线安全路径)。
本模块把 chat 端点同源的配置(llm_baseline.local.toml → 自定义供应商回退)包成鸭子类型
registry——planner/clarify/builder 只消费 chat(role, messages);缺 key 返回 None,
调用方退回模板路径并如实发事件(不假成功,与 chat.py fail-closed 同口径)。

方言固定 openai-completions(与 chat.py 一致);custom providers 的 api_format 差异
(anthropic/openai-responses)当前不支持,留待统一供应商方言层后接入。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from openbimagent.providers.dialects import Dialect
from openbimagent.providers.dialects import chat as dialect_chat
from openbimagent.providers.dialects import reasoning_payload
from openbimagent.providers.registry import _REASONING_DEFAULTS

#: planner 单次生成整图 Scene Graph IR,比对话轮次长;对齐 registry 韧性超时量级
_TIMEOUT_S = 180
#: 有界重试(429/5xx/瞬时网络);4xx 直接上抛由调用方降级
_RETRY_MAX = 2
_RETRY_BASE_MS = 800


def _is_retryable(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class WorkbenchChatRegistry:
    """鸭子类型 ModelRegistry:仅实现 chat();每次调用重读设置(小文件,免缓存陈旧)。"""

    def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: threading.Event | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from openbimagent.server.chat import _resolve_llm

        resolved = _resolve_llm()
        if resolved is None:
            raise RuntimeError("工作台未配置可用模型(基线缺 key 且无已启用供应商);planner 降级模板")
        model, base_url, api_key = resolved
        extra_params = reasoning_payload(model, _REASONING_DEFAULTS.get(role)) or None
        last_exc: Exception | None = None
        for attempt in range(_RETRY_MAX):
            try:
                result = dialect_chat(
                    Dialect.OPENAI_COMPLETIONS,
                    model=model,
                    messages=messages,
                    base_url=base_url,
                    api_key=api_key,
                    tools=tools,
                    tool_choice=tool_choice,
                    timeout_s=_TIMEOUT_S,
                    cancel_event=cancel_event,
                    extra_params=extra_params,
                )
            except Exception as exc:
                last_exc = exc
                if cancel_event is not None and cancel_event.is_set():
                    raise
                if not _is_retryable(exc) or attempt == _RETRY_MAX - 1:
                    raise
                time.sleep(_RETRY_BASE_MS / 1000 * (2**attempt))
            else:
                result["model_resolved"] = model
                return result
        raise last_exc  # pragma: no cover - 循环内必然 raise 或 return


def workbench_registry() -> WorkbenchChatRegistry | None:
    """设置页可用(基线带 key 或存在已启用供应商)才返回 registry;否则 None → 模板降级。"""
    from openbimagent.server.chat import _resolve_llm

    try:
        return WorkbenchChatRegistry() if _resolve_llm() is not None else None
    except Exception:  # noqa: BLE001 — 配置读取失败视同未配置,降级模板
        return None
