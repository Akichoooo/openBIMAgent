"""Skill 系统（P0-1；agentskills.io 兼容 SKILL.md + 渐进披露 + 自蒸馏候选·人工批准）。

设计（收敛自 pi / Codex / Hermes / OpenClaw）：
- Skill = ``skills/{name}/SKILL.md``：YAML frontmatter（name/description/when_to_use?/tools?）+ Markdown 正文。
- **渐进披露**：目录态只暴露 name+description（``catalog_fragment`` 进上下文）；
  调用（``skill:invoke``）才返回完整正文——上下文成本按使用付费（pi 的反膨胀纪律）。
- **自蒸馏（Hermes × 我方 fail-closed 治理）**：运行成功交付后自动生成 SKILL.md 草稿进
  ``skills/_candidates/``；**必须人工批准**（``POST /api/v1/skills/candidates/{id}/approve``）才转正生效；
  候选永不自动激活（与规则集"production 两类样例缺一不可"同一哲学）。
- 发现源：仓库 ``skills/`` + ``domain_packs/*/skills/`` + ``OPENBIMAGENT_SKILLS_DIR``（测试/外部扩展）。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]


def builtin_skills_root() -> Path:
    """内置技能根目录：OPENBIMAGENT_SKILLS_ROOT 覆盖（测试沙箱）→ 缺省仓库 skills/。"""
    import os

    override = os.environ.get("OPENBIMAGENT_SKILLS_ROOT")
    return Path(override) if override else _REPO_ROOT / "skills"


def candidates_dir() -> Path:
    """自蒸馏候选区（永不自动生效，须人工批准转正）。"""
    return builtin_skills_root() / "_candidates"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")
_REQUIRED_KEYS = ("name", "description")


@dataclass(frozen=True)
class Skill:
    """一个已生效技能（frontmatter 元数据 + 正文）。"""

    name: str
    description: str
    body: str
    when_to_use: str = ""
    tools: tuple[str, ...] = ()
    source: str = ""  # 来源（builtin / domain_pack:<pack> / external / candidate 转正）

    def catalog_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "tools": list(self.tools),
            "source": self.source,
        }


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """解析 YAML 子集 frontmatter（标量键值 + [a, b] 行内列表；失败抛 ValueError）。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("缺少 --- frontmatter --- 头")
    raw, body = match.group(1), match.group(2).strip()
    meta: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter 行缺少冒号: {line!r}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            meta[key] = items
        else:
            meta[key] = value.strip("'\"")
    meta["_body"] = body
    return meta


def load_skill(path: Path, *, source: str) -> Skill:
    """加载并校验单个 SKILL.md；frontmatter 缺键/名称非法/正文为空即拒载（失败关闭）。"""
    meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
    for key in _REQUIRED_KEYS:
        if not meta.get(key):
            raise ValueError(f"frontmatter 缺必需键 {key}: {path}")
    name = str(meta["name"])
    if not _NAME_RE.match(name):
        raise ValueError(f"技能名非法（需 slug）: {name!r}")
    body = str(meta["_body"])
    if not body:
        raise ValueError(f"技能正文为空: {path}")
    tools = meta.get("tools") or ()
    if isinstance(tools, str):
        tools = (tools,)
    return Skill(
        name=name,
        description=str(meta["description"]),
        when_to_use=str(meta.get("when_to_use", "")),
        tools=tuple(str(t) for t in tools),
        body=body,
        source=source,
    )


class SkillRegistry:
    """技能发现与校验注册表（失败关闭：单个坏文件不拖垮全局，计入 rejected）。"""

    def __init__(self, *, extra_dirs: list[Path] | None = None) -> None:
        self._lock = threading.Lock()
        self._skills: dict[str, Skill] = {}
        self.rejected: list[dict[str, str]] = []
        roots: list[tuple[Path, str]] = [
            (builtin_skills_root(), "builtin"),
            *[(p, "domain_pack") for p in sorted((_REPO_ROOT / "domain_packs").glob("*/skills")) if p.is_dir()],
        ]
        import os

        external = os.environ.get("OPENBIMAGENT_SKILLS_DIR")
        if external:
            roots.append((Path(external), "external"))
        if extra_dirs:
            roots.extend((p, "external") for p in extra_dirs)
        for root, source in roots:
            self._discover(root, source)

    def _discover(self, root: Path, source: str) -> None:
        if not root.is_dir():
            return
        for path in sorted(root.glob("*/SKILL.md")):
            if "_candidates" in path.parts:
                continue  # 候选区永不自动生效
            try:
                skill = load_skill(path, source=source if source != "domain_pack" else f"domain_pack:{path.parents[1].name}")
                with self._lock:
                    self._skills[skill.name] = skill
            except (ValueError, OSError) as exc:
                self.rejected.append({"path": str(path), "error": str(exc)})

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def catalog(self) -> list[dict[str, Any]]:
        return [s.catalog_row() for s in sorted(self._skills.values(), key=lambda s: s.name)]

    def catalog_fragment(self, *, max_rows: int = 30) -> str:
        """目录态上下文片段（渐进披露：只有 name+description，不含正文）。"""
        rows = self.catalog()[:max_rows]
        if not rows:
            return ""
        lines = [f"- {r['name']}: {r['description']}" + (f"（适用：{r['when_to_use']}）" if r["when_to_use"] else "") for r in rows]
        return "[可用技能 · /skills 查看 · skill:invoke 调用]\n" + "\n".join(lines)


_REGISTRY: SkillRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def default_skill_registry() -> SkillRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = SkillRegistry()
        return _REGISTRY


def reload_skills() -> SkillRegistry:
    """批准候选/外部变更后重载。"""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = SkillRegistry()
        return _REGISTRY


# ---------- 自蒸馏（运行成功 → SKILL.md 候选） ----------

_CANDIDATE_TEMPLATE = """---
name: {name}
description: {description}
when_to_use: {when_to_use}
---

# {title}

> 自蒸馏候选 · 源任务 session={session_id} · 交付于 {archived_at}
> 批准前请人工核对步骤与数值（本文件不会自动生效）。

## 适用输入
{brief}

## 已验证步骤（源任务真实事件）
{steps}

## 交付工件（参照 auto_archive）
{files}

## 注意
- 坐标/标高必须重新经 Solver 计算，禁止照抄源任务数值（C2 铁律）。
- 规则限值以 Domain Pack knowledge/ 受信任源为准。
"""


def distill_candidate(
    skills_root: Path,
    *,
    session_id: str,
    brief: str,
    playbook: str,
    files: list[str],
    archived_at: str,
) -> Path | None:
    """从成功交付的运行蒸馏 SKILL.md 候选；name 冲突时追加会话后缀。返回候选路径（无内容则不写）。"""
    if not brief.strip():
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", brief.lower())[:32].strip("-") or "task"
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"t{slug}"
    candidates = skills_root / "_candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    path = candidates / f"{slug}-{session_id[:8]}.md"
    steps = "\n".join(
        f"{i + 1}. 阶段 {stage}"
        for i, stage in enumerate(["clarify（槽位确认）", "planner/solver（确定性求解）", "domain_gate（规则核验）", "deliver（审批交付）"])
    )
    path.write_text(
        _CANDIDATE_TEMPLATE.format(
            name=slug,
            description=f"从「{brief[:40]}」蒸馏的{playbook}交付经验",
            when_to_use=brief[:60],
            title=f"{playbook} 交付经验 · {brief[:30]}",
            session_id=session_id,
            archived_at=archived_at,
            brief=brief,
            steps=steps,
            files="、".join(files) if files else "（无）",
        ),
        encoding="utf-8",
    )
    return path
