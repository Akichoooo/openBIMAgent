# 多模型接力开发工作流(RELAY WORKFLOW)

目的:用 GLM / Gemini 承接重活杂活,节省主会话(Kimi)token;主会话只做架构决策、提示词编排、质量评审。

## 运营模式(v2,2026-07-22 起,限额墙驱动)

- **GLM 5.2 = 实施主力**:代码实现、测试、文档、重构——凡 GLM 能干好的全部派给它(用户手动接力)。
- **主会话(Kimi)= 编排 + 评审**:架构裁决、提示词编写、审查 GLM 交回的证据、小修小补;不再自己跑大规模实现子代理(Kimi 账号 5 小时配额连续两次撞墙)。
- **Gemini 3.1 Pro / Grok = 调研主力**(额度无限)。
- **证据契约**:GLM 交活必须带回——①三条验收命令(compileall/pytest/ruff)原始输出;②改动文件清单;③需注意问题;④入库检查单。主会话照单评审,不合格出返工提示词。

## 角色分工

| 角色 | 模型 | 干什么 |
|---|---|---|
| 架构 + 编排 + 评审 | 主会话(Kimi) | 出架构、写接力提示词、审产出、整合入库、拍板返工 |
| 深度调研 + 重实现 | Gemini 3.1 Pro | 源码级调研、核心模块初稿、大文件阅读 |
| 实现 / 重构 | GLM 5.2 | 按规格写模块、改 bug、重构 |
| 文档 / 测试 / 样板 | Gemini 3.5 Flash | 测试用例、文档、样板代码、格式化 |

## 流程

1. 主会话把任务写成**自包含提示词**,落盘到 `docs/relays/NNN_<模型>_<任务>.md`。
2. 用户把提示词贴给对应模型执行(模型有本地文件访问权,提示词里写绝对路径)。
3. 模型把报告/代码写入提示词指定的输出路径。
4. 用户告知主会话「完成」,主会话**审查产出**(读文件、跑检查),通过则整合入库,不通过则写返工提示词。

## 提示词写作契约

- **自包含**:模型之间无记忆,背景、目标、路径、约束全部写进提示词。
- **输出契约明确**:写到哪个文件、什么格式(报告=Markdown,事实/推断分开标注,来源带 URL;代码=完整文件,禁止省略号占位)。
- **禁止事项写死**:不改提示词范围外的文件、不装依赖、不跑破坏性命令。
- **验收标准可检查**:主会话能照着逐条核对(测试通过/文件存在/格式符合)。

## 质量门(主会话评审时查什么)

- 报告类:事实是否有来源、推断是否标注、建议是否可执行、有没有答非所问。
- 代码类:能跑(ruff/pytest 过)、完整(无占位符)、符合架构文档的模块边界、改动不超范围。

## 当前待执行

调研类提示词统一遵守 `RESEARCH_PROTOCOL.md`(五段式方法论、中间产物落 `relay_workspace/<NNN>/`、报告契约、入库检查单)。五条战线可全部并行(Gemini 额度无限):

- `001_gemini_research_prompt.md` — blender-mcp / vwx-mcp 源码级解剖 + VLM 评分 rubric。
- `002_gemini_agent_landscape_prompt.md` — Claude Code / OpenClaw / Goose / OpenHands / Cline / Aider 等 agent 产品全景 + 多模型沟通专题。
- `003_gemini_3d_cad_ecosystem_prompt.md` — 3D/CAD/场景生成生态(SceneCraft/Infinigen/Holodeck/CadQuery/资产源 API/磨损生成专题)。
- `004_gemini_trace_observability_prompt.md` — trace/观测/评测生态(OTel GenAI/Langfuse/pi session 格式/评测导出)。
- `005_gemini_agent_ui_prompt.md` — agent↔UI 协议与流式前端(AI SDK v6 parts/AG-UI/assistant-ui/MCP-UI,出 server SSE 事件 schema 草案)。
- `006_gemini_utility_domain_prompt.md` — Gemini:市政管网规范硬约束(constraints.yaml)+ IFC/VW 映射 + 三模型 API 参数核实(毕设领域知识主力)。
- `007_grok_community_intel_prompt.md` — Grok 4.5:社区情报(blender-mcp 用户反馈/VW 社区痛点/VLM 评分讨论/从业者声音),报告由用户复制入库。
- `008_glm_constraints_verify_prompt.md` — GLM 5.2:constraints.yaml 对照 GB 原文二轮核实 + 扩充(检查井间距/净距/雨污分流/管径序列)。✅ 已入库(12 号报告)

## M0 实施接力(运营模式 v2,GLM 当主力)

- `009_glm_stage3b2_render_loop.md` — GLM:render_loop 接 fork + mcp_clients 真实客户端 + clarify PyYAML 补丁(**当前待跑**)。
- `010_glm_stage4_assembly.md` — GLM:CLI 装配 + HITL 命令集 + 真实 builder(009 过审后跑)。
- `011_glm_m0_smoke.md` — GLM:M0 六道验收真实冒烟(010 过审后跑;唯一允许动真实 LLM/Blender)。
