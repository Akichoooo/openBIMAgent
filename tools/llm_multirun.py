"""LLM-Direct 基线多 run 方差测量（论文用：均值 ± 标准差）。

跑法：OPENBIMAGENT_RUN_LLM_BASELINE=1 .venv/Scripts/python.exe tools/llm_multirun.py [runs]
默认 3 个独立 run；结果打印并落盘 benchmark_results/llm_multirun_<date>.json。
注意：真实网络调用（config/llm_baseline.local.toml 已配 key 时才生效）。
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from openbimagent.benchmark.llm_direct_baseline import run_llm_direct_baseline

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "benchmark_results"


def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    rows = []
    for i in range(1, runs + 1):
        print(f"=== run {i}/{runs} ===", flush=True)
        row = run_llm_direct_baseline()
        if row is None:
            raise SystemExit("LLM 基线未配置（缺 config 或 key 为占位符）")
        if "402" in str(row.provenance) or row.avg_latency_ms == 0:
            raise SystemExit("疑似配额/网络故障（全部调用失败），结果不可用于论文，请检查 key 余额")
        d = asdict(row) if is_dataclass(row) else (row.model_dump() if hasattr(row, "model_dump") else dict(row))
        rows.append(d)
        print(json.dumps(d, ensure_ascii=False, default=str), flush=True)

    def mean_std(key: str) -> tuple[float, float]:
        vals = [float(r[key]) for r in rows]
        return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)

    summary = {}
    for key in ("topology_valid_rate", "rule_compliance_rate", "hydraulic_valid_rate", "avg_latency_ms", "avg_token_count"):
        m, s = mean_std(key)
        summary[key] = {"mean": round(m, 2), "std": round(s, 2), "values": [round(float(r[key]), 2) for r in rows]}
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"llm_multirun_{datetime.now():%Y%m%d_%H%M}.json"
    out.write_text(
        json.dumps({"runs": runs, "model": rows[0].get("method_name", "?"), "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n=== 汇总 (mean ± std, n=%d) ===" % runs)
    for k, v in summary.items():
        print(f"{k}: {v['mean']} ± {v['std']}  {v['values']}")
    print("落盘:", out)


if __name__ == "__main__":
    main()
