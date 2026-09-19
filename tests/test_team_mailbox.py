"""D8 测试:durable mailbox(post→落盘→deliver→queued-minus-delivered→重放恢复)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbimagent.orchestrator.team_mailbox import TeamMailbox


def test_post_then_inbox_then_deliver(tmp_path: Path) -> None:
    box = TeamMailbox(tmp_path, "team-a")
    message = box.post(sender="lead", target="modeler", body="先建主管")
    assert [m.message_id for m in box.inbox("modeler")] == [message.message_id]
    assert box.inbox("other") == []
    delivered = box.deliver(message.message_id)
    assert delivered.delivered_at is not None
    assert box.inbox("modeler") == [], "送达后不再出现在 queued-minus-delivered 邮箱"
    assert len(box.history()) == 1


def test_delivery_is_idempotent(tmp_path: Path) -> None:
    box = TeamMailbox(tmp_path, "team-b")
    message = box.post(sender="lead", target="critic", body="复核")
    first = box.deliver(message.message_id)
    second = box.deliver(message.message_id)
    assert first.delivered_at == second.delivered_at
    lines = (tmp_path / "team-b.mailbox.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 一条 post + 一条 delivery,重复 deliver 不加行


def test_replay_after_restart_recovers_state(tmp_path: Path) -> None:
    box = TeamMailbox(tmp_path, "team-c")
    a = box.post(sender="lead", target="modeler", body="A")
    b = box.post(sender="lead", target="modeler", body="B")
    box.deliver(a.message_id)
    reopened = TeamMailbox(tmp_path, "team-c")
    pending = [m.message_id for m in reopened.inbox("modeler")]
    assert pending == [b.message_id], "重启后恢复:已送达的 A 不再排队,B 仍在"
    assert len(reopened.history()) == 2


def test_validation_fail_closed(tmp_path: Path) -> None:
    box = TeamMailbox(tmp_path, "team-d")
    with pytest.raises(ValueError):
        box.post(sender="", target="x", body="y")
    with pytest.raises(ValueError):
        box.post(sender="x", target="y", body="   ")
    with pytest.raises(KeyError):
        box.deliver("no-such-message")
    with pytest.raises(ValueError):
        TeamMailbox(tmp_path, "")
