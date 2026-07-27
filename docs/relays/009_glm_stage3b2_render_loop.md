# Relay 009 · GLM 5.2:阶段 3b2 render_loop 接线 + clarify 补丁

用法:整段代码块贴给 GLM 5.2,完成后告诉主会话「009 完成」。

```text
你是 openBIMAgent 项目的实施工程师(GLM 5.2)。项目根:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent(Git Bash,Python 一律用 `uv run`)。任务:M0 阶段 3b2——把 Blender 精检环接到已验收的 blender-mcp fork 上,并修一个 clarify 的 bug。测试基线 125 passed,完工必须全绿。

# 必读(先读再动手)

- src/openbimagent/vision/render_loop.py(骨架 TODO)、src/openbimagent/vision/{rubric.py,critic.py,scad_loop.py,html_report.py}(已实现)
- src/openbimagent/mcp_clients/blender.py(占位)
- mcp_servers/blender_mcp/FORK_NOTES.md(fork 八项改造落点、socket 协议、describe_capabilities)与 mcp_servers/blender_mcp/tests/(真实测试客户端范例)——fork 已验收冻结,一个字节不许改
- relay_workspace/m0_spikes/fork_test_report.md(10/10 PASS)
- docs/architecture/ARCHITECTURE.md §3(双环/防放水五件套)、§6.5(每批 HTML 验收页)
- src/openbimagent/planner/instantiate.py 的 `_load_frontmatter`(PyYAML `?` 怪癖的引号修补回退)
- src/openbimagent/clarify/slots.py(有同款 bug 未修)

# 任务(只允许改:src/openbimagent/mcp_clients/、src/openbimagent/vision/render_loop.py、src/openbimagent/clarify/、tests/、relay_workspace/m0_spikes/blender_spike.md)

1. **mcp_clients/blender.py 真实客户端**:优先 MCP stdio 握手(fastmcp client 连 fork server,stdio 端到端从未测过,你做第一轮);跑不通就回退直连 addon socket(端口 9887,协议见 fork tests),并把取舍原因写在模块 docstring。统一接口:connect / health_check / describe_capabilities / execute_code / set_editable_scope / screenshot_or_render / batch_render / turntable。
2. **render_loop.py 实现**:批次流程——**每批先 set_editable_scope(范围锁,fork 默认解锁,必须显式设)** → execute_code 建模(代码由注入的 builder_fn 产出) → screenshot_or_render 自检图 + batch_render/turntable 验收图 → VLMCritic(role=critic_render,六维)评分 → 未达阈值:把 actionable_rework_command 交给 builder_fn 重改(最多 max_iters) → 收敛判定复用 scad_loop 的四选一 + best-so-far(快照由 fork 快照机制承载)。每批结束调 write_html_report 出 HTML 验收页;screenshot/score/patch/snapshot 事件落 SessionStore。
3. **clarify PyYAML 补丁**:clarify/slots.py 的 frontmatter 解析加与 planner `_load_frontmatter` 一致的「直解失败→引号修补回退」(值逐字保留),并补一个用真实 `domain_packs/single_asset_hero/playbook.md` 加载的回归测试(当前必炸)。
4. **blender_spike.md 补记一小节**:fork 发现的新坑——background 下 bl_rna 动态枚举漏报 addon 注册引擎,需赋值试探。
5. **测试**:socket/MCP 全 mock 的单测(范围锁被调用、rework 循环收敛、HTML 页生成、事件链)+ 真实 Blender 集成测试 1 例(headless 起 fork addon,connect → describe_capabilities → 建方块 → 截图非黑,带 skipif 守卫)+ clarify 回归测试。VLMCritic 一律 mock,禁真实 LLM 请求。

# 验收与交接(入库检查单)

完成后必须交回:① `uv run python -m compileall src/ tests/ -q`、`uv run pytest -q`、`uv run ruff check src/ tests/` 三条的原始输出;② 改动文件清单;③ MCP stdio vs socket 取舍结论;④ 需注意问题;⑤ 入库检查单(完成项/未完成项/建议下一步)。
禁止:改 mcp_servers/、docs/、domain_packs/、config/、tools/、agents/、src 其他包;测试发真实 LLM 请求;打印任何 key。
```
