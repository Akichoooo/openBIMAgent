"""The repository .env is loaded once by the CLI and FastAPI startup."""
from __future__ import annotations

import os

from openbimagent.env import load_dotenv


def test_load_dotenv_fills_missing_values_without_overriding(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nEXISTING_KEY=from-file\nNEW_KEY='new-value'\nEMPTY_LINE=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING_KEY", "from-process")
    applied = load_dotenv(env_file)
    assert os.environ["EXISTING_KEY"] == "from-process"
    assert os.environ["NEW_KEY"] == "new-value"
    assert applied == {"NEW_KEY": "new-value", "EMPTY_LINE": ""}


def test_fastapi_startup_invokes_the_shared_dotenv_loader(monkeypatch):
    from fastapi.testclient import TestClient

    from openbimagent.server.fastapi_app import build_demo_app

    calls: list[int] = []
    monkeypatch.setattr("openbimagent.env.load_dotenv", lambda: calls.append(1) or {})
    with TestClient(build_demo_app()) as client:
        assert client.get("/healthz").status_code == 200
    assert calls == [1]
