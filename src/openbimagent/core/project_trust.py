"""F5 Project Trust(pi Project Trust 语义):项目本地插件/skills 配置加载前须显式信任。

防仓库内恶意配置:插件/技能目录位于项目内(含 .git 的仓库)时,首次加载须先经
人工 approve 写入 trust 记录;未信任前一律跳过(fail-closed,不静默执行)。
全局/系统目录(用户 ~/.openbimagent/skills、builtin)不受此门约束。

trust 记录:~/.openbimagent/trust.json = {"trusted_roots": [<abs path>...]}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

_TRUST_FILE = Path.home() / ".openbimagent" / "trust.json"
_LOCK = RLock()
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _trust_file_path() -> Path:
    override = os.environ.get("OPENBIMAGENT_TRUST_FILE")
    return Path(override) if override else _TRUST_FILE


def _read_roots() -> set[str]:
    path = _trust_file_path()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    roots = data.get("trusted_roots") if isinstance(data, dict) else None
    return {str(r) for r in roots} if isinstance(roots, list) else set()


def _write_roots(roots: set[str]) -> None:
    path = _trust_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trusted_roots": sorted(roots)}, ensure_ascii=False, indent=2), encoding="utf-8")


def is_project_local(directory: Path | str) -> bool:
    """目录是否位于项目内(自身或祖先含 .git),即受 Project Trust 约束。"""
    d = Path(directory).resolve()
    for candidate in [d, *d.parents]:
        if (candidate / ".git").is_dir():
            return True
    return False


def is_trusted(directory: Path | str) -> bool:
    """目录(或其任一祖先)是否已被显式信任。"""
    d = str(Path(directory).resolve())
    with _LOCK:
        roots = _read_roots()
    return any(d == r or d.startswith(r + os.sep) for r in roots)


def require_trust(directory: Path | str, *, reason: str = "") -> bool:
    """项目本地目录是否通过信任门。

    规则:
    - 非项目本地目录 → True(不约束,如用户 ~/.openbimagent 下或纯数据目录);
    - 目录位于本产品仓库根内 → True(随产品分发的受版本控制的插件是本产品代码的一部分,
      信任门针对的是外部/第三方项目目录里运行期出现的配置);
    - 其余项目本地目录 → 须已显式信任。
    """
    if not is_project_local(directory):
        return True
    resolved = Path(directory).resolve()
    repo = _REPO_ROOT.resolve()
    if resolved == repo or str(resolved).startswith(str(repo) + os.sep):
        return True
    return is_trusted(directory)


def approve(directory: Path | str) -> str:
    """把目录登记为已信任(写 trust.json);返回登记的根路径。"""
    d = str(Path(directory).resolve())
    with _LOCK:
        roots = _read_roots()
        roots.add(d)
        _write_roots(roots)
    return d


def revoke(directory: Path | str) -> None:
    """撤销信任(测试/清理用)。"""
    d = str(Path(directory).resolve())
    with _LOCK:
        roots = _read_roots()
        roots = {r for r in roots if r != d}
        _write_roots(roots)


__all__ = ["approve", "is_project_local", "is_trusted", "require_trust", "revoke"]
