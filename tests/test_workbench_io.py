"""workbench_io 端点测试：LLM 设置读写 + 附件上传。

隔离方式：OPENBIMAGENT_LLM_BASELINE / OPENBIMAGENT_UPLOADS_DIR / OPENBIMAGENT_ENV_FILE
全部指向 tmp_path，不触碰仓库真实配置。
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "llm_baseline.local.toml"))
    monkeypatch.setenv("OPENBIMAGENT_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OPENBIMAGENT_ENV_FILE", str(tmp_path / ".env"))
    for key in ("GLM_API_KEY", "GEMINI_API_KEY", "AGENTROUTER_API_KEY", "FREETOKENFAUCET_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from openbimagent.server.fastapi_app import build_demo_app

    return TestClient(build_demo_app())


def test_get_settings_unconfigured(client: TestClient) -> None:
    resp = client.get("/api/v1/settings/llm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["baseline"]["configured"] is False
    assert data["baseline"]["api_key_set"] is False
    assert all(p["key_set"] is False for p in data["provider_keys"])


def test_put_settings_roundtrip_and_key_never_echoed(client: TestClient, tmp_path: Path) -> None:
    resp = client.put(
        "/api/v1/settings/llm",
        json={
            "model": "gpt-5.6-terra",
            "base_url": "https://freetokenfaucet.com/v1",
            "api_key": "sk-test-secret-123",
            "provider_keys": {"GLM_API_KEY": "glm-secret", "NOT_ALLOWED_KEY": "nope"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["configured"] is True
    assert body["baseline"]["model"] == "gpt-5.6-terra"
    # key 本体绝不出现在响应里
    assert "sk-test-secret-123" not in resp.text and "glm-secret" not in resp.text

    # 基线文件落盘且评测加载器可读（与 benchmark 真实消费路径一致）
    raw = tomllib.loads((tmp_path / "llm_baseline.local.toml").read_text(encoding="utf-8"))
    assert raw["api_key"] == "sk-test-secret-123"
    assert raw["repetitions"] == 3  # 缺省字段补齐
    from openbimagent.benchmark.llm_direct_baseline import load_llm_baseline_config

    cfg = load_llm_baseline_config(tmp_path / "llm_baseline.local.toml")
    assert cfg is not None and cfg.model == "gpt-5.6-terra"

    # provider key：白名单内即时入环境 + .env 持久化；白名单外忽略
    assert os.environ["GLM_API_KEY"] == "glm-secret"
    assert "NOT_ALLOWED_KEY" not in os.environ
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GLM_API_KEY=glm-secret" in env_text and "NOT_ALLOWED_KEY" not in env_text

    # GET 反映 key_set 状态
    data = client.get("/api/v1/settings/llm").json()
    glm = next(p for p in data["provider_keys"] if p["env"] == "GLM_API_KEY")
    assert glm["key_set"] is True


def test_put_settings_rejects_bad_body(client: TestClient) -> None:
    resp = client.put("/api/v1/settings/llm", content=b"not-json", headers={"content-type": "application/json"})
    assert resp.status_code == 400


def test_upload_and_list(client: TestClient, tmp_path: Path) -> None:
    payload = b"ifc test bytes \x00\x01"
    resp = client.post("/api/v1/uploads?name=管网场景.ifc", content=payload)
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["size"] == len(payload)
    assert item["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "uploads" / item["id"]).read_bytes() == payload

    listed = client.get("/api/v1/uploads").json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == item["id"]


def test_upload_empty_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/uploads?name=x.bin").status_code == 400
