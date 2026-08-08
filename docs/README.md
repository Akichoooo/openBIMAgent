# openBIMAgent Wiki(文档首页)

> openBIMAgent 唯一 Wiki 首页。代码结构以根 README 为准；实时进度只看 `PROJECT_HANDOFF_STATUS.md`，避免多文档状态漂移。

## 当前状态快照

- 阶段：**M1 G6 真实双宿主验收**。
- 已完成：G1–G5；Blender 5.2.0 LTS 真实 typed 执行与幂等重放。
- 待完成：Vectorworks 2024 GUI approved job、真实双宿主语义比较、G7 总验收。
- 成熟度：**工程 Alpha / 受控 Beta 候选待 Vectorworks 真机验证**。
- 最新 HEAD、测试数字、工件 hash 和准确下一步：[持续交接与进度状态](architecture/PROJECT_HANDOFF_STATUS.md)。

## 阅读顺序（新加入或新会话）

1. [持续交接与进度状态](architecture/PROJECT_HANDOFF_STATUS.md) — 当前做到哪里、下一步、测试证据和接管提示词
2. [已完成任务压缩摘要与当前接管点](../outputs/openBIMAgent_已完成任务压缩摘要与当前接管点_2026-08-08.md) — 面向后续执行模型的能力摘要、当前阻塞和复核点
3. [项目整体任务流程](architecture/PROJECT_MASTER_WORKFLOW.md) — M0/M1/M1.5/M2/M3 工作包、依赖和门禁
4. [架构总览](architecture/ARCHITECTURE.md) — 系统主链、协议边界和里程碑
5. [组件详设](architecture/COMPONENTS.md) — 每个组件、Agent、模型配置和上下文管理
6. [M1 长任务执行契约](architecture/M1_EXECUTION_CONTRACT.md) — G1–G7 完成标准、权限和停止条件
7. [全局决议](architecture/DECISIONS_DRAFT.md) — 已拍板选型及延后项
8. [文档治理与 K3 历史映射](architecture/DOCUMENTATION_GOVERNANCE.md) — K3 规划落点、Wiki 分层和同步规则
9. [接力工作流](relays/RELAY_WORKFLOW.md) + [调研协议](relays/RESEARCH_PROTOCOL.md) — 多模型接力和研究质量门

## 架构(architecture/)

| 文档 | 内容 | 状态 |
|---|---|---|
| [PROJECT_HANDOFF_STATUS.md](architecture/PROJECT_HANDOFF_STATUS.md) | 当前门禁、HEAD、测试、真实工件、下一动作和新会话提示词 | **CURRENT** |
| [PROJECT_MASTER_WORKFLOW.md](architecture/PROJECT_MASTER_WORKFLOW.md) | M0–M3 整体任务流、工作包、依赖、门禁和完成定义 | **CURRENT** |
| [ARCHITECTURE.md](architecture/ARCHITECTURE.md) | 设计原则、任务生命周期、Domain Pack、双宿主、Subagent Runtime、HITL 和里程碑 | **CURRENT v0.9** |
| [COMPONENTS.md](architecture/COMPONENTS.md) | 组件总表、Utility/typed host/Deliver/Runtime 模块规格、模型和安全 | **CURRENT v0.9** |
| [M1_EXECUTION_CONTRACT.md](architecture/M1_EXECUTION_CONTRACT.md) | 从工程 Alpha 到受控 Beta 候选的 G1–G7 门禁和权限边界 | **ACTIVE v1.0** |
| [DOCUMENTATION_GOVERNANCE.md](architecture/DOCUMENTATION_GOVERNANCE.md) | K3/Kimi 历史映射、Wiki 四层、单一事实来源与同步事务 | **CURRENT v1.0** |
| [DECISIONS_DRAFT.md](architecture/DECISIONS_DRAFT.md) | 早期全局架构决议、选型表和延后项 | **REFERENCE v1.1** |
| [M0_PLAN.md](architecture/M0_PLAN.md) | M0 六道验收、实施阶段和附条件收官记录 | **HISTORICAL v1** |
| [M1_MASTER_PROMPT.md](architecture/M1_MASTER_PROMPT.md) | M1 原始总控提示词 | **SUPERSEDED**：新会话使用交接状态文档中的最新提示词 |
| [openBIMAgent_Architecture_Graph.md](architecture/openBIMAgent_Architecture_Graph.md) | 学术展示用全景图，已增加当前 typed IR/双宿主状态注记 | **REFERENCE** |

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
| [11_kimi_intake.md](research/11_kimi_intake.md) | 早期 Kimi/K3 主架构师评审：质量门、假设验证、Domain Pack 断层和选型建议 | 历史评审 2026-07-21 |
| [12_glm_constraints_verify.md](research/12_glm_constraints_verify.md) | 市政 `constraints.yaml` 规范二轮核实与扩充依据 | GLM Relay 008，REFERENCE |

## 正式报告（outputs/）

| 文档 | 内容 | 状态 |
|---|---|---|
| [Subagent Runtime v1完整实施与验收报告.md](../outputs/Subagent%20Runtime%20v1完整实施与验收报告.md) | Runtime P0–P1f 协议、实现与阶段测试合并报告 | REFERENCE |
| [市政Solver v0实施与验收报告.md](../outputs/市政Solver%20v0实施与验收报告.md) | 市政 Solver 最小切片实施与验收 | REFERENCE |
| [compiled utility IR v1实施与架构成熟度报告.md](../outputs/compiled%20utility%20IR%20v1实施与架构成熟度报告.md) | Compiled Utility IR 契约和成熟度 | REFERENCE |
| [M1_G6真实宿主预检与阻塞报告.md](../outputs/M1_G6真实宿主预检与阻塞报告.md) | G6 初始预检历史报告；adapter 缺口已解除，待真机后更新 | ACTIVE / 部分过时 |

## 接力（relays/）

| 文档 | 内容 |
|---|---|
| [RELAY_WORKFLOW.md](relays/RELAY_WORKFLOW.md) | 角色分工、流程、提示词契约、质量门 |
| [RESEARCH_PROTOCOL.md](relays/RESEARCH_PROTOCOL.md) | 调研五段式、中间工作区约定、报告模板、回执 |

001–018 的阶段任务均已执行并收口到源码、架构文档和 `docs/research/` 正式报告；对应一次性提示词已于 2026-08-01 清理，必要时可从 Git 历史恢复。

## 中间产物

接力脚本、日志、克隆源码和原始摘录只在 gitignored 的临时工作区中存在，任务收口后删除；正式结论必须进入 `docs/research/`、架构文档或验收报告。约定详见 RESEARCH_PROTOCOL §2。

## K3/Kimi 历史说明

K3/Kimi 是早期主架构师和 Relay 编排会话身份，**不是项目里程碑，也不等于 M3**。其整体规划已吸收到架构、组件、决议、M0/M1、Agent profiles 和 Wiki。Relay 001–018 是一次性任务书，已在 `466afba` 中完成正式收口并从工作树清理，必要时从 Git 历史恢复，不重新堆回 Wiki。详细映射见 [DOCUMENTATION_GOVERNANCE.md](architecture/DOCUMENTATION_GOVERNANCE.md)。

## 维护约定

- 当前门禁、HEAD、测试数字、真实工件和下一动作只维护在 `PROJECT_HANDOFF_STATUS.md`。
- 项目路线、工作包和完成定义只维护在 `PROJECT_MASTER_WORKFLOW.md`。
- 架构/协议变化同步修改 ARCHITECTURE、COMPONENTS，并在本节追加变更日志。
- 调研报告标来源，事实与推断分开；历史报告不重写为当前状态。
- 接力产出入库前必须过质量门；一次性提示词在正式结论入库后清理。
- 每次更新至少执行 `git diff --check` 和 Markdown 相对链接检查。

## 变更日志

- 2026-07-21:初版落成(架构 2 篇 + 调研 3 篇 + 接力 3 篇)。
- 2026-07-21:调研面放宽——新增 RESEARCH_PROTOCOL + 003/004/005 三条战线;补 GenCAD 审计(04)。
- 2026-07-21:决议 v1 拍板;ARCHITECTURE/COMPONENTS 改写 v0.2;新增 domain_packs/ 三包 + src/ M0 代码骨架。
- 2026-07-21:006/007 入库;主会话评审(11);ARCHITECTURE v0.3(评分分层/防放水五件套/HITL 基座/预览双线/模板族/并行路径);DECISIONS 附录 A(v1.1);`domain_packs/_base/` 创作指南;models.toml 同步调研值。
- 2026-07-22~27:M0 实施(relay 008-011):constraints.yaml 二轮核实(25 条规则);核心链路(session/providers/schema_gate/clarify/loop);blender-mcp fork 八项改造(真实 Blender 10/10);双环(scad_loop/rubric/html_report/VLMCritic);装配层(pipeline/batch_executor/builder/cli);M0 冒烟附条件收官，最终结论已回填 M0_PLAN.md。测试基线 229 passed。
- 2026-08-01:Subagent Runtime v1 完成 P1d 只读 Control Plane、P1e loopback Runtime IPC 与 P1f 本地 Operator Console；浏览器读持久化投影、写控制由服务端代理到唯一 Runtime lease owner，IPC token 不进入浏览器。ARCHITECTURE/COMPONENTS 更新至 v0.6。
- 2026-07-31:市政主线新增 `municipal-straight-gravity-solver v0.1.0`：Solver v0 输出 compiled utility IR v1、坡度/管径/覆土/井距 RuleEvidence，并接入 Domain Gate；碰撞/水力保持 UNKNOWN 阻断。ARCHITECTURE/COMPONENTS 更新至 v0.8。
- 2026-08-01:市政 Solver 升级至 v0.2.0：版本化 `collision_context` 支持 AABB 与既有直圆管三维实体净距，完整上下文将 `clash_free` 产出为可审计 PASS/FAIL；上下文缺失仍 UNKNOWN。ARCHITECTURE/COMPONENTS 更新至 v0.8.2。
- 2026-08-01:市政 Solver 升级至 v0.3.0：新增 `MunicipalRuleSet v1.0`，从 Domain Pack 受信任 `constraints.yaml` 编译净距规则；Solver 输入移除调用方 `ClearanceRule`。仅高置信建筑物 2.5m 规则可生产 PASS/FAIL，中置信给水/燃气/电力/通信规则失败关闭为 UNKNOWN。ARCHITECTURE/COMPONENTS 更新至 v0.8.3。
- 2026-08-01:市政 Solver 升级至 v0.4.0 / Input v0.4、`MunicipalRuleSet v1.1` / compiler v0.2.0：核验 `GB 50289-2016` 第 4.1.9 条/表 4.1.9 的政府公开扫描副本并用第二 PDF 版面交叉复核，新增结构化 `RuleVerification` 与不可绕过 production 晋级门禁；纠正通信和燃气旧值，编译建筑物/给水/燃气/电力/通信共 12 条 production 规则。净距算法修正为 XY 平面实体表面水平距离，Evidence 可回溯规范副本 SHA-256 和原表定位；安全措施减距仍失败关闭。ARCHITECTURE/COMPONENTS 更新至 v0.8.4。
- 2026-08-01:完成文档分层清理：Subagent Runtime P0–P1f 的 8 份阶段报告合并为 `outputs/Subagent Runtime v1完整实施与验收报告.md`；删除已执行的 relay 001–018 一次性提示词和临时工作区，保留接力工作流、调研协议、正式研究、架构决策及规范核验证据。
- 2026-08-02:建立 `M1_EXECUTION_CONTRACT.md` 长任务契约：目标为双宿主可交付闭环，按 G1–G7 阶段门禁持续推进；采用受控自主、门禁通过后本地提交、先模拟后真实宿主审批、仅门禁或阻塞汇报。
- 2026-08-02:新增 `M1_MASTER_PROMPT.md` 总控长任务提示词，可在新会话中自动审计 Git、项目记录和门禁证据，从首个未完成阶段恢复并连续执行；网络/上下文中断后复用同一提示词，禁止重做已通过阶段，G6 真机审批仍不可绕过。
- 2026-08-02~03:完成 M1 G1–G5 与双宿主 typed adapter：Vectorworks typed plan、不可变 Manifest、双宿主语义协议、IFC4X3/IDS、失败恢复、Blender/Vectorworks 真实宿主 adapter 均形成边界提交；Blender 5.2.0 LTS 真实 G6 通过，Vectorworks 2024 GUI approved job 待执行。最新证据统一转由 `PROJECT_HANDOFF_STATUS.md` 维护。
- 2026-08-03:Wiki 收口为四层文档模型；新增 `PROJECT_HANDOFF_STATUS.md`、`PROJECT_MASTER_WORKFLOW.md` 和 `DOCUMENTATION_GOVERNANCE.md`，明确 K3/Kimi 是早期架构/接力身份而非里程碑，Relay 历史留在 Git；ARCHITECTURE/COMPONENTS 同步标记 v0.9，README 与架构图改用当前 typed IR、双宿主和 G6 状态。
