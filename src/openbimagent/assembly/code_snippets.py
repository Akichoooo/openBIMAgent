"""已验证建模代码片段的捕获/检索/注入（RAG few-shot，MCP4IFC 式 ICL 的生成侧版本）。

问题：modeler 每轮从零生成 bpy 代码，API 语法幻觉（不存在的函数/旧版枚举/bpy.mathutils）
是硬错误直接烧掉一轮迭代预算。builder prompt 里手写的范围锁模板已证明"一小段范例"
能钉住调用形态——本模块把它做成动态检索。

机制（与 runs.py 归档反哺同一设计口径）：
- 捕获：render_loop 收敛 PASS 后把当轮代码入池（只有过验收的代码才入库——语料库本身
  受验收门保护，坏代码不会自我繁殖）；entry 记录资产 categories/tags/分数供检索。
- 检索：纯词法/结构化打分（batch 资产 category 与 entry categories 的 Jaccard + tag 命中），
  无向量库无 embedding；语料是代码不是自然语言，关键词命中比语义相似更可靠。
- 注入：builder prompt 的「参考实现」块，top-k 截断行数，刻意少量——few-shot 是纠语法
  幻觉的，不是教设计的。
- 种子：包内 snippets_seed.json（人工核验的市政域典型操作），随检索池合并，冷启动不空池。

边界：In-Context Retrieval，非权重更新；目录默认 .code_snippets（测试用
OPENBIMAGENT_SNIPPETS_DIR 隔离）。所有失败路径静默降级为"无范例"，绝不阻断建模。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SNIPPETS_DIR = Path(".code_snippets")
"""默认语料目录（项目根）；生产可指向共享卷。"""

_SEED_FILE = Path(__file__).parent / "snippets_seed.json"

_MAX_SNIPPET_LINES = 60
"""注入 prompt 时单条片段截断行数（few-shot 钉语法,不搬整段设计）。"""

_lock = threading.Lock()


def _snippets_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_SNIPPETS_DIR")
    return Path(override) if override else DEFAULT_SNIPPETS_DIR


def _entry_id(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def _batch_features(batch_ctx: dict[str, Any], *, include_ids: bool = True) -> set[str]:
    """从 batch_ctx 提取检索特征：资产 category + tags + description 关键词（确定性小写化）。

    include_ids=False 用于捕获侧：asset id 一次性、跨批次永不命中，入池只存可泛化特征。
    """
    batch = {str(a) for a in batch_ctx.get("batch") or []}
    ir = batch_ctx.get("ir") or {}
    features: set[str] = set()
    for asset in ir.get("assets") or []:
        if not isinstance(asset, dict) or str(asset.get("id")) not in batch:
            continue
        category = str(asset.get("category") or "").strip().lower()
        if category:
            features.add(category)
        for tag in asset.get("tags") or []:
            features.add(str(tag).strip().lower())
        desc = str(asset.get("description") or "").lower()
        for keyword in ("管", "井", "阀", "杆", "柜", "灯", "路", "terrain", "pipe", "manhole", "valve"):
            if keyword in desc:
                features.add(keyword)
    if include_ids:
        features.update(a.lower() for a in batch)
    return features


def _entry_features(entry: dict[str, Any]) -> set[str]:
    return {str(t).strip().lower() for t in entry.get("categories") or []} | {
        str(t).strip().lower() for t in entry.get("tags") or []
    }


def capture_snippet(
    code: str,
    batch_ctx: dict[str, Any],
    *,
    score: float,
    session_id: str | None = None,
) -> str | None:
    """把一段通过验收的建模代码入池；返回 entry id（重复/失败返回 None）。

    调用方责任：只在收敛 PASS 时调用（score ≥ 验收线）。
    """
    code = code.strip()
    if not code or score <= 0:
        return None
    entry_id = _entry_id(code)
    features = sorted(_batch_features(batch_ctx, include_ids=False))
    entry = {
        "id": entry_id,
        "categories": features,
        "asset_ids": [str(a) for a in batch_ctx.get("batch") or []],
        "score": round(float(score), 2),
        "session_id": session_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        root = _snippets_dir()
        root.mkdir(parents=True, exist_ok=True)
        code_path = root / f"{entry_id}.py"
        if code_path.exists():  # 同码已入池（hash 去重，与 AssetCache 同策略）
            return None
        code_path.write_text(code, encoding="utf-8")
        with _lock:
            index_path = root / "index.json"
            index: list[dict[str, Any]] = []
            if index_path.is_file():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    index = []
            index.append(entry)
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return None
    return entry_id


def _load_pool() -> list[dict[str, Any]]:
    """检索池 = 包内种子 + 运行时索引（运行时条目附 code_text）。"""
    pool: list[dict[str, Any]] = []
    try:
        seeds = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
        if isinstance(seeds, list):
            pool.extend(e for e in seeds if isinstance(e, dict) and e.get("code"))
    except (OSError, json.JSONDecodeError):
        pass
    try:
        index_path = _snippets_dir() / "index.json"
        if index_path.is_file():
            for entry in json.loads(index_path.read_text(encoding="utf-8")):
                if not isinstance(entry, dict):
                    continue
                code_path = _snippets_dir() / f"{entry.get('id')}.py"
                if code_path.is_file():
                    entry = dict(entry)
                    entry["code"] = code_path.read_text(encoding="utf-8")
                    pool.append(entry)
    except (OSError, json.JSONDecodeError):
        pass
    return pool


def retrieve_snippets(batch_ctx: dict[str, Any], *, top_k: int = 2) -> list[dict[str, Any]]:
    """按 batch 资产特征检索 Top-K 已验证片段；零命中返回空（不硬凑）。"""
    features = _batch_features(batch_ctx)
    if not features:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in _load_pool():
        cand = _entry_features(entry)
        if not cand or not (features & cand):
            continue
        overlap = len(features & cand) / len(features | cand)
        score = overlap + 0.01 * float(entry.get("score") or 0.0) / 10.0  # 验收分小幅加权
        scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[:top_k]]


def format_snippet_block(entries: list[dict[str, Any]]) -> str | None:
    """把检索命中渲染成 prompt「参考实现」块；无条目返回 None。"""
    if not entries:
        return None
    parts = [
        "参考实现（历史已通过验收的代码片段，仅供 API 调用形态/结构参考；"
        "几何参数必须按本次批次资产声明重新推导，禁止照抄坐标与尺寸）："
    ]
    for i, entry in enumerate(entries, 1):
        code = str(entry.get("code") or "").strip()
        lines = code.splitlines()
        if len(lines) > _MAX_SNIPPET_LINES:
            code = "\n".join(lines[:_MAX_SNIPPET_LINES]) + "\n# ...（截断）"
        cats = ",".join(entry.get("categories") or []) or "general"
        parts.append(f"# 范例 {i}（categories={cats}）\n{code}")
    return "\n\n".join(parts)
