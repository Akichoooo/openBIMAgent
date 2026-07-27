# Relay 010 · GLM 5.2:阶段 4 端到端组装(CLI + HITL + 真实 builder)

用法:等「009 完成」且主会话评审通过后,整段代码块贴给 GLM 5.2,完成后告诉主会话「010 完成」。

```text
你是 openBIMAgent 项目的实施工程师(GLM 5.2)。项目根:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent(Git Bash,Python 一律 `uv run`)。任务:M0 阶段 4 组装——把已实现的模块串成可用的 CLI 产品。前置:阶段 3b2 已完成(mcp_clients/blender.py 与 render_loop.py 可用)。测试基线以 009 完成后为准(约 140+),完工必须全绿。

# 必读

- docs/architecture/M0_PLAN.md(六道验收)、ARCHITECTURE.md §2(生命周期)/§6.5(HITL 基座/命令集)、COMPONENTS.md §2
- src/openbimagent/(core/loop.py、clarify/slots.py、planner/instantiate.py、orchestrator/dispatch.py、vision/{scad_loop,render_loop,critic,html_report}.py、deliver/gate.py、session/store.py、providers/registry.py)
- domain_packs/single_asset_hero/playbook.md、agents/*.md

# 任务(只允许改:src/openbimagent/(新增 cli.py、__main__.py,以及 assembly/ 或 core/ 内必要的装配代码)、tests/、README.md 的使用说明一节)

1. **装配层**(新模块,如 src/openbimagent/assembly/):把任务生命周期串起来——
   load playbook → clarify(CLI 一问一答,input 真实交互) → planner.instantiate(registry 真实,LLM 生成语义 IR,失败回退确定性模板) → schema_gate → orchestrator.run_plan(agent_fn=真实批次执行器) → 批次执行器 = builder(modeler LLM 产出 bpy 代码,经 mcp_clients 执行;FIX 时按 rework 指令重改) + scad_loop/render_loop 双环 → deliver 门禁 → 输出交付清单。
   builder 的 LLM 调用走 providers registry(role="modeler"),所有 LLM 调用可注入替换(测试全 mock)。
2. **CLI**(`uv run python -m openbimagent` 或 `openbimagent` 入口):
   - `run --playbook domain_packs/single_asset_hero/playbook.md`:跑全流程;
   - HITL 命令:/sessions(多会话列表)/`/tree <id>`(回退)/`/undo` `/redo` `/retry` `/compact` `/model` `/export`;Ctrl+C 中断 → 落 checkpoint 事件 → 可续跑;
   - 审批门:MCP 写操作/execute_code/deliver 前 CLI 确认(y/N,可 --yes 跳过);
   - 每批结束打印 HTML 验收页路径。
3. **README.md**:补「快速开始」一节(uv sync、.env、run 命令、HITL 命令表),不改其他章节。
4. **测试**(全 mock,禁真实 LLM/Blender):装配链路状态机(追问→规划→门禁→编排→双环→交付)用 fake registry/fake client/fake critic 走通;CLI 命令解析;中断 checkpoint;审批门 y/N。

# 验收与交接(入库检查单)

① compileall/pytest/ruff 三条原始输出;② 改动文件清单;③ 装配层设计要点(10 行内);④ 需注意问题;⑤ 入库检查单。
禁止:改 mcp_servers/、docs/、domain_packs/、config/、tools/、agents/;测试发真实 LLM 请求或连真实 Blender;打印任何 key。
```
