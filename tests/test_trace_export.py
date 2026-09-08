"""trace_export 单测:SFT/DPO 导出(fail-closed:无交付不导出;无自愈证据不伪造偏好对)。"""

import json
from pathlib import Path

from openbimagent.training.trace_export import export_traces


def _write_session(d: Path, sid: str, events: list[dict]) -> None:
    (d / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8"
    )


def test_sft_export_only_delivered(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "s1",
        [
            {"type": "message", "payload": {"role": "user", "content": "布一条 DN300 污水管"}},
            {"type": "tool_call", "payload": {"tool": "grid_route_solver", "status": "ok"}},
            {"type": "custom", "payload": {"customType": "delivery_receipt", "data": {}}},
        ],
    )
    _write_session(
        tmp_path, "s2", [{"type": "message", "payload": {"role": "user", "content": "失败任务"}}]
    )
    result = export_traces("sft", sessions_dir=tmp_path)
    assert result["count"] == 1
    item = result["items"][0]
    assert item["messages"][0]["content"] == "布一条 DN300 污水管"
    assert "[tool] grid_route_solver -> ok" in item["messages"][1]["content"]
    assert "[delivery] delivery_receipt" in item["messages"][1]["content"]
    assert item["meta"]["session_id"] == "s1"


def test_dpo_requires_heal_evidence(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "s3",
        [
            {"type": "message", "payload": {"role": "user", "content": "避让建筑 A"}},
            {"type": "message", "payload": {"role": "assistant", "content": "初始路径穿碰撞区"}},
            {"type": "tool_call", "payload": {"tool": "collision_check", "status": "conflict"}},
            {"type": "custom", "payload": {"customType": "score", "data": {"iter": 0, "score": 4.2}}},
            {"type": "tool_call", "payload": {"tool": "grid_route_solver", "status": "ok"}},
            {"type": "custom", "payload": {"customType": "score", "data": {"iter": 2, "score": 8.1}}},
            {"type": "custom", "payload": {"customType": "delivery_receipt", "data": {}}},
        ],
    )
    _write_session(
        tmp_path,
        "s4",
        [
            {"type": "message", "payload": {"role": "user", "content": "简单任务"}},
            {"type": "custom", "payload": {"customType": "delivery_receipt", "data": {}}},
        ],
    )
    result = export_traces("dpo", sessions_dir=tmp_path)
    assert result["count"] == 1  # s4 无自愈证据不构造偏好对
    item = result["items"][0]
    assert item["prompt"] == "避让建筑 A"
    assert "collision_check" in item["rejected"]
    assert "[delivery] delivery_receipt" in item["chosen"]


def test_empty_dir(tmp_path: Path) -> None:
    assert export_traces("sft", sessions_dir=tmp_path) == {"format": "sft", "count": 0, "items": []}