# openBIMForge 代码库审计(重构资产盘点)

调研日期:2026-07-21 · 对象:`../openBIMForge`,分支 `refactor/v2-agent-mcp`,基线 tag `pre-refactor-v1`
方法:只读探索,全部结论有文件路径佐证。

## 1. 系统是什么

LLM 驱动的生成式 BIM 系统:自然语言/图片需求 → 建筑方案 IR(`BuildingPlan`)→ 确定性校验 → 预览(OpenSCAD/Three.js 截图 + VLM 视觉闭环)→ 可选 Preview IFC → 高精度交付时编译为 Vectorworks 脚本在真宿主执行,过 IFC/IDS 门禁。

两条铁律(新项目继承):

- **C2:LLM 出语义、Solver 出坐标,禁止 LLM 直接喷坐标/IFC**
- **C5:任何 compile/export/deliver 只接 accepted PlanEnvelope**

## 2. 关键结论:v2 链路已断,且方向证伪

v2(`agent_core/` + 前端 MCP 模式)不仅「流式不美观」,而是根本不通:

1. **SSE 协议不兼容**:`app/api/agent-chat/route.ts:95-145` 把 Python stdout 的自定义事件(`clarification/plan/step_start/…`)原样包成 SSE `data:` 帧;前端 AI SDK v6 的 `DefaultChatTransport` 按 `uiMessageChunkSchema` 严格校验每个 chunk,未知类型直接抛错(`node_modules/ai/dist/index.mjs:13419-13437`)。MCP 模式在前端必然 onError。
2. **编译断点**:`app/[lang]/agent/page.tsx` 仍 import 已删除的 `components/agent-ui/*`。
3. **视觉验证必炸**:`agent_core/api.py:114` 把 `vision_critic_agent` 置为 `None`,主流程 `_visual_validation_phase` 调用它,跑到必崩。
4. **重复造轮子**:v2 另起「前端直连 → Python 轻量编排 → 外部 CLI 子进程」栈,绕开了 v1 全部成熟资产(见下),且不带任何门禁/治理/trace。
5. **「MCP」名不副实**:全仓库无任何标准 MCP SDK 依赖。`agent_core/mcp/` 三个文件实为 CLI 子进程包装(`blender --background --python`、`openscad -o`、生成 `import vs` 脚本),无 initialize/tools/list/transport。

教训:**新项目必须基于标准 MCP 协议,事件流必须符合前端 SDK 的 schema(或自定义渲染层与协议一起设计),视觉 critic 是一等公民而不是可选挂载。**

## 3. 可抽取的成熟资产(v1)

| 资产 | 路径 | 成熟度 | 新项目处置 |
|---|---|---|---|
| 视觉自检环 | `forge_core/vision_loop/`(`json2scad.py` IR→SCAD、`screenshot_service.py` Playwright 三视角、`vision_critic.py` 6 维 VLM 评分、`json_patch_applier.py` 校验 old_value、`convergence_controller.py` best-so-far 回退) | 高(文档自评 8/10) | **移植为 SCAD 结构快检环** |
| 收敛治理 | ADR-0004(`docs/adr/0004-vision-loop-gating.md`):四选一收敛 perfect_score / convergence_delta / hard_limit / divergence_fallback;env 门控 | 高 | 原样继承到双环 |
| 追问/澄清 | `lib/bim/clarification-loop.ts`(槽位/别名/正则抽取、zh/en、`ready_to_generate/completion_score/missing_fields` 评分,阈值 85,可独立 clarify 模型) | 高(TS) | 思路移植,Python 重写,槽位定义移入 playbook frontmatter |
| 多 agent 编排 | `forge_core/build_agent/orchestrator.py`(advisor-orchestrator-worker,PASS/FIX/ESCALATE,`MAX_CONCURRENCY=4`,814 行) | 中(代码+测试在,默认 runtime 未接) | 参考重写为子代理调度 |
| Golden Trace | `forge_core/build_agent/trace_recorder.py`(JSONL+SQLite+脱敏+TrainingExport,1893 行) | 中 | 参考,改为 pi 式 JSONL 树重写 |
| Vectorworks 执行链 | `vectorworks_execute.py`(vs.* 副作用前重验 handoff/hash/approval,AST/import/builtin 限制)、`RUN_IN_VECTORWORKS_START_FRONTEND.py`(VW 内轮询 `forge_runtime/handoffs/`)、`forge_core/design_agent/vs.py`(1.4MB 自动生成 vs 绑定)、`vectorworks_plugin/openBIMForge2024/` | 高(已跑通单体) | **拆分为 vectorworks-mcp 的宿主侧**(本次重构核心动作) |
| Blender MCP 实验 | `gork/blender_mcp_lab/`(pin ahujasid commit `6641189`,AST allowlist 安全约束,产物落 `forge_runtime/paper_experiments/blender_mcp/`) | 中 | 移植为 blender-mcp fork 的安全层 |
| 提示词资产 | `forge_core/prompts/*.md`(plan/code/review/ask/cad_first/forgevision/mep 版本化)、`forge_core/design_agent/muti_agent_prompt/*.txt` | 中 | 参考进 playbooks |
| 建筑类型知识 | `forge_core/knowledge/typologies/*.json`(10 种 typology)+ `typology_templates.py` | 中 | 按需抽取进知识库 |
| v2 `agent_core/` | `agent_core/agents/*`(clarification/planning 可用,execution 多 TODO,checker 全 TODO) | 低 | **弃**,仅保留 StreamEventType 事件枚举思路 |
| 前端 | `app/`、`components/`(3204 行 message-display,`sanitizeDisplayText()` 一摞正则补丁含乱码清理) | — | **弃**,将来按「工具结果双视图 + SDK parts」重做 |

## 4. 四类机制现状对照(新项目的起点线)

| 机制 | openBIMForge 现状 | openBIMAgent 目标 |
|---|---|---|
| 追问澄清 | v1 TS 成熟,v2 骨架 | playbook 槽位驱动,问齐才允许开工 |
| 截图视觉检测 | v1 SCAD 环成熟,Blender 环无 | **双环**:SCAD 结构快检 + Blender 美学精检 |
| 多 agent 分工 | orchestrator 代码在但未接 | Markdown 定义角色 + child session + 并发 |
| trace/回放 | Golden Trace(Python)与 Langfuse(TS)分立,trace ID 未统一 | 单一 JSONL 会话树,截图/评分/工具调用全留痕,可回放 |

## 5. 部署隐患(新项目规避)

- `spawn("python")` 依赖系统 PATH 解释器,与 `.venv`/uv 脱节 → 新项目统一 uv 管理,子进程用显式解释器路径。
- `agent_core/requirements.txt` 与主 `pyproject.toml` 双依赖源 → 新项目单一 `pyproject.toml`。
- 前端把上游脏输出靠渲染层正则打补丁(`sanitizeDisplayText`,含 mojibake 清理)→ 新项目协议层保证干净,渲染层不打补丁。
