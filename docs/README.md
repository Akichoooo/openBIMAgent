# openBIMAgent Wiki(文档首页)

> 设计阶段文档库。读文档的顺序见下;代码仓库结构以根 README 为准。
> 规模合适后(>15 篇)再考虑迁 mdBook / GitHub Wiki,现在先用目录内索引维持轻量。

## 阅读顺序(新加入者)

1. [架构总览 ARCHITECTURE.md](architecture/ARCHITECTURE.md) — 系统在做什么、流程图、里程碑
2. [组件详设 COMPONENTS.md](architecture/COMPONENTS.md) — 每个组件/agent/模型配置/上下文管理
3. [决议 DECISIONS_DRAFT.md](architecture/DECISIONS_DRAFT.md) — v1 已拍板 + v1.1 社区/领域情报对齐附录
4. [接力工作流 relays/RELAY_WORKFLOW.md](relays/RELAY_WORKFLOW.md) + [调研协议 relays/RESEARCH_PROTOCOL.md](relays/RESEARCH_PROTOCOL.md) — 开发怎么分工、调研怎么做

## 架构(architecture/)

| 文档 | 内容 | 状态 |
|---|---|---|
| [ARCHITECTURE.md](architecture/ARCHITECTURE.md) | 设计原则、架构图、任务生命周期、双环+评分分层、Domain Pack 模板族、两个 MCP 规格、HITL 基座与预览双线、里程碑 | v0.3 当前有效 |
| [COMPONENTS.md](architecture/COMPONENTS.md) | 组件总表、core 模块规格、角色-模型绑定、多厂家模型配置、上下文管理、多模型沟通、安全权限 | v0.2 + models.toml 调研值同步 |
| [DECISIONS_DRAFT.md](architecture/DECISIONS_DRAFT.md) | 全局架构决策决议:Domain Pack、12 条 P0、选型表 + 附录 A(v1.1 对齐 V1-V7) | v1.1 当前有效 |
| [M0_PLAN.md](architecture/M0_PLAN.md) | M0 实施计划:六道验收、阶段 0-4、主会话/GLM/Flash 分工、风险 | v1 待开工 |

## 调研(research/)

| 文档 | 内容 | 来源 |
|---|---|---|
| [01_codebase_audit.md](research/01_codebase_audit.md) | openBIMForge 审计:v2 断链证据、可抽取资产表 | 主会话 |
| [02_opensource_landscape.md](research/02_opensource_landscape.md) | opencode / pi / blender-mcp / vwx-mcp / SceneCraft 对标 | 主会话 |
| [04_gencad_main_audit.md](research/04_gencad_main_audit.md) | GenCAD-main 盘点 | 主会话 |
| [03_gemini_mcp_vision_report.md](research/03_gemini_mcp_vision_report.md) | blender-mcp/vwx-mcp 源码解剖 + VLM rubric | Gemini 001 ✅ |
| [05_gemini_agent_landscape.md](research/05_gemini_agent_landscape.md) | agent 产品全景 + 多模型沟通专题 | Gemini 002 ✅ |
| [06_gemini_3d_cad_ecosystem.md](research/06_gemini_3d_cad_ecosystem.md) | 3D/CAD 生态 + 磨损两路线 | Gemini 003 ✅ |
| [07_gemini_trace_observability.md](research/07_gemini_trace_observability.md) | trace/观测/评测 + JSONL 事件 schema | Gemini 004 ✅ |
| [08_gemini_agent_ui_protocols.md](research/08_gemini_agent_ui_protocols.md) | UI 协议 + SSE 事件 schema 草案 | Gemini 005 ✅ |
| [09_gemini_utility_domain.md](research/09_gemini_utility_domain.md) | 市政管网规范约束 + IFC/VW 映射 + 模型参数核实 | Gemini 006 ✅(带保留,见 11) |
| [10_grok_community_intel.md](research/10_grok_community_intel.md) | 社区情报:blender-mcp 五坑/vs 幻觉/VLM-judge/从业者/设计院 | Grok 007 ✅ 优秀 |
| [11_kimi_intake.md](research/11_kimi_intake.md) | 主架构师评审:质量门、假设验证表、Domain Pack 断层评估、models.toml 建议、变更清单 | 主会话 2026-07-21 |

## 接力(relays/)

| 文档 | 内容 |
|---|---|
| [RELAY_WORKFLOW.md](relays/RELAY_WORKFLOW.md) | 角色分工、流程、提示词契约、质量门 |
| [RESEARCH_PROTOCOL.md](relays/RESEARCH_PROTOCOL.md) | 调研五段式、`relay_workspace/` 约定、报告模板、回执 |
| [001](relays/001_gemini_research_prompt.md)-[005](relays/005_gemini_agent_ui_prompt.md) | 五条调研战线(已执行完毕,报告过审) |
| [006](relays/006_gemini_utility_domain_prompt.md) / [007](relays/007_grok_community_intel_prompt.md) | 市政管网领域(Gemini)/ 社区情报(Grok)(已执行完毕,评审见 11) |

## 中间产物

接力调研的脚本/日志/原始摘录落 `relay_workspace/<NNN_task_slug>/`(gitignored);只有 `docs/research/` 正式报告入库。约定详见 RESEARCH_PROTOCOL §2。

## 维护约定

- 调研报告标来源(文件路径/URL),事实与推断分开。
- 架构决策变更必须同时改 ARCHITECTURE/COMPONENTS 并在本节记一行变更日志。
- 接力产出入库前必须过主会话质量门(见 RELAY_WORKFLOW §质量门)。

## 变更日志

- 2026-07-21:初版落成(架构 2 篇 + 调研 3 篇 + 接力 3 篇)。
- 2026-07-21:调研面放宽——新增 RESEARCH_PROTOCOL + 003/004/005 三条战线;补 GenCAD 审计(04)。
- 2026-07-21:决议 v1 拍板;ARCHITECTURE/COMPONENTS 改写 v0.2;新增 domain_packs/ 三包 + src/ M0 代码骨架。
- 2026-07-21:006/007 入库;主会话评审(11);ARCHITECTURE v0.3(评分分层/防放水五件套/HITL 基座/预览双线/模板族/并行路径);DECISIONS 附录 A(v1.1);`domain_packs/_base/` 创作指南;models.toml 同步调研值。
- 2026-07-22~27:M0 实施(relay 008-011):constraints.yaml 二轮核实(25 条规则);核心链路(session/providers/schema_gate/clarify/loop);blender-mcp fork 八项改造(真实 Blender 10/10);双环(scad_loop/rubric/html_report/VLMCritic);装配层(pipeline/batch_executor/builder/cli);M0 冒烟收官(附条件通过,报告见 `relay_workspace/m0_smoke/report.md`,结论回填 M0_PLAN.md)。测试基线 229 passed。
