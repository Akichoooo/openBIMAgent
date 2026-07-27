#!/usr/bin/env python3
"""Probe the agentrouter API surface (OpenAI vs Anthropic dialect).

Reads AGENTROUTER_API_KEY (and optional AGENTROUTER_BASE_URL) from the project
root .env, parsed manually (no third-party deps). The API key is NEVER printed
or written to disk.

Every request carries User-Agent "claude-cli/2.0.0 (external, cli)":
agentrouter.org filters clients by UA and 401s anything else.

Checks:
  1. GET  {base}/models                      (OpenAI-style, Bearer auth)
  2. POST {base}/chat/completions  glm-5.2   (text round-trip)
  3. POST {base}/chat/completions  gpt-5.5   (vision: 1x1 red PNG data-URI)
  4. POST {base}/messages                    (Anthropic dialect fallback, only
     if checks 1+2 both look unsupported/404)

Results -> relay_workspace/m0_spikes/agentrouter_probe.json (no key).
stdout  -> human-readable summary.

Usage (after .env exists):
    python tools/probe_agentrouter.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
OUT_PATH = ROOT / "relay_workspace" / "m0_spikes" / "agentrouter_probe.json"
DEFAULT_BASE_URL = "https://agentrouter.org/v1"
TIMEOUT_SECONDS = 30

# 1x1 pure-red PNG (RGBA, verified: pixel = R255 G0 B0)
RED_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Model used only for the Anthropic-dialect probe (check 4); any id works
# because we only care whether the endpoint/dialect exists at all.
ANTHROPIC_PROBE_MODEL = "claude-sonnet-4-5"

# agentrouter.org filters clients by User-Agent: requests without this exact UA
# get 401 "unauthorized client detected". Sent on EVERY request below.
CLIENT_UA = "claude-cli/2.0.0 (external, cli)"


def load_env(path: Path) -> dict:
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional quotes."""
    values = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        values[key] = val
    return values


def mask(text, api_key: str):
    """Guarantee the key never leaks into logs/results."""
    if isinstance(text, str) and api_key:
        return text.replace(api_key, "***")
    return text


def http_json(method: str, url: str, headers: dict, payload: dict | None):
    """Returns (status_code | None, body_text). Never raises.

    Always sends the client User-Agent (agentrouter 401s without it); an
    explicit User-Agent in `headers` takes precedence.
    """
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", CLIENT_UA)
    for k, v in headers.items():
        req.add_header(k, v)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:  # URLError, timeout, SSL, ...
        return None, f"{type(e).__name__}: {e}"


def try_parse_json(body: str):
    try:
        return json.loads(body)
    except Exception:
        return None


def extract_chat_content(parsed) -> str:
    """Pull assistant text out of an OpenAI chat.completion response.

    Reasoning models (e.g. glm-5.2 on agentrouter) may return empty content with
    the text in reasoning_content instead — fall back to it so the probe still
    shows a meaningful excerpt (prefixed to mark the source).
    """
    try:
        message = parsed["choices"][0]["message"]
        content = message["content"]
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            # content-part list form
            return "".join(p.get("text", "") for p in content if isinstance(p, dict))
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            return f"[reasoning_content] {reasoning}"
        return ""
    except Exception:
        return ""


def check_models(base: str, key: str) -> dict:
    status, body = http_json(
        "GET", f"{base}/models", {"Authorization": f"Bearer {key}"}, None
    )
    result = {"name": "models", "endpoint": f"GET {base}/models", "status": status}
    parsed = try_parse_json(body)
    if status == 200 and isinstance(parsed, dict):
        ids = [m.get("id") for m in parsed.get("data", []) if isinstance(m, dict)]
        ids = [i for i in ids if i]
        result.update(ok=True, model_count=len(ids), models_first10=ids[:10])
    else:
        result.update(ok=False, body_excerpt=mask(body[:300], key))
    return result


def check_chat(base: str, key: str, model: str, messages: list, name: str) -> dict:
    payload = {"model": model, "messages": messages, "max_tokens": 64}
    status, body = http_json(
        "POST",
        f"{base}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        payload,
    )
    result = {
        "name": name,
        "endpoint": f"POST {base}/chat/completions",
        "model": model,
        "status": status,
    }
    parsed = try_parse_json(body)
    if status == 200 and isinstance(parsed, dict):
        content = extract_chat_content(parsed)
        result.update(ok=True, reply_first100=mask(content[:100], key))
    else:
        result.update(ok=False, body_excerpt=mask(body[:300], key))
    return result


def check_vision(base: str, key: str) -> dict:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这张图片是什么颜色?用一个词回答。"},
                {"type": "image_url", "image_url": {"url": RED_PNG_DATA_URI}},
            ],
        }
    ]
    result = check_chat(base, key, "gpt-5.5", messages, "vision")
    reply = result.get("reply_first100", "")
    result["vision_verdict"] = (
        "mentions-red" if ("红" in reply or "red" in reply.lower()) else "unclear"
    ) if result.get("ok") else "untested"
    return result


def check_anthropic(base: str, key: str) -> dict:
    payload = {
        "model": ANTHROPIC_PROBE_MODEL,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "用一个字回答:好"}],
    }
    status, body = http_json(
        "POST",
        f"{base}/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        payload,
    )
    result = {
        "name": "anthropic_messages",
        "endpoint": f"POST {base}/messages",
        "model": ANTHROPIC_PROBE_MODEL,
        "status": status,
    }
    parsed = try_parse_json(body)
    if status == 200 and isinstance(parsed, dict):
        text = "".join(
            b.get("text", "") for b in parsed.get("content", []) if isinstance(b, dict)
        )
        result.update(ok=True, reply_first100=mask(text[:100], key))
    else:
        result.update(ok=False, body_excerpt=mask(body[:300], key))
    return result


def looks_unsupported(check: dict) -> bool:
    if check.get("status") in (404, 405, 501):
        return True
    if check.get("status") is None:  # connection-level failure
        return True
    body = (check.get("body_excerpt") or "").lower()
    return "not found" in body and not check.get("ok")


def main() -> int:
    env = load_env(ENV_PATH)
    api_key = env.get("AGENTROUTER_API_KEY", "").strip()
    base = env.get("AGENTROUTER_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")

    results = {
        "probe_time_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "env_file": str(ENV_PATH),
        "env_file_found": ENV_PATH.exists(),
        "api_key_present": bool(api_key),
        "checks": [],
        "dialect": "unknown",
    }

    if not api_key:
        results["error"] = "AGENTROUTER_API_KEY not found in .env"
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[probe] ERROR: AGENTROUTER_API_KEY not found in {ENV_PATH}")
        print(f"[probe] wrote {OUT_PATH}")
        return 2

    # check 1: models
    c1 = check_models(base, api_key)
    results["checks"].append(c1)
    if c1.get("ok"):
        print(f"[1/4] GET /models -> 200, {c1['model_count']} models; first: {c1['models_first10']}")
    else:
        print(f"[1/4] GET /models -> status={c1.get('status')} FAIL: {c1.get('body_excerpt', '')[:120]}")

    # check 2: text chat (glm-5.2)
    c2 = check_chat(
        base, api_key, "glm-5.2",
        [{"role": "user", "content": "用一个字回答:好"}],
        "chat_text",
    )
    results["checks"].append(c2)
    if c2.get("ok"):
        print(f"[2/4] chat glm-5.2 -> 200, reply: {c2['reply_first100']!r}")
    else:
        print(f"[2/4] chat glm-5.2 -> status={c2.get('status')} FAIL: {c2.get('body_excerpt', '')[:120]}")

    # check 3: vision (gpt-5.5 + red pixel)
    c3 = check_vision(base, api_key)
    results["checks"].append(c3)
    if c3.get("ok"):
        print(f"[3/4] vision gpt-5.5 -> 200, reply: {c3['reply_first100']!r} ({c3['vision_verdict']})")
    else:
        print(f"[3/4] vision gpt-5.5 -> status={c3.get('status')} FAIL: {c3.get('body_excerpt', '')[:120]}")

    # check 4: anthropic dialect, only if checks 1+2 both look unsupported
    if looks_unsupported(c1) and looks_unsupported(c2):
        c4 = check_anthropic(base, api_key)
        results["checks"].append(c4)
        if c4.get("ok"):
            print(f"[4/4] anthropic /messages -> 200, reply: {c4['reply_first100']!r}")
        else:
            print(f"[4/4] anthropic /messages -> status={c4.get('status')} FAIL: {c4.get('body_excerpt', '')[:120]}")
    else:
        c4 = None
        print("[4/4] anthropic /messages -> skipped (OpenAI dialect responded)")

    # dialect verdict
    if c1.get("ok") or c2.get("ok"):
        results["dialect"] = "openai"
    elif c4 and c4.get("ok"):
        results["dialect"] = "anthropic"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[probe] dialect={results['dialect']}  vision={c3.get('vision_verdict')}")
    print(f"[probe] wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
