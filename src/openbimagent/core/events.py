"""SSE 事件类型(M0 定 schema,M2 实现 server)。

对应文档:
- docs/architecture/ARCHITECTURE.md §6 SSE 事件协议(草案全文见 08 报告)
- docs/architecture/COMPONENTS.md §1 server 组件(M2)

一切自定义事件包进 AI SDK v6 `data-*` part。
双视图(ARCH §0 原则 5):`tool-result` 给 LLM 纯文本视图;同 tick 的 `data` 流给 UI 渲染素材视图;
两个视图各自演进,渲染层永不打补丁。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class SSEEventType(StrEnum):
    """自定义 SSE 事件枚举,值即 AI SDK v6 `data-*` part 的 type。"""

    PROGRESS = "data-progress"  # 进度事件(批次/阶段推进)
    VISION_SCORECARD = "data-vision-scorecard"  # 双环评分卡(对应 session score 事件)
    CLARIFY_FORM = "data-clarify-form"  # Clarify 追问表单(槽位 + 默认值)


class DataPart(TypedDict):
    """AI SDK v6 `data-*` part 统一信封;各事件的 data 字段草案以 08 报告为准。

    已对接 server SSE 流(server/contracts.py 消费 SSEEventType)。"""

    type: SSEEventType
    data: dict[str, Any]


def to_data_part(type: SSEEventType, data: dict[str, Any]) -> DataPart:
    """把自定义事件包进 AI SDK v6 `data-*` part 信封(data 必须是 dict)。"""
    if not isinstance(data, dict):
        raise TypeError(f"data 必须是 dict,收到 {type(data).__name__}")
    return {"type": type, "data": data}
