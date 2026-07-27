"""Clarify 追问:槽位抽取、缺口判定、completion_score ≥ 85 放行。

对应文档:
- docs/architecture/COMPONENTS.md §2.2 clarify
- docs/architecture/ARCHITECTURE.md §2 生命周期步骤 1、§4 playbook `slots:`

流程:规则抽取(正则/别名,zh/en)→ 缺口判定 → 逐 slot 一问一答(带默认值,回车接受)→
回填 → `completion_score` ≥ 85 放行。追问全程写 session 树,可 `/tree` 回改重跑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

_FLOW_QMARK_SCALAR = re.compile(r"(\b\w+\s*:\s*)([^\"'\n{}\[\],]*?\?+)(\s*[,}\]])")
"""PyYAML 已知怪癖的修补:flow mapping 内裸标量结尾的 `?`(如 `question: 做什么资产?,`)
会被误判为显式 key token 触发 ParserError;回退时把这类标量包成双引号(值逐字保留)再解析。
与 planner.instantiate._FLOW_QMARK_SCALAR 同规则(单一事实源:playbook frontmatter 解析)。"""

PASS_THRESHOLD = 85
"""completion_score 放行阈值(COMPONENTS §2.2)。"""


@dataclass
class Slot:
    """playbook `slots:` 单条:{id, question, default, aliases};value 为回填结果。"""

    id: str
    question: str
    default: str | None = None
    value: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class SlotState:
    """一次追问的槽位总状态(落 session 树,可 /tree 回改重跑)。"""

    slots: list[Slot] = field(default_factory=list)
    asked: set[str] = field(default_factory=set)  # 已问过的槽位 id(防重复追问)

    @property
    def completion_score(self) -> float:
        """已填槽位占比 × 100(显式回填或带默认值的槽位都算已填,§2.2)。"""
        if not self.slots:
            return 100.0
        filled = sum(1 for s in self.slots if s.value is not None or s.default is not None)
        return filled / len(self.slots) * 100


def load_slots(frontmatter: dict[str, Any]) -> list[Slot]:
    """从 playbook frontmatter 的 `slots:` 解析槽位定义(id/question 必填,default/aliases 可选)。"""
    raw = frontmatter.get("slots") or []
    slots: list[Slot] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("question"):
            raise ValueError(f"slot 定义缺 id/question: {entry!r}")
        aliases = entry.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        slots.append(
            Slot(
                id=str(entry["id"]),
                question=str(entry["question"]),
                default=None if entry.get("default") is None else str(entry["default"]),
                aliases=[str(a) for a in aliases],
            )
        )
    return slots


def load_playbook_slots(path: Path) -> list[Slot]:
    """读 playbook.md 的 YAML frontmatter(--- 包围段)并解析 slots。

    PyYAML 的 flow-`?` 怪癖走引号修补回退(与 planner.instantiate._load_frontmatter 同规则,
    单一事实源):如 `question: 做什么资产?,` 会被误判为显式 key token 触发 ParserError;
    回退时把这类裸标量包成双引号(值逐字保留)再解析,仍失败报清晰错误。
    """
    text = Path(path).read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path} 缺少 YAML frontmatter(--- 包围段)")
    frontmatter = _load_frontmatter(match.group(1), Path(path))
    return load_slots(frontmatter)


def _load_frontmatter(raw: str, path: Path) -> dict[str, Any]:
    """frontmatter YAML → dict;PyYAML 的 flow-`?` 怪癖走引号修补回退,仍失败报清晰错误。

    与 planner.instantiate._load_frontmatter 同实现(单一事实源):直解失败 → 把 flow mapping
    内裸标量结尾的 `?` 包成双引号(值逐字保留)→ 再解析;仍失败抛 ValueError。
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        repaired = _FLOW_QMARK_SCALAR.sub(lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}', raw)
        try:
            data = yaml.safe_load(repaired)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} 的 frontmatter YAML 解析失败(含 flow-? 修补回退): {exc}") from exc
    frontmatter = data or {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path} 的 frontmatter 须为 YAML mapping,实收 {type(frontmatter).__name__}")
    return frontmatter


def _extract_value(user_input: str, key: str) -> str | None:
    """按单个关键词(id/别名)从用户文本抽值:支持 k:v / k=v / k是v 三类写法(zh/en)。"""
    esc = re.escape(key)
    patterns = [
        rf"{esc}\s*[:：=]\s*[\"'“](?P<v>[^\"'”]+)[\"'”]",  # 带引号的值
        rf"{esc}\s*[:：=]\s*(?P<v>[^\n,;,;。]+)",  # key: value(到标点为止)
        rf"{esc}\s*(?:是|为|用|使用|选择|选)\s*[\"'“]?(?P<v>[^\n,;,;。\"'”]+)",  # key 是 value
    ]
    for pattern in patterns:
        match = re.search(pattern, user_input)
        if match:
            value = match.group("v").strip().strip("\"'“”")
            if value:
                return value
    return None


def extract_slots(user_input: str, slots: list[Slot]) -> list[Slot]:
    """规则抽取(正则/别名表,zh/en),回填已命中槽位;默认值关键词出现也视为命中。

    TODO(M1): 规则未命中的槽位走小模型兜底抽取(COMPONENTS §1:规则 + 小模型)。
    """
    for slot in slots:
        if slot.value is not None:
            continue
        for key in [slot.id, *slot.aliases]:
            value = _extract_value(user_input, key)
            if value is not None:
                slot.value = value
                break
        else:
            if slot.default and slot.default in user_input:
                slot.value = slot.default
    return slots


def next_question(state: SlotState) -> Slot | None:
    """返回下一个待问槽位(声明序;未回填且未问过);None 表示已问齐。"""
    for slot in state.slots:
        if slot.value is None and slot.id not in state.asked:
            return slot
    return None


def run_clarify(
    state: SlotState,
    *,
    input_func: Callable[[str], str] = input,
    question_provider: Callable[[Slot], str] | None = None,
) -> SlotState:
    """一问一答循环:逐槽位提问(带默认值),回车 = 接受默认;input_func/question_provider 可注入。"""
    provide = question_provider or (lambda s: s.question)
    while (slot := next_question(state)) is not None:
        state.asked.add(slot.id)
        prompt = provide(slot)
        if slot.default is not None:
            prompt = f"{prompt} [{slot.default}]"
        answer = (input_func(prompt) or "").strip()
        slot.value = answer if answer else slot.default
    return state


def may_proceed(state: SlotState) -> bool:
    """completion_score ≥ 85 放行(COMPONENTS §2.2)。

    TODO(M1): 放行前生成确认单等用户点头(ARCH §2 步骤 1)。
    """
    return state.completion_score >= PASS_THRESHOLD
