# 未实施项登记（明确结论，非遗漏）

> 来源：2026-09-19 对《软件学报》大模型与软件工程专题论文 + Codex/Claude Code/opencode/pi/OpenClaw/dsh/grok build 七工具调研的吸收清单（原 81 项）。
> 本文档只登记**经决策不实施或暂缓**的项及其明确结论；已实施项见 `git log feat/agent-engineering-absorption`（17 个提交）。
> 纪律：每条给出「未实施原因」与「触发条件」——不是 TODO 池，而是有结论的决策记录。

## 0. 本轮已实施概览（对照用）

已落地 40+ 项，关键包括：session 事件 `schema_version` + 邻接迁移链、压缩 token 预算 + split-turn elision、OBSK 结构化溢写、doom-loop、stop-gate、WORKSPACE.md 项目指令、`lookup_api` 懒发现、子代理 `output_schema` 契约、投影式 rewind、fork interrupted 语义、无 LLM 会话摘要、prompt 快照、clarify 表达特征规则库 + 小模型兜底、归档结构化对齐检索、headless `oba agent`、SQLite 幂等 store、SSE `server.connected`、MCP 健康面板、写入自保护、critic 规则回溯、操作复杂度指标、组织级 requirements 锁、path-scoped 规则、`/api/v1/context`、Project Trust、反向 MCP serve、记忆 provenance、durable mailbox、JSON 修复重试、sibling roster、宿主事件通道、审批 fail-closed。

另附两个**已修复的真实缺陷**（实施 F4 时发现）：
1. `check_permission` 规则值未归一化 → 传字符串规则时 `perm is Permission.ASK` 判空，**审批门被静默跳过**（fail-open）；
2. 审批回调抛异常时异常逃出权限门 → **审批通道崩溃等于放行**（现改为 fail-closed 拒绝）。

## 1. 安全 / 权限

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| F7 | OS 级沙箱（Landlock+seccomp / Seatbelt / Windows Restricted Token + Job Object） | 已有三层防线：`execute_plan` typed-only 白名单、`vs_index` arity 预检、F3 写入自保护 + F8 组织锁 | L 级工程（进程树改造 + 平台原语 + 网络代理），需与宿主宿主进程管理联动，单批实施会半途而废 | 对外发布/多租户部署，或接入第三方不可信宿主时 |
| F2 | 学习式 amendment（审批通过后自动提议记忆 allow 规则；宽泛前缀永不放行） | 审批门 + 组织锁已具备；缺"从审批历史学习规则"层 | 收益<风险：自动放宽权限规则需先有 F7 的沙箱兜底，否则等于把 fail-closed 改成 fail-open 的温床 | F7 落地后随批实施 |
| F6 | 供应链加固（依赖精确 pin、`min-release-age`、lifecycle script 白名单、`--ignore-scripts`） | `uv.lock` 已锁定；无发布期门禁 | 当前为单机研究阶段，风险面小；实施需改 CI 与安装流程 | 对外分发/CI 公开化时 |

## 2. 会话 / Trace

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| B2 | "model-visible means logged" 运行时断言 | 压缩/工具结果均落 session，语义已满足但无强制断言 | 断言会在历史消息被合法裁剪时误报；需要先把"合法裁剪"与"未落盘"区分开 | 出现一次"模型看到了 log 里没有的内容"类事故时 |
| B5 | 弃用分支生成 `branch_summary` | fork/branch/rewind 已完整；被弃分支仍可 `/tree` 回访 | 旧分支内容不丢失（可回访），摘要属锦上添花 | 方案比选场景变多、需要在新分支携带旧分支结论时 |
| B6 | 会话目录多文件拆分（summary.json/signals.json/rewind_points.jsonl） | 单文件 JSONL + index.json 元数据已满足检索与续跑 | M 级重构且会动 session 格式（已上 v2 版本链，可做但收益低） | 会话文件规模>50MB 或索引读取成为瓶颈时 |
| B10 | 事件三域语义显式化（durable facts / live events / capability hooks） | 事实上已分域（JSONL 事实 / SSE 投影 / HookBus） | 纯文档化收益，代码已按此分层 | 编写架构文档时顺带 |

## 3. 上下文 / 压缩

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| C3 | 溢出时从头裁剪保 prefix cache | C1/C2 的 token 预算 + elision 已覆盖绝大多数溢出场景 | 属性能微优化（省 cache 命中率），当前无实测证据表明必要 | 出现 provider 侧 cache miss 计费显著上升时 |
| C4 | 压缩请求用新 session ID + 禁 cache 写 | 摘要请求独立于主链 | 同上，且需改 providers 层路由参数 | 同上 |
| C10 | 精确 tokenizer（现为 UTF-8 字节上界） | 已按字节上界保守估算（防中文低估） | 需引入各供应商 tokenizer 依赖；上界法已 fail-safe（偏保守） | 出现因估算偏保守导致过度压缩的实际案例时 |

## 4. 编排 / 多 agent

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| D3 | 子代理继承父级 MCP 连接管理器 | 父级 loop 持 `mcp_clients`；child 经 `_default_child_runner` 构造时**未透传 `mcp_clients`**（已核实） | 需要先定义"多 child 共享宿主连接的并发写"协议（宿主写是串行资源），否则共享即竞态 | 领域 agent 需要直接调宿主（当前建模写路径由父级 typed plan 统一执行，child 只读）时 |
| D6 | capability fail-loud 全面显式化 | `output_schema` 已 fail-loud；其余能力（toolFilter/persona）以 frontmatter ceiling 静态约束 | 现有 ceiling 已 fail-closed（越权配置直接拒绝加载） | 增加新能力维度时沿用同一模式 |
| D7 | continuable children + Activation | **设计上已满足**：`resume`（新 attempt + lineage）+ `steer`（step 边界注入）+ checkpoint 冷恢复 = dsh 语义 | 无需新增代码 | — |
| D8 | Agent Teams 任务 DAG + `writeScopes` advisory 前缀 | durable mailbox 已落地并测试；DAG/writeScopes 未做 | 当前单 orchestrator 派发（并发 ≤8）下无多写者冲突场景 | 多团队并行改同一模型、出现写作用域重叠实际冲突时 |
| D9 | persona 声明式 inputs/outputs 契约 | `SubagentRequest/Result` DTO + `artifact_contract` 已约束；缺声明式文件契约 | artifact-mediated 原则已覆盖"产物传递"，声明式契约收益有限 | agent 流水线需要自动串联（A 的输出自动成为 B 的输入）时 |
| D11 | 独立 evaluator 每 turn 复查目标（/goal 式） | stop-gate 机制已落地（可挂任意 gate 回调）；未接具体 evaluator | 需要先有可机读的"目标条件"表达（当前是自然语言 brief） | 长任务（>10 步）常态化后 |
| D12 | Guardian 策略分类器（risk/authorization 标签） | 权限三态 + 组织锁 + 自保护已覆盖已知高危面 | 分类器需独立模型调用（成本）且规则面尚未穷举 | 接入外部不可信输入（用户上传的宿主工程/脚本）时 |
| D14 | 开放文本自一致性聚合（多采样投票） | 多裁判 critic 已有 A/B swap 防偏置 | 采样成本 ×N，且当前任务多为确定性门禁可判 | 出现"门禁全过但结论仍错"的主观判断类任务时 |
| D15 | D-Bot 交叉审查（专家互评直到无新意见） | doom-loop + critic 返工环已控制收敛 | 同上（成本），且当前单 critic 角色已分家（生成/评判物理隔离） | 多领域联合评审场景（结构+机电+市政）出现时 |

### 4.1 双轨编排现状与收敛路径（2026-09-21 补记）

代码库目前存在**两条编排轨**，均有真实调用方，属有意分层而非重复建设：

| 轨 | 入口 | 驱动方式 | 治理面 |
|---|---|---|---|
| 模板流水线轨（M0/M1） | `assembly/pipeline.py run_pipeline` | playbook → clarify → planner.instantiate → schema_gate → `orchestrator/dispatch.py run_plan`（批次 PASS/FIX/ESCALATE + doom_loop，可选 concurrent=True 并发 ≤4）→ domain_gate → deliver 门禁 | **不经过** `core/loop.py` 的 8 工具治理面：权限三态、审批 fail-closed、stop-gate、压缩预算均不生效；批次执行器直连宿主 client 与审批回调 |
| 通用 AgentLoop 轨 | `core/loop.py AgentLoop` + `orchestrator/runtime.py LocalSubagentRuntime` | 8 工具循环；子代理经 SubagentRequest/ResultEnvelope 契约、独立 child session、工件清单、后台线程池（并发 ≤4） | 完整：权限门、审批 fail-closed、stop-gate、doom-loop、output_schema 终态校验 |

两轨并存的原因：模板轨服务"确定 playbook → 可复放交付"的研究管线，步骤与产物清单固定，绕开通用工具循环可减少方差、保证 benchmark 可复现；AgentLoop 轨面向开放式任务，必须挂全治理面。**治理缺口集中在模板轨**：builder/critic 双环直接持有宿主写能力，绕过 `loop.py` 权限门——目前靠 typed plan 白名单、F3 写自保护、F8 组织锁兜底，风险可控但属事实缺口，不是设计完成态。

收敛路径建议（暂不实施，触发条件：模板轨需要接入审批/权限语义或对外开放调用时）：
1. 把模板轨批次执行器改造为经 `LocalSubagentRuntime` 派发的 child（复用 child session、工件清单、审批 broker），`run_plan` 退化为纯裁决环；
2. 宿主写统一走 typed plan 单写路径，使权限门对两条轨同一生效；
3. 收敛前模板轨保持 `run_plan` 顺序执行——批次执行器共享宿主场景、`.blend` 输出与 `out/batches` 工作目录，并行不安全；dispatch 的 `concurrent=True` 仅供批次真正独立的调用方显式启用。

## 5. 记忆

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| E3 | Dreaming 后台巩固 pass（确定性门筛 → LLM 合并重写 → content-hash 乐观并发） | provenance 分级 + recall-loop 防护 + 审批写入已落地；缺"后台巩固重写" | L 级：涉及记忆重写（有损）、人工编辑冲突检测、pre-image 存档三套机制 | 记忆文件接近 256KB 上限或出现条目冲突需要合并时 |
| E6 | 索引限载（200 行 / 25KB） | 单文件 256KB 硬限 + 末 N 条注入已防爆上下文 | 现有两道上限已达成同一目的 | 记忆条目数 >500 时 |

## 6. Server / Workbench

| 编号 | 事项 | 现状基础 | 未实施原因 | 触发条件 |
|---|---|---|---|---|
| A7 | CLI attach 复用常驻 server（避免 MCP 冷启动） | `runtime-serve`（常驻 IPC owner）+ `operator-console` 已提供常驻形态 | headless `oba agent` 与 server 是两种运行形态，attach 需先定义"多入口共享 lease"的会话归属协议 | MCP 冷启动成为批量脚本瓶颈时 |
| G4 | OpenAPI 全 schema 驱动（paths 自动生成 + SDK） | components 已由 pydantic 生成；paths 为手写只读基线 | 只读面很小（<20 端点），手写基线可审计性更强 | 端点规模翻倍或出现多语言客户端需求时 |
| G5 | 事件 `seq`/`stateVersion` + 断档拉快照 | Last-Event-ID/cursor 回放已实现；无单调 seq | 文件投影天然有序（JSONL 追加序即 seq），额外序号冗余 | 引入多写入方或分区存储时 |
| G7 | 同步/异步双端点（prompt 204 + 异步轮询） | `POST /runs` 已异步（后台线程 + 状态查询），等价语义已存在 | 命名/形态差异，无功能缺口 | — |
| G9 | Gateway 拥有会话、客户端只是投影 | FastAPI 服务 + session JSONL 已构成单一事实源 | 多客户端（web/CLI/IDE）同时在线场景尚未出现 | 引入 IDE 插件或 IM 渠道时 |
| G8 | `/debug-config` 配置层来源可视化 | 权限键/规则来源可在代码中追溯 | P3 收益（调试便利），非功能缺口 | 配置层数增加（出现 managed 层）时 |
| G10 | sidecar 临时分支（/side 调研不污染主链） | fork/branch 已有（可选任意历史点），手工建分支即可 | 交互便利性，非能力缺口 | TUI 交互密集化后 |

## 7. 评测 / 学术（论文侧实验，非工程功能）

| 编号 | 事项 | 现状基础 | 未实施原因 |
|---|---|---|---|
| H4 | 事件级 precision/recall/error-rate 评测粒度（完全/部分正确两档 + 3 人仲裁取中位） | `complexity_metrics` + B1-B10 benchmark 已有任务级指标 | 需要标注语料与多人仲裁流程，属小论文实验设计 |
| H5 | BIM 需求评测集构建（真实招投标语料 + 图做 ground truth + 案例三标准） | clarify 规则库（EF1-EF8）已从方法论落地 | 同上：语料采集 + 人工标注 + 一致性问题 |
| H6 | MJ-CCE 多裁判投票补进防放水五件套 | A/B swap 防位置偏置已实现 | 需 ≥3 裁判模型同评（成本 ×3），当前双环 critic 已满足验收 |
| H8 | 多路推理 + 融合验证（论文 07 消融：仅 +1~3%，CoT 是主菜 +8.2%） | critic 规则回溯 CoT 已落地（H2） | 论文自证收益小（多路+自验证合计 <5%），成本 ×3 |
| H9 | 推理型模型选型实证（R1 类在规则回溯任务上显著优于通用模型） | models.toml 多模型可配；`llm_multirun` 工具有多跑基建 | 属对照实验（需 API 配额与实验设计），非代码功能 |
| H10 | H-Helpfulness 式专家评估协议（Likert + 双盲） | benchmark 已有确定性指标 | 需领域专家参与，属论文实验 |
| H11 | 语义最小化工具接口审计（对照 RCAgent 检查 8 工具入参） | 8 工具入参已极简（如 `mcp_call` 仅 server/tool/arguments） | 属审计活动而非改造；typed IR 已使工具面最小化 |
| H12 | LATS 树搜索作为 trace 自蒸馏的学术对标 | 树形 trace + fork 已落地 | 纯写作素材（开题/论文对标章节） |

## 8. 结论

- 工程侧的高价值项（P0/P1 全部 + P2 多数）已实施并测试（1325 passed）；
- 上表未实施项分三类：**L 级工程**（F7/E3/D12）需专项立项、**成本敏感**（D14/D15/H6/H8）待出现真实验证需求、**学术实验**（H4/H5/H9/H10）属小论文阶段；
- 其中 D3（child 未继承 MCP 连接）是**唯一已知的功能缺口**，但其触发条件（领域 agent 直接写宿主）在当前架构下被 typed plan 单写路径有意规避——如需放开，必须先定并发写协议。

---

# 附录 A：本次调研内容（2026-09-19）

> 调研目的：为 openBIMAgent 找可吸收的外部经验。两部分来源——①《软件学报》2026 年第 37 卷第 8 期"大模型与软件工程"专题论文 7 篇；② 编码智能体工具 7 个（Codex / Claude Code / opencode / pi / OpenClaw / dsh / grok build）2026-09 时点状态。

## A.1 论文调研（7 篇，含精读与取舍结论）

| # | 论文 / 单位 | 核心内容 | 可吸取 / 取舍结论 |
|---|---|---|---|
| 01 | SmartGen-AADL 多智能体需求分析与 AADL 模型生成（哈工大） | 三阶段单向流水线：文档结构化 → 子问题分解 → RAG 融合生成 AADL；构建"条目化需求-AADL 组件"知识库与语义对齐数据集 | **可取**：需求条目-构件对齐知识库 + RAG 检索相似组件（→ E5）、ISO/IEC/IEEE 29148 需求"单一性"原则（→ clarify 条目化）；**反证**：单向流水线慢且无容错、参数靠模型猜、无回溯——恰好印证"正向并发 + 反向卡口"路线 |
| 02 | 基于 LLM 智能体的代码生成综述（北大 李戈团队） | 单智能体（ReAct/Reflexion/LATS）与多智能体（ChatDev/MetaGPT）两条线；研究重心从模型微调转向工程实现（工作流管理、可靠性、工具集成、人机协同） | **对标关键词**：LATS 树搜索与我们的树形 trace + fork 同构（→ H12 写作素材）；评测生态 SWE-bench/aider 是行业基准；"多智能体聊天式协作 Token 开销大、语义漂移"支持强类型 DTO 契约 |
| 03 | 面向 Linux 内核开发知识的 LLM 问答能力评测（复旦 彭鑫团队） | 首个复杂系统基准 LKQABench（202 问答对）+ **MJ-CCE 多裁判协同评测**（多裁判投票，治自恋偏置与位置偏置） | **可取**：多裁判投票补进防放水五件套（→ H6 待做）；benchmark"从真实社区数据提纯"的构建法；结论"概念答得好 ≠ 工程能做对"支持确定性门禁 |
| 04 | 面向软件测试领域知识问答的大模型评估（南大 吴化尧团队） | 以教材为基准构建 700 题；结论：概念准确性/流畅性优良，但前沿研究现状与多条件交叉推演存在概念漂移与推理幻觉 | **结论采用**："会说不等于能做"——工业级 Agent 的质量判定必须落在机器可执行门禁（Schema Gate/Domain Gate）上；属教学场景评测，工程侧不直接吸收 |
| 05 | 基于并行探索的大模型缺陷定位增强 PRIME（国防科大 毛晓光团队） | 单决策路径易 Early Return 丢失真根因；并行采样 + 方法调用图拓扑中心性重排序，Top-1 提升 >18%；**多路径交叉验证才认根因**（单路证据标"疑似误报"继续上溯） | **可取**：FIX 环多路径共识（→ H7，现 judge 双证据已覆盖设计）、并行探索 + 确定性结构收敛范式（对应 Solver/Scene Graph）；树形 trace + fork 反事实重跑的直接学术支撑 |
| 06 | 大语言模型智能体在软件系统根因分析中的应用综述（国防科大 王怀民团队） | 三阶段框架：数据收集（人工→固定程序→**智能体自动，function calling/MCP**）→ 根因定位（微调/单智能体/多智能体）→ 效果评估（客观/人类/LLM 三层）；梳理 RCAgent OBSK 快照键、语义最小化工具接口、控制器-执行器代码生成、D-Bot 交叉审查、LDRA 辩论投票、mABC 投票、Flow-of-Action"思考-动作集-动作-观察" | **重点吸收**：**OBSK 快照键**（→ A3，超长结果溢写 + hash 引用）、JSON 修复稳定器（→ D13）、多 agent 共识与交叉审查（→ D15 待做）、评估体系缺"操作复杂度/成本"两个维度（→ H3 complexity_metrics，学术空档=小论文卖点）；综述点名未来方向：知识图谱/因果图嵌入、可解释性、多维评估标准 |
| 07 | 基于文本表达特征分析的 LLM 协议交互抽取（南航 钱巨团队） | 从 100 篇真实专利协议文本归纳 **8 类表达特征**（分支遗漏 43% / 自交互混淆 41% / 过程压缩 36% / 解释夹杂 33% / 前提与动作混淆 31% / 指称混用 18% / 主体层次化 15% / 一对多合并 13%），提炼应对规则 → **规则回溯思维链** → 3 路并行推理 + 零样本融合验证；消融：规则回溯 CoT 贡献 +8.2%，多路推理/自验证各仅 +1~3%；推理型模型（DeepSeek-R1）显著优于通用模型 | **方法论整体吸收**：①失败模式驱动的 prompt 工程（证明 APE 同义替换式优化基本无效）→ **BIM 需求表达特征规则库 EF1-EF8**（H1，从真实口语化工程需求提炼同一套句式陷阱）；②规则回溯 CoT → critic 先标【候选问题】再按规则回溯确认（H2）；③事件级 precision/recall/error-rate + 三人仲裁取中位（→ H4 实验设计）；④数据集构建法：专利作真实混乱语料 + 附件图作 ground truth + 案例三标准（→ H5） |

## A.2 工具调研（7 个，2026-09 时点身份已核实）

| 工具 | 身份（核实） | 最深可借鉴机制 |
|---|---|---|
| **Codex** | OpenAI 官方开源 Rust 编码 agent（~110 crate 分层，stable v0.155） | OS 原生沙箱矩阵（Landlock+seccomp / Seatbelt / Windows Restricted Token+MXC）+ 网络代理；**Starlark exec 规则三值决策**（未匹配倾向询问而非禁止）+ 学习式修正 + BANNED_PREFIX 永不放行；**Guardian 独立审查者**（对工具调用打 risk/authorization 标签、按实际效果而非自述意图判定、递归评估嵌套调用）；delegate 契约（子代理继承父级 MCP 管理器、强制 never-ask、`final_output_json_schema`、事件降噪）；`ForkSnapshot` 两种语义；AGENTS.md 合并策略（项目根→cwd、override 优先、字节预算、per-turn 重载）；compaction 20k 保留预算 + 摘要置底 + 从头裁剪保 prefix cache |
| **Claude Code** | Anthropic CLI（v2.1；编排逻辑写在 prompt 而非代码状态图） | **子代理即文件**（Markdown+frontmatter：description/model/tools/maxTurns，20 并发 / 3 层深，fresh context + sibling roster）；**验证阶梯**（内联 → `/goal` 独立 evaluator → **Stop hook：不过不许结束 turn，连续 8 次阻塞后放行** → fresh-context 验证子代理）；skills 三层渐进披露 + 压缩后 5k/25k 重挂；path-scoped rules（paths frontmatter）；`/context` 上下文构成可视化；每 turn checkpoint；Agent Teams（mailbox JSON 文件 + 共享任务列表带依赖与文件锁） |
| **opencode** | SST 开源 TS agent（client/server 分离，Effect-TS，30+ packages） | **OpenAPI 驱动 SDK**；SSE 首事件 `server.connected` + 全局 event bus；**TUI 控制协议**（`control/next` 阻塞等待 + `control/response` 应答）；权限按**工具入参** glob + 最后匹配胜出 + **doom_loop 检测**（同参重复 3 次触发 ask）；`permission.task` 编排（deny 的 agent 从工具描述整体移除省上下文）；compaction 参数（`min(15k, max(2k, usable×0.25))`、工具输出 2000 字符截断、`[Old tool result content cleared]`）；`--attach` 复用常驻 server 避免 MCP 冷启动 |
| **pi** | badlogic（Mario Zechner）极简 harness，2026-04 并入 Earendil Works（pi.dev），新增 chord/pi-durable/pi-server 服务化 | **system message 重放式 prompt 状态**（首 entry 存完整 prompt sections + 工具清单，变更存增量 patch，重放即得任意时刻配置）；`branch_summary` 弃用分支摘要；压缩 entry 带 `{readFiles, modifiedFiles}`；**切点绝不落在 tool result**；Project Trust（未信任不加载项目级资源）；**反 MCP 论**（CLI+README 225 tokens vs Playwright MCP 13.7k tokens）；供应链加固（min-release-age、lifecycle script 白名单） |
| **OpenClaw** | Peter Steinberger 个人 AI 助理（Clawdbot→Moltbot→OpenClaw，501(c)(3) 基金会治理，20+ 消息渠道） | **Gateway 拥有会话、客户端只是投影**（session attachment）；**记忆五层分层 + provenance**（列级 origin class 防文本伪造、supersession key、recall-loop prevention、网络污染传播标记）+ **Dreaming 双门巩固**（确定性门结构性排除 untrusted → LLM 合并重写 → content-hash 乐观并发 + pre-image 存档）；事件带 `seq`/`stateVersion`（断档拉快照而非重放）；副作用方法强制 idempotency key；Skill Workshop（agent 起草 + 人工批准 + immutable revision） |
| **dsh** = **DeepSeek Harness**（不是 deep shell） | DeepSeek 官方开源 agent harness（Cordis DI 框架，"一切皆插件"，2026-08 发布即 229k stars） | **`session.vN.jsonl` 版本化 + 邻接迁移链**（committed 代永不重命名、每迁移包只管一步 vN→vN+1）；**"Model-visible means logged"** 运行时断言；事件三域（durable session facts / live agent events / capability events）；工具执行管线（pre-execute → **monotonic guards** → approval **审批器不可用即 deny** → execute → fs write-intent 门 → post-execute → **冻结的 result**）；**capability fail-loud**（UNSUPPORTED_CAPABILITY 绝不 accepted-then-ignored）；Agent Teams（durable mailbox、task DAG revision CAS、`writeScopes` advisory 前缀、`foldTeam()` 从 log 重放全部状态） |
| **grok build** | xAI（品牌 SpaceXAI）官方 Rust 编码 agent（`grok` CLI/TUI + headless + ACP，直接移植了 codex/opencode 的工具实现） | **沙箱自保护**（对自身 hooks/config 目录内核级 write-deny——即使 profile 授权写也只对当前 session 生效，symlink 的 GROK_HOME 直接拒绝启动，防 agent 通过改配置提权）；**deny 永远赢** + 组织级 requirements.toml 锁定；**工具懒发现**（`search_tool`/`use_tool` meta-tool，tool_definitions.json 不含 MCP 工具）；单会话目录多文件分工（summary.json / updates.jsonl / system_prompt.txt 渲染快照 / rewind_points.jsonl / signals.json）；workspace 身份=git origin（同仓库 worktree 共享记忆）；**无 LLM 自动会话摘要**（结构化元数据，跳过 trivial session）；Agent vs Persona 两层 + I/O 契约（可链式组合）；与 Claude Code 的 bridge（默认只读：`--permission-mode plan --sandbox read-only`） |

## A.3 代码库现状摸底结论（取舍依据，file:line 已核实）

| 摸底项 | 结论 | 对吸收清单的影响 |
|---|---|---|
| LLM 看到的工具面 | 仅 8 个硬编码工具（`core/loop.py:32`），`mcp_call` 白名单只放行 `execute_plan/ping/describe_capabilities`；2865 个 VectorScript 签名从不进 prompt（只在 `mcp_clients/vectorworks.py` 做 AST 客户端预检） | "MCP 懒发现"已用 **typed IR + 白名单**解决得更彻底（LLM 根本看不到工具面）→ A1 划掉，仅补 M0 自由代码路径的 `lookup_api` |
| 工具结果回流 | 双视图（llm_view/ui_view）+ 截断（MCP 20k / read 50k / bash 20k 字符） | ≈ pi 的 output/details 分离已具备；A2/A3 收敛为"结构化裁剪 + OBSK 溢写" |
| 权限体系 | 三态 + 入参级 glob（`bash:<cmd>` / `mcp_call:<server>.<tool>`）+ `execute_plan` 强制 ASK 不可降级 | F1 已实现；但发现规则值未归一化的 fail-open 漏洞（见附录 B.1） |
| 上下文压缩 | `_maybe_compact` 已有锚点 + 原子组不拆 + LLM 摘要失败退确定性骨架 | 压缩底座质量高，缺 token 预算制与 split-turn（C1/C2 补齐） |
| 记忆 / 技能 / 检索 | MemoryStore 审批写入 + fail-closed；skills 渐进披露；FTS5 会话检索；词法 exemplar 检索（bigram Jaccard） | 已有项跳过；E2 provenance、E5 结构化对齐为增量 |
| clarify 抽取 | 纯正则/别名，小模型兜底是 `TODO(M1)`（`clarify/slots.py:138`） | H1 直接把该 TODO 落地 |
| session 格式 | 事件 `{id,parentId,timestamp,type,payload}` 无版本字段；`truncate_after`（物理 rewinding）刚在工作区被删 | B1（版本链）与 B9（投影式 rewind，不重写文件）为最高优先 |

---

# 附录 B：全部修复与补强（按提交）

> 规模：17 个提交、60 个文件（含本文档）、约 +3900/−720 行；测试 1249 → **1325 passed / 9 skipped**（净增 76 个测试，每批提交前全量回归，全程绿）。

## B.1 缺陷修复（3 个真实缺陷 + 4 处契约/测试修正）

### 缺陷 1（安全级，fail-open）：字符串权限规则可静默跳过审批门

- **发现路径**：实施 F4（审批 fail-closed）时，测试传 `permission_rules={"write": "ask"}` 后发现工具**未经审批直接执行**。
- **根因**：`core/permissions.py check_permission` 原样返回规则值；`loop.py` 的判定写作 `perm is Permission.ASK`。当调用方传字符串（非枚举）时 `is` 判定为 False，DENY/ASK 两个分支都不命中 → 直接落入执行路径。生产路径（角色 frontmatter 经 `Permission(value)` 转换）不受影响，但任何直接构造规则字典的调用方（插件、SDK、测试、未来接入方）都会踩中。
- **修复**：规则值一律归一化为 `Permission` 枚举，非法值 fail-loud 抛错（`permissions.py`）。
- **测试**：`tests/test_host_events_and_approval.py::test_ask_without_approver_fails_closed` 断言拒绝且未落盘；`test_permissions.py` 全量回归。

### 缺陷 2（安全级，fail-open）：审批通道崩溃 = 放行

- **根因**：`loop.py _dispatch` 的 ASK 分支未包裹异常处理；`_cli_approval` 在无 stdin 环境（headless/CI）抛 `EOFError` 时异常逃出权限门，直到 `run()` 层才炸——期间没有任何"拒绝"语义。
- **修复**：审批调用包 try/except，异常一律按 **fail-closed 拒绝**返回（`permission=approval_unavailable`，附原始错误），与 HookBus 的 fail-closed 纪律对齐。
- **测试**：同文件两条用例（stdin 不可用、回调显式拒绝）。

### 修正 1-3：门禁 schema 三处枚举同步（新增字段/类型必须同步 JSON Schema）

| JSON Schema | 变更 | 不修的后果（已实测） |
|---|---|---|
| `schemas/session_event.schema.json` | 新增可选 `schema_version` 属性；customType 枚举新增 `host_event` | `additionalProperties: false` 直接拒绝所有 v2 事件（`test_approval_broker` 等 2 例失败） |
| `schemas/subagent_request.schema.json` | 新增可选 `output_schema`（`type: ["object","null"]`，因 `model_dump` 总会输出该键） | 所有子代理请求被门禁拒绝（23 例失败） |

**经验教训（已写入惯性纪律）**：pydantic 模型加字段 ⇒ 必须同步同名 JSON Schema 门禁；`model_dump` 会带上值为 null 的键，schema 需允许 null 而非仅 optional。

### 修正 4：测试与工具链

- `uv` 不在本机 PATH → 统一改用 `.venv/Scripts/python.exe -m pytest`（后台任务管道曾吞掉 pytest 退出码，改用 `> log 2>&1; echo EXIT=$?` 落盘校验）；
- `test_session_store.py` 五元组断言更新为六元组（含 `schema_version`）；
- SSE 首帧测试适配 `server.connected` 控制帧（G2 的预期行为变更）；
- 自查修复我引入的两处测试 bug：OBSK 断言需 `as_posix()`（Windows 路径分隔符）、F5 测试目录层级传参、`default_plugin_registry` 是单例而非工厂。

## B.2 补强清单（按提交，含关键文件）

| 提交 | 类别 | 内容 | 关键文件 |
|---|---|---|---|
| `7d12379` | 核心批 | **B1** 事件 `schema_version` + 邻接迁移链（v1 投影迁移、未来版本 fail-loud、`upgrade_file` 带 pre-vN 备份、`schema_versions()` 分布报告）；**C1** 近期保留 token 预算制；**C2** split-turn elision（最新工具组绝不丢弃、超长消息保头部+sha256 引用）；**A2/A3** OBSK（超长结果 content-addressed 溢写 `out/results/`，回灌头部+可 read 引用）；**A8** doom-loop（同参连续 3 次拦截，轮询类豁免）；**C5** 角色 `compaction_retain`；**C6** WORKSPACE.md 项目指令（根→cwd 收集、预算截断不失败）；**D10** stop-gate（拦截→反馈回灌→继续，上限 8 次）；**B4** 压缩事件附 `read_files/modified_files` | `session/schema.py`、`session/store.py`、`core/loop.py`、`mcp_clients/vectorworks.py` |
| `69128be` | 子代理契约 | **D4** `output_schema`（任务注入 + 终态 Draft202012 校验 + 违反 FAILED + 角色未声明即拒绝请求）；**D5** `run_plan max_concurrency`（1..8 fail-loud）；**C5 接线** frontmatter `compaction_retain` → child AgentLoop | `orchestrator/contracts.py`、`runtime.py`、`dispatch.py`、`agents/*.md` |
| `5ddcf60` | Session v2 | **B9** 投影式 rewind（移 head 不重写字节，旧分支仍可回访）；**B7** `fork(mode="interrupted")` 打断式分叉标记；**E4** 无 LLM 会话摘要（结构化元数据、trivial 标记）；**B3** child 首条 `[prompt-snapshot]`（prompt_sha256 + model/tools/retain 血缘） | `session/store.py`、`orchestrator/runtime.py` |
| `4b8ea57` | 论文 07 | **H1** `EXPRESSION_FEATURE_RULES` EF1-EF8（指称混用/过程压缩/前提混淆/分支遗漏/一对多合并/口语量词/背景夹杂/无对象要求）+ `extract_slots_with_fallback`（规则优先、JSON 兜底、编造 id 忽略、失败静默回退）+ pipeline `brief` 预填 + clarify.md 同步 | `clarify/slots.py`、`assembly/pipeline.py`、`agents/clarify.md` |
| `54bdec1` | 论文 01 | **E5** 归档检索结构化对齐（0.6×词法 + 0.4×领域/DN/rule_id 特征，IR 损坏降级纯词法，命中附 match 元数据与特征匹配展示） | `server/runs.py` |
| `f2e1e12` | headless | **G1** `oba agent <task> --json`（信封 `{status,content,session_id,head}`、`--session` 续跑、`--yes` 免审批、`--tools/--max-steps/--system-prompt`）；`run --brief` | `cli.py` |
| `2d3429a` | 幂等 | **G3** `M2SqliteIdempotencyStore`（BEGIN IMMEDIATE 真 CAS + revision 校验、WAL、跨实例持久、损坏记录 fail-loud；协议从 `implemented: False` 变为有实现） | `server/idempotency_store_sqlite.py` |
| `fd62e3e` | Server | **G2** SSE 首事件 `server.connected`（含 replay 计数）；**A9** `/api/v1/mcp/health`（宿主清单 + 探针入口 + 治理白名单） | `server/sse_endpoint.py`、`fastapi_app.py` |
| `b5c33f6` | 安全/评测 | **F3** 写自保护（禁改 agents/schemas/config/.openbimagent/pyproject 等治理配置）；**H2** critic 规则回溯 CoT（先标候选问题再按规则回溯，单证据不 PASS）；**H3** `complexity_metrics`（重试数/轨迹长度/ESCALATE 批次——论文 06 点名的学术界空档） | `core/loop.py`、`orchestrator/dispatch.py`、`agents/critic_*.md` |
| `05a2dd8` | 韧性 | **D13** `_repair_json`（尾逗号/智能引号/注释）先修复再判失败，减少无谓重试；**D2** sibling roster 注入 | `planner/instantiate.py`、`core/loop.py` |
| `0ece12b` | 治理 | **B8** `find_children` 子树溯源；**F8** 组织级 `requirements.toml` 强制 DENY（覆盖角色配置）+ `oba trust` 同批；**C7** path-scoped 规则（.vwx/.gh/.ifc/.aadl 按需注入）；**C8** `/api/v1/context` 构成可视化 | `session/store.py`、`core/requirements.py`、`core/path_scoped_rules.py`、`fastapi_app.py` |
| `0bbabe9` | 生态/信任/记忆 | **F5** Project Trust（+CLI）；**A5** 反向 MCP server（4 个只读工具，任意 IDE 可直连）；**E3** 记忆 provenance（owner/agent/system 分级、仅 owner 注入、recall-loop 防护）；**D8** durable mailbox（先落盘、送达幂等、queued-minus-delivered 恢复、重放） | `core/project_trust.py`、`server/mcp_serve.py`、`core/memory.py`、`orchestrator/team_mailbox.py` |
| `9ae0078` | 修复+通道 | 上述**缺陷 1/2** 的修复；**A6** 宿主事件通道（渲染/导出完成主动推 `host_event` 事件落 session，CustomType + schema 枚举同步） | `core/permissions.py`、`core/loop.py`、`session/schema.py` |
| `0fe098b` | 修复 | **F5 语义修正**：产品仓库自身目录内的插件视为随产品分发代码；信任门只拦外部项目目录（否则会误拦仓库自带的 `plugins/example-echo`） | `core/project_trust.py` |
| `24a1f06` | 文档 | 本文档（未实施项登记 + 附录调研与修复记录） | `docs/architecture/DEFERRED_ITEMS.md` |

## B.3 验证方式与已知瑕点

- **验证**：每批提交前跑全量 pytest（6-7 分钟/次，共 6 次全量 + 多次子集）；最终 1325 passed / 9 skipped / EXIT=0；
- **字节钉死资源**：`.gitattributes` 归一化后立即回归 `test_m2_readonly_service` 等哈希敏感测试（79 例通过），确认 benchmark SHA pin / OpenAPI baseline / T7 bundle 不受行尾影响；
- **已知瑕点**：`test_approval_broker` 的单个用例带 5s 超时与线程，在高负载下偶发一次失败（随后同组合连跑 3 次全部通过），属既有测试的时序敏感性，非本次改动引入；
- **未推送**：本次工作的提交已合并到本地 `main`（`24a1f06`），远端推送因网络中断未完成；恢复后执行 `git push origin main` 即可。
