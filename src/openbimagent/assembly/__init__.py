"""装配层:把任务生命周期串成可用 CLI 产品(M0 阶段4)。

对应文档:
- docs/architecture/M0_PLAN.md 阶段 4(端到端 + HITL)
- docs/architecture/ARCHITECTURE.md §2 一次任务的完整生命周期、§6.5 HITL 基座
- docs/architecture/COMPONENTS.md §2 Agent Core 内部模块规格

装配链路(ARCH §2):
  load playbook → clarify(CLI 一问一答) → planner.instantiate(registry 真实 / 模板回退)
  → schema_gate → orchestrator.run_plan(agent_fn=真实批次执行器)
  → 批次执行器 = builder(modeler LLM → bpy 代码,经 mcp_clients 执行) + scad_loop/render_loop 双环
  → deliver 门禁 → 输出交付清单。

所有 LLM 调用走 providers registry(role=modeler/planner/critic_*),可注入替换(测试全 mock)。
MCP 写操作 / execute_code / deliver 前 CLI 审批(y/N,--yes 跳过);Ctrl+C 落 checkpoint 可续跑。
"""

from openbimagent.assembly.batch_executor import make_batch_executor
from openbimagent.assembly.builder import BuilderError, make_builder_fn
from openbimagent.assembly.pipeline import PipelineResult, run_pipeline

__all__ = [
    "BuilderError",
    "PipelineResult",
    "make_batch_executor",
    "make_builder_fn",
    "run_pipeline",
]
