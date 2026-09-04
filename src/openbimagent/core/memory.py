"""轻量记忆层（P0-4，对标 Codex/Hermes 的 MEMORY.md + user profile 双文件语义）。

- 两个纯 Markdown 文件：``MEMORY.md``（长期事实/偏好）与 ``USER.md``（用户画像/习惯）；
- **写入走能力策略门**：``memory:record`` 注册为 prompt 策略能力（与 CAD 写盘同级治理），
  必须显式 confirm=True——记忆是跨会话持久化，绝不静默写入；
- **读取免费**：``prompt_fragment`` 在每轮运行注入上下文（与归档范例同一通道）；
- 根目录：``OPENBIMAGENT_MEMORY_DIR`` 覆盖（测试沙箱）→ 缺省仓库 ``memory/``（gitignored）。
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]

FILES = {"memory": "MEMORY.md", "user": "USER.md"}
_MAX_ENTRY_CHARS = 500
_MAX_FILE_BYTES = 256 * 1024  # 单文件 256KB 上限（防爆；超限需人工整理，如实报错）


def default_memory_root() -> Path:
    override = os.environ.get("OPENBIMAGENT_MEMORY_DIR")
    return Path(override) if override else _REPO_ROOT / "memory"


class MemoryStore:
    """MEMORY.md / USER.md 的追加式存储（只增不改，时间戳条目）。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or default_memory_root()
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, file_key: str) -> Path:
        if file_key not in FILES:
            raise ValueError(f"未知记忆文件: {file_key}（可选：{sorted(FILES)}）")
        return self._root / FILES[file_key]

    def append(self, file_key: str, entry: str) -> dict[str, Any]:
        """追加一条记忆（单行 bullet + UTC 时间戳）；返回写入结果元数据。"""
        text = re.sub(r"\s+", " ", entry).strip()  # 压平为单行，防 Markdown 结构注入
        if not text:
            raise ValueError("记忆条目不能为空")
        if len(text) > _MAX_ENTRY_CHARS:
            raise ValueError(f"记忆条目超长（{len(text)} > {_MAX_ENTRY_CHARS} 字符）")
        path = self._path(file_key)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"- [{stamp}] {text}\n"
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            existing = path.stat().st_size if path.is_file() else 0
            if existing + len(line.encode("utf-8")) > _MAX_FILE_BYTES:
                raise ValueError(f"{FILES[file_key]} 已达 256KB 上限，请人工整理后再写（fail-closed）")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        return {"file": FILES[file_key], "entry": text, "recorded_at": stamp}

    def tail(self, file_key: str, n: int = 10) -> list[str]:
        """读取末 n 条（不存在返回空）。"""
        path = self._path(file_key)
        if not path.is_file():
            return []
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []
        return lines[-max(1, n) :]

    def read_entries(self, file_key: str, limit: int = 100) -> list[dict[str, Any]]:
        """带物理行号的条目清单（删除操作的寻址基础；末 limit 条）。"""
        path = self._path(file_key)
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = [{"line": i + 1, "text": ln} for i, ln in enumerate(lines) if ln.strip()]
        return entries[-max(1, limit) :]

    def delete_line(self, file_key: str, line_no: int) -> bool:
        """按物理行号删除一条记忆（只删目标行，其余原样保留）；行号不存在返回 False。"""
        path = self._path(file_key)
        if not path.is_file():
            return False
        with self._lock:
            try:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            except OSError:
                return False
            if line_no < 1 or line_no > len(lines):
                return False
            del lines[line_no - 1]
            path.write_text("".join(lines), encoding="utf-8")
        return True

    def prompt_fragment(self, max_entries: int = 8) -> str:
        """上下文注入片段（末 N 条长期记忆 + 用户画像；空则返回空串不注水）。"""
        mem = self.tail("memory", max_entries)
        user = self.tail("user", max_entries)
        if not mem and not user:
            return ""
        parts = ["[长期记忆（人工审批写入，仅供对齐偏好，不得当作工程证据）]"]
        if mem:
            parts.append("MEMORY:" + "\n".join(mem))
        if user:
            parts.append("USER:" + "\n".join(user))
        return "\n".join(parts)


_DEFAULT: MemoryStore | None = None
_DEFAULT_LOCK = threading.Lock()


def default_memory_store() -> MemoryStore:
    """进程级默认 store（root 随 env 动态解析，env 变更时自动重建——测试隔离友好）。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        root = default_memory_root()
        if _DEFAULT is None or _DEFAULT.root != root:
            _DEFAULT = MemoryStore(root)
        return _DEFAULT
