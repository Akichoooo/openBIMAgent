"""用量流水账测试：chat 真实调用入账 → /api/v1/usage 聚合（图形化仪表盘数据源）。

隔离方式：OPENBIMAGENT_USAGE_LOG 指向 tmp_path，LLM 方言调用打桩——不触网、不碰真实 out/。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "llm_baseline.local.toml"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "usage_log.jsonl"))
    tmp_path.joinpath("llm_baseline.local.toml").write_text(
        'model = "glm-5.2"\nbase_url = "https://open.bigmodel.cn/api/paas/v4"\napi_key = "sk-test"\n',
        encoding="utf-8",
    )
    from openbimagent.server.fastapi_app import build_demo_app

    # Keep legacy fallback reads away from the developer's real out/ directory.
    monkeypatch.setattr("openbimagent.server.workbench_io._REPO_ROOT", tmp_path)
    monkeypatch.setenv("OPENBIMAGENT_PENDING_APPROVALS", str(tmp_path / "pending.json"))

    class _RidClient(TestClient):
        def build_request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            request = super().build_request(method, url, **kwargs)
            request.headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            request.headers.setdefault("Authorization", "Bearer test-wb-token")
            return request

    with _RidClient(build_demo_app()) as test_client:
        yield test_client


def _stub_llm(monkeypatch: pytest.MonkeyPatch, reply: str = "ok") -> None:
    import openbimagent.server.chat as chat_mod

    def fake_chat(dialect, *, model, messages, base_url=None, api_key=None, **kwargs):
        return {
            "choices": [{"message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }

    monkeypatch.setattr(chat_mod, "dialect_chat", fake_chat)


def test_chat_records_into_ledger(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_llm(monkeypatch)
    resp = client.post("/api/v1/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert resp.json()["usage"]["latency_ms"] >= 0

    ledger = tmp_path / "usage_log.jsonl"
    assert ledger.is_file()
    lines = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["model"] == "glm-5.2"
    assert entry["prompt_tokens"] == 100
    assert entry["completion_tokens"] == 40
    assert entry["total_tokens"] == 0  # Missing total is stored as zero; aggregation supplies in+out.
    assert entry["source"] == "chat"
    assert "sk-test" not in json.dumps(entry)  # 账本绝不记 key


def test_usage_endpoint_aggregates_ledger(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_llm(monkeypatch)
    for message in ("第一问", "第二问"):
        response = client.post("/api/v1/chat", json={"message": message})
        assert response.status_code == 200, response.text

    resp = client.get("/api/v1/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "ledger"
    u = body["usage"]
    assert u["total"]["calls"] == 2
    assert u["total"]["prompt_tokens"] == 200
    assert u["total"]["completion_tokens"] == 80
    assert u["total"]["total_tokens"] == 280  # 缺 total 上报时按 in+out 兜底
    assert u["by_model"]["glm-5.2"]["calls"] == 2
    assert u["by_source"]["chat"] == 2
    # 按日窗口：今天必有消耗；固定 14 天占位
    assert len(u["daily"]) == 14
    assert u["daily"][-1]["total_tokens"] == 280
    assert u["daily"][-1]["calls"] == 2
    # 最近调用（倒序，最新在前）
    assert len(u["recent"]) == 2
    assert u["recent"][0]["model"] == "glm-5.2"
    assert u["recent"][0]["latency_ms"] >= 0


def test_usage_endpoint_empty_returns_null(client: TestClient) -> None:
    resp = client.get("/api/v1/usage")
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "usage": None}


def test_ledger_tolerates_corrupt_lines(client: TestClient, tmp_path: Path) -> None:
    ledger = tmp_path / "usage_log.jsonl"
    ledger.write_text('{"ts": "bad-ts", "model": "glm-5.2"\nnot-json\n', encoding="utf-8")  # 半行/坏行
    resp = client.get("/api/v1/usage")
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "usage": None}
