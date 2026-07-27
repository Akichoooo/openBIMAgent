# Relay 011 · GLM 5.2:M0 真实端到端冒烟(售货机)

用法:等「010 完成」且主会话评审通过后,整段代码块贴给 GLM 5.2,完成后告诉主会话「011 完成」。这是唯一允许动真实 LLM 与真实 Blender 的接力任务。

```text
你是 openBIMAgent 项目的实施工程师(GLM 5.2)。项目根:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent(Git Bash,Python 一律 `uv run`)。任务:M0 六道验收——用真实 agentrouter(profile=test)和真实 Blender 5.2,把 `domain_packs/single_asset_hero/playbook.md`(日式自动售货机)端到端跑通。前置:阶段 4 CLI 已可用。

# 环境

- `.env` 已有 AGENTROUTER_API_KEY(额度不高,省着用:失败重跑前先修因,不盲目重试);OPENBIMAGENT_PROFILE=test。
- Blender:D:\devloop\blender\blender.exe(5.2.0);OpenSCAD:C:\Program Files\OpenSCAD\openscad.exe。
- 角色绑定(test profile):modeler=claude-opus-4-8(写 bpy 代码)、critic=gpt-5.5、其余=glm-5.2-ar。

# 任务

1. 起 headless Blender + fork addon(方式见 mcp_servers/blender_mcp/FORK_NOTES.md)。
2. `uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md`,追问环节:asset=一台日式自动售货机,style=江户x赛博,wear_level=6,其余接受默认。
3. 跑完后按 M0_PLAN.md 的六道验收逐项核对并取证:
   a. 追问一问一答可用(截图/日志);
   b. .blend 生成 + HTML 验收页含六维评分(记录分数;若 <8,记录返工轨迹与最终分);
   c. (若可操作)中断-续跑演示;
   d. /tree 回退演示;
   e. session JSONL 五类事件齐全(写个小脚本统计 type/customType 分布);
   f. 全程 agentrouter 跑通,token 消耗统计(从 session 事件或 usage 汇总)。
4. 产物集中放 forge_sessions/ 或 session artifacts 目录,并写一份《M0 冒烟报告》到 relay_workspace/m0_smoke/report.md:六道验收逐项 PASS/FAIL + 证据路径 + token 统计 + 发现的问题清单。
5. 若中途失败:先诊断修因(允许小修 src/ 里的装配代码,不许动 mcp_servers/ 与 docs/),每次修复记录进报告;连续 3 次卡同一问题就停手,把现象写清楚交回。

# 交接(入库检查单)

① 冒烟报告路径与六道验收结论;② HTML 验收页与 .blend 路径;③ token 消耗统计;④ 修复记录(若有);⑤ 未完成项与建议。
禁止:改 mcp_servers/、docs/、domain_packs/、config/;打印任何 key;无诊断盲目重跑(额度有限)。
```
