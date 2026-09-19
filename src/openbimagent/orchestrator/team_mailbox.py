"""D8 Agent Teams 最小实现:durable mailbox(dsh Agent Teams 语义)。

- 消息先完整落盘(durable)才算存在;送达标记也落盘——"queued minus delivered" 即恢复邮箱;
- append-only JSONL,重启后按事件重放恢复(不依赖内存态);
- 去重键 message_id:重复 deliver 幂等,不产生第二条送达记录。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openbimagent.session.schema import uuid7

_TYPE_POST = "team_message"
_TYPE_DELIVERY = "team_delivery"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TeamMessage:
    message_id: str
    team_id: str
    sender: str
    target: str
    body: str
    created_at: str
    delivered_at: str | None = None


class TeamMailbox:
    """单团队 append-only 邮箱;跨进程一致性由调用方文件锁/单写者保证(与 SessionStore 同纪律)。"""

    def __init__(self, root: Path | str, team_id: str) -> None:
        if not team_id:
            raise ValueError("team_id 不能为空")
        self.root = Path(root)
        self.team_id = team_id
        self.path = self.root / f"{team_id}.mailbox.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._messages: dict[str, TeamMessage] = {}
        self._deliveries: dict[str, str] = {}  # message_id -> delivered_at
        self._replay()

    def _replay(self) -> None:
        if not self.path.is_file():
            return
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == _TYPE_POST:
                    message = TeamMessage(
                        message_id=event["message_id"],
                        team_id=event["team_id"],
                        sender=event["sender"],
                        target=event["target"],
                        body=event["body"],
                        created_at=event["created_at"],
                    )
                    self._messages[message.message_id] = message
                elif event.get("type") == _TYPE_DELIVERY:
                    self._deliveries[event["message_id"]] = event["delivered_at"]

    def _append(self, event: dict) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()

    def post(self, *, sender: str, target: str, body: str) -> TeamMessage:
        """投递一条消息(先落盘再可见;发送方不得为空)。"""
        if not sender or not target:
            raise ValueError("sender/target 不能为空")
        if not body.strip():
            raise ValueError("消息正文不能为空")
        message = TeamMessage(
            message_id=str(uuid7()),
            team_id=self.team_id,
            sender=sender,
            target=target,
            body=body,
            created_at=_now(),
        )
        self._append(
            {
                "type": _TYPE_POST,
                "message_id": message.message_id,
                "team_id": message.team_id,
                "sender": message.sender,
                "target": message.target,
                "body": message.body,
                "created_at": message.created_at,
            }
        )
        with self._lock:
            self._messages[message.message_id] = message
        return message

    def deliver(self, message_id: str) -> TeamMessage:
        """标记送达(幂等):durable 之后才算送达;重复调用不产生第二条记录。"""
        with self._lock:
            message = self._messages.get(message_id)
            if message is None:
                raise KeyError(f"消息 {message_id!r} 不在团队 {self.team_id!r} 邮箱中")
            if message_id in self._deliveries:
                return self._with_delivery(message)
            delivered_at = _now()
        self._append({"type": _TYPE_DELIVERY, "message_id": message_id, "delivered_at": delivered_at})
        with self._lock:
            self._deliveries[message_id] = delivered_at
            return self._with_delivery(self._messages[message_id])

    def _with_delivery(self, message: TeamMessage) -> TeamMessage:
        return TeamMessage(
            message_id=message.message_id,
            team_id=message.team_id,
            sender=message.sender,
            target=message.target,
            body=message.body,
            created_at=message.created_at,
            delivered_at=self._deliveries.get(message.message_id),
        )

    def inbox(self, target: str) -> list[TeamMessage]:
        """queued-minus-delivered:该 target 尚未送达的消息(重启后可恢复的邮箱视图)。"""
        with self._lock:
            return [
                self._with_delivery(m)
                for m in self._messages.values()
                if m.target == target and m.message_id not in self._deliveries
            ]

    def history(self) -> list[TeamMessage]:
        """全量消息(含已送达),按投递顺序。"""
        with self._lock:
            return [self._with_delivery(m) for m in self._messages.values()]


__all__ = ["TeamMailbox", "TeamMessage"]
