"""Local transport tests for call diagnostics, deadlines and fallback metadata.

No external network. The mock server can send a complete SSE response, a partial
response that stalls, or a 429. These tests verify transport behavior only; they
do not prove a real VLM can interpret engineering drawings.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from openbimagent.providers.registry import ModelRegistry


class MockState:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self.first_chunk_sent = threading.Event()
        self.stop = threading.Event()
        self.server: ThreadingHTTPServer | None = None


def start_server(state: MockState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802 — http.server API
            length = int(self.headers.get("content-length", "0") or 0)
            body = self.rfile.read(length)
            request = json.loads(body.decode("utf-8"))
            state.requests.append({"path": self.path, "body": request})
            if self.path.startswith("/v1"):
                self._send_429()
                return
            if state.mode == "partial_slow":
                self._send_partial_then_stall()
                return
            self._send_complete()

        def _send_429(self) -> None:
            payload = b'{"error":{"message":"rate limited","type":"rate_limit"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_complete(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunks = [
                {"id": "x", "model": "service-model-id", "choices": [{"delta": {"content": "ok"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def _send_partial_then_stall(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = {"model": "service-model-id", "choices": [{"delta": {"reasoning_content": "partial reasoning"}}]}
            try:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                state.first_chunk_sent.set()
                # Keep the stream open without a valid terminal event.
                for _ in range(100):
                    if state.stop.wait(0.05):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def base_url(server: ThreadingHTTPServer, path: str = "/fallback") -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}{path}"


def registry_for(*providers: tuple[str, str]) -> ModelRegistry:
    provider_map = {}
    models = {}
    for index, (name, url) in enumerate(providers):
        provider_map[name] = {"type": "openai-compatible", "base_url": url, "api_key_env": "MOCK_KEY"}
        models[f"m{index}"] = {"provider": name}
    return ModelRegistry(
        providers=provider_map,
        models=models,
        profiles={"official": {"orchestrator": "m0"}},
        fallbacks={"m0": ["m1"]} if len(models) > 1 else {},
        resilience={"retry": {"max": 1, "base_ms": 1}, "timeout_s": 10, "circuit_breaker": {"failures": 5, "cooldown_s": 300}},
        active_profile="official",
    )


@pytest.fixture(autouse=True)
def _mock_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_KEY", "test-key")


@pytest.fixture
def server_factory():
    servers: list[tuple[ThreadingHTTPServer, MockState]] = []

    def make(mode: str = "ok") -> tuple[ThreadingHTTPServer, MockState]:
        state = MockState(mode)
        server = start_server(state)
        servers.append((server, state))
        return server, state

    yield make
    for server, state in servers:
        state.stop.set()
        server.shutdown()
        server.server_close()


def test_pre_cancelled_call_sends_zero_requests(server_factory):
    server, state = server_factory("ok")
    registry = registry_for(("p0", base_url(server, "/fallback")))
    cancel = threading.Event()
    cancel.set()
    diagnostics: dict[str, Any] = {}
    result = registry.chat(
        "orchestrator",
        [{"role": "user", "content": "hi"}],
        cancel_event=cancel,
        cancel_reason="deadline",
        diagnostics=diagnostics,
    )
    assert result["aborted"] is True
    assert result["cancel_reason"] == "deadline"
    assert state.requests == []
    assert diagnostics["attempts"] == []
    assert diagnostics["cancel_before_model"] == "m0"


def test_deadline_during_partial_stream_does_not_start_fallback(server_factory):
    server, state = server_factory("partial_slow")
    registry = registry_for(
        ("p0", base_url(server, "/primary")),
        ("p1", base_url(server, "/fallback")),
    )
    cancel = threading.Event()
    diagnostics: dict[str, Any] = {}
    result: dict[str, Any] = {}

    def call() -> None:
        result.update(
            registry.chat(
                "orchestrator",
                [{"role": "user", "content": "hi"}],
                cancel_event=cancel,
                cancel_reason="deadline",
                diagnostics=diagnostics,
            )
        )

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    assert state.first_chunk_sent.wait(5), "mock server did not send the first stream chunk"
    cancel.set()
    worker.join(10)
    state.stop.set()
    assert not worker.is_alive()
    assert result.get("aborted") is True
    assert len(state.requests) == 1, "deadline must not start the fallback request"
    assert [item["path"] for item in state.requests] == ["/primary/chat/completions"]
    attempt = diagnostics["attempts"][0]
    assert attempt["model"] == "m0"
    assert attempt["request_sent"] is True
    assert attempt["aborted"] is True
    assert attempt["first_event_at_ms"] is not None
    assert attempt["first_reasoning_at_ms"] is not None
    assert attempt["reasoning_chars"] > 0
    assert isinstance(attempt["worker_exited"], bool)
    assert diagnostics.get("cancel_reason") == "deadline"


def test_fallback_metadata_matches_the_attempt_that_succeeded(server_factory):
    server, state = server_factory("ok")
    registry = registry_for(
        ("p0", base_url(server, "/v1")),  # returns 429
        ("p1", base_url(server, "/fallback")),
    )
    diagnostics: dict[str, Any] = {}
    result = registry.chat(
        "orchestrator",
        [{"role": "user", "content": "hi"}],
        diagnostics=diagnostics,
    )
    assert result["model_resolved"] == "m1"
    assert result["model_provider"] == "p1"
    assert result["model_endpoint"].endswith("/fallback/chat/completions")
    assert result["response_model"] == "service-model-id"
    assert diagnostics["selected_model"] == "m1"
    assert diagnostics["selected_provider"] == "p1"
    assert diagnostics["response_model"] == "service-model-id"
    assert [attempt["model"] for attempt in diagnostics["attempts"]] == ["m0", "m1"]
    assert diagnostics["attempts"][0]["http_status"] == 429
    assert diagnostics["attempts"][1]["request_sent"] is True
    assert len(state.requests) == 2


def test_complete_stream_records_timings_without_payload_bodies(server_factory):
    server, _state = server_factory("ok")
    registry = registry_for(("p0", base_url(server, "/fallback")))
    diagnostics: dict[str, Any] = {}
    result = registry.chat(
        "orchestrator",
        [{"role": "user", "content": "secret-free prompt"}],
        diagnostics=diagnostics,
    )
    attempt = diagnostics["attempts"][0]
    assert result["choices"][0]["message"]["content"] == "ok"
    assert attempt["request_sent"] is True
    assert attempt["http_status"] == 200
    assert attempt["first_event_at_ms"] is not None
    assert attempt["first_content_at_ms"] is not None
    assert attempt["content_chars"] == 2
    assert attempt["usage_present"] is True
    assert attempt["finish_reason"] == "stop"
    assert "Authorization" not in json.dumps(diagnostics)
    assert "test-key" not in json.dumps(diagnostics)
