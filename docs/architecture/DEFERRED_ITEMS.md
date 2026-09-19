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
