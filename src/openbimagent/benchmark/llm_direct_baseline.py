"""LLM-Direct Prompting 真实基线 (对标神经-符号架构的纯 LLM 直出对照)。

对每个 B 场景构造 zero-shot prompt：喂节点坐标 / 管段 / GB 50289 约束，
要求 LLM 直接输出逐节点管底标高 JSON；再用项目同口径几何与水力规则评测
（覆土 / 坡度方向 / Manning 防淤流速）。配置走 gitignored 本地文件，
key 绝不入库、绝不打印；未配置时返回 None，调用方回落占位行。

安全边界：本模块会向外部 LLM 端点发送场景数据（节点坐标等工程输入）。
仅在本地受信任配置存在且显式 opt-in 时才发起真实网络调用。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from openbimagent.benchmark.academic_bench import MethodBenchmarkMetrics
from openbimagent.benchmark.m1_5_t7 import build_benchmark_scenarios
from openbimagent.providers.dialects import reasoning_payload

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm_baseline.local.toml"
MIN_COVER_M = 0.70  # GB 50289 车行道最小覆土
DIAMETER_M = 0.30  # DN300
MANNING_N = 0.013  # 混凝土糙率
MIN_VELOCITY = 0.60  # 最小防淤流速
MIN_SLOPE = 0.003  # DN300 最小设计坡度
_PLACEHOLDER_KEY = "sk-replace-me"

# 429/限流编排常量：低并发供应商（SenseNova flash-lite 等）易限流，串行 + 指数退避重试。
# 401 纳入：SenseNova 免费额度超速率限制时返回 401（非标准 429）——单次成功、连续快速调用即 401
# 是其典型特征；key 有效性由首次成功调用佐证，故对 401 也做长退避重试而非立即判失败。
# 524 纳入：freetokenfaucet 等网关对 reasoning 模型长 prompt 有 ~15s origin 超时（524），
# 配合 stream=True 首 token 快速建连即可绕过；非流式偶发 524 亦做退避重试。
_RETRYABLE_STATUS = frozenset({401, 429, 500, 502, 503, 504, 524})
_MAX_RETRIES = 5
_BACKOFF_BASE_S = 1.5
_BACKOFF_CAP_S = 30.0
# reasoning 模型（mimo-v2.5）长工程 prompt 需大 max_tokens 让 thinking 跑完并吐出 content 答案；
# 过小会被 reasoning 截断导致 content 空。stream 首 token <2s，总时长可达 ~110s。
_MAX_TOKENS = 6000


def _retry_after_seconds(resp: Any, attempt: int) -> float:
    """退避时长：优先 Retry-After 头，否则指数退避 1.5×2^n，封顶 30s。"""
    header = None
    try:
        header = resp.headers.get("retry-after") if resp is not None and hasattr(resp, "headers") else None
    except Exception:  # noqa: BLE001 — 头解析失败不影响退避
        header = None
    if header:
        try:
            return min(float(header), _BACKOFF_CAP_S)
        except (TypeError, ValueError):
            pass
    return min(_BACKOFF_BASE_S * (2**attempt), _BACKOFF_CAP_S)


@dataclass(frozen=True)
class LLMBaselineConfig:
    base_url: str
    model: str
    api_key: str
    max_scenarios: int = 10
    repetitions: int = 3
    temperature: float = 0.0
    request_timeout_s: int = 60


def load_llm_baseline_config(path: Path | str | None = None) -> LLMBaselineConfig | None:
    """加载 LLM 基线配置。优先级：本地 toml（显式）> 环境变量（统一 .env 的 key）。

    文件缺失/key 占位且无环境变量时返回 None（调用方回落占位行）。环境变量回落让用户
    只在 .env 填一次 SENSENOVA_API_KEY 即可同时驱动 A4/D/LLM 基线（key 绝不入库/打印）。
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            api_key = str(data.get("api_key", "")).strip()
            if api_key and not api_key.startswith(_PLACEHOLDER_KEY):
                return LLMBaselineConfig(
                    base_url=str(data["base_url"]).rstrip("/"),
                    model=str(data["model"]),
                    api_key=api_key,
                    max_scenarios=int(data.get("max_scenarios", 10)),
                    repetitions=int(data.get("repetitions", 3)),
                    temperature=float(data.get("temperature", 0.0)),
                    request_timeout_s=int(data.get("request_timeout_s", 60)),
                )
        except (tomllib.TOMLDecodeError, OSError, KeyError) as exc:
            logger.warning("LLM 基线配置解析失败: %s", exc)
    # 回落到环境变量（统一 .env：SENSENOVA_API_KEY 或 OPENBIMAGENT_LLM_BASELINE_KEY）
    env_key = (
        os.environ.get("OPENBIMAGENT_LLM_BASELINE_KEY")
        or os.environ.get("SENSENOVA_API_KEY")
        or ""
    ).strip()
    if env_key and not env_key.startswith(_PLACEHOLDER_KEY):
        return LLMBaselineConfig(
            base_url=os.environ.get(
                "OPENBIMAGENT_LLM_BASELINE_URL", "https://token.sensenova.cn/v1"
            ).rstrip("/"),
            model=os.environ.get("OPENBIMAGENT_LLM_BASELINE_MODEL", "sensenova-6.8-flash-lite"),
            api_key=env_key,
            max_scenarios=int(os.environ.get("OPENBIMAGENT_LLM_BASELINE_SCENARIOS", "10")),
            repetitions=int(os.environ.get("OPENBIMAGENT_LLM_BASELINE_REPS", "3")),
        )
    return None


def _build_prompt(payload: dict[str, Any]) -> list[dict[str, str]]:
    nodes = [
        {"id": n["node_id"], "x": n["x_m"], "y": n["y_m"], "ground": n["ground_elevation_m"]}
        for n in payload["nodes"]
    ]
    segments = [
        {"from": s["start_node_id"], "to": s["end_node_id"], "slope": s.get("design_slope", MIN_SLOPE)}
        for s in payload["segments"]
    ]
    system = (
        "你是市政管线工程师。为 DN300 混凝土重力污水管确定逐节点管底标高 invert_z。"
        "严格满足 GB 50289-2016：车行道最小覆土 0.70m（覆土=地面高程-invert_z-0.30），"
        "沿水流方向管底单调下降，相邻节点坡度不小于 0.003。"
        '只返回 JSON：{"nodes":[{"node_id":"...","invert_z":<float>}, ...]}，不要解释文字。'
    )
    user = json.dumps({"nodes": nodes, "segments": segments}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _call_llm(
    config: LLMBaselineConfig,
    messages: list[dict[str, str]],
    *,
    post_fn: Callable[..., Any] | None = None,
    max_retries: int = _MAX_RETRIES,
    reasoning_level: str | None = None,
) -> tuple[str, float, int]:
    """调用 OpenAI 兼容 /chat/completions，返回 (content, latency_ms, total_tokens)。

    429 编排（低并发供应商友好，如 SenseNova flash-lite）：对 429/5xx/超时/连接错
    做指数退避重试（优先 Retry-After 头，否则 1.5×2^n 封顶 30s），最多 max_retries 次；
    本模块串行调用（不并发），从源头避免触发限流。
    """
    url = f"{config.base_url}/chat/completions"
    # stream=True 绕过网关对 reasoning 模型的 ~15s origin 超时（524）：首 token <2s 建连，
    # 后续逐块返回（mimo-v2.5 长工程 prompt reasoning 总时长 ~110s 仍保持连接不断）。
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": _MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_level:
        body.update(reasoning_payload(config.model, reasoning_level))  # mimo thinking.type 等各家 wire
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            if post_fn is not None:
                # 测试注入路径（非 stream mock，向后兼容单测）
                resp = post_fn(url, json=body, headers=headers)
                status = getattr(resp, "status_code", 200)
                if status in _RETRYABLE_STATUS and attempt < max_retries - 1:
                    time.sleep(_retry_after_seconds(resp, attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                latency_ms = (time.perf_counter() - t0) * 1000.0
                content = data["choices"][0]["message"]["content"]
                tokens = int(data.get("usage", {}).get("total_tokens", 0))
                return content, latency_ms, tokens
            # 真实路径：流式累积 content（reasoning_content 是 thinking，不作为最终答案）
            content = ""
            tokens = 0
            with httpx.Client(timeout=config.request_timeout_s) as client:
                with client.stream("POST", url, json=body, headers=headers) as resp:
                    status = resp.status_code
                    if status in _RETRYABLE_STATUS and attempt < max_retries - 1:
                        resp.read()
                        time.sleep(_retry_after_seconds(resp, attempt))
                        continue
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        usage = chunk.get("usage")
                        if usage:
                            tokens = int(usage.get("total_tokens", tokens))
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            content += delta.get("content") or ""
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return content, latency_ms, tokens
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(_retry_after_seconds(None, attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM 调用重试耗尽")


def _parse_inverts(content: str) -> dict[str, float] | None:
    """从可能带 Markdown 围栏的响应中稳健提取逐节点管底标高。"""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    nodes = obj.get("nodes")
    if not isinstance(nodes, list):
        return None
    result: dict[str, float] = {}
    for item in nodes:
        if not isinstance(item, dict):
            return None
        nid = item.get("node_id")
        iz = item.get("invert_z")
        if nid is None or iz is None:
            return None
        try:
            result[str(nid)] = float(iz)
        except (TypeError, ValueError):
            return None
    return result or None


def _evaluate_scenario(payload: dict[str, Any], inverts: dict[str, float]) -> dict[str, bool]:
    """同口径评测：拓扑完整 + 覆土 + 坡度方向 + Manning 防淤流速。"""
    nodes = {n["node_id"]: n for n in payload["nodes"]}
    topology_ok = set(nodes).issubset(inverts)
    if not topology_ok:
        return {"topology": False, "rule": False, "hydraulic": False}

    rule_ok = True
    hydraulic_ok = True
    for seg in payload["segments"]:
        s = nodes[seg["start_node_id"]]
        e = nodes[seg["end_node_id"]]
        iz_s = inverts[seg["start_node_id"]]
        iz_e = inverts[seg["end_node_id"]]

        cover_s = s["ground_elevation_m"] - iz_s - DIAMETER_M
        cover_e = e["ground_elevation_m"] - iz_e - DIAMETER_M
        if min(cover_s, cover_e) < MIN_COVER_M - 1e-6:
            rule_ok = False

        length = math.hypot(e["x_m"] - s["x_m"], e["y_m"] - s["y_m"])
        drop = iz_s - iz_e  # 沿流向 (start→end) 管底应下降
        if length <= 0 or drop < -1e-6:
            rule_ok = False
            continue
        slope = drop / length
        if slope < seg.get("design_slope", MIN_SLOPE) - 1e-6:
            rule_ok = False
        velocity = (1.0 / MANNING_N) * (DIAMETER_M / 4.0) ** (2.0 / 3.0) * math.sqrt(max(slope, 0.0))
        if velocity < MIN_VELOCITY - 1e-6:
            hydraulic_ok = False

    return {"topology": True, "rule": rule_ok, "hydraulic": hydraulic_ok}


def run_llm_direct_baseline(
    scenarios: tuple[str, ...] | None = None,
    *,
    config: LLMBaselineConfig | None = None,
    post_fn: Callable[..., Any] | None = None,
    throttle_s: float | None = None,
) -> MethodBenchmarkMetrics | None:
    """执行 LLM-Direct 真实基线评测，返回实测指标行；未配置时返回 None。

    网络调用通过可注入的 post_fn 实现可测试性；生产路径使用 httpx。
    每场景重复多次取多数判定（temperature=0 时通常一致）。
    """
    cfg = config if config is not None else load_llm_baseline_config()
    if cfg is None:
        return None
    # 限流预防：真实调用间最小间隔（SenseNova 等低并发供应商超限返回 401/429）；
    # post_fn 注入（测试）时不 throttle。默认从 OPENBIMAGENT_LLM_BASELINE_THROTTLE_S 读。
    if throttle_s is None:
        throttle_s = float(os.environ.get("OPENBIMAGENT_LLM_BASELINE_THROTTLE_S", "0") or 0.0)

    registry = {s.scenario_id: s for s in build_benchmark_scenarios()}
    selected = tuple(scenarios) if scenarios else tuple(f"B{i}" for i in range(1, cfg.max_scenarios + 1))
    measured = len(selected)
    top_ok = rule_ok = hyd_ok = 0
    total_latency = 0.0
    total_tokens = 0
    calls = 0
    _first_call = True

    for sid in selected:
        scenario = registry.get(sid)
        if scenario is None:
            continue
        payload = deepcopy(scenario.input_payload)
        if "nodes" not in payload or "segments" not in payload:
            # 非几何场景（如 B8 规则歧义）：LLM-Direct 无法产出几何，计全不达标
            continue
        verdicts: list[dict[str, bool]] = []
        for _ in range(cfg.repetitions):
            if not _first_call and throttle_s > 0 and post_fn is None:
                time.sleep(throttle_s)  # 调用间限流预防（仅真实调用）
            _first_call = False
            try:
                content, lat, tokens = _call_llm(cfg, _build_prompt(payload), post_fn=post_fn)
                inverts = _parse_inverts(content)
                calls += 1
                total_latency += lat
                total_tokens += tokens
                verdicts.append(
                    _evaluate_scenario(payload, inverts)
                    if inverts is not None
                    else {"topology": False, "rule": False, "hydraulic": False}
                )
            except Exception as exc:  # noqa: BLE001 — 单次失败计入不达标, 不中断整批
                logger.warning("LLM-Direct 基线场景 %s 调用失败: %s", sid, exc)
                verdicts.append({"topology": False, "rule": False, "hydraulic": False})

        if verdicts:
            half = len(verdicts) / 2
            if sum(v["topology"] for v in verdicts) > half:
                top_ok += 1
            if sum(v["rule"] for v in verdicts) > half:
                rule_ok += 1
            if sum(v["hydraulic"] for v in verdicts) > half:
                hyd_ok += 1

    all_calls_failed = calls == 0
    return MethodBenchmarkMetrics(
        method_name=f"LLM-Direct Prompting ({cfg.model})",
        total_cases=measured,
        topology_valid_rate=round(top_ok / max(1, measured) * 100.0, 1),
        rule_compliance_rate=round(rule_ok / max(1, measured) * 100.0, 1),
        hydraulic_valid_rate=round(hyd_ok / max(1, measured) * 100.0, 1),
        avg_latency_ms=round(total_latency / max(1, calls), 1) if calls else 0.0,
        avg_tool_calls=round(calls / max(1, measured), 1) if measured else 1.0,
        avg_token_count=round(total_tokens / max(1, measured)) if measured else 0,
        measured=not all_calls_failed,
        provenance=(
            "⚠️ 全部 LLM 调用失败（限流 401/429 或认证/网络），未获得任何有效实测响应；"
            "本行占位（measured=False）禁止作为实验数据引用；需待供应商限流冷却后补跑。"
            if all_calls_failed
            else (
                f"LLM-Direct 真实调用 {cfg.base_url} model={cfg.model} "
                f"(repetitions={cfg.repetitions}, 成功解析 {calls} 次)：拓扑口径=LLM 输出逐节点标高可解析，"
                "合规口径=覆土0.70m+坡度0.003+流向单调，水力口径=Manning 防淤流速≥0.60m/s；"
                "同口径几何规则评测，无求解器矩阵 / 自愈介入。"
            )
        ),
    )
