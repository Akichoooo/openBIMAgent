# openBIMAgent 项目与 Agent Core 实现详解

> 更新日期：2026-08-01
> 当前判断：**M0 已附条件收官；M1 模块级能力已大体实现并完成首轮产品级接线；Subagent Runtime v1 已收口至 P1d；M1.5 市政管网的 Solver、真实 BIM 构件生成和端到端交付仍未完成。**

## 1. 项目是什么

`openBIMAgent` 是一个面向生成式 BIM / 3D 场景构建的自研 Agent 系统。它不是单纯把一句话交给大模型生成脚本，而是将需求澄清、规划、结构化工件、Schema 门禁、子代理调度、Blender/Vectorworks 执行、视觉自检、领域硬约束和交付检查组成可回放流水线。

核心目标链路：

```text
自然语言需求
→ Clarify 槽位澄清
→ Planner / Scene Graph IR
→ Schema Gate
→ Domain Gate
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
| `relay_workspace/` | 历史并行研发任务、报告和验证产物 |

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
- P1d 新增只读 `ReadOnlyControlPlane` 与 `control` CLI，查询 attempts、lineage、approvals、resumes、steers；投影默认不返回 task/instruction 原文，可与活跃 Runtime 并行读取且不获取 lease。无 IPC 服务前不伪装成跨进程写控制面。
- 可靠性语义采用失败关闭：不可变 Artifact 通过原子“存在即失败”发布；child 声明的输出文件缺失时不会误报 `completed`，而会生成结构化 `FAILED`、错误工件、manifest 和 delivery receipt。
- 10 个 K3/Kimi 既有 `agents/*.md` 均纳入 profile 解析回归，保持 Markdown + YAML frontmatter、禁嵌套和 artifact-mediated 协作约束。

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

### 3.10 SCAD 与 Blender 双环

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

### 3.11 Blender MCP

Agent Core 的 `BlenderMCPClient` 已实现 stdio 生命周期与工具调用，服务端/addon 侧已具备：

- 能力描述和版本探针。
- 受限 Python 执行。
- AST allowlist。
- editable scope 范围锁。
- 操作前快照。
- 截图、多视角、turntable、回滚。
- 遥测硬关闭与错误归一化。

真实稳定性仍取决于 Blender 宿主、addon 版本和运行环境。

### 3.12 Vectorworks MCP

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
- `execute_vs_code(code, approved=False)`。
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

当前接口已打通，但市政领域的生产级 `VectorworksBuilder` 尚未实现；它需要 Solver/compiled utility IR 和 IFC 映射共同驱动。

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

- 路由 Solver。
- 带管径、坡度、覆土、流速、拓扑和坐标的 compiled utility IR。
- 全部约束的确定性执行器。
- 生产级 Vectorworks `vs.*`/IFC Builder。
- IFC/IDS 真实验证。
- 双宿主端到端冒烟与交付证据。

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

`control` 是 P1d 的只读控制面，不取得 Runtime lease，也不会执行 resume/steer/approval 副作用。写控制仍必须在持有 lease 的 Runtime 进程内调用；仓库没有 IPC 服务时不提供会误导调用方的跨进程写 CLI。

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

P1d Actor/Resume 幂等/Control Plane 的最终验收结果：专项及兼容测试 `101 passed`；全仓普通测试 `412 passed, 3 skipped, 1 warning`；源码/测试/Vectorworks 范围 ruff、compileall 与 `git diff --check` 通过。3 个 skip 为显式真实 Blender 1 项和 freetokenfaucet 2 项；本轮环境没有真实模型 key，因此没有把跳过冒充在线通过。完整证据见 `outputs/Subagent Runtime v1 P1d控制面产品化与验收报告.md`。

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
| Subagent Runtime v1 | P0 + P1a + P1b-A + P1b-B + P1c + P1d 已实现 | 版本化契约、child Session、不可变 artifact、manifest、生命周期与 receipt；background/status/cancel/join、并发≤4、跨进程索引锁、Runtime lease、重启 rehydrate、Approval Broker、child ask 转发、decision receipt、显式新 attempt 的 resume、ActorRef、幂等 Resume 和只读 Control Plane、按安全轮次边界 steer 已接通 |
| Builder | 已实现 | LLM、AST 预检、模板回退、可选缓存 |
| SCAD Loop | 模块已实现、主链条件启用 | 缺 Solver 几何 IR 时跳过 |
| Blender Loop | 已实现 | 执行、截图、多视角、评分、回滚 |
| Blender MCP | 已实现 | 待持续真实宿主回归 |
| Vectorworks MCP Server | 已实现 | 文件 IPC、arity、toolset、审批 |
| Vectorworks Agent Client | 已实现 | 本轮完成，离线契约通过 |
| targets 分发 | 已接通 | Python API 可双端；CLI 参数尚未产品化 |
| domain_gate 裁决 | 最小可用 | 显式 evidence 四态；不是完整规则计算器 |
| 市政 Solver | 未实现 | M1.5 核心缺口 |
| 市政 Vectorworks Builder | 未实现 | 需 compiled IR + IFC 映射 |
| Deliver Gate | 已实现 | 文件 + 分数 + 审批 |
| 通用 AgentLoop 内 MCP/vision/deliver tools | 未接通 | pipeline 已有独立生产链；subagent 已接 Runtime v1 |
| 双宿主真实 E2E | 未完成 | 需要 Gemini、Blender、Vectorworks 环境 |

## 11. 当前最重要的后续路线

### P0：真实可运行闭环

1. 给 CLI 增加 Blender/Vectorworks client 创建、`vectorworks_builder` 和 domain evidence 输入。
2. 用有效 `GEMINI_API_KEY` 验证 official profile 的真实模型名、文本、图像和 tool call。
3. 在真实 Vectorworks 宿主跑 `ping → describe_capabilities → execute_vs_code`。
4. 在真实 Blender 宿主跑单资产 Playbook。
5. 跑双 target Playbook，验证部分失败、审批、重试和结果文件。

### P1：市政毕设主线

1. 设计 `utility_compiled_ir.schema.json`。
2. 实现路线、标高、坡度、管径、井位和拓扑 Solver。
3. 将 `constraints.yaml` 编译为确定性规则执行器。
4. 产出 `domain_evidence`，由现有 domain_gate 裁决。
5. 实现市政 `VectorworksBuilder`，绑定 IFC 语义。
6. 接 IFC/IDS 校验和纵断面交付。

### P2：架构统一与效率

1. 将 `AgentLoop` 的 MCP/vision/deliver 占位工具接到现有 pipeline 模块，消除双轨入口；subagent 已在 Runtime v1 接通。
2. Subagent Runtime 的后台 status/cancel、跨进程锁、重启 rehydrate、Approval Broker、child ask、decision receipt、显式新 attempt 的 `resume`、幂等 key、稳定 actor identity、只读 Control Plane 与安全轮次边界 `steer` 已完成；下一步应先增加单机 Runtime IPC 服务，再评估外部持久任务队列和多 Runtime 分布式所有权。
3. 将现有 Control Plane、Approval Broker 与 Runtime 写 API 接到未来 Web/TUI；UI 必须传稳定 `ActorRef` 和 Resume `idempotency_key`，不能依赖显示名或把只读 CLI 误当成写控制入口。
4. 在双宿主稳定后默认启用 Builder cache 和 orchestrator 并发。
5. 更新根 `README.md` 与 `pyproject.toml` 中仍停留在“M0 骨架/设计阶段”的描述。
6. 将真实冒烟命令、环境依赖和故障恢复固化为验收脚本。

## 12. 一句话结论

这个项目已经不是“只有架构文档的设计稿”，而是具备 Clarify、Planner、Schema、Session、Provider、Orchestrator、视觉双环、两套 MCP 和 Deliver 的可测试 Agent 基座。本轮进一步补齐了 Google official 方言、Vectorworks Agent 客户端、双 target 分发、四态 domain gate 和统一 judge。当前最大的边界不在 Agent 框架，而在**市政 Solver/compiled IR、生产级 Vectorworks BIM Builder、CLI 双端配置和真实双宿主 E2E 验收**。
