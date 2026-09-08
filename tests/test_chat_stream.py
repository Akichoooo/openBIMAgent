"""chat 流式端点测试：POST /api/v1/chat/stream（SSE 逐字下发 + 中断/异常口径）。

隔离方式：OPENBIMAGENT_LLM_BASELINE / SESSIONS_DIR / USAGE_LOG 指向 tmp_path，
上游流式方言以 monkeypatch 打桩（chat 模块内绑定名 stream_openai_completions）——不触网。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "llm_baseline.local.toml"))
    monkeypatch.setenv("OPENBIMAGENT_CUSTOM_PROVIDERS", str(tmp_path / "custom_providers.json"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "usage_log.jsonl"))
    # 注意：基线文件不在此写——未配置用例（422）依赖它不存在；配置用例显式调 _write_baseline
    from openbimagent.server.fastapi_app import build_demo_app

    c = TestClient(build_demo_app())
    c.headers["Authorization"] = "Bearer test-wb-token"
    return c


def _write_baseline(tmp_path: Path) -> None:
    tmp_path.joinpath("llm_baseline.local.toml").write_text(
        'model = "glm-5.2"\nbase_url = "https://open.bigmodel.cn/api/paas/v4"\napi_key = "sk-test"\n',
        encoding="utf-8",
    )


def _make_session(tmp_path: Path) -> str:
    from openbimagent.session.store import SessionStore

    store = SessionStore(tmp_path / "sessions" / "sess-stream-1.jsonl", title="stream 测试", playbook="municipal_utility")
    return store.session_id


def _stub_stream(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]]) -> None:
    import openbimagent.server.chat as chat_mod

    def fake(*args: Any, **kwargs: Any):
        yield from events

    monkeypatch.setattr(chat_mod, "stream_openai_completions", fake)


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE 文本 → [(event, data)] 序列，用于断言事件顺序与内容。"""
    out: list[tuple[str, dict[str, Any]]] = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        ev, data = "", {}
        for line in frame.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if ev:
            out.append((ev, data))
    return out


def test_stream_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "t")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "b.toml"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "u.jsonl"))
    from openbimagent.server.fastapi_app import build_demo_app

    c = TestClient(build_demo_app())
    resp = c.post("/api/v1/chat/stream", json={"message": "hello"})
    assert resp.status_code == 401


def test_stream_unconfigured_returns_422(client: TestClient) -> None:
    resp = client.post("/api/v1/chat/stream", json={"message": "净距?"})
    assert resp.status_code == 422
    assert "模型设置" in resp.json()["error"]


def test_stream_roundtrip(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    sid = _make_session(tmp_path)
    _stub_stream(
        monkeypatch,
        [
            {"type": "reasoning", "text": "查 GB 50289-2016 表 4.1.9…"},
            {"type": "delta", "text": "GB 50289-2016 §4.1.9："},
            {"type": "delta", "text": "建筑物基础水平净距 ≥ 2.5m。"},
            {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}},
        ],
    )
    resp = client.post("/api/v1/chat/stream", json={"message": "建筑净距要求多少？", "session_id": sid})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "reasoning" in kinds and "delta" in kinds and "usage" in kinds
    deltas = [d["text"] for e, d in events if e == "delta"]
    assert "".join(deltas) == "GB 50289-2016 §4.1.9：建筑物基础水平净距 ≥ 2.5m。"
    done = [d for e, d in events if e == "done"][0]
    assert done["persisted"] is True

    # 成对落盘：user + assistant（reasoning 思维链不入会话——只记正文）
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events_json = [json.loads(ln) for ln in lines]
    roles = [e["payload"]["role"] for e in events_json if e["type"] == "message"]
    assert roles[-2:] == ["user", "assistant"]
    assert "50289" in resp.text
    assert "查 GB" in resp.text  # SSE 里 reasoning 有下发
    assert all("查 GB" not in json.dumps(e["payload"]) for e in events_json if e["type"] == "message")

    # 真实调用入用量流水账
    ledger = [json.loads(ln) for ln in (tmp_path / "usage_log.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert len(ledger) == 1
    assert ledger[0]["model"] == "glm-5.2"
    assert ledger[0]["prompt_tokens"] == 10
    assert ledger[0]["source"] == "chat"
    assert ledger[0]["latency_ms"] >= 0
    assert "sk-test" not in json.dumps(ledger)


def test_stream_error_before_delta_maps_401(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("HTTP 401 Unauthorized")
        yield  # pragma: no cover - 生成器语法要求

    import openbimagent.server.chat as chat_mod

    monkeypatch.setattr(chat_mod, "stream_openai_completions", boom)
    resp = client.post("/api/v1/chat/stream", json={"message": "hi"})
    assert resp.status_code == 200  # 错误在流内以 error 事件下发（HTTP 层仍 200）
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert "error" in kinds and "done" not in kinds
    err = [d for e, d in events if e == "error"][0]
    assert "鉴权未通过" in err["error"]


def test_stream_midstream_error_keeps_partial_but_does_not_persist(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_baseline(tmp_path)
    sid = _make_session(tmp_path)

    def partial_then_die(*args: Any, **kwargs: Any):
        yield {"type": "delta", "text": "覆土"}
        raise RuntimeError("connection reset")

    import openbimagent.server.chat as chat_mod

    monkeypatch.setattr(chat_mod, "stream_openai_completions", partial_then_die)
    resp = client.post("/api/v1/chat/stream", json={"message": "覆土要求？", "session_id": sid})
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert "delta" in kinds and "error" in kinds
    # 断流失败不落盘（与非流式 /chat 的 502 不落盘口径一致）
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").strip().splitlines()
    roles = [json.loads(ln)["payload"]["role"] for ln in lines if json.loads(ln)["type"] == "message"]
    assert "assistant" not in roles


def test_stream_empty_reply_emits_error(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_baseline(tmp_path)
    _stub_stream(monkeypatch, [{"type": "usage", "usage": {}}])
    resp = client.post("/api/v1/chat/stream", json={"message": "hi"})
    events = _parse_sse(resp.text)
    err = [d for e, d in events if e == "error"][0]
    assert "模型返回为空" in err["error"]
