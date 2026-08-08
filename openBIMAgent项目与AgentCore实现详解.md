# openBIMAgent 项目与 Agent Core 实现详解

> 原始整理日期：2026-08-01 · 状态：**REFERENCE / 实现编年说明**
> 最新状态同步：2026-08-03。本文主体保留 2026-08-01 的模块级实现记录，其中历史测试数字和“待实现”描述不再作为当前事实来源。
> 当前权威入口：实时进度与接管提示词见 `docs/architecture/PROJECT_HANDOFF_STATUS.md`；M0–M3 总路线见 `docs/architecture/PROJECT_MASTER_WORKFLOW.md`；文档治理与 K3 映射见 `docs/architecture/DOCUMENTATION_GOVERNANCE.md`。
>
> **2026-08-03 当前判断**：M1 G1–G5 已通过；生产级 typed `VectorworksBuilder`、Blender/Vectorworks typed host adapter、`SemanticSnapshot v1`、IFC4X3/IDS、RuleEvidence、ArtifactManifest 和恢复安全链均已实现。Blender 5.2.0 LTS 真实 G6 已通过；Vectorworks 2024 adapter 已离线闭合，但 GUI approved job、真实 `.vwx`/sidecar、幂等重放和双宿主真实比较待完成。项目仍是工程 Alpha / 受控 Beta 候选待真机验证。

## 1. 项目是什么

`openBIMAgent` 是一个面向生成式 BIM / 3D 场景构建的自研 Agent 系统。它不是单纯把一句话交给大模型生成脚本，而是将需求澄清、规划、结构化工件、Schema 门禁、子代理调度、Blender/Vectorworks 执行、视觉自检、领域硬约束和交付检查组成可回放流水线。

核心目标链路：

```text
自然语言需求
→ Clarify 槽位澄清
→ Planner / Scene Graph IR
→ Schema Gate
→ Domain Solver / Compiled Domain IR（Playbook 声明时）
→ RuleEvidence / Domain Gate
→ Orchestrator
→ SCAD 快检（有几何 IR 时）
→ Blender 精检
→ Vectorworks BIM 执行（声明 target 时）
→ Deliver Gate
→ 工件与会话证据
```

核心原则：

1. **工件即协议**：PLAN、TODO、Scene Graph IR、评分、结果 JSON 都有稳定结构。
2. **语义与几何分离**：Planner 只产语义 IR，坐标和工程硬约束应交给 Solver/确定性检查器。
3. **硬门禁不交给主观模型**：Schema、碰撞、坡度、覆土等能程序校验的规则必须确定性执行。
4. **人在环上**：MCP 写操作、危险操作和最终交付支持审批。
5. **全流程留痕**：消息、工具调用、评分、快照和分支会话可审计、可回放。
6. **失败要可恢复**：Provider fallback、Builder 模板回退、FIX 重试、doom-loop 升级和 checkpoint。

## 2. 仓库内容

| 路径 | 职责 |
|---|---|
| `src/openbimagent/` | Agent Core 与产品流水线源码 |
| `src/openbimagent/core/` | 通用 ReAct/tool-call AgentLoop、权限和事件 |
| `src/openbimagent/clarify/` | 槽位澄清、85 分放行、会话续答 |
| `src/openbimagent/planner/` | Playbook 解析、Planner、IR/PLAN/TODO 生成 |
| `src/openbimagent/schema_gate/` | JSON Schema 硬门禁 |
| `src/openbimagent/orchestrator/` | PASS/FIX/ESCALATE、重试、并发、doom-loop |
| `src/openbimagent/assembly/` | Builder、批次执行器、多 target 分发、完整 pipeline |
| `src/openbimagent/vision/` | SCAD 快检、Blender 多视角精检、rubric、HTML 报告 |
| `src/openbimagent/providers/` | 模型注册表、方言、fallback、重试与熔断 |
| `src/openbimagent/mcp_clients/` | Blender 与 Vectorworks MCP 客户端 |
| `src/openbimagent/session/` | JSONL 事件树、fork、export、BIMBench |
| `src/openbimagent/deliver/` | 交付物与验收分数门禁 |
| `schemas/` | PLAN、Scene Graph IR、SCAD IR、会话等 Schema |
| `agents/` | researcher/planner/modeler/critic 等角色说明 |
| `domain_packs/` | 领域 Playbook、角色覆盖和知识库 |
| `mcp_servers/blender_mcp/` | Blender MCP 服务端与 addon |
| `mcp_servers/vectorworks_mcp/` | Vectorworks MCP、文件 IPC runner、API 索引与门禁 |
| `tests/` | 离线单测、集成测试与契约测试 |
| `docs/` | 架构、决策、运行与研究材料 |
| `outputs/` | 当前有效的专题设计、实施与验收报告；Runtime P0–P1f 已合并为一份总报告 |

## 3. Agent Core 已实现什么

### 3.1 通用 AgentLoop

`src/openbimagent/core/loop.py` 实现了模型对话与工具调用循环：

- 统一接收 OpenAI `chat.completion` 形态响应。
- 解析文本、`tool_calls` 和完成状态。
- 支持 `read/write/edit/bash` 基础工具。
- 对工具调用执行权限检查和审批。
- 写入 tool call/result 事件。
- 支持取消信号和 checkpoint。
- 限制最大步数，避免无限循环。

但要注意：通用 `AgentLoop` 内的 `_tool_mcp_call()`、`_tool_vision_check()`、`_tool_deliver()` 仍是占位；`_tool_subagent()` 已在 Subagent Runtime v1 P0 中接通。当前产品主链仍是 `assembly.pipeline.run_pipeline()`，通用循环尚未替代确定性的 Pipeline。

### 3.2 权限系统

`src/openbimagent/core/permissions.py` 提供三态权限：

```text
ALLOW / ASK / DENY
```

默认只读允许，`mcp_*` 与 `execute_*_code` 默认询问。流水线层还在 Blender 执行、Vectorworks 执行和 deliver 前设置审批点；`--yes` 可用于受控的非交互运行。

### 3.3 Session 事件树

`SessionStore` 使用 JSONL 保存事件链，已实现：

- 创建/打开会话。
- 追加消息、工具调用、评分、快照等事件。
- 维护 `parentId` 形成事件树。
- 从任意事件 `fork()` / `branch()`。
- 获取某个事件之前的完整祖先链。
- 记录 `.blend` 快照及 hash。
- 导出 JSONL 和 BIMBench 形态。
- `index.json` 管理多会话、标题、Playbook、分支来源和 Subagent `child_of` 父子关联。
- Subagent 生命周期、工件提交和投递回执使用强类型 custom 事件留痕。

Clarify 问答会成对写入会话，分支会话可从历史回答恢复，只追问尚未完成的槽位。Subagent 过程写入独立 child Session，父会话只保存紧凑结果和工件指针。

### 3.4 Clarify

`clarify/slots.py` 已实现：

- 从 Playbook YAML frontmatter 加载槽位。
- 从用户文本提取 `key=value`。
- 按顺序一问一答。
- 空回答时接受默认值。
- 用 `completion_score` 衡量完整度。
- `PASS_THRESHOLD = 85`，未达到时禁止进入 Planner。
- 支持从 Session 分支恢复已答槽位。

### 3.5 Planner 与工件

`planner/instantiate.py` 已实现：

- 解析 Playbook frontmatter 和正文。
- 规范化 `targets / slots / phases / acceptance / deliverables`。
- 生成 `PLAN.md`、`TODO.md`、`scene_graph_ir.json`。
- registry 可用时调用 `role="planner"`。
- Planner 输出非法时重试；结构漂移不静默吞掉。
- Provider 基础设施失败时回退确定性语义模板。
- Scene Graph IR 坚持 C2：语义为主，不由 LLM 直接决定坐标。
- 校验批次引用、资产 ID 和 Schema。

现有 Scene Graph IR 主要包含 `id/category/description/count/material_ref/tags` 及语义空间关系。它**不是**完整市政管网计算 IR。

### 3.6 Schema Gate

`schema_gate/gate.py` 自动加载 `schemas/*.schema.json`，按 Draft 2020-12 校验，并把错误整理为精确 JSON 路径，例如：

```text
$.phases[0].status: 'weird' is not one of ...
```

失败抛 `SchemaGateError`，可直接转成 FIX 指令。本轮已扩展 `plan.schema.json`，正式允许：

```yaml
acceptance:
  domain_gate:
    clash_free: true
    slope_in_spec: true
```

### 3.7 Orchestrator

`orchestrator/dispatch.py` 已实现：

- `PASS / FIX / ESCALATE` 三态裁决。
- FIX 指令传入下一次同批次执行。
- 最大重试次数。
- FIX 无可执行指令时直接升级。
- doom-loop 检测：连续 FIX 且评分无进展时升级。
- hint 超 200 字截断和告警。
- 禁止子代理嵌套派发。
- 工具调用过程写入 Session。
- `concurrent=True` 时最多四批并发，结果顺序稳定。
- 子代理结果归一为 `SubagentResult`。

P0 已新增真实 Subagent Runtime v1：

- `orchestrator/contracts.py`：版本化 `SubagentRequest`、`SubagentHandle`、`SubagentResultEnvelope`、状态和结构化错误。
- `orchestrator/runtime.py`：同步 `LocalSubagentRuntime`，每次运行创建独立 child Session，角色配置作为能力上限。
- `orchestrator/artifacts.py`：summary/output 原子提交到不可变目录，生成记录 size 和 SHA-256 的 manifest。
- `schemas/subagent_request.schema.json`、`subagent_result.schema.json`、`artifact_manifest.schema.json`：请求、结果和工件全部通过 Schema Gate。
- `AgentLoop._tool_subagent()` 已接通，只向模型暴露 `role/task/context_mode/execution_mode/artifact_contract`，不允许模型自行选择 model/tools/permissions。
- P1a 已支持进程内 background、status、cancel、join，并发上限为 4；取消信号贯穿 child AgentLoop 与 Provider。
- P1b-A 已实现跨进程持久化与 rehydrate：background 状态原子写入 `sessions/_runtime`；终态重启后仍可 `status/join` 且不重跑模型；`finalizing` 幂等补齐 lifecycle/receipt；遗留运行任务以可重试 `RuntimeRestarted` 失败关闭。
- Runtime 以跨进程 lease 独占同一 `sessions` 目录，避免两个活跃进程互相误恢复；`sessions/index.json` 使用进程内共享锁、Windows/POSIX 跨进程锁和原子替换。
- 状态损坏采用严格失败关闭并指出具体文件；状态记录不保存 API key、Authorization header 或 child 原始上下文。
- P1b-B Approval Broker 已实现：child 的 `Permission.ASK` 同时写入父/子 Session；父侧可使用旧 bool callback 或 `pending_approvals()/decide_approval()` 异步决策；每个决策生成稳定 `DecisionReceipt`。
- 审批支持 approved/rejected/cancelled/timed_out/runtime_restarted，同决策幂等、冲突决策拒绝。Runtime 重启会对账父子 Session，复用任一侧已有合法 receipt 补齐另一侧；均未决才失败关闭，冲突事实严格失败。
- 审批与工具调用事件只保存“参数名→值类型”的摘要和 canonical SHA-256，不保存 code/content/token 等原始参数；callback 异常也只记录异常类型，不写异常消息。
- P1c `resume/steer` 已实现：`request_id` 表示单个 attempt，逻辑谱系由 `lineage_id` 关联，`attempt_number` 递增。`resume` 必须创建新 request、agent 和 child Session，旧终态与不可变 Artifact 只作只读上下文，禁止静默重放旧工具副作用。
- `steer` 只绑定 queued/running 的当前 attempt，并核对 request/agent/child/lineage/attempt；指令只在下一轮 Provider 调用前应用，不打断 Provider 请求、不插入工具批次、不绕过 Approval。未消费指令在终态、取消、身份不匹配或 Runtime 重启时签发明确回执，重启不重新入队，也不会串入 resume attempt。
- Resume request/receipt 已纳入 RuntimeState，重启可幂等补齐父 Session、source child 和 new child 三方事件；steer 请求/回执对账父子 Session，单边缺失补齐、冲突严格失败。
- P1d 新增 `ActorRef(actor_id, actor_type, display_name)`：Approval、Resume、Steer 的新控制事实使用稳定身份，历史字符串 actor 读取时升级为 `legacy`，显示名不再承担授权与幂等身份。
- P1d Resume 强制调用方提供 `idempotency_key`，并持久化 `instruction_sha256`。幂等域为 `actor_id + idempotency_key`：同 source/同指令重试返回原 Handle/Receipt，不创建或重跑 attempt；同键不同 source/指令严格冲突；Runtime 重启后仍从持久状态复用原事实。
- P1d 新增只读 `ReadOnlyControlPlane` 与 `control` CLI，查询 attempts、lineage、approvals、resumes、steers；投影默认不返回 task/instruction 原文，可与活跃 Runtime 并行读取且不获取 lease。
- P1e 新增单机 `RuntimeIpcServer/RuntimeIpcClient`：`runtime-serve` 持有唯一 Runtime lease 并绑定 `127.0.0.1`，`control-write` 经 discovery、私有 token、ActorRef 和幂等键提交 Approval/Resume/Steer/Cancel。服务端不接受外部 runtime/legacy actor，不保存 token，不允许客户端自行重建 Runtime；请求采用白名单 payload、大小上限、超时和 message_id 校验。
- P1f 新增本地 `OperatorConsoleServer/OperatorConsoleService`：`operator-console` 独立展示 attempts、approvals、resumes、steers，并在服务端代理 Ping、Approve/Reject、Resume、Steer、Cancel。它不获取 Runtime lease，浏览器不读取 IPC bearer token，ActorRef 在启动时固定；HTTP 强制 loopback、Host/Origin/CSRF、JSON、64 KiB 上限、16 并发和 CSP 等安全头。
- 可靠性语义采用失败关闭：不可变 Artifact 通过原子“存在即失败”发布；child 声明的输出文件缺失时不会误报 `completed`，而会生成结构化 `FAILED`、错误工件、manifest 和 delivery receipt。
- 10 个早期 K3/Kimi 会话规划形成的 `agents/*.md` 均纳入 profile 解析回归，保持 Markdown + YAML frontmatter、禁嵌套和 artifact-mediated 协作约束。K3/Kimi 是早期架构/接力身份，不是项目里程碑；当前映射见 `DOCUMENTATION_GOVERNANCE.md`。

本轮补齐统一 `judge()`：

- Gate 失败且有反馈 → FIX。
- Gate 失败但无可执行反馈 → ESCALATE。
- Gate 通过但缺评分 → ESCALATE。
- 分数达到默认 `8.5` → PASS。
- 分数不足 → 生成包含当前分、目标分和反馈的 FIX 指令。

### 3.8 Provider Registry 与模型方言

`providers/registry.py` 负责：

- 从 `config/models.toml` 加载 provider/model/profile。
- 按角色选择模型。
- fallback chain。
- API key 环境变量读取。
- 超时、重试、退避和熔断。
- 对上层保持统一 `registry.chat(role, messages, tools, ...)` 接口。

`providers/dialects.py` 已支持 OpenAI Chat Completions，并在本轮补齐 Google Gen AI：

- system instruction。
- 文本和 OpenAI content-parts。
- base64 `data:` URI 图片。
- OpenAI function tools → Gemini `FunctionDeclaration`。
- Gemini function call → OpenAI `tool_calls`。
- reasoning 和 usage metadata 归一化。
- 调用前/调用后取消检查。

限制：`ANTHROPIC`、`OPENAI_RESPONSES` 仍未实现；Google 同步 SDK 在阻塞请求过程中无法被 `cancel_event` 立即强制中断；官方模型名和账号权限仍需真实 key 在线验证。

### 3.9 Builder 与缓存

`assembly/builder.py` 已实现：

- LLM modeler 生成 `bpy` 代码。
- 把上一轮 critic 的 `actionable_feedback` 送入返工提示。
- 代码 fence 提取与 AST 预校验。
- 对齐 Blender addon import/危险 builtin allowlist。
- Blender 5.2、材质、灯光、集合范围锁等约束提示。
- LLM 失败时回退确定性 cube 模板。
- 可选 `AssetCache`：同参 hash 去重和 429 退避。

产品流水线目前仍默认 `use_cache=False`，并发也未默认开启；这是为了在真实双 MCP 稳定前减少故障变量。

### 3.10 compiled utility IR v1

`src/openbimagent/utility/` 已建立市政语义层与宿主 Builder 之间的确定性协议边界：

- `CompiledUtilityIR` 持有 source IR SHA-256、Solver 身份、坐标参考、系统、节点/端口、管段和逐对象规则证据。
- 管段包含 centerline、水平长度、起终内底标高、坡度、管径、材质、覆土和 IFC 类型。
- Pydantic 运行时校验 ID 唯一、引用闭合、端口归属、系统一致、centerline 端点、坡度/标高/长度一致和重力流非逆坡。
- `schemas/compiled_utility_ir.schema.json` 纳入 Schema Gate，禁止未知字段和协议漂移。
- canonical JSON/SHA-256 对集合按稳定 ID 排序，避免 Solver 输出数组顺序改变审计摘要。
- `domain_evidence()` 将逐对象 PASS/FAIL/UNKNOWN 按 `check_name` 确定性聚合，直接供现有 Domain Gate 使用。
- `compile_solved_utility_ir()` 只校验 Solver 已完成的输出，不做路线求解、不猜测工程事实、不生成占位坐标。

因此“编译 IR 契约”、两井一直管竖向 Solver 和针对当前直管的确定性碰撞/净距检查已实现；路线寻优、多井布置、水力和自动避让仍未实现，不能把当前切片误称为完整市政计算引擎。

### 3.11 市政 Solver v0

Pipeline 接线状态：Playbook 的 `solver/solver_version/input_schema/output/acceptance` 已纳入正式 Plan 协议；`assembly.run_pipeline()` 在 Planner 后、Domain Gate 前执行 Solver。CLI 通过 `--utility-solver-input <json>` 显式提供版本化输入，流水线落盘 `compiled_utility_ir.json` 与 `domain_gate_report.json`。输入缺失不猜测工程事实而保持 UNKNOWN；Solver 已明确的 PASS/FAIL 不能被外部 evidence 覆盖，外部检查器只能补齐 UNKNOWN 项。

`src/openbimagent/utility/solver.py` 已实现第一个确定性市政切片：

- 输入协议为 `StraightGravitySolverInput v0.4`，由 `utility_solver_input.schema.json` 双重门禁；Runtime/Playbook Solver 版本为 `0.4.0`。
- `src/openbimagent/utility/rules.py` 将 Playbook 包内受信任 `knowledge/constraints.yaml` 编译为 `MunicipalRuleSet v1.1`（compiler `0.2.0`），保存 source SHA-256、编译器身份、结构化 `RuleVerification` 和 canonical SHA-256，并由 `municipal_rule_set.schema.json` 校验。
- 障碍物输入只描述类别、几何和工程属性；Schema 明确禁止调用方填写 `rule_id`、`required_clearance_m` 或条款，从信任边界上消除自降限值。
- production 必须同时满足 `confidence=high` 与 `verification.production_eligible()`：标准身份和现行状态、官方公开副本、核验日期/URL、第 4.1.9 条/表 4.1.9、规范内容 SHA-256、原表定位、独立交叉复核和适用条件缺一不可；只调高置信度不能绕过。
- 双 PDF 原表核验后共生产执行 12 条：建筑物 2.5m；给水外径 ≤200mm/＞200mm 为 1.0/1.5m；燃气 low/medium_b/medium_a/sub_high_b/sub_high_a 为 1.0/1.2/1.2/1.5/2.0m；通信 direct_buried/duct 均 1.0m；电力 direct_buried/protective_conduit 均 0.5m。旧通信 0.5m 和旧燃气中压 1.5m 已纠正，电力目标单元格不按电压分档。
- 仅支持单一重力污水系统、两井一直管、DN300 混凝土管；已知两端平面坐标和地面标高，正坡度表示沿 start 到 end 下降。未指定起点内底时计算同时满足两端覆土的最浅剖面。
- `collision_context=null` 表示碰撞范围事实缺失，`clash_free=UNKNOWN`；`coverage=complete` 表示清单完整，空清单可 PASS。表 4.1.9 定义水平净距，AABB 使用 XY 投影矩形，既有直圆管使用 XY 中心线投影并扣除两侧半径；Z 高差不能放大结果，`1e-6m` 容差用于边界判定。
- 规则未晋级、属性缺失、无适用规则或选择歧义同样失败关闭为 UNKNOWN；只有受信任 Rule Set 明确选出 production 规则时才计算 PASS/FAIL。规范允许安全措施减距，但系统尚无独立例外审批协议，因此不会自动放宽。
- 生成管径、坡度、覆土、井距和碰撞 RuleEvidence；净距 Evidence 带标准身份、表号、规范副本 SHA-256、原表定位和 Rule Set hash。水力因缺少输入保持 UNKNOWN，障碍物不混入 `CompiledUtilityIR` 的交付实体。

v0.4 不是路线寻优、自动避让或水力引擎；它补齐了“规范原表核验 → 结构化晋级证据 → 可执行规则集 → XY 水平净距 → Evidence”的受信任规则链。

### 3.12 SCAD 与 Blender 双环

SCAD 环：

- 面向编译后的几何 IR。
- 生成 OpenSCAD、多视图渲染、critic 评分和迭代修复。
- 当前 Planner 语义 IR 常不含 `primitive/size/position`，因此生产主链会跳过 SCAD；要稳定启用必须接 Solver/compiled IR。

Blender 环：

- 为批次设置 editable scope。
- Builder 生成代码并调用 Blender MCP。
- 自动快照和错误回滚。
- 视口截图、指定相机或 turntable 多视角渲染。
- critic 按几何、构图、材质、风格等六维评分。
- perfect score 通过，hard limit/convergence delta 返工，divergence fallback 升级。
- 输出 HTML 验收页并保留 best-so-far。

### 3.13 Blender MCP

Agent Core 的 `BlenderMCPClient` 已实现 stdio 生命周期与工具调用，服务端/addon 侧已具备：

- 能力描述和版本探针。
- 受限 Python 执行。
- AST allowlist。
- editable scope 范围锁。
- 操作前快照。
- 截图、多视角、turntable、回滚。
- 遥测硬关闭与错误归一化。

真实稳定性仍取决于 Blender 宿主、addon 版本和运行环境。

### 3.14 Vectorworks MCP

链路：

```text
Agent Core
-- MCP stdio --> vectorworks-mcp
-- jobs/results 文件 IPC --> Vectorworks runner
-- vs.* --> Vectorworks
```

服务端已实现：

- `ping`。
- `describe_capabilities`。
- 当前正式市政主链使用 typed `execute_plan` 与 approved job；`execute_vs_code(code, approved=False)` 仅保留为历史兼容/受审批逃生路径，不能作为 G6 验收证据。
- `vs_index.json` arity 校验，防止参数错误导致宿主崩溃。
- `full/modeling/minimal` toolset。
- handoff 摘要、参数 hash、高风险审批。
- jobs/results 轮询、running/failed、超时清理。

本轮完成 Agent Core `VectorworksMCPClient`：

- FastMCP stdio connect/close。
- ping + capabilities 双探针。
- MCP 工具白名单。
- 本地 toolset 预检与服务端硬校验双层保护。
- `structured_content`/TextContent 解包。
- validation/gate 错误归一化。
- 审批协议修正：公开 MCP 参数使用 `approved: bool`，服务端内部再转 `_approved=True`。
- fake MCP 契约测试。

尚未完成真实 Vectorworks GUI 宿主端到端冒烟。

## 4. 新接通的 targets 多后端分发

Playbook 可声明：

```yaml
targets: [blender, vectorworks]
```

`assembly/pipeline.py` 现在真正消费该字段：

- `blender` → 现有 SCAD/Blender 批次执行器。
- `vectorworks` → `make_vectorworks_batch_executor()`。
- 多 target 由 `combine_target_executors()` 聚合。
- 任一 target ESCALATE → 整批 ESCALATE。
- 否则任一 target FIX → 聚合带 `[target]` 前缀的返工指令。
- 所有 target PASS → 整批 PASS。
- 声明目标但缺 client 时不静默跳过，明确升级。
- Vectorworks 缺 `vectorworks_builder` 时也升级，因为不能从语义 IR 伪造工程 BIM 代码。
- 未声明 targets 的旧 Playbook 默认 `[blender]`，保持向后兼容。

Vectorworks 成功结果会写入：

```text
batches/vectorworks/batch_XX_vectorworks_result.json
```

2026-08-03 同步：市政 typed `VectorworksBuilder` 和真实宿主 adapter 已实现并通过离线契约，正式链由 `CompiledUtilityIR v1` 生成 `VectorworksExecutionPlan v1`，不再以自由 `vs.*` 脚本作为验收证据；Vectorworks 2024 GUI approved job 仍待执行。

## 5. 最小可用 domain_gate

新增 `src/openbimagent/domain_gate.py`，结论有四态：

```text
PASS / FAIL / UNKNOWN / SKIPPED
```

规则：

- Playbook 未启用硬规则 → `SKIPPED`。
- 所有启用规则均有显式 `True` evidence → `PASS`。
- 任一显式 `False` → `FAIL`。
- 证据缺失、`None` 或无法识别 → `UNKNOWN`。
- `FAIL` 和 `UNKNOWN` 都在后端构建之前阻断。

调用形态：

```python
evaluate_domain_gate(
    {"clash_free": True, "slope_in_spec": True},
    {
        "clash_free": {"ok": True, "source": "solver-clash-v1"},
        "slope_in_spec": True,
    },
)
```

这只是**领域事实裁决器**，不是完整市政 Solver。`constraints.yaml` 中的管径、坡度、流速、检查井间距、雨污分流、覆土和净距规则仍需由 compiled utility IR 与确定性检查器消费并产出 evidence。

## 6. Domain Pack

当前包含：

- `single_asset_hero`：单资产 Blender 英雄镜头验证包。
- `street_block` 等通用场景包。
- `municipal_utility`：市政管网毕设主线。

`municipal_utility` 已具备：

- 双端目标 `[blender, vectorworks]`。
- research / route_planning / clash_check / bim_build / visual_build / deliver 阶段定义。
- `constraints.yaml` 规范规则库。
- IFC 映射知识。
- 碰撞和坡度 domain gate 声明。

仍缺：

- 多井、多管段、路线寻优、复杂标高协调和水力 Solver（两井一直管 Solver v0 已实现）。
- 水平/垂直净距全量确定性执行器（当前已核验并生产执行表 4.1.9 中适用当前污水切片的 12 条建筑物/给水/燃气/电力/通信规则）。
- 规范安全措施减距的独立项目例外、专业审批和审计协议。
- Vectorworks 2024 GUI approved typed job 与真实 `.vwx`/sidecar 验收。
- 真实双宿主语义比较与 G7 最终交付证据。

## 7. Deliver Gate

`deliver/gate.py` 已实现：

- 按文件名、后缀和归一化名称查找交付物。
- 从 Session 获取最后评分。
- 根据 acceptance 阈值判断是否已验收。
- 交付物缺失或评分未达标时不放行。
- deliver 前可由用户审批。

## 8. CLI 与运行方式

入口：

```bash
uv run python -m openbimagent --help
```

主要命令：

```bash
uv run python -m openbimagent run --playbook <path/to/playbook.md> --out <out_dir>
uv run python -m openbimagent sessions
uv run python -m openbimagent tree <session_id> <event_id>
uv run python -m openbimagent export <session_id> <out_path>
uv run python -m openbimagent control attempts --sessions-dir out/sessions --json
uv run python -m openbimagent control lineage <lineage_id> --sessions-dir out/sessions --json
uv run python -m openbimagent control approvals --pending-only --sessions-dir out/sessions --json
uv run python -m openbimagent control resumes --sessions-dir out/sessions --json
uv run python -m openbimagent control steers --request-id <request_id> --sessions-dir out/sessions --json
```

`control` 是 P1d 的只读控制面，不取得 Runtime lease，也不会执行 resume/steer/approval 副作用。P1e 的 `control-write` 只连接由 `runtime-serve` 启动、持有 lease 的本机 Runtime；它不能离线写 Session，也不能越过 Runtime 内存中的 Approval Broker/Steer Queue。P1f 的 `operator-console` 复用相同边界：读侧投影持久事实，写侧只代理到活跃 IPC，不自行构造 Runtime；浏览器只访问本地 HTTP，不读取 IPC token。

CLI 已能完成单 Blender 主链，但当前 CLI 尚未增加 Vectorworks client/builder 与 `domain_evidence` 的配置入口。因此双 target 目前主要通过 Python API 注入，CLI 产品化仍需下一轮接线。

## 9. 测试与当前验证

接管时基线：

```text
324 passed, 1 skipped, 1 warning
```

本轮新增/修改的重点测试：

- `tests/test_dialects.py`：Google Gen AI 文本、多模态、tools、usage、取消。
- `tests/test_vectorworks_client.py`：Agent Core Vectorworks 客户端。
- `tests/test_vw_server.py`：审批参数 contract。
- `tests/test_domain_gate.py`：四态领域门禁。
- `tests/test_target_executor.py`：多 target 聚合和 Vectorworks 执行器。
- `tests/test_orchestrator_dispatch.py`：统一 `judge()`。
- `tests/test_assembly.py`：pipeline domain gate 前置阻断与旧流程回归。
- `tests/test_schema_gate.py`：PLAN 接受 domain_gate。

针对性验证已通过：

```text
Google 方言：9 passed
Vectorworks 协议：14 passed
Domain/targets/judge 集成：59 passed
ruff 针对性检查：All checks passed
```

当前 P1b-A 交付最终验证结果：

```text
主测试套件（排除大型 vs.py AST 索引）：377 passed, 3 skipped
Vectorworks 索引套件：4 passed, 1 warning
合计：381 passed, 3 skipped, 1 warning

真实 freetokenfaucet foreground/background Runtime：2 passed in 4.51s

uv run ruff check src/ tests/ mcp_servers/vectorworks_mcp/
All checks passed!

uv run python -m compileall -q src mcp_servers/vectorworks_mcp
通过（无输出，退出码 0）

git diff --check
通过（仅 Windows LF/CRLF 提示，无空白错误）
```

普通全量测试默认无网络，真实模型测试仅在 `OPENBIMAGENT_RUN_REAL_LLM=1` 时启用。唯一 warning 来自上游 `vs.py` 内容的 `SyntaxWarning: invalid escape sequence`，不影响退出码和本轮实现。

P1b-B Approval Broker 收口后的最终验证结果：

```text
P1b-B 专项及兼容测试：47 passed
无网络主测试套件：390 passed, 3 skipped
Vectorworks 索引套件：4 passed, 1 warning
普通测试合计：394 passed, 3 skipped, 1 warning
真实 freetokenfaucet Runtime：2 passed in 5.11s
ruff / compileall / git diff --check：通过
```

P1c Resume/Steer 收口后的最终验证结果：

```text
P1c 专项及兼容测试：51 passed
无网络主测试套件：397 passed, 3 skipped
Vectorworks 索引套件：4 passed, 1 warning
普通测试合计：401 passed, 3 skipped, 1 warning
真实 freetokenfaucet Runtime：2 passed in 6.77s
P1c 针对性 ruff / compileall / git diff --check：通过
```

全仓 `ruff check .` 仍报告 41 项历史问题，全部位于 `mcp_servers/blender_mcp` 的 fork/vendor 文件；P1c 修改范围的 ruff 为全绿，本阶段未批量改写上游 vendor 代码。

Runtime P0–P1f 的阶段协议、测试演进和最终边界已合并到 `outputs/Subagent Runtime v1完整实施与验收报告.md`。P1e 当时的验收结果为：P1e + CLI + Schema Gate + Runtime 专项 `75 passed`，Vectorworks 文件 IPC 原子归档专项 `8 passed`，全仓 `420 passed, 3 skipped, 1 warning`；P1f 收口时全仓为 `427 passed, 3 skipped, 1 warning`。这些数字是阶段审计记录，当前基线以最新全仓回归为准。

## 10. 实现状态矩阵

| 能力 | 状态 | 说明 |
|---|---|---|
| Clarify | 已实现 | 85 分放行、默认值、问答留痕、fork 续答 |
| Planner | 已实现 | LLM + 模板回退、语义 IR、三件套工件 |
| Schema Gate | 已实现 | 字段级错误、漂移即 FIX |
| Session/Event Tree | 已实现 | JSONL、fork、snapshot、export |
| Provider Registry | 已实现 | profile/fallback/retry/circuit breaker |
| Google Gen AI 方言 | 代码与离线契约已实现 | 待真实 key/model 在线冒烟 |
| OpenAI Chat 方言 | 已实现 | 统一 completion 契约 |
| Anthropic/Responses 方言 | 未实现 | 仍会 NotImplemented |
| Orchestrator | 已实现 | 重试、doom-loop、并发、统一 judge |
| Subagent Runtime v1 | P0 + P1a + P1b-A + P1b-B + P1c + P1d + P1e + P1f 已实现 | 版本化契约、child Session、不可变 artifact、manifest、生命周期与 receipt；background/status/cancel/join、并发≤4、跨进程索引锁、Runtime lease、重启 rehydrate、Approval Broker、显式新 attempt 的 resume、ActorRef、幂等 Resume、只读 Control Plane、按安全轮次边界 steer、loopback Runtime IPC 写控制和本地 Operator Console 已接通 |
| Builder | 已实现 | LLM、AST 预检、模板回退、可选缓存 |
| SCAD Loop | 模块已实现、主链条件启用 | 缺 Solver 几何 IR 时跳过 |
| Blender Loop | 已实现 | 执行、截图、多视角、评分、回滚 |
| Blender MCP | 已实现 | 待持续真实宿主回归 |
| Vectorworks MCP Server | 已实现 | 文件 IPC、arity、toolset、审批 |
| Vectorworks Agent Client | 已实现 | 本轮完成，离线契约通过 |
| targets 分发 | 已接通 | Python API 可双端；CLI 参数尚未产品化 |
| domain_gate 裁决 | 最小可用 | 显式 evidence 四态；不是完整规则计算器 |
| compiled utility IR v1 | 已实现 | 严格契约、Schema Gate、拓扑/坡度数值门禁、canonical hash、evidence 投影 |
| 市政 Solver | v0.4 最小切片已实现 | 两井一直管 DN300 混凝土污水；MunicipalRuleSet v1.1 从受信任 constraints 编译并以 RuleVerification 晋级；表 4.1.9 的 12 条已核验水平净距规则可 PASS/FAIL；XY 几何防止 Z 高差误放行；调用方禁自填限值，例外减距和水力仍 UNKNOWN |
| 市政 Vectorworks Builder | typed plan 与宿主 adapter 已实现 | 离线契约通过；Vectorworks 2024 GUI approved job 待执行 |
| Deliver Gate | 已实现 | 文件 + 分数 + 审批 |
| 通用 AgentLoop 内 MCP/vision/deliver tools | 未接通 | pipeline 已有独立生产链；subagent 已接 Runtime v1 |
| 双宿主真实 E2E | G6 进行中 | Blender 真实执行已通过；Vectorworks GUI 和真实快照比较待完成 |

## 11. 当前最重要的后续路线

### P0：完成当前 M1 G6–G7

1. 在 Vectorworks 2024 空白未命名文档中运行 approved typed job。
2. 验证真实 `.vwx`、sidecar、13/13 receipt、6 个稳定对象、米制单位、records、geometry 和 topology。
3. 幂等重放同一 job，确认没有重复副作用。
4. 将 Vectorworks 真实 `SemanticSnapshot v1` 与 Blender 真机快照严格比较。
5. 完成 G7 全量测试、Schema、静态检查、正式 benchmark、交付清单和最终报告。

### P1：市政毕设深化

1. 扩展多井、多管段、支路与汇流拓扑。
2. 实现路线候选、平面避障、复杂标高和纵断面协调。
3. 建立水力 Solver 输入、工况、Evidence 和 UNKNOWN 边界。
4. 扩充水平/垂直净距及安全措施减距例外审批协议。
5. 扩展宿主构件、IFC 映射和中大型领域 benchmark。

### P2：架构统一与效率

1. 将 `AgentLoop` 的 MCP/vision/deliver 占位工具接到现有 pipeline 模块，消除双轨入口；subagent 已在 Runtime v1 接通。
2. Subagent Runtime 的后台 status/cancel、跨进程锁、重启 rehydrate、Approval Broker、child ask、decision receipt、显式新 attempt 的 `resume`、幂等 key、稳定 actor identity、只读 Control Plane、安全轮次边界 `steer`、单机 Runtime IPC 和本地 Operator Console 已完成；下一步不急于分布式化，转回市政 compiled utility IR 与确定性 Solver 主线。
3. 后续完整 Web/TUI 应复用 Operator Console 已验证的读写边界：ActorRef 必须由可信服务端固定或认证映射，写操作保留稳定 idempotency_key，浏览器不得直接读取 Runtime IPC token。
4. 在双宿主稳定后默认启用 Builder cache 和 orchestrator 并发。
5. 更新根 `README.md` 与 `pyproject.toml` 中仍停留在“M0 骨架/设计阶段”的描述。
6. 将真实冒烟命令、环境依赖和故障恢复固化为验收脚本。

## 12. 一句话结论

这个项目已经不是“只有架构文档的设计稿”，而是具备 Clarify、Planner、Schema、Session、Provider、Orchestrator、视觉双环、Subagent Runtime、两套 typed MCP 主链、RuleEvidence、IFC/IDS、不可变交付和恢复审计的可测试工程 Alpha。当前最近的硬边界是 **Vectorworks 2024 GUI approved job、真实双宿主语义比较和 G7 Beta 候选总验收**；其后才进入多节点市政、水力 Solver、M2 产品化和 M3 评测论文路线。
