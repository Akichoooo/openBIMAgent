"""Session 事件记录 schema(schema 定稿自 07 报告 §2/§3)。

对应文档:
- docs/research/07_gemini_trace_observability.md §2 Session JSONL 事件 Schema(对齐 OTel GenAI)
- docs/architecture/COMPONENTS.md §2.6 session
- schemas/session_event.schema.json(同一 schema 的 JSON Schema 形态,供门禁校验)

每条记录 `{id, parentId, timestamp, type, payload}`:
- type=message → payload.role、gen_ai.request.model、gen_ai.usage.prompt_tokens 等 OTel 字段;
- type=tool_call → payload.toolCallId、payload.toolName、参数摘要;
- type=custom → customType ∈ screenshot / score / patch / snapshot,子型字段平铺进 payload。
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    CUSTOM = "custom"


class CustomType(StrEnum):
    SCREENSHOT = "screenshot"
    SCORE = "score"
    PATCH = "patch"
    SNAPSHOT = "snapshot"


class MessagePayload(BaseModel):
    """type=message 的 payload;OTel `gen_ai.*` 指标以点号键平铺(extra 容纳)。"""

    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: str = ""


class ToolCallPayload(BaseModel):
    """type=tool_call 的 payload(对应 OTel span;参数只落摘要,防爆 context)。

    phase=call 为调用记录;phase=result 为结果记录,携带双视图
    (result_llm_view 给模型,result_ui_view 给 UI;ARCH §0 原则 5)。
    """

    toolCallId: str
    toolName: str
    args_summary: str = ""
    phase: Literal["call", "result"] = "call"
    result_llm_view: str | None = None
    result_ui_view: dict[str, Any] | None = None
    status: Literal["ok", "error", "denied", "rejected"] | None = None


class CustomPayload(BaseModel):
    """type=custom 的 payload 基座;customType 决定子型,子型字段平铺(extra 容纳)。"""

    model_config = ConfigDict(extra="allow")

    customType: CustomType


class ScreenshotPayload(CustomPayload):
    """customType=screenshot:phase=scad|blender;截图降采样进上下文,原图全尺寸只落盘(COMPONENTS §5)。"""

    customType: Literal[CustomType.SCREENSHOT] = CustomType.SCREENSHOT
    camera_view: str
    image_path: str
    phase: Literal["scad", "blender"]


class ScorePayload(CustomPayload):
    """customType=score:critic 评分落盘;强制 CoT 与防放水留痕(07 报告 §3、ARCH §3)。"""

    customType: Literal[CustomType.SCORE] = CustomType.SCORE
    rubric_scores: dict[str, float]
    reasoning: str  # CoT:必须先推理后打分
    anchor_ref: str  # 评分使用的金标准锚点图/锚点词引用
    actionable_feedback: str  # 可执行返工指令;<8 分强制 actionable_rework_command,禁止空泛建议
    critic_model: str


class PatchPayload(CustomPayload):
    """customType=patch:SCAD 环 JSON patch 等返工修改记录(校验 old_value 后应用)。"""

    customType: Literal[CustomType.PATCH] = CustomType.PATCH
    target_file: str
    diff: str


class SnapshotPayload(CustomPayload):
    """customType=snapshot:每次 MCP 写操作前自动落盘的快照记录(回滚点)。"""

    customType: Literal[CustomType.SNAPSHOT] = CustomType.SNAPSHOT
    blend_file_path: str
    hash: str


class SessionEvent(BaseModel):
    """Session JSONL 树的一条记录;parentId 指父节点,构成单文件原地分支树。"""

    id: str
    parentId: str | None
    timestamp: datetime
    type: EventType
    payload: (
        MessagePayload
        | ToolCallPayload
        | ScreenshotPayload
        | ScorePayload
        | PatchPayload
        | SnapshotPayload
        | CustomPayload
    ) = Field(discriminator=None)


def uuid7() -> uuid.UUID:
    """RFC 9562 uuid7:48 位毫秒时间戳 + 版本/变体位 + 74 位随机数,保证 id 大体有序。"""

    ts_ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    raw = bytearray(16)
    raw[0:6] = ts_ms.to_bytes(6, "big")
    raw[6:8] = (0x7000 | (rand & 0x0FFF)).to_bytes(2, "big")
    raw[8:16] = (0x8000000000000000 | (rand >> 12)).to_bytes(8, "big")
    return uuid.UUID(bytes=bytes(raw))


_CLOCK_LOCK = threading.Lock()
_LAST_TS: datetime | None = None


def _monotonic_now(clock: Any) -> datetime:
    """取当前时间并保证全进程单调递增(同毫秒/时钟回拨时 +1ms)。"""
    global _LAST_TS
    ts = clock()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    with _CLOCK_LOCK:
        if _LAST_TS is not None and ts <= _LAST_TS:
            ts = _LAST_TS + timedelta(milliseconds=1)
        _LAST_TS = ts
    return ts


def _coerce_payload(
    type: EventType,
    payload: MessagePayload | ToolCallPayload | CustomPayload | dict[str, Any],
) -> MessagePayload | ToolCallPayload | CustomPayload:
    """dict payload 按 type 落成具体模型;custom 再按 customType 落到子型。"""
    if isinstance(payload, BaseModel):
        return payload
    if type is EventType.MESSAGE:
        return MessagePayload(**payload)
    if type is EventType.TOOL_CALL:
        return ToolCallPayload(**payload)
    custom_cls: dict[CustomType, type[CustomPayload]] = {
        CustomType.SCREENSHOT: ScreenshotPayload,
        CustomType.SCORE: ScorePayload,
        CustomType.PATCH: PatchPayload,
        CustomType.SNAPSHOT: SnapshotPayload,
    }
    custom_type = CustomType(payload.get("customType"))
    return custom_cls.get(custom_type, CustomPayload)(**payload)


def new_event(
    type: EventType,
    payload: MessagePayload | ToolCallPayload | CustomPayload | dict[str, Any],
    parent_id: str | None = None,
    *,
    event_id: str | None = None,
    clock: Any = None,
) -> SessionEvent:
    """构造一条新事件:uuid7 id(有序)+ 单调递增 timestamp(UTC),挂 parentId。

    clock 可注入(无参可调用,返回 datetime),便于回放测试固定时间。
    """
    ts = _monotonic_now(clock or (lambda: datetime.now(timezone.utc)))
    return SessionEvent(
        id=event_id or str(uuid7()),
        parentId=parent_id,
        timestamp=ts,
        type=type,
        payload=_coerce_payload(type, payload),
    )
