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

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm_baseline.local.toml"
MIN_COVER_M = 0.70  # GB 50289 车行道最小覆土
DIAMETER_M = 0.30  # DN300
MANNING_N = 0.013  # 混凝土糙率
MIN_VELOCITY = 0.60  # 最小防淤流速
MIN_SLOPE = 0.003  # DN300 最小设计坡度
_PLACEHOLDER_KEY = "sk-replace-me"


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
    """加载本地 LLM 基线配置；文件缺失或 key 仍为占位值时返回 None（回落占位行）。"""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        return None
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("LLM 基线配置解析失败: %s", exc)
        return None
    api_key = str(data.get("api_key", "")).strip()
    if not api_key or api_key.startswith(_PLACEHOLDER_KEY):
        return None
    return LLMBaselineConfig(
        base_url=str(data["base_url"]).rstrip("/"),
        model=str(data["model"]),
        api_key=api_key,
        max_scenarios=int(data.get("max_scenarios", 10)),
        repetitions=int(data.get("repetitions", 3)),
        temperature=float(data.get("temperature", 0.0)),
        request_timeout_s=int(data.get("request_timeout_s", 60)),
    )


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
) -> tuple[str, float, int]:
    """调用 OpenAI 兼容 /chat/completions，返回 (content, latency_ms, total_tokens)。"""
    url = f"{config.base_url}/chat/completions"
    body = {"model": config.model, "messages": messages, "temperature": config.temperature}
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    if post_fn is None:
        with httpx.Client(timeout=config.request_timeout_s) as client:
            resp = client.post(url, json=body, headers=headers)
    else:
        resp = post_fn(url, json=body, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    latency_ms = (time.perf_counter() - t0) * 1000.0
    content = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens", 0))
    return content, latency_ms, tokens


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
) -> MethodBenchmarkMetrics | None:
    """执行 LLM-Direct 真实基线评测，返回实测指标行；未配置时返回 None。

    网络调用通过可注入的 post_fn 实现可测试性；生产路径使用 httpx。
    每场景重复多次取多数判定（temperature=0 时通常一致）。
    """
    cfg = config if config is not None else load_llm_baseline_config()
    if cfg is None:
        return None

    registry = {s.scenario_id: s for s in build_benchmark_scenarios()}
    selected = tuple(scenarios) if scenarios else tuple(f"B{i}" for i in range(1, cfg.max_scenarios + 1))
    measured = len(selected)
    top_ok = rule_ok = hyd_ok = 0
    total_latency = 0.0
    total_tokens = 0
    calls = 0

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

    return MethodBenchmarkMetrics(
        method_name=f"LLM-Direct Prompting ({cfg.model})",
        total_cases=measured,
        topology_valid_rate=round(top_ok / max(1, measured) * 100.0, 1),
        rule_compliance_rate=round(rule_ok / max(1, measured) * 100.0, 1),
        hydraulic_valid_rate=round(hyd_ok / max(1, measured) * 100.0, 1),
        avg_latency_ms=round(total_latency / max(1, calls), 1) if calls else 0.0,
        avg_tool_calls=round(calls / max(1, measured), 1) if measured else 1.0,
        avg_token_count=round(total_tokens / max(1, measured)) if measured else 0,
        measured=True,
        provenance=(
            f"LLM-Direct 真实调用 {cfg.base_url} model={cfg.model} "
            f"(repetitions={cfg.repetitions})：拓扑口径=LLM 输出逐节点标高可解析，"
            "合规口径=覆土0.70m+坡度0.003+流向单调，水力口径=Manning 防淤流速≥0.60m/s；"
            "同口径几何规则评测，无求解器矩阵 / 自愈介入。"
        ),
    )
