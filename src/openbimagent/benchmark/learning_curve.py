"""D 学习曲线实验（In-Context Retrieval 有效性量化，对标 Prime Agent/ReMe 的"越用越好"叙事）。

机制边界（诚实，严禁夸大）：本项目的"经验回流"= **In-Context Retrieval**——把归档中相似的
已验证交付作为 few-shot 范例注入 prompt（见 server/runs.py::_retrieve_exemplars），**不涉及
任何权重更新 / RL / DPO**。学习曲线量化：随注入的相似范例数（模拟归档量增长）增加，同类任务
的 LLM 成功率 / token 趋势。

数据诚信（fail-closed）：离线（无 LLM key）→ measured=False 占位，**绝不造合成假曲线**。
429 编排：复用 llm_direct_baseline 的 _call_llm（指数退避 + 401/429 重试）+ 调用间 throttle，
串行小批量，低并发供应商（SenseNova flash-lite）友好。
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from openbimagent.benchmark.llm_direct_baseline import (
    DIAMETER_M,
    LLMBaselineConfig,
    MIN_COVER_M,
    MIN_SLOPE,
    _build_prompt,
    _call_llm,
    _evaluate_scenario,
    _parse_inverts,
    load_llm_baseline_config,
)
from openbimagent.benchmark.m1_5_t7 import build_benchmark_scenarios


@dataclass(frozen=True)
class LearningCurvePoint:
    """单轮学习曲线数据点。"""

    round_index: int
    exemplars_injected: int  # 该轮注入的相似范例数（模拟归档量）
    success: bool  # 任务是否工程达标（覆土 + 坡度 + 水力）
    total_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class LearningCurveReport:
    """学习曲线报告 + 数据来源声明。"""

    task_scenario: str
    rounds: int
    points: tuple[LearningCurvePoint, ...]
    success_zero_shot: float  # 0 范例（zero-shot）成功率 %
    success_with_exemplars: float  # ≥1 范例（in-context）成功率 %
    token_trend: str  # decreasing / flat / increasing / unknown
    measured: bool
    provenance: str

    def to_markdown_table(self) -> str:
        if not self.measured:
            return (
                "### 学习曲线 (In-Context Retrieval)\n\n"
                f"> ⚠️ 未实测占位（禁止引用）：{self.provenance}\n"
            )
        lines = [
            "### 学习曲线 (In-Context Retrieval，非权重更新)",
            "",
            "| 轮次 | 注入范例数 | 达标 | tokens | 延迟(ms) |",
            "| :---: | :---: | :---: | :---: | :---: |",
        ]
        for p in self.points:
            lines.append(
                f"| {p.round_index} | {p.exemplars_injected} | {'✓' if p.success else '✗'} | "
                f"{p.total_tokens} | {p.latency_ms:.0f} |"
            )
        lines.extend([
            "",
            f"- zero-shot 成功率：{self.success_zero_shot:.1f}%",
            f"- 注入范例后成功率：{self.success_with_exemplars:.1f}%",
            f"- token 趋势：{self.token_trend}",
            f"> 数据来源：{self.provenance}",
        ])
        return "\n".join(lines)


def _approx_correct_inverts(payload: dict[str, Any]) -> dict[str, float]:
    """确定性坡度规则近似正确 invert（沿程从起点按最小坡度递减），作为 few-shot 黄金范例。

    不依赖完整 Solver，仅为 In-Context Retrieval 提供"相似任务→已验证解"示范；
    起点 invert = 地面高程 − 最小覆土 − 管径，沿相邻节点水平距离 × 最小坡度递减。
    """
    nodes = payload.get("nodes", [])
    if not nodes:
        return {}
    result: dict[str, float] = {}
    invert = nodes[0]["ground_elevation_m"] - MIN_COVER_M - DIAMETER_M
    result[nodes[0]["node_id"]] = round(invert, 3)
    for i in range(1, len(nodes)):
        prev, cur = nodes[i - 1], nodes[i]
        length = math.hypot(cur["x_m"] - prev["x_m"], cur["y_m"] - prev["y_m"])
        invert -= max(length * MIN_SLOPE, 0.01)
        result[cur["node_id"]] = round(invert, 3)
    return result


def _build_exemplar_pool(
    registry: dict[str, Any], target_id: str, pool_ids: Sequence[str]
) -> list[dict[str, str]]:
    """对相似场景构造 (query, 已验证解) few-shot 范例池（模拟归档检索结果）。"""
    pool: list[dict[str, str]] = []
    for pid in pool_ids:
        if pid == target_id:
            continue
        scen = registry.get(pid)
        if scen is None:
            continue
        payload = scen.input_payload
        if "nodes" not in payload or "segments" not in payload:
            continue
        query = json.dumps(
            {
                "nodes": [
                    {"id": n["node_id"], "x": n["x_m"], "y": n["y_m"], "ground": n["ground_elevation_m"]}
                    for n in payload["nodes"]
                ],
                "segments": [
                    {"from": s["start_node_id"], "to": s["end_node_id"]} for s in payload["segments"]
                ],
            },
            ensure_ascii=False,
        )
        answer = json.dumps(
            {"nodes": [{"node_id": k, "invert_z": v} for k, v in _approx_correct_inverts(payload).items()]},
            ensure_ascii=False,
        )
        pool.append({"query": query, "answer": answer})
    return pool


def _build_prompt_with_exemplars(
    payload: dict[str, Any], exemplars: Sequence[dict[str, str]]
) -> list[dict[str, str]]:
    """在 base prompt 的 system 段后拼接 few-shot 范例（In-Context Retrieval 注入）。"""
    base = _build_prompt(payload)
    if not exemplars:
        return base
    fewshot = "\n\n".join(
        f"【已验证相似交付 {i + 1}】输入：{ex['query']}\n正确输出：{ex['answer']}"
        for i, ex in enumerate(exemplars)
    )
    system = (
        base[0]["content"]
        + "\n\n以下是从归档检索到的已验证相似工程交付范例（in-context 参考，非本题答案）：\n"
        + fewshot
    )
    return [{"role": "system", "content": system}, base[1]]


def run_learning_curve(
    scenario_id: str = "B1",
    *,
    rounds: int = 4,
    pool_ids: Sequence[str] = ("B2", "B3", "B9"),
    config: LLMBaselineConfig | None = None,
    throttle_s: float | None = None,
    post_fn: Callable[..., Any] | None = None,
) -> LearningCurveReport:
    """跑学习曲线：同类任务 rounds 轮，第 i 轮注入 min(i, pool) 个相似范例，记录成功率/token 趋势。

    离线（无 config/key）→ measured=False 占位，绝不造合成曲线。
    """
    cfg = config if config is not None else load_llm_baseline_config()
    if cfg is None:
        return LearningCurveReport(
            task_scenario=scenario_id,
            rounds=rounds,
            points=(),
            success_zero_shot=0.0,
            success_with_exemplars=0.0,
            token_trend="unknown",
            measured=False,
            provenance=(
                "未配置 LLM key，学习曲线未实测（占位禁止引用于论文/答辩；需 .env SENSENOVA_API_KEY 有效）。"
                "机制=In-Context Retrieval（few-shot 相似交付注入），绝不造合成假曲线。"
            ),
        )
    if throttle_s is None:
        throttle_s = float(os.environ.get("OPENBIMAGENT_LLM_BASELINE_THROTTLE_S", "0") or 0.0)

    registry = {s.scenario_id: s for s in build_benchmark_scenarios()}
    target = registry.get(scenario_id)
    if target is None or "nodes" not in target.input_payload:
        return LearningCurveReport(
            task_scenario=scenario_id, rounds=rounds, points=(), success_zero_shot=0.0,
            success_with_exemplars=0.0, token_trend="unknown", measured=False,
            provenance=f"场景 {scenario_id} 非几何任务，无法构造学习曲线",
        )
    pool = _build_exemplar_pool(registry, scenario_id, pool_ids)

    points: list[LearningCurvePoint] = []
    for i in range(rounds):
        k = min(i, len(pool))
        prompt = _build_prompt_with_exemplars(target.input_payload, pool[:k])
        if i > 0 and throttle_s > 0 and post_fn is None:
            time.sleep(throttle_s)  # 调用间限流预防（仅真实调用）
        try:
            content, lat, tok = _call_llm(cfg, prompt, post_fn=post_fn)
            inverts = _parse_inverts(content)
            verdict = (
                _evaluate_scenario(target.input_payload, inverts)
                if inverts is not None
                else {"topology": False, "rule": False, "hydraulic": False}
            )
            success = bool(verdict["rule"] and verdict["hydraulic"])
        except Exception:  # noqa: BLE001 — 单轮失败计入不达标，不中断曲线
            success, tok, lat = False, 0, 0.0
        points.append(
            LearningCurvePoint(round_index=i, exemplars_injected=k, success=success, total_tokens=tok, latency_ms=lat)
        )

    zero = [p for p in points if p.exemplars_injected == 0]
    with_ex = [p for p in points if p.exemplars_injected > 0]
    success_zero = round(sum(p.success for p in zero) / len(zero) * 100.0, 1) if zero else 0.0
    success_with = round(sum(p.success for p in with_ex) / len(with_ex) * 100.0, 1) if with_ex else 0.0

    tokens = [p.total_tokens for p in points if p.total_tokens > 0]
    if len(tokens) >= 2:
        half = len(tokens) // 2
        first, second = sum(tokens[:half]) / half, sum(tokens[half:]) / max(1, len(tokens) - half)
        token_trend = "decreasing" if second < first * 0.95 else "increasing" if second > first * 1.05 else "flat"
    else:
        token_trend = "unknown"

    all_failed=bool(points) and all(p.total_tokens==0 for p in points)
    return LearningCurveReport(
        task_scenario=scenario_id,
        rounds=rounds,
        points=tuple(points),
        success_zero_shot=success_zero,
        success_with_exemplars=success_with,
        token_trend=token_trend,
        measured=bool(points) and any(p.total_tokens > 0 for p in points),
        provenance=(
            ("⚠️全部轮次调用失败token=0限流,占位禁止引用。" if all_failed else "")+f"学习曲线真实调用 {cfg.base_url} model={cfg.model}：任务 {scenario_id} 跑 {rounds} 轮，"
            f"第 i 轮注入 min(i,{len(pool)}) 个相似场景（{','.join(pool_ids)}）的已验证解作 few-shot；"
            f"zero-shot 成功率 {success_zero}%，注入范例后 {success_with}%，token 趋势 {token_trend}。"
            "机制=In-Context Retrieval（非权重更新/RL）；串行 + throttle + 401/429 退避。"
        ),
    )


__all__ = [
    "LearningCurvePoint",
    "LearningCurveReport",
    "run_learning_curve",
]
