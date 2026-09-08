"""自愈轨迹导出器:把成功收敛的会话轨迹导出为 SFT/DPO 标准微调格式。

数据源 = out/sessions/*.jsonl(事件溯源,append-only 不可变);
仅导出有交付回执的成功案例(fail-closed:无 delivery_receipt/artifact_committed 不导出);
DPO 偏好对仅在有自愈迭代证据(score 事件)时构造,不伪造 rejected。
机制 = 轨迹采集与打标(供后续离线训练),非运行时权重更新。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DELIVERY_TYPES = frozenset({"delivery_receipt", "artifact_committed"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_events(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for ln in raw:
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _custom_type(ev: dict[str, Any]) -> str:
    p = ev.get("payload") or {}
    return str(p.get("customType") or p.get("custom_type") or "")


def _trajectory_text(events: list[dict[str, Any]]) -> str:
    """事件流 → 训练用轨迹文本(工具序列 + 关键结果 + 交付标记)。"""
    lines: list[str] = []
    for ev in events:
        t = ev.get("type")
        p = ev.get("payload") or {}
        if t == "message":
            role, content = p.get("role"), str(p.get("content") or "").strip()
            if role in ("user", "assistant", "steer") and content:
                lines.append(f"[{role}] {content}")
        elif t == "tool_call":
            tool = p.get("tool") or p.get("name") or "?"
            status = p.get("status") or ("error" if p.get("error") else "ok")
            lines.append(f"[tool] {tool} -> {status}")
        elif t == "custom":
            ct = _custom_type(ev)
            if ct == "score":
                data = json.dumps(p.get("data") or p, ensure_ascii=False)[:200]
                lines.append(f"[score] {data}")
            elif ct == "steer_requested":
                lines.append(f"[steer] {str(p.get('instruction') or '')[:200]}")
            elif ct in _DELIVERY_TYPES:
                lines.append(f"[delivery] {ct}")
    return "\n".join(lines)


def export_traces(fmt: str = "sft", sessions_dir: Path | None = None) -> dict[str, Any]:
    """导出微调轨迹。fmt=sft(指令对) / dpo(偏好对:自愈前 rejected vs 收敛全程 chosen)。"""
    sessions_dir = sessions_dir or Path.cwd() / "out" / "sessions"
    items: list[dict[str, Any]] = []
    if not sessions_dir.is_dir():
        return {"format": fmt, "count": 0, "items": items}
    for f in sorted(sessions_dir.glob("*.jsonl")):
        events = _load_events(f)
        if not events:
            continue
        if not any(ev.get("type") == "custom" and _custom_type(ev) in _DELIVERY_TYPES for ev in events):
            continue  # fail-closed:无交付证据不导出
        brief = ""
        for ev in events:
            p = ev.get("payload") or {}
            if ev.get("type") == "message" and p.get("role") == "user" and str(p.get("content") or "").strip():
                brief = str(p["content"]).strip()
                break
        traj = _trajectory_text(events)
        if fmt == "dpo":
            score_idx = next(
                (i for i, ev in enumerate(events) if ev.get("type") == "custom" and _custom_type(ev) == "score"),
                None,
            )
            if score_idx is None or score_idx < 1:
                continue  # 无自愈迭代证据,不构造偏好对(不伪造 rejected)
            rejected = _trajectory_text(events[:score_idx])
            if not rejected.strip():
                continue
            items.append(
                {
                    "prompt": brief,
                    "chosen": traj,
                    "rejected": rejected,
                    "meta": {"session_id": f.stem, "source": "self_heal_trace", "exported_at": _now()},
                }
            )
        else:
            items.append(
                {
                    "messages": [
                        {"role": "user", "content": brief},
                        {"role": "assistant", "content": traj},
                    ],
                    "meta": {"session_id": f.stem, "source": "delivered_trace", "exported_at": _now()},
                }
            )
    return {"format": fmt, "count": len(items), "items": items}