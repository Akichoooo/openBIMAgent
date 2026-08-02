"""Deliver 交付门禁(C5,确定性检查)。

对应文档:
- docs/architecture/ARCHITECTURE.md §0 原则 3(C5:deliver 只接 accepted 产物)、§2 步骤 7
- docs/architecture/COMPONENTS.md §1 deliver 组件(M1)

核对 playbook `deliverables`,出交付清单;未过双环验收(无 accepted 记录)的产物一律拒收。
accepted 判定参数化(accepted_fn 注入):默认实现 is_accepted 取 session 最后一个
score 事件,overall ≥ playbook.acceptance 阈值才视为 accepted;无 score 事件 / 无判定
函数一律不 accepted(C5 从严,不靠模型主观判断)。
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbimagent.session.schema import CustomType, EventType, SessionEvent
from openbimagent.session.store import SessionStore

DEFAULT_LOOP = "blender_loop"
"""C5 默认对照的验收环(交付前最后一道是 Blender 精检环;playbook.acceptance.<loop>.min_score)。"""


@dataclass(frozen=True)
class DeliveryReport:
    """交付核对报告:逐项 deliverable 的 found/missing + C5 accepted;ok = 全 found 且 accepted。"""

    ok: bool  # 门禁总判定:全部 found 且 accepted 才放行
    accepted: bool  # C5 验收判定(accepted_fn 结果;无判定函数一律 False)
    items: dict[str, bool] = field(default_factory=dict)  # deliverable → found(True)/missing(False)
    resolved: dict[str, str] = field(default_factory=dict)  # deliverable → 已解析本地文件路径
    notes: str = ""

    @property
    def missing(self) -> tuple[str, ...]:
        """缺失的 deliverable 清单(items 的便捷视图)。"""
        return tuple(name for name, found in self.items.items() if not found)


# ---------- C5 accepted 判定(可注入,默认实现基于 session score 事件) ----------


def _load_events(session: SessionStore | Path) -> list[SessionEvent]:
    """SessionStore 直接 load;Path 逐行解析(只读,不触发 SessionStore 的 index.json 写副作用)。"""
    if isinstance(session, SessionStore):
        return session.load()
    events: list[SessionEvent] = []
    text = Path(session).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(SessionEvent.model_validate(json.loads(line)))
        except Exception as exc:  # 损坏行容错:跳过并告警(与 SessionStore.load 同策略)
            warnings.warn(f"{session}:{lineno} 损坏行已跳过: {exc}", stacklevel=2)
    return events


def last_score_overall(session: SessionStore | Path) -> float | None:
    """session 最后一个 score 事件的 overall 分(显式 overall_score 优先,否则 rubric_scores 均值);无则 None。"""
    last: Any = None
    for event in _load_events(session):
        if event.type is EventType.CUSTOM and getattr(event.payload, "customType", None) is CustomType.SCORE:
            last = event.payload
    if last is None:
        return None
    overall = getattr(last, "overall_score", None)  # 环内落盘时随 payload 平铺(schema 额外字段)
    if isinstance(overall, (int, float)) and not isinstance(overall, bool):
        return float(overall)
    scores = getattr(last, "rubric_scores", None) or {}
    if isinstance(scores, dict) and scores:
        return sum(float(v) for v in scores.values()) / len(scores)
    return None


def acceptance_threshold(acceptance: dict[str, Any] | int | float, *, loop: str = DEFAULT_LOOP) -> float:
    """playbook acceptance → 阈值数值:字典取 <loop>.min_score,数值直接用。"""
    if isinstance(acceptance, bool):
        raise ValueError(f"acceptance 阈值形态非法: {acceptance!r}")
    if isinstance(acceptance, (int, float)):
        return float(acceptance)
    cfg = acceptance.get(loop) or {}
    if "min_score" not in cfg:
        raise ValueError(f"acceptance 缺少 {loop}.min_score: {acceptance!r}")
    return float(cfg["min_score"])


def is_accepted(
    session: SessionStore | Path,
    acceptance: dict[str, Any] | int | float,
    *,
    loop: str = DEFAULT_LOOP,
) -> bool:
    """C5 默认判定:session 最后一个 score 事件 overall ≥ acceptance 阈值(无 score 记录一律拒收)。"""
    overall = last_score_overall(session)
    if overall is None:
        return False
    return overall >= acceptance_threshold(acceptance, loop=loop)


def make_acceptance_fn(
    session: SessionStore | Path,
    acceptance: dict[str, Any] | int | float,
    *,
    loop: str = DEFAULT_LOOP,
) -> Callable[[], bool]:
    """把 is_accepted 包成零参判定函数,供 check_deliverables 注入(判定逻辑参数化)。"""
    return lambda: is_accepted(session, acceptance, loop=loop)


# ---------- deliverable 齐备核对 ----------


def _normalize(text: str) -> str:
    """大小写/空白归一(子串匹配用)。"""
    return " ".join(text.lower().split())


def _find_deliverable(deliverable: str, artifacts_dir: Path) -> Path | None:
    """在 artifacts_dir 下定位 deliverable:M0 三级确定性匹配(直接路径 → 精确文件名 → 令牌子串)。

    令牌子串:deliverable 首令牌是扩展名形态(如 ".blend 工程" 的 ".blend")按后缀匹配,
    否则按归一化子串匹配文件名;人类措辞与文件命名的更细对齐留 M1。
    """
    direct = artifacts_dir / deliverable
    if direct.exists():
        return direct
    files = [p for p in artifacts_dir.rglob("*") if p.is_file()]
    for p in files:
        if p.name == deliverable:
            return p
    first_token = deliverable.split()[0] if deliverable.split() else ""
    needle = _normalize(deliverable)
    for p in files:
        if first_token.startswith(".") and len(first_token) > 1 and p.suffix.lower() == first_token.lower():
            return p
        if needle and needle in _normalize(p.name):
            return p
    return None


def check_deliverables(
    deliverables: list[str],
    artifacts_dir: Path,
    *,
    accepted_fn: Callable[[], bool] | None = None,
) -> DeliveryReport:
    """C5 门禁:逐项核对 playbook deliverables(found/missing),只接 accepted 产物。

    accepted_fn 注入验收判定(如 make_acceptance_fn(session, playbook.acceptance));
    缺省 None = 无验收证据,accepted=False(C5 从严:未过双环验收的产物一律拒收)。
    ok = 全部 found 且 accepted;逐项缺失与验收结论写入 notes 供人审签。
    """
    root = Path(artifacts_dir)
    items: dict[str, bool] = {}
    resolved: dict[str, str] = {}
    for d in deliverables:
        name = str(d)
        path = _find_deliverable(name, root) if root.is_dir() else None
        items[name] = path is not None
        if path is not None:
            resolved[name] = str(path.resolve())
    accepted = bool(accepted_fn()) if accepted_fn is not None else False
    missing = [name for name, found in items.items() if not found]
    ok = accepted and not missing
    parts = []
    if missing:
        parts.append(f"缺失 {len(missing)} 项: {', '.join(missing)}")
    if not accepted:
        parts.append("无 accepted 验收记录(C5):未过双环验收的产物一律拒收")
    if ok:
        parts.append(f"全部 {len(items)} 项齐备且已验收,放行进入人审签")
    return DeliveryReport(ok=ok, accepted=accepted, items=items, resolved=resolved, notes=";".join(parts))


__all__ = [
    "DeliveryReport",
    "acceptance_threshold",
    "check_deliverables",
    "is_accepted",
    "last_score_overall",
    "make_acceptance_fn",
]
