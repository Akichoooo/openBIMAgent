"""Clarify 追问:槽位抽取、缺口判定、completion_score ≥ 85 放行。

对应文档:
- docs/architecture/COMPONENTS.md §2.2 clarify
- docs/architecture/ARCHITECTURE.md §2 生命周期步骤 1、§4 playbook `slots:`

流程:规则抽取(正则/别名,zh/en)→ 缺口判定 → 逐 slot 一问一答(带默认值,回车接受)→
回填 → `completion_score` ≥ 85 放行。追问全程写 session 树,可 `/tree` 回改重跑。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import yaml

from openbimagent.session.schema import EventType

if TYPE_CHECKING:
    from openbimagent.session.store import SessionStore

_FLOW_QMARK_SCALAR = re.compile(r"(\b\w+\s*:\s*)([^\"'\n{}\[\],]*?\?+)(\s*[,}\]])")
"""PyYAML 已知怪癖的修补:flow mapping 内裸标量结尾的 `?`(如 `question: 做什么资产?,`)
会被误判为显式 key token 触发 ParserError;回退时把这类标量包成双引号(值逐字保留)再解析。
与 planner.instantiate._FLOW_QMARK_SCALAR 同规则(单一事实源:playbook frontmatter 解析)。"""

PASS_THRESHOLD = 85
"""completion_score 放行阈值(COMPONENTS §2.2)。"""

EXPRESSION_FEATURE_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "EF1",
        "feature": "指称混用",
        "rule": "同一实体用不同名字(“这个管”/“刚才那根”/“旁边那个井”)时,必须根据上下文统一到唯一实体名,禁止当成多个实体。",
    },
    {
        "id": "EF2",
        "feature": "过程压缩",
        "rule": "“把水从 A 排到 B”这类压缩表达隐含了路径、坡度、中途井等中间过程;抽取时要拆开,不得把隐含步骤当作已明确。",
    },
    {
        "id": "EF3",
        "feature": "前提与动作混淆",
        "rule": "“如果有现状管线就避让”里的避让是约束/前提,不是新任务;前提条件归入约束,不得提取为独立动作。",
    },
    {
        "id": "EF4",
        "feature": "分支遗漏",
        "rule": "用户只描述了常见分支时,要显式确认其他分支(如雨天/检修/事故工况),不得默认只剩一条路径。",
    },
    {
        "id": "EF5",
        "feature": "一对多合并",
        "rule": "“做三个井”这类合并表达里每个对象的参数可能不同;要拆成独立对象逐一确认,不得共用同一组参数。",
    },
    {
        "id": "EF6",
        "feature": "口语量词与单位",
        "rule": "“三百的管”要确认是 DN300 还是 300mm 壁厚;口语量词(几个、那一带)必须追问到精确值与单位。",
    },
    {
        "id": "EF7",
        "feature": "背景说明夹杂",
        "rule": "项目背景、历史沿革等解释性文字不是需求;不得从中提取槽位值,但可作约束上下文保留。",
    },
    {
        "id": "EF8",
        "feature": "无对象要求",
        "rule": "“现场要平整”这类没有明确对象的要求,归入场地/环境前提确认,不得当成建模任务直接执行。",
    },
)
"""BIM 需求口语化表达特征规则库(论文 07 方法论:真实语料失败模式 → 显式应对规则)。

提炼自真实工程需求表达的高频失败模式;clarify 抽取与追问 prompt 注入本表,
小模型兜底抽取的指令同样携带(见 extract_slots_with_fallback)。
"""


def expression_rules_fragment() -> str:
    """把表达特征规则渲染为 prompt 片段(供 clarify 角色与兜底抽取共用,单一事实源)。"""
    lines = ["处理用户需求时必须遵守以下表达特征规则:"]
    for rule in EXPRESSION_FEATURE_RULES:
        lines.append(f"- {rule['id']} {rule['feature']}:{rule['rule']}")
    return "\n".join(lines)


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
    """规则抽取(正则/别名表,zh/en),回填已命中槽位;默认值关键词出现也视为命中。"""
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


def _strip_json_fences(text: str) -> str:
    """剥掉 ```json 围栏与前后杂讯,取第一个 JSON 对象文本。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def extract_slots_with_fallback(
    user_input: str,
    slots: list[Slot],
    *,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> tuple[list[Slot], list[str]]:
    """规则抽取 + 小模型兜底(M1 TODO 落地;COMPONENTS §1 "规则 + 小模型")。

    先跑确定性规则抽取;仍有未填槽位时,用 clarify 角色携带表达特征规则做一次
    JSON 兜底抽取(论文 07:规则回溯优于泛化提示)。chat_fn 缺省时尝试默认
    registry;模型不可用/输出非法 → 静默回退到纯规则结果(离线确定性优先,
    追问流程绝不因兜底失败而阻塞)。只回填已知 slot id 的非空字符串值,
    模型编造的槽位 id 一律忽略。返回 (slots, 兜底填充的槽位 id 列表)。
    """
    extract_slots(user_input, slots)
    missing = [s for s in slots if s.value is None]
    if not missing:
        return slots, []
    fn = chat_fn
    if fn is None:
        try:
            from openbimagent.providers.registry import get_default_registry

            fn = get_default_registry().chat
        except Exception:
            return slots, []
    slot_spec = [{"id": s.id, "question": s.question, "default": s.default} for s in missing]
    prompt = (
        "你是工程需求槽位抽取器。\n"
        + expression_rules_fragment()
        + "\n\n从用户需求中为下列槽位抽取值;抽取不到就填 null,禁止编造:\n"
        + json.dumps(slot_spec, ensure_ascii=False)
        + "\n\n用户需求:\n"
        + user_input
        + '\n\n只输出 JSON 对象,形如 {"<slot_id>": "<值或null>"},不要输出其他文字。'
    )
    try:
        resp = fn(role="clarify", messages=[{"role": "user", "content": prompt}])
        content = resp.get("content") or ""
        if not content and resp.get("choices"):
            content = (resp["choices"][0].get("message") or {}).get("content") or ""
        data = json.loads(_strip_json_fences(str(content)))
    except Exception:
        return slots, []
    if not isinstance(data, dict):
        return slots, []
    filled: list[str] = []
    for slot in missing:
        value = data.get(slot.id)
        if isinstance(value, str) and value.strip():
            slot.value = value.strip()
            filled.append(slot.id)
    return slots, filled


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
    resume: bool = False,
) -> SlotState:
    """一问一答循环:逐槽位提问(带默认值),回车 = 接受默认;input_func/question_provider 可注入。

    resume=True(断点续跑):跳过 state.asked 中已问过的槽位(由 resume_from_session 预填);
    resume=False(默认):重置 state.asked,全部按 value 重新问(向后兼容,旧调用无感)。
    """
    provide = question_provider or (lambda s: s.question)
    if not resume:
        state.asked.clear()  # 非续跑:重置 asked,next_question 仅按 value 判断
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


def resume_from_session(
    slots: list[Slot],
    session: "SessionStore",
    from_event_id: str | None = None,
) -> SlotState:
    """从 session 事件链恢复 Clarify 状态(已问过的槽位 + 已填的答案),供断点续跑。

    匹配规则:遍历事件链,assistant 问(content 含 slot.question)→ 紧跟的 user 答 → 回填
    slot.value 并加入 state.asked;若 assistant 问后无 user 答(只问未答),仅标记 asked,
    value 保持 None。slots 深拷贝避免污染调用方传入的定义。

    Args:
        slots: 槽位定义(从 playbook 加载,不会被修改)
        session: 分支会话(含 fork 复制来的问答事件)
        from_event_id: 恢复到的事件 id(默认 session 当前 head)

    Returns:
        SlotState:已回填的 value + asked 集合;无匹配则返回空状态(slots 未回填,asked 空)
    """
    chain = session.get_event_chain(from_event_id)
    state = SlotState(slots=[deepcopy(s) for s in slots])
    i = 0
    while i < len(chain):
        event = chain[i]
        if event.type is EventType.MESSAGE and getattr(event.payload, "role", "") == "assistant":
            content = getattr(event.payload, "content", "")
            for slot in state.slots:
                if slot.question in content:
                    state.asked.add(slot.id)
                    if i + 1 < len(chain):
                        next_event = chain[i + 1]
                        if (next_event.type is EventType.MESSAGE
                                and getattr(next_event.payload, "role", "") == "user"):
                            slot.value = getattr(next_event.payload, "content", "")
                            i += 1  # 跳过已配对的 user 答
                    break
        i += 1
    return state
