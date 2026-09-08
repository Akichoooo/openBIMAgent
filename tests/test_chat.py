"""chat 端点测试：对话主循环（P0-5）。

隔离方式：OPENBIMAGENT_LLM_BASELINE / OPENBIMAGENT_SESSIONS_DIR 指向 tmp_path，
LLM 方言调用以 monkeypatch 打桩（chat 模块内绑定名 dialect_chat）——不触网、不碰真实配置。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _write_baseline(tmp_path: Path, *, model: str = "glm-5.2", base_url: str = "https://open.bigmodel.cn/api/paas/v4", api_key: str = "sk-test-chat-key") -> None:
    tmp_path.joinpath("llm_baseline.local.toml").write_text(
        f'model = "{model}"\nbase_url = "{base_url}"\napi_key = "{api_key}"\n',
        encoding="utf-8",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "llm_baseline.local.toml"))
    monkeypatch.setenv("OPENBIMAGENT_CUSTOM_PROVIDERS", str(tmp_path / "custom_providers.json"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "usage_log.jsonl"))  # 不污染真实用量流水账
    from openbimagent.server.fastapi_app import build_demo_app

    c = TestClient(build_demo_app())
    c.headers["Authorization"] = "Bearer test-wb-token"
    return c


def _make_session(tmp_path: Path) -> str:
    from openbimagent.session.store import SessionStore

    store = SessionStore(tmp_path / "sessions" / "sess-chat-1.jsonl", title="chat 测试", playbook="municipal_utility")
    return store.session_id


def _stub_llm(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any], reply: str = "GB 50289-2016 §4.1.9：建筑物基础净距 ≥ 2.5m。") -> None:
    import openbimagent.server.chat as chat_mod

    def fake_chat(dialect, *, model, messages, base_url=None, api_key=None, **kwargs):
        captured["model"] = model
        captured["messages"] = messages
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return {
            "choices": [{"message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

    monkeypatch.setattr(chat_mod, "dialect_chat", fake_chat)


def test_chat_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "t")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "b.toml"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "u.jsonl"))
    from openbimagent.server.fastapi_app import build_demo_app

    c = TestClient(build_demo_app())
    resp = c.post("/api/v1/chat", json={"message": "hello"})
    assert resp.status_code == 401


def test_chat_unconfigured_returns_422_with_hint(client: TestClient) -> None:
    resp = client.post("/api/v1/chat", json={"message": "净距要求是多少？"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "error"
    assert "模型设置" in body["error"]


def test_chat_empty_message_rejected(client: TestClient, tmp_path: Path) -> None:
    _write_baseline(tmp_path)
    resp = client.post("/api/v1/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_roundtrip_persists_pair(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    sid = _make_session(tmp_path)
    captured: dict[str, Any] = {}
    _stub_llm(monkeypatch, captured)

    resp = client.post("/api/v1/chat", json={"message": "建筑净距要求多少？", "session_id": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["reply"].startswith("GB 50289")
    assert body["persisted"] is True
    assert body["usage"]["prompt_tokens"] == 10
    assert "sk-test-chat-key" not in resp.text  # key 绝不出现在响应

    # 落盘：user + assistant 成对（事件溯源，与 pipeline 同一 Session JSONL）
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(ln) for ln in lines]
    roles = [e["payload"]["role"] for e in events if e["type"] == "message"]
    assert roles[-2:] == ["user", "assistant"]

    # 第二轮：系统提示注入 + 上一轮双方消息进入历史
    resp2 = client.post("/api/v1/chat", json={"message": "覆土呢？", "session_id": sid})
    assert resp2.status_code == 200
    msgs = captured["messages"]
    contents = [m["content"] for m in msgs]
    assert msgs[0]["role"] == "system"
    assert any("建筑净距要求多少？" == c for c in contents)  # 上一轮 user 进入历史
    assert any(c.startswith("GB 50289-2016 §4.1.9") for c in contents)  # 上一轮 assistant 进入历史


def test_chat_upstream_failure_returns_502(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    import openbimagent.server.chat as chat_mod

    def boom(*a, **k):
        raise RuntimeError("HTTP 401 Unauthorized")

    monkeypatch.setattr(chat_mod, "dialect_chat", boom)
    resp = client.post("/api/v1/chat", json={"message": "hello"})
    assert resp.status_code == 502
    body = resp.json()
    assert "鉴权未通过" in body["error"]
    assert "sk-test-chat-key" not in resp.text  # 异常串也不得带 key


def test_chat_unknown_session_not_created(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    captured: dict[str, Any] = {}
    _stub_llm(monkeypatch, captured, reply="ok")
    resp = client.post("/api/v1/chat", json={"message": "hi", "session_id": "nonexistent-session"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "nonexistent-session"
    assert not (tmp_path / "sessions" / "nonexistent-session.jsonl").exists()  # 不隐式新建会话文件


def test_baseline_uses_configured_model_and_key(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path, model="gpt-5.6-terra", base_url="https://freetokenfaucet.com/v1", api_key="sk-faucet-777")
    captured: dict[str, Any] = {}
    _stub_llm(monkeypatch, captured, reply="ok")
    client.post("/api/v1/chat", json={"message": "hi"})
    assert captured["model"] == "gpt-5.6-terra"
    assert captured["base_url"] == "https://freetokenfaucet.com/v1"
    assert captured["api_key"] == "sk-faucet-777"


def test_create_session_endpoint_creates_pure_chat_session(client: TestClient, tmp_path: Path) -> None:
    """POST /api/v1/sessions 创建纯净新会话（0 事件，无后台 pipeline）。"""
    resp = client.post(
        "/api/v1/sessions",
        json={"title": "新工程会话测试", "playbook": "municipal_utility", "workspace": "ws-test-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    sid = data["session_id"]
    assert sid.startswith("sess-")
    assert data["title"] == "新工程会话测试"
    assert data["workspace"] == "ws-test-1"

    # 会话 JSONL 文件已创建，初始无事件
    session_file = tmp_path / "sessions" / f"{sid}.jsonl"
    assert session_file.is_file()
    assert session_file.read_text(encoding="utf-8").strip() == ""

    # index.json 索引已正确登记
    index_file = tmp_path / "sessions" / "index.json"
    assert index_file.is_file()
    index_data = json.loads(index_file.read_text(encoding="utf-8"))
    entries = [s for s in index_data.get("sessions", []) if s.get("id") == sid]
    assert len(entries) == 1
    assert entries[0]["title"] == "新工程会话测试"
    assert entries[0]["workspace"] == "ws-test-1"
