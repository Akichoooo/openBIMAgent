"""Load the repository ``.env`` without overriding an explicit environment.

The provider registry reads credentials from ``os.environ``. ``uv run`` does not
load ``.env`` by itself, so both the CLI entry point and the FastAPI startup hook
use this one loader. Values are never logged or returned by API surfaces.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
"""``src/openbimagent/env.py`` → repository root."""


def load_dotenv(path: Path | str | None = None, *, override: bool = False) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from ``.env``.

    Existing process variables win by default, matching the previous CLI loader.
    Returns only the keys that were applied in this call; never returns values to
    callers that intend to expose configuration status.
    """
    env_path = Path(path) if path is not None else REPO_ROOT / ".env"
    applied: dict[str, str] = {}
    if not env_path.is_file():
        return applied
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        if not key or (key in os.environ and not override):
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
