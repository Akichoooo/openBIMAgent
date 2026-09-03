# Agent Core 增强方案（对标 nanobot / pi / DSH / Codex / OpenClaw / Hermes）

日期：2026-09-03 · 状态：**已全部落地（2026-09-03 实施，逐项带测试；证据见 §5 落地台账）** · 调研均标注一手来源

## 0. 现状基线（已扎实，不虚）

| 维度 | 现状 | 评级 |
|---|---|---|
| 工具管理 | 微内核 8 tools + registry.invoke 16 能力 + 三态策略门（最长前缀/justification/self_tests）+ 外部插件 manifest 发现 | ★★★★★ |
| 上下文 | 预算压缩（Codex auto-compaction 式）+ 双视图工具结果 + 子代理隔离 + 工件介质交接 | ★★★★☆ |
| 会话/追踪 | JSONL 树 + fork/branch + SSE + checkpoint/resume + 压缩审计 | ★★★★★ |
| 权限/安全 | execpolicy 三态门 + Bearer token + scope lock + 规则编译期自检 | ★★★★★ |
| 规划 | PLAN/TODO 外置 + clarify 槽位 + planner 阶段 | ★★★★☆ |
| 多模型 | registry + 3 profiles + fallback 链 + 重试/熔断集中层 | ★★★★☆ |

## 1. 一手调研事实卡

**pi**（[earendil-works/pi](https://github.com/earendil-works/pi)）：极简内核 4 工具；刻意**无内置权限系统**（靠容器化：Gondolin micro-VM / Docker / OpenShell 三模式）；skills 遵循 **agentskills.io**；Extensions=TS 模块注册工具/命令/事件；供应链加固（锁定+签名审计）。

**Codex**（openai/codex Rust）：`skills/`、`hooks/`、`memories/`、`context-fragments` 独立 crate；execpolicy=声明式规则+自带单测；app-server 背压 -32001。

**DSH**（[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，[架构文档](https://deepseek-harness.github.io/deepseek-harness/en/reference/)）：**一切皆是插件**（Cordis 范式）——模型适配器/工具注册表/会话日志/agent loop 本身都是可替换插件；profile+bundle 分层组合（cordis.patch.yml 覆盖）；**Session log 是唯一事实源**（"Model-visible means logged"运行时不变式）；typed events 瀑布（agent/pre-step、tools/pre-execute）；agent.inject() 注入上下文；8400+ 插件目录生态。

**nanobot**（[HKUDS/nanobot](https://github.com/HKUDS/nanobot)）：~4000 行 Python 对标 OpenClaw 的 430k 行；小 agent loop + **Dream 长期记忆** + Skills/Apps 发现 + 模型路由 + cron 自动化 + OpenAI 兼容 API；哲学=记忆与技能"只作上下文注入，不做重编排层"。

**OpenClaw**（430k 行个人 agent）：SOUL.md 人格 + MEMORY.md/USER.md 记忆 + 技能库 + 命令白名单审批；Hermes 提供 `claw migrate` 迁移通道（=事实上的格式标准）。

**Hermes**（[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)）：**闭环学习**——任务后自动创建 skill、使用中自我改进、定期 nudge 持久化；**FTS5 会话全文检索 + LLM 摘要做跨会话召回**；Honcho 用户建模；兼容 agentskills.io；40+ 工具分 toolset；子代理隔离并行；RPC 脚本折叠多步调用为零上下文成本。

## 2. 收敛点（多项目独立做同一件事 = 优先级最高）

1. **Skill = Markdown 文件 + frontmatter，渐进披露**（pi/Codex/Hermes/OpenClaw/agentskills.io 五方收敛）
2. **会话日志是唯一事实源**（DSH 不变式 / pi JSONL 树 / 我们已对齐）
3. **记忆 = 文件型可审查存储 + 上下文注入**（OpenClaw MEMORY.md / nanobot Dream / Hermes agent-curated memory）
4. **检索增强召回**（Hermes FTS5 / 我们 bigram 检索的弱版）
5. **工具数量控制**（pi 反 MCP 膨胀论 / vwx-mcp VWX_TOOLSET 预设 / DSH scoped registry）

## 3. 增强项（按优先级）

### P0-1 Skill 系统（agentskills.io 兼容）⭐ 旗舰
- **借鉴**：agentskills.io 标准 + Hermes 自创建/自改进 + Codex skills crate
- **设计**：`skills/{name}/SKILL.md`（frontmatter：name/description/when_to_use/tools?），注册进微内核为 `skill:*` 能力域；**渐进披露**——上下文只放 name+description，用户 `/skill` 或模型 invoke 时才注入正文；Domain Pack 可携带包级 skills
- **自蒸馏（对齐 Hermes + 我方治理）**：运行成功交付后生成 SKILL.md **草稿**进 `skills/_candidates/`，**人工批准**才转正（fail-closed：绝不自动进库，与我们规则集治理同构）——这是"越用越好"的学术可答辩形态
- **验收**：加载/发现/注入/调用全链测试；候选→批准→生效 E2E；Frontmatter schema 校验失败即拒载

### P0-2 会话全文检索（FTS5）
- **借鉴**：Hermes FTS5 session search + LLM 摘要召回
- **设计**：stdlib sqlite3 FTS5 索引 sessions JSONL（增量索引于 append 时维护）；`search_sessions(query)` 能力 + composer `/recall 关键词`；召回片段进 clarify/exemplar 上下文（与现有归档检索合并为统一 recall 层）
- **验收**：索引增量一致；中文分词可用（unicode61）；检索结果可溯源（session_id+event_id）

### P0-3 MCP 宿主生命周期管理 + 工具集预设
- **借鉴**：vwx-mcp 三层 + VWX_TOOLSET 预设 + blender-mcp 宿主 addon 形态
- **设计**：Host Supervisor（启动/心跳/崩溃检测/自动重连/退避重启）管理 Blender/VW runner；`toolset` 预设（minimal/modeling/full）控制注入上下文的宿主工具数（pi 的膨胀警告）
- **验收**：杀 runner 进程→自动重连成功；预设切换后工具 schema 数变化可测

### P0-4 轻量记忆层（MEMORY.md 风格）
- **借鉴**：OpenClaw MEMORY.md/USER.md + nanobot Dream + Hermes nudge
- **设计**：`memory/MEMORY.md`（项目事实）+ `memory/USER.md`（用户偏好），agent 经**交付门/审批**写入（不自由写）；系统 prompt 注入最近 N 条；`hermes claw migrate` 格式兼容备未来迁移
- **验收**：写入走审批事件；注入有上限；文件人工可审可改

### P1-1 通用 MCP client（挂载第三方 MCP server）
- **借鉴**：blender-mcp/vwx-mcp 的 MCP 宿主协议 + fastmcp（已在依赖里）
- **设计**：`openbimagent-plugin.toml` 扩展 `mcp_servers` 段（stdio 命令/环境），registry 把远端工具映射为 `mcp:<server>:<tool>` 能力，过同一策略门
- **验收**：挂一个示例 MCP server（如 filesystem）→ invoke 成功 + 策略门生效

### P1-2 Hooks（生命周期事件钩子）
- **借鉴**：DSH typed events 瀑布（agent/pre-step、tools/pre/post-execute）+ Codex hooks crate
- **设计**：registry 增事件总线：`pre_tool / post_tool / turn_end / run_end` 钩子点，插件可注册监听（只读观察优先，可否决后置）；审计进 session
- **验收**：测试钩子被触发次序；否决钩子能阻断工具执行

### P2（暂缓，论文 future work）
- Agent Teams / task board（DSH experimental）、插件市场（dsh plugin dir 已有 GitHub topic 约定可承接）、RPC 脚本折叠多步调用（Hermes）

## 4. 实施顺序与工作量估计

| 序 | 项 | 工作量 | 价值 |
|---|---|---|---|
| 1 | P0-2 FTS5 会话检索 | 0.5 天 | 直接强化"越用越好"叙事 |
| 2 | P0-1 Skill 系统（加载/调用先行，自蒸馏随后） | 1.5–2 天 | 旗舰差异点 |
| 3 | P0-3 宿主 Supervisor + 工具集预设 | 1 天 | 真机稳定性痛点 |
| 4 | P0-4 记忆层 | 0.5–1 天 | 叙事+实用 |
| 5 | P1-1 MCP client | 1 天 | 生态开放性 |
| 6 | P1-2 Hooks | 1 天 | 扩展性基石 |

每项配测试与文档；全部结束后更新 ARCHITECTURE/COMPONENTS 与 handoff。

## 5. 落地台账（2026-09-03 实施完毕）

| 项 | 落地文件 | 端点 / 接线 | 测试 | 与设计偏差 |
|---|---|---|---|---|
| P0-2 FTS5 检索 | `session/search.py`（unicode61 + CJK bigram 展开，水位线增量索引） | `GET /api/v1/sessions/search?q=&limit=`；前端 `/recall 关键词` 斜杠命令 + `#recall=` 深链 | `tests/test_session_search.py` 5 测 | 无 |
| P0-1 Skill 系统 | `skills/registry.py`（frontmatter 校验失败拒载；builtin + domain_packs/*/skills + `OPENBIMAGENT_SKILLS_ROOT/DIR` 多源发现）；内置 `skills/municipal-gravity-brief`、`skills/ir-inspection` | `GET /api/v1/skills`（目录+候选+拒载清单）、`POST /skills/invoke`（渐进披露正文）、`POST /skills/candidates/approve`（人工转正）；运行成功交付后自动蒸馏候选（runs.py finally）；技能目录片段注入新任务上下文；前端 `/skills` + `#skills` 深链 | `tests/test_skills.py` 14 测 | 候选区 `_candidates/*.md` 单文件（非子目录），批准时落 `{name}/SKILL.md` |
| P0-3 宿主 Supervisor + 工具集 | `mcp_clients/supervisor.py`（TCP 探活/up-down-restarting-external 状态机/有界线性退避重启/超限如实拒绝；VW 恒 external 不伪探测）；`core/toolset.py`（minimal/modeling/full） | `GET /api/v1/hosts`（supervisor 状态）、`POST /hosts/{id}/restart`；`GET/PUT /api/v1/toolset`；过滤双层生效：`/api/v1/plugins` 清单 + `/plugins/invoke` 403 门；设置弹层可选预设、宿主芯片可点重启 | `tests/test_host_supervisor.py` 13 测（含假 Blender 子进程真拉起真探活） | 重启命令支持 `OPENBIMAGENT_BLENDER_CMD` 完整命令行覆盖（Windows 下 shlex 会吞反斜杠，NT 原样传 CreateProcess） |
| P0-4 记忆层 | `core/memory.py`（MEMORY.md/USER.md 追加式、单行压平防注入、256KB 上限 fail-closed） | `memory:record` 注册为 prompt 策略能力（与 CAD 写盘同级）；`GET /api/v1/memory`、`POST /memory/record`（无 confirm → 409 need_confirm）；记忆片段注入新任务上下文；设置弹层查看+确认写入 | `tests/test_memory.py` 9 测（含 repo 根定位回归） | 无 |
| P1-1 通用 MCP client | `mcp_clients/external.py`（ExternalMcpPlugin：构造期真实发现工具，映射 `mcp:<server>:<tool>`；每次调用短连接；`_run_sync` 兼容事件循环内外） | `OPENBIMAGENT_MCP_SERVERS`（JSON，fastmcp MCPConfig servers 段）在 app 构建时挂载；`mcp:*` 默认 prompt 策略（第三方面 fail-closed）；失败 server 跳过不拖垮启动 | `tests/test_mcp_external.py` 7 测（fastmcp 内存传输 toy server，真实调用 2+3=5） | 配置通道用 env JSON（未改 openbimagent-plugin.toml，保持插件清单纯净） |
| P1-2 Hooks | `core/hooks.py`（pre_tool 可否决 fail-closed / post_tool、turn_end、run_end 观测型；handler 崩溃即否决；200 条 ring buffer） | `registry.invoke` 接 pre/post_tool；runs.py finally 触发 run_end；demo 求解回合触发 turn_end；代码级注册（无 HTTP 注册面，防注入） | `tests/test_hooks.py` 9 测（次序/否决阻断/异常隔离/有界/run_end 真实运行触发） | 无 |

**横切修复**：`_REPO_ROOT` 定位 bug ×2（skills/registry、memory 均曾误指 `src/`）已修并加回归测试；4 个跑真实 pipeline 的测试文件补 `OPENBIMAGENT_SKILLS_ROOT` 沙箱，蒸馏候选不污染仓库；`tools/build_web_ui.py` 从 scratch/ 迁出并改为相对路径（产物与原件字节级一致），构建脚本首次入版本控制。
