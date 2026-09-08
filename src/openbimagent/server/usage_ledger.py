"""LLM 用量流水账：append-only ``out/usage_log.jsonl`` + 聚合读取（用量仪表盘数据源）。

背景（对照 ZCode/Codex 的 usage 面板）：
- 原先 ``/api/v1/usage`` 只读 CLI pipeline 落的 ``out/usage_summary.json`` 快照——
  服务器进程里的对话调用（POST /api/v1/chat）真实消耗 token 却从不入账，
  面板因此长期显示"暂无用量记录"，数据与真实消耗脱节。
- 本模块建立统一流水账：任何**真实 LLM 调用**（chat 端点 / CLI pipeline）各写一行
  JSONL（append-only、进程安全、单行损坏容忍），聚合端点按需汇总——
  总量、按日趋势（近 14 天）、按模型分布、最近调用明细。
- 记录永不包含消息内容与 API key，只有模型名/token 数/时间戳/来源。

环境变量：
- ``OPENBIMAGENT_USAGE_LOG``：覆盖流水账路径（测试隔离用）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LOG = _REPO_ROOT / "out" / "usage_log.jsonl"

_WRITE_LOCK = threading.Lock()

#: 聚合端点保留的最近调用条数（明细区可见窗口）
_RECENT_LIMIT = 30
#: 按日趋势窗口（天）
_DAILY_WINDOW = 14


def _log_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_USAGE_LOG")
    return Path(override) if override else _DEFAULT_LOG


def record_call(
    *,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    source: str = "chat",
    session_id: str | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any] | None:
    """追加一条真实调用记录（供 chat 端点 / pipeline 包装器调用）。

    token 缺报（部分方言不回 usage）按 0 落账并标 ``usage_reported: false``；
    任何落盘失败不影响调用方主流程（返回 None）。
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": str(model or "unknown"),
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "source": str(source or "unknown"),
        "session_id": session_id or None,
        "latency_ms": latency_ms,
        "usage_reported": bool(prompt_tokens or completion_tokens or total_tokens),
    }
    try:
        with _WRITE_LOCK:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception:
        return None


def _iter_entries() -> list[dict[str, Any]]:
    """全量读取流水账（单行损坏/空文件容忍，坏行跳过）。"""
    path = _log_path()
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue  # 坏行（并发截断等）跳过，账本其余部分照常聚合
                if isinstance(item, dict):
                    entries.append(item)
    except Exception:
        return []
    return entries


def _day_key(ts: str) -> str | None:
    """ISO 时间戳 → ``YYYY-MM-DD``；无法解析返回 None（该条不计入按日，仍计总量）。"""
    try:
        return datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def aggregate() -> dict[str, Any]:
    """流水账 → 仪表盘聚合（total / daily / by_model / recent / calls）。"""
    entries = _iter_entries()
    total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    by_model: dict[str, dict[str, int]] = {}
    by_source: dict[str, int] = {}
    daily_counts: dict[str, dict[str, int]] = {}

    today = datetime.now(timezone.utc).date()
    for i in range(_DAILY_WINDOW):  # 近 14 天固定窗口（无数据的天也占位为 0，柱状图不断轴）
        day = (today - timedelta(days=i)).isoformat()
        daily_counts[day] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    for e in entries:
        pt = int(e.get("prompt_tokens", 0) or 0)
        ct = int(e.get("completion_tokens", 0) or 0)
        tt = int(e.get("total_tokens", 0) or 0) or pt + ct  # 方言缺 total 时按 in+out 兜底
        model = str(e.get("model", "unknown"))
        source = str(e.get("source", "unknown"))

        total["calls"] += 1
        total["prompt_tokens"] += pt
        total["completion_tokens"] += ct
        total["total_tokens"] += tt

        slot = by_model.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        slot["calls"] += 1
        slot["prompt_tokens"] += pt
        slot["completion_tokens"] += ct
        slot["total_tokens"] += tt
        by_source[source] = by_source.get(source, 0) + 1

        day = _day_key(str(e.get("ts", "")))
        if day in daily_counts:
            daily_counts[day]["prompt_tokens"] += pt
            daily_counts[day]["completion_tokens"] += ct
            daily_counts[day]["total_tokens"] += tt
            daily_counts[day]["calls"] += 1

    daily = [
        {"date": day, **counts}
        for day, counts in sorted(daily_counts.items())
    ]
    recent = [
        {
            "ts": e.get("ts", ""),
            "model": str(e.get("model", "unknown")),
            "source": str(e.get("source", "unknown")),
            "session_id": e.get("session_id"),
            "prompt_tokens": int(e.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(e.get("completion_tokens", 0) or 0),
            "total_tokens": int(e.get("total_tokens", 0) or 0),
            "latency_ms": e.get("latency_ms"),
        }
        for e in entries[-_RECENT_LIMIT:][::-1]
    ]

    return {
        "total": total,
        "daily": daily,
        "by_model": dict(sorted(by_model.items(), key=lambda kv: kv[1]["total_tokens"], reverse=True)),
        "by_source": by_source,
        "recent": recent,
        "logged_calls": len(entries),
    }
