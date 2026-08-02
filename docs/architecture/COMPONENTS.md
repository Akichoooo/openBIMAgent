# openBIMAgent 组件与运行配置详设

版本:v0.8.4(municipal verified rule promotion v1)· 2026-08-01 · 姊妹篇:[ARCHITECTURE.md](ARCHITECTURE.md)

本文档回答四个问题:**每个组件干什么、每个 agent 怎么配、多厂家模型怎么统管、上下文怎么扛住。**

## 1. 组件总表

| 组件 | 职责 | 技术 | 来源/参考 | 状态 |
|---|---|---|---|---|
| `agent-core` | 极简 agent 循环 + 工具集 | Python 3.11+, uv | pi | M0 |
| ├ `clarify` | 追问:槽位检查、一问一答带默认值、放行评分 | 规则 + 小模型 | openBIMForge clarification-loop 思路 | M0 |
| ├ `planner` | playbook → Scene Graph IR + PLAN.md/TODO.md | 强模型 JSON 输出 | SceneCraft/LayoutGPT | M0 |
| ├ `schema_gate` | 工件 JSON Schema 校验,漂移即 FIX | jsonschema | 05 报告 | M0 |
| ├ `utility` | 市政 Solver v0、compiled IR 契约、跨引用/数值门禁、canonical hash、evidence 投影 | Pydantic + JSON Schema | C2 + municipal utility pack | M1.5(v0 Solver + v1 契约) |
| ├ `orchestrator` | 子代理调度、PASS/FIX/ESCALATE、并发 ≤4 | Markdown 定义角色 | Claude Code subagents + orchestrator.py | M1 |
| ├ `vision` | 双环自检、rubric、收敛 | 移植 + 新写 | forge_core/vision_loop + 03/10 报告 | M0(SCAD)/M1(Blender) |
| ├ `deliver` | 交付门禁,C5 | 确定性检查 | delivery_gate 思路 | M1 |
| ├ `session` | JSONL 树、快照、回放、**多会话存储(侧边栏数据源)** | 纯文件 | pi + 07 报告 schema | M0 |
| ├ `providers` | 多厂家模型统一调用、profile 切换 | 4 种 API 方言 | pi-ai | M0 |
| └ `context` | 上下文预算、自动/手动压缩、handoff | 见 §5 | opencode + pi | M1 |
| `blender-mcp` | Blender 宿主桥 + 预置材质/破损库 | fork ahujasid | ARCH §5(八项改造) | M0 |
| `vectorworks-mcp` | VW 宿主桥 | 自研 FastMCP + 文件 IPC | ARCH §5 | M1 |
| `domain_packs/` | 领域专家包 + 模板族(_base) | Markdown/YAML/JSON | ARCH §4 | M0 三包 |
| `knowledge_base` | **知识库四层**(见下) | 文件/检索,向量库 P2 | openBIMForge 知识资产 | M1 |
| `asset_cache` | 资产本地缓存(hash + 429 退避) | 文件系统 | 06 报告 | M1 |
| `operator-console` | Subagent Runtime 本地操作面板 | Python 标准库 HTTP + 内嵌 HTML/JS | P1d Control Plane + P1e IPC | P1f |
| `server` | 全产品 SSE 事件流 + OpenAPI | FastAPI | 08 报告 schema | M2 |
| `tui` | 完整客户端(**含会话侧边栏**,Codex CLI 风格) | Textual/Rich | pi-tui + Codex | M2 |

### 知识库四层(knowledge_base,用户指定确认需要)

| 层 | 内容 | 来源 |
|---|---|---|
| Prompt 模板库 | playbooks + agents/*.md(模板族即模板库) | 本仓库 domain_packs/ + forge_core/prompts 参考 |
| RAG 知识库 | 规范条文、vs_index、API 坑清单、references.md——**先文件/关键词检索,向量库 P2** | constraints.yaml + openBIMForge rag_index 思路 |
| 示例库 | 黄金截图集(judge 校准)+ approved 资产示例 + 成功代码沉淀(SceneCraft library learning) | 10 报告 §3 + 06 报告 |
| 组件模板 | typology 模板(保底生成)+ GeoNodes/材质预设 | forge_core/knowledge/typologies、component_patterns |

## 2. Agent Core 内部模块规格

### 2.1 loop(极简循环)

- 工具集(≤8):`read / write / edit / bash / mcp_call / vision_check / subagent / deliver`。
- system prompt + 工具定义 < 2000 token;状态外置,中断恢复 = 重读文件 + session 树定位。

### 2.2 clarify(追问)

规则抽取(zh/en)→ 缺口判定 → CLI 一问一答(每槽位带默认值,回车接受)→ 回填 → `completion_score` ≥ 85 放行。追问全程写 session 树,可 `/tree` 回改重跑。

### 2.3 planner

输出三件套:**Scene Graph IR**(JSON,资产+空间约束)、`PLAN.md`、`TODO.md`。只出语义不出坐标(C2);批次粒度 = 一次渲染检查单位。

### 2.4 utility(compiled utility IR v1 + Solver v0)

- `contracts.py` 定义严格、冻结、禁止额外字段的 `CompiledUtilityIR`，包含 source hash、坐标参考、UtilitySystem、Node/Port、PipeSegment 和 RuleEvidence。
- 运行时失败关闭：ID 唯一；节点、端口、管段、系统和证据引用必须闭合；管段 centerline 必须连接端口；水平长度、起终标高和坡度必须一致；重力流不允许逆坡；NaN/Infinity 不进入协议。
- `canonical_json()/canonical_sha256()` 对系统、节点、端口、管段和证据按稳定 ID 排序，确保相同网络不因输入数组顺序产生不同摘要。
- `domain_evidence()` 按 `check_name` 聚合逐对象证据，FAIL 优先、UNKNOWN 次之、全 PASS 才通过，可直接交给现有 `evaluate_domain_gate()`。
- `compile_solved_utility_ir()` 是最小编译门禁，不负责路线求解，不推断工程属性，不生成占位坐标。
- `rules.py` 定义 `MunicipalRuleSet v1.1`、`CompiledClearanceRule`、`RuleVerification` 与选择结果四态。compiler `0.2.0` 读取受信任 `constraints.yaml`，严格核对 rule ID、category、parameter、unit 与数值形态，保存源 SHA-256、编译器身份、逐规则核验证据和 canonical SHA-256；规则按稳定 `rule_key` 排序。
- production 晋级门禁：`confidence=high` 只是必要条件，还必须由 `verification.production_eligible()` 确认标准身份/状态、官方公开副本、核验日期和 URL、第 4.1.9 条/表 4.1.9、内容 SHA-256、原表定位、独立交叉复核及适用条件完整。任何证据漂移都降为 `review_required`，不能靠手改置信度绕过。属性缺失、无适用规则或多条同时适用分别为 `review_required/unsupported/ambiguous`，均不能产生生产 PASS/FAIL。
- 双 PDF 原表核验后共编译 12 条 production 规则：建筑物 2.5m；给水外径 ≤200mm 为 1.0m、>200mm 为 1.5m；燃气 low/medium_b/medium_a/sub_high_b/sub_high_a 分别为 1.0/1.2/1.2/1.5/2.0m；通信 direct_buried/duct 均为 1.0m；电力 direct_buried/protective_conduit 均为 0.5m。旧通信 0.5m 和旧燃气中压 1.5m 已纠正；电力目标单元格不按电压分档。
- `solver.py` 定义 `StraightGravitySolverInput v0.4` 与 `municipal-straight-gravity-solver v0.4.0`：限定两井一直管 DN300 混凝土污水，根据两端平面坐标、地面标高、设计坡度和地表类型计算最浅合规内底标高。障碍物只携带类别、几何及 `outer_diameter_mm/pressure_class/burial_method/voltage_kv` 等工程事实，不能携带 `ClearanceRule`。
- `collision_context` 缺失时 `clash_free=UNKNOWN`；`coverage=complete` 时按表 4.1.9 的水平净距语义，在 XY 平面计算设计管到 AABB 投影矩形或既有管投影中心线的实体表面距离并扣除管半径，Z 高差不参与。规则选择为 production 时逐障碍物生成 PASS/FAIL RuleEvidence；未晋级或选择不完整时生成 UNKNOWN。显式完整空清单可 PASS，等于限值按 `CLASH_TOLERANCE_M=1e-6` 通过。
- 障碍物保持为 Solver Context，不进入 `CompiledUtilityIR` 交付实体集合；Evidence 保存障碍物 ID、实测水平净距、规则键、标准/表号、规范副本 SHA-256、原表定位和 Rule Set hash。安全措施减距尚无独立审批协议，不能自动放宽；水力参数仍缺失，`hydraulics_in_spec=UNKNOWN`。
- Pipeline 只在 Playbook 声明 Solver 时执行，并把 `rule_source` 限制在当前 Domain Pack 内，拒绝绝对路径、`..` 越界、缺文件、Rule Set Schema/版本漂移。结果落盘 `municipal_rule_set.json`、`compiled_utility_ir.json` 与 `domain_gate_report.json`；Solver 已判定的 PASS/FAIL 不允许被外部 evidence 覆盖，外部检查器只能补齐 UNKNOWN。

### 2.5 assembly.vectorworks_plan(G1 typed 宿主边界)

- `VectorworksBuilder` 只接受通过严格模型校验的完整 `CompiledUtilityIR v1`，按 stable ID 排序并生成 `VectorworksExecutionPlan v1`；计划显式包含对象类型、图层、分类、记录、IFC 语义、单位、坐标、尺寸和拓扑，不含自然语言命令或自由脚本。
- `vectorworks_execution_plan.schema.json` 与 Pydantic 模型共同限制操作 allowlist：`create_object`、`set_record`、`connect_topology`；未知操作、未知对象类型、版本漂移、字段缺失、hash 篡改和引用不闭合均失败关闭。
- canonical SHA-256 排除其派生的 `plan_id/idempotency_key`，相同 IR 不受集合顺序影响；幂等键固定为 `vw-plan:<sha256>`。宿主执行前必须对协议版本、Vectorworks 2024 API、单位和 capability allowlist 预检。
- `FakeVectorworksExecutor` 提供离线 receipt、部分成功注入、补偿提示和幂等恢复；重试跳过已应用 operation，完成后同计划复用 receipt，同 stable object ID 不同语义严格冲突。
- Pipeline 的 typed 主路径只注入 Solver 生成的完整 `CompiledUtilityIR`；缺失时拒绝拿 Scene Graph IR 顶替。旧三参数 `vs.*` Builder 仅保留兼容路径，不能作为 G1 证据，typed executor 也不得回退 `execute_code`。

### 2.6 assembly.semantic_snapshot(G3 双宿主语义一致性)

- `SemanticSnapshot v1` 以 stable ID 表达 system/node/port/segment，携带位置或中心线、拓扑、直径、长度、起终标高、坡度、工程材质、IFC 分类、领域属性和 canonical `source_ir_path`；Pydantic 与 JSON Schema 均禁止额外字段和非有限数值。
- `FakeBlenderSemanticExecutor` 直接从严格 `CompiledUtilityIR` 生成离线 Blender 语义；`FakeVectorworksSemanticExecutor` 必须执行 `VectorworksExecutionPlan`，再从 fake executor 的对象、记录和连接状态反投影，避免直接复制 IR 掩盖 Builder 丢字段。
- `compare_semantic_snapshots()` 按 stable ID 和字段路径递归比较，报告对象 ID、字段、左右值及双方 IR 来源。允许差异固定为宿主内部 `host_handle` 与表现层 `presentation_material`；坐标、尺寸、坡度、拓扑、工程材质、分类和领域属性均不可忽略。

### 2.7 deliver.manifest(G2 统一不可变交付)

- `ArtifactManifest v1.1` 是 Pipeline、Core Loop 与 Subagent Runtime 的统一协议；`ArtifactRecord` 记录受控相对路径、媒体类型、生成者、source attempt、依赖、状态、size 与 SHA-256，Manifest 记录 lineage/attempt/resume、幂等键、语义摘要和 Domain Gate 状态。
- `commit_delivery_manifest()` 只接受 workdir 内相对路径，重算源文件 hash 后复制为不可变副本；同键同语义复用、同键异义冲突、不同键保留独立历史。工件已发布但 Manifest 未发布时，可在 expected SHA-256 一致的前提下恢复记录。
- 完成态 Manifest 只接受 completed 工件。已声明领域门禁时必须为 `PASS`；未声明时只接受并保留 `SKIPPED`。`domain_gate_required` 与状态必须严格匹配，`FAIL/UNKNOWN` 不能交付，调用者也不能用 `SKIPPED` 绕过实际门禁。
- `deliver.gate` 返回验收通过的解析路径；Pipeline 仅在 C5 accepted 且 plan 成功后提交 Manifest。Core Loop 的 `deliver` 工具使用结构化工件列表、稳定幂等键和显式 `PASS`，不接受自由清单文本。

### 2.8 orchestrator(子代理调度)

- 角色文件 = Markdown + YAML frontmatter。基础字段:`name/model/tools/permissions`;Runtime v1 字段:`context_mode/max_turns/artifact_contract/nesting`;正文为 system prompt。角色文件是受信任的能力上限,调用者不能通过请求提升 model/tools/permissions。
- 派发:PASS / FIX(带可执行返工指令)/ ESCALATE(升模型或问人);禁嵌套;并发 ≤4。
- **子代理返回** = 结构化摘要 + 工件路径 + **<200 字核心提示/警告**;原始过程留 child session,父代理按需深翻。
- **Runtime v1**:`contracts.py` 定义版本化 Request/Handle/Result;`runtime.py` 创建真实 child Session 并执行受控 child runner;`artifacts.py` 原子提交不可变工件和 `manifest.json`(size + SHA-256)。请求、结果、manifest 均经过 Schema Gate。
- **P1a 后台生命周期**:`LocalSubagentRuntime.submit/status/cancel/join` 使用最多 4 worker 的进程内线程池;取消信号传到 child Loop/Provider。终态与 Manifest、lifecycle、receipt 同步发布。
- **P1b-A 持久恢复**:`state.py` 将 background Request/Handle/status/result 以原子 JSON 写入 `sessions/_runtime`；`runtime.py` 在独占 `RuntimeLease` 内 rehydrate。终态不重跑，`finalizing` 幂等补签，遗留运行任务以 `RuntimeRestarted` 失败关闭；损坏记录严格失败。`SessionStore` 以进程内共享锁、跨进程文件锁和原子替换保护 `sessions/index.json`。
- **P1b-B Approval Broker**:`approval.py` 定义 `ApprovalRequest/DecisionReceipt` v1。child `Permission.ASK` 经父 Session 转发，支持旧 bool callback 和父侧 `pending_approvals()/decide_approval()`；超时、取消、回调异常和 Runtime 重启均失败关闭。恢复时对账父子事件并补齐单边 receipt，冲突事实严格失败。工具参数仅落字段类型摘要与 SHA-256。
- **P1c Resume/Steer**：`control.py` 定义 `ResumeRequest/ResumeReceipt/SteerDirective/SteerReceipt`。每次 `resume` 创建新 `request_id/agent_id/child_session_id`，共享 `lineage_id` 并递增 `attempt_number`；旧 Artifact 只按路径和 SHA-256 引用，任务前缀禁止假设或重放旧副作用。`steer` 同时绑定 request/agent/child/lineage/attempt，只在 `AgentLoop` 下一轮 Provider 调用前消费，不插入 Provider 请求或同一工具批次中间；取消、终态、身份不匹配或 Runtime 重启签发失败回执，历史 accepted 指令不重新入队。
- **P1c 恢复与审计**：RuntimeState 持久化 resume request/receipt 并在重启时幂等补齐父、source child、new child 三方事件；steer 请求/回执对账父子 Session，单边缺失自动补齐、冲突事实严格失败。遗留非终态 attempt 仍按 P1b-A 失败关闭，绝不自动重新提交模型或工具。
- **P1d Actor 与 Resume 幂等**：`actor.py` 定义版本化 `ActorRef`；Approval、Resume、Steer 新事实使用稳定 `actor_id/actor_type`，历史字符串兼容读取为 `legacy`。Resume 强制调用方 `idempotency_key` 和 `instruction_sha256`；相同 actor/key/语义返回原 Handle/Receipt，不同语义失败，RuntimeState 使重启后仍可复用。
- **P1d Control Plane**：`control_plane.py` 从 RuntimeState 和父/子 Session 构建只读投影，查询 attempts、lineage、approvals、resumes、steers，去重重复事实并对冲突/损坏失败关闭；默认视图不返回 task/instruction 原文。CLI `control` 子命令支持文本/JSON，查询不获取 Runtime lease。
- **P1e Runtime IPC**：`ipc.py` 定义版本化 `IpcRequest/IpcResponse/IpcDiscovery`，以及 loopback-only `RuntimeIpcServer/RuntimeIpcClient`。`runtime-serve` 持有 Runtime lease 并启动 IPC，`control-write` 只经 discovery/token 调用该实例。服务路由 `approval.decide/attempt.resume/attempt.steer/attempt.cancel`，按稳定 ActorRef 与调用方幂等键拒绝重放冲突；协议限制消息大小、socket 超时和 payload 白名单，认证错误不回显输入或 token。
- **P1f Operator Console**：`console.py` 将 `ReadOnlyControlPlane` 和 `RuntimeIpcClient` 组合为独立 loopback HTTP 操作界面。GET snapshot 展示 attempts/approvals/resumes/steers；POST control 代理 Ping、Approve/Reject、Resume、Steer、Cancel。浏览器不读取 IPC discovery/token，ActorRef 在 Console 启动时固定；写请求必须通过 Host、Origin、CSRF、Content-Type、请求大小和契约校验。服务使用标准库与内嵌静态页面，不新增 Web 框架或 Node 构建链，也不获取 Runtime lease。
- `AgentLoop.subagent` 支持 `dispatch/status/cancel/join/resume/steer`;resume 必须带稳定 `idempotency_key`；dispatch 对模型只暴露 `role/task/context_mode/execution_mode/artifact_contract`,所有 child AgentLoop 都移除 `subagent` 工具以维持禁嵌套。

### 2.6 vision(双环自检 + 评分分层)

- 评分分层:确定性维由 Solver/规则执行器针对 compiled utility IR 生成 evidence，再走 domain_gate 四态裁决；软评分维走 VLM 六维。
- 两环维度裁剪与 rubric 定稿见 ARCH §3;收敛四选一 + best-so-far(ADR-0004)。
- **critic 强制 CoT**;评分事件落盘 `rubric_scores` + `reasoning` + `anchor_ref` + `actionable_feedback`。
- 环阈值在 playbook `acceptance`;超限 ESCALATE 不死循环。

### 2.7 session(trace + 多会话,schema 定稿自 07 报告)

每条记录 `{id, parentId, timestamp, type, payload}`:

- `type: message` → `payload.role`、`gen_ai.request.model`、`gen_ai.usage.*` 等 OTel 字段。
- `type: tool_call` → `payload.toolCallId`、`payload.toolName`、参数字段类型摘要和 `args_sha256`；不保存原始工具参数。
- `type: custom` → `customType: screenshot` / `score` / `patch` / `snapshot` / Subagent lifecycle / `artifact_committed` / `delivery_receipt` / `approval_requested` / `approval_decided`(字段见 ARCH §6)。

**多会话**:每会话一个 JSONL 文件 + `sessions/index.json`(id/标题/ playbook/最后活跃/分支摘要/`child_of`)——TUI 侧边栏与未来 Web UI 会话列表的数据源;`/sessions` 列出,点击跳转。单文件原地分支(`/tree`);快照在每次 MCP 写操作前自动落盘;M3 按需导出 BIMBench 评测格式。索引写入受同进程共享 `RLock` 与跨进程文件锁双重保护，并通过临时文件、`fsync`、`os.replace` 原子发布。

## 3. 内置 agent 规格(角色-模型绑定,定稿)

| 角色 | 默认模型 | 工具 | 输出工件 | 备注 |
|---|---|---|---|---|
| `clarify` | gemini-3.5-flash | read | 槽位填充表 + 追问 | 高频便宜 |
| `planner` | gemini-3.1-pro | read, write | Scene Graph IR + PLAN/TODO | |
| `researcher` | gemini-3.1-pro(联网) | web_search, fetch, write | references.md + 资产缓存 | |
| `modeler` | **gemini-3.1-pro** | mcp_call, read, write, bash | .blend 资产 / vs 语义 | **质量咽喉,不降 Flash**(已拍板;实现提示词硬性写明) |
| `materialist` | gemini-3.1-pro | mcp_call, read | 材质/磨损参数 | **只调预置材质库/GeoNodes 参数,禁手写节点树与 boolean** |
| `lighter` | gemini-3.1-pro | mcp_call, read | 灯光/机位/相机轨迹 | |
| `critic_scad` | gemini-3.5-flash | vision_check | 两维评分 + patch 建议 | 高频 |
| `critic_render` | gemini-3.1-pro | vision_check | 六维评分 + FIX 指令 | 强制 CoT + 防放水五件套 |
| `orchestrator` | glm-5.2 | subagent, read, write | PASS/FIX/ESCALATE | |
| `deliver` | glm-5.2 | read, bash | 交付核对报告 | |

## 4. 多厂家模型统一配置(providers,双通道)

官方搭档(Aider Architect/Editor 模式):**Pro 出规划与建模,Flash 跑高频杂活,GLM 当调度**。
通道分两档(用户指定 agentrouter 为联调测试通道,额度不高):

```toml
# config/models.toml(数值为 09 报告 2026-07-21 调研值,未经官方控制台确认)
# —— 通道一:官方(GLM/ Gemini 直连,正式跑批用)——
[providers.glm]
type = "openai-compatible"
base_url = "https://open.bigmodel.cn/api/paas/v4"
api_key_env = "GLM_API_KEY"

[providers.gemini]
type = "google-genai"
api_key_env = "GEMINI_API_KEY"

# —— 通道二:agentrouter 聚合端点(联调测试,低额度;方言/路径以控制台文档为准)——
[providers.agentrouter]
type = "openai-compatible"          # 待核实:若实为 Anthropic 方言,dialects 层映射
base_url = "https://agentrouter.org/v1"   # 待核实准确路径
api_key_env = "AGENTROUTER_API_KEY"

[models."glm-5.2"]
provider = "glm"
context_window = 1_000_000          # 调研值,待确认
capabilities = ["tools"]            # 不支持 vision(用户控制台确认 2026-07-21)
cost_per_mtoken = { input = 0.84, output = 2.64 }

[models."gemini-3.1-pro"]
provider = "gemini"
context_window = 1_000_000
capabilities = ["tools", "vision"]
cost_per_mtoken = { input = 2.0, output = 12.0 }

[models."gemini-3.6-flash"]
provider = "gemini"
capabilities = ["tools", "vision"]  # 用户持有的最新模型;context/价格待官方文档确认

# agentrouter 通道模型(额度低:glm 扛高频,opus 只在质量咽喉,gpt-5.5 做评判)
[models."glm-5.2-ar"]
provider = "agentrouter"
capabilities = ["tools"]            # 不支持 vision(控制台确认)

[models."claude-opus-4-8"]
provider = "agentrouter"
capabilities = ["tools", "vision"]

[models."claude-opus-4-6"]
provider = "agentrouter"
capabilities = ["tools", "vision"]  # 降级链

[models."gpt-5.5"]
provider = "agentrouter"
capabilities = ["tools", "vision"]

[profiles]                          # OPENBIMAGENT_PROFILE 切换,默认 official
[profiles.official]
orchestrator = "glm-5.2"
planner = "gemini-3.1-pro"
modeler = "gemini-3.1-pro"
critic_scad = "gemini-3.6-flash"
critic_render = "gemini-3.1-pro"
clarify = "gemini-3.6-flash"

[profiles.test]                     # agentrouter 联调:省额度;judge 与生成模型分家(Claude 生成 → GPT 评判)
orchestrator = "glm-5.2-ar"
planner = "glm-5.2-ar"
modeler = "claude-opus-4-8"         # 质量咽喉上最强
critic_scad = "gpt-5.5"
critic_render = "gpt-5.5"
clarify = "glm-5.2-ar"

[fallbacks]
"gemini-3.1-pro" = ["glm-5.2"]
"gpt-5.5" = ["claude-opus-4-6"]
"claude-opus-4-8" = ["claude-opus-4-6", "glm-5.2-ar"]

[resilience]                        # 韧性集中在 providers 层,业务代码不重复造
retry = { max = 3, backoff = "exponential", base_ms = 1000 }
timeout_s = 120
circuit_breaker = { failures = 5, cooldown_s = 300 }

[tracking]
log_tokens = true
log_cost = true
```

要点:韧性集中 providers 层;`capabilities` 决定谁能进 vision 环/当子代理;模型目录与代码分离(models.dev 思路);全程可 abort 且返回部分结果。

## 5. 上下文管理(定稿)

| 层 | 预算策略 |
|---|---|
| system prompt + 工具 | < 2k token;MCP 工具 ≤12;VW 工具集预设 |
| 知识/参考 | 渐进披露:知识库四层(§1)全部文件化,模型按需 `read`/`append`,不预注 |
| 会话历史 | 阈值**自动压缩**(compaction 子代理,有损摘要)+ `/compact` **手动压缩**;JSONL 全史永留,`/tree` 可溯 |
| 视觉输入 | 截图降采样进上下文;每批评分只传当批视角,原图全尺寸只落盘 |
| 跨模型交接 | artifact-mediated:工件 + 摘要,不直接传上下文(见 §6) |
| 多会话 | 每会话一 JSONL + index.json;侧边栏跳转不加载全史,按需打开 |

## 6. 多模型沟通设计(定稿)

**artifact-mediated(工件介导)协作**:模型之间不直接对话,交接媒介 = 工件文件 + session 树;子代理返回结构化摘要 + 工件路径 + <200 字核心提示;换模型时 thinking 转文本标签(pi 规则);工件过 Schema 门禁防漂移。与接力开发流同构:人机、机机同一套「写工件→审工件」协议。

## 7. 安全与权限(定稿)

- 权限三态(ask/allow/deny)+ 工具 glob;默认:读 allow、MCP 写 ask、`execute_*_code` ask。
- `execute_blender_code` / `run_script`:AST allowlist + 操作前快照 + 危险 API(文件系统/网络/子进程)审批。
- **范围锁**(社区越界投诉对应):blender 侧可编辑集合白名单,Agent 不得触碰集合外对象;VW 侧同理按 handoff 范围。
- VW 侧 handoff/hash/approval 重验原样继承。
- doom_loop:同一资产连续 N 次 FIX 无进展 → ESCALATE 问人。
