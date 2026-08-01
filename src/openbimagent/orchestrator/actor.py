"""P1d 稳定 actor identity 契约。

控制协议的新写入使用 ActorRef；历史字符串 actor 仍可读取，避免破坏 P1b/P1c 已落盘事实。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

ACTOR_PROTOCOL_VERSION = "1.0"


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"
    RUNTIME = "runtime"
    LEGACY = "legacy"


class ActorRef(BaseModel):
    """不依赖显示名称的稳定控制面身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(default=ACTOR_PROTOCOL_VERSION, pattern=r"^1(?:\.\d+)?$")
    actor_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@/-]+$")
    actor_type: ActorType
    display_name: str | None = Field(default=None, min_length=1, max_length=128)

    @classmethod
    def legacy(cls, value: str) -> "ActorRef":
        normalized = value.strip()
        if not normalized:
            raise ValueError("actor identity 不能为空")
        safe = "".join(character if character.isalnum() or character in "_.:@/-" else "_" for character in normalized)
        return cls(actor_id=f"legacy:{safe}"[:128], actor_type=ActorType.LEGACY, display_name=normalized[:128])


ActorLike = ActorRef | str


def actor_ref(value: ActorLike, *, default_type: ActorType = ActorType.AGENT) -> ActorRef:
    """把 API 边界的历史字符串规范化为新写入 ActorRef。"""
    if isinstance(value, ActorRef):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError("actor identity 不能为空")
    if ":" in normalized:
        return ActorRef(actor_id=normalized, actor_type=default_type)
    return ActorRef(actor_id=f"{default_type.value}:{normalized}", actor_type=default_type, display_name=normalized)


__all__ = [
    "ACTOR_PROTOCOL_VERSION",
    "ActorLike",
    "ActorRef",
    "ActorType",
    "actor_ref",
]
