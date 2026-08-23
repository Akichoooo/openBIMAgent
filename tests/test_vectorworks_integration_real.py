"""真实 Vectorworks 2024 集成测试（registry.invoke 全链路，带 skipif 守卫）。

跳过条件（任一即跳过，不报失败）：
- 环境变量 OPENBIMAGENT_RUN_REAL_VW=1 未设置（默认不跑）；
- 显式配置契约不完整（OPENBIMAGENT_VW_JOBS_DIR / RESULTS_DIR / AUTHORIZED_ROOT）；
- VW 宿主不可达（探测 jobs 目录无 runner 消费迹象时由用例内超时给出明确失败）。

前置（人工，一次性；runner 现用固定 IPC 根 D:/devloop/vw_ipc，心跳可探活）：
  1. 启动 Vectorworks 2024（从哪里启动都行，不依赖 CWD）；
  2. 在 VW 内运行 mcp_servers/vectorworks_mcp/runner.py —— VW UI 显示"未响应"是
     预期形态（死循环占住脚本线程但仍消费 jobs）；确认
     D:/devloop/vw_ipc
unner_heartbeat.json 存在且时间戳持续更新即存活；
  3. 导出环境变量后运行：
     export OPENBIMAGENT_VW_JOBS_DIR=D:/devloop/vw_ipc/jobs
     export OPENBIMAGENT_VW_RESULTS_DIR=D:/devloop/vw_ipc/results
     export OPENBIMAGENT_VW_AUTHORIZED_ROOT=D:/devloop/G6_Test
     OPENBIMAGENT_RUN_REAL_VW=1 uv run pytest tests/test_vectorworks_integration_real.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RUN_REAL_VW = os.environ.get("OPENBIMAGENT_RUN_REAL_VW") == "1"
_VW_ENV_COMPLETE = all(
    os.environ.get(name, "").strip()
    for name in (
        "OPENBIMAGENT_VW_JOBS_DIR",
        "OPENBIMAGENT_VW_RESULTS_DIR",
        "OPENBIMAGENT_VW_AUTHORIZED_ROOT",
    )
)

requires_real_vw = pytest.mark.skipif(
    not RUN_REAL_VW or not _VW_ENV_COMPLETE,
    reason=(
        f"跳过真实 VW 集成测试(OPENBIMAGENT_RUN_REAL_VW={RUN_REAL_VW}, "
        f"env 契约完整={_VW_ENV_COMPLETE})"
    ),
)


@requires_real_vw
def test_registry_invoke_vectorworks_execute_policy_gated(tmp_path: Path) -> None:
    """M3 验收：invoke 全链路——自愈求解 → prompt 策略 → VW 宿主 runner execute_plan。"""
    from openbimagent.benchmark.self_healing_ablation import build_demo_invocation
    from openbimagent.core.plugin import (
        PluginPolicyPromptRequiredError,
        create_default_plugin_registry,
    )

    registry = create_default_plugin_registry()
    solved = registry.invoke("solver:self_healing", **build_demo_invocation("SH-2"))
    assert solved.converged and solved.final_ir is not None

    # 默认治理：真机宿主写入无确认被拒
    with pytest.raises(PluginPolicyPromptRequiredError):
        registry.invoke("cad_host:vectorworks.execute", ir=solved.final_ir)

    root = Path(os.environ["OPENBIMAGENT_VW_AUTHORIZED_ROOT"])
    output = root / "m3_registry_e2e.vwx"
    receipt = registry.invoke(
        "cad_host:vectorworks.execute", ir=solved.final_ir, output_path=output, confirm=True
    )
    assert receipt["status"] == "completed"
    assert receipt["applied_operations"] >= 1
    assert output.is_file() and output.stat().st_size > 0
