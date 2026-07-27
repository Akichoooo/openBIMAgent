# openBIMAgent 代理生态格局调研 (Agent Landscape)

> **TL;DR**  
> 本调研解剖了 8 种主流/前沿 AI Agent 形态及 3 大框架。核心结论：**openBIMAgent 的“极简内核 + artifact-mediated 多模型协作”架构方向极其正确**。Aider 的 Architect/Editor 模式证明了强弱模型（Pro/Flash）搭配干活的可行性；Claude Code 的 Markdown Subagent 机制为我们的角色定义提供了完美的 schema 参考；OpenHands 的 Event Stream 印证了我们的 JSONL 会话树设计。建议吸纳 Claude Code 的子代理权限 yaml 机制与 Aider 的快照回滚理念，进一步完善 `COMPONENTS.md` 和 `ARCHITECTURE.md`。

---

## 1. 逐产品解剖

### 1.1 Claude Code (Anthropic) (CLI · 专有/EULA)
- **一句话定位**: Anthropic 官方推出的终端级代码智能体，主打防止“上下文焦虑”。
- **架构形态**: 纯 CLI，深度绑定 Anthropic 生态。
- **Agent 循环与多 Agent 机制**: 支持 **Subagents** 机制。可以并发生成多个子代理，每个子代理拥有独立的上下文。
- **上下文管理**: 主副代理上下文隔离，子代理完成任务后只将结构化结论或最终产物回传主代理，有效避免了长思考/搜索带来的主干上下文污染 (Context Anxiety)。
- **模型抽象**: 绑定单提供商，但可在子代理级别绑定不同模型（如 opus / sonnet / haiku）。
- **权限与安全**: 细粒度控制，在子代理的配置文件中明确分配权限（如 read-only, grep, bash）。
- **Playbook 类似物**: 在 `.claude/agents/` 下定义的 Markdown 文件（带有 YAML frontmatter），用自然语言+元数据定义角色行为。
- **对 openBIMAgent 的可借鉴点**:
  1. **Subagent 格式**: 完全采用其 `Markdown + YAML frontmatter (定义权限和模型)` 的机制，用于我们的 `agents/*.md`。
  2. **上下文隔离**: 坚定了我们“主代理只收结果，不看子代理中间推理过程”的设计。

### 1.2 OpenClaw (Peter Steinberger) (URL · >1k stars · 高活跃 · Open Source)
- **一句话定位**: 原 Moltbot/Warelay，支持多渠道（Slack, WhatsApp）路由的开源 Agent 底座。
- **架构形态**: 跨平台 CLI harness，Gateway 控制面架构。
- **Agent 循环与多 Agent 机制**: 侧重于将 LLM 接入不同即时通讯平台，而非单机多代理分工。
- **模型抽象**: 支持多提供商接入，通过 `openclaw onboard` 配置。
- **对 openBIMAgent 的可借鉴点**:
  1. 其 Gateway 控制面架构对我们 M2 阶段的 Server/SSE 接口暴露及多端（CLI/TUI/Web）分发有参考价值。

### 1.3 Goose (Block) (URL · 高活跃 · Apache 2.0)
- **一句话定位**: MCP-first 的开源 CLI 智能体，以 Recipes 驱动共享工作流。
- **架构形态**: CLI 终端，重度依赖 MCP 协议（70+ 预集成）。
- **Agent 循环与多 Agent 机制**: 基于 **Recipes**（工作流配方）进行多步骤任务驱动。
- **Playbook 类似物**: Recipes。
- **对 openBIMAgent 的可借鉴点**:
  1. **MCP-first**: 证实了“纯 MCP 扩展”是正确的，所有非核心逻辑全扔给 `blender-mcp` 和 `vwx-mcp`。
  2. **GitHub Recipes**: 借鉴其支持远程拉取剧本的能力 (`GOOSE_RECIPE_GITHUB_REPO`)，未来我们的 Playbook 也支持从统一中心拉取。

### 1.4 OpenHands (OpenDevin) (URL · >35k stars · 极高活跃 · MIT)
- **一句话定位**: 全能型开源自主 AI 程序员，拥有成熟的微代理和沙盒隔离机制。
- **架构形态**: AgentHub (控制面) + Event Stream + Docker Sandbox (执行面)。
- **Agent 循环与多 Agent 机制**: 使用 **CodeAct** (ReAct-style 循环) 控制流，支持 Multi-agent 委托 (Microagents)。
- **上下文管理**: 维护 session 级别的项目上下文。
- **模型抽象**: 基于 LiteLLM 接入 100+ 模型。
- **权限与安全**: 强制 Docker 沙盒隔离，避免宿主系统损坏。
- **Session / Trace**: 使用 **Event Stream** 记录所有决策与观测。
- **对 openBIMAgent 的可借鉴点**:
  1. **Event Stream**: 验证了我们的 JSONL Session 树概念是目前主流方案中的标配。
  2. **Sandbox 理念**: 虽然我们目前在本地宿主执行，但对其带来的风险需警惕（尤其是 Vectorworks 端）。

### 1.5 Cline / Roo Code (URL · >10k stars · 极高活跃 · Apache 2.0)
- **一句话定位**: VS Code 最热门的 Agent 插件，以 “Plan-and-Act” 强人类审批流闻名。
- **架构形态**: 纯客户端（IDE 插件），内部直连 MCP。
- **模型抽象**: Roo Code 首创了 **Modes（模式/角色）** 机制。
- **权限与安全**: 人类审批门禁，执行 Bash 或关键写文件必须人工点头。
- **Playbook 类似物**: Modes（如 Architect/Ask/Code）与 `.mcp_settings.json`。
- **对 openBIMAgent 的可借鉴点**:
  1. **强审批门禁**: 可用于我们的交付 (Deliver) 节点和高危 CLI 命令。
  2. 模式切换虽然不如我们的 Playbook 流水线复杂，但其通过 JSON 配置 `mcp_settings` 指定每个 mode 挂载哪些 MCP 的思路非常适合在子代理中应用。

### 1.6 Aider (URL · >20k stars · 极高活跃 · Apache 2.0)
- **一句话定位**: 终端 AI 结对编程界的天花板，首创且跑通了 “Architect/Editor” 双模型异构协作。
- **架构形态**: 纯 CLI，强关联 Git 仓库。
- **Agent 循环与多 Agent 机制**: **Architect (强模型如 o1) 负责系统规划，Editor (如 Claude 3.5 Sonnet) 负责具体打补丁**。
- **上下文管理**: 基于 AST 的 Repo Map 提供全局感知。
- **权限与安全**: 通过频繁的 Git 自动 Commit 提供任意节点回滚。
- **对 openBIMAgent 的可借鉴点**:
  1. **双模型协作范本**: 完美论证了我们使用 Gemini Pro（Planner/Architect）结合 Flash（Modeler/Editor/视觉快检）的可行性。
  2. **底层快照**: Aider 的 Git commit 对应我们 `.blend` 和 IR 的快照回滚机制，这是防污染的核心。

### 1.7 Gemini CLI 与 Codex CLI 对比
- **Gemini CLI** (Google): TypeScript/Monorepo，Apache 2.0。侧重深植入云原生与本地环境，极高上下文支持。
- **Codex CLI** (OpenAI): Rust 开发，部分开源。强调局部极速推理与自主 PR 闭环。
- **对 openBIMAgent 的启示**: AI 基础设施的巨头不约而同采用了“极简轻量级 CLI + 函数工具调用”的架构，而非笨重的微服务集群，这进一步印证了 openBIMAgent **极简内核设计** 的时代红利。

---

## 2. 横向对比大表

| 产品 | 架构形态 | 多Agent/分工 | 跨模型(Provider) | Trace格式 | Playbook 类似物 | 安全与回滚 |
|:---|:---|:---|:---|:---|:---|:---|
| **Claude Code** | CLI | Subagents (上下文隔离) | 单 Provider，细分模型 | 黑盒/未公开 | 带有 YAML 的 Markdown | 权限分级声明 |
| **OpenHands** | AgentHub+Sandbox | Microagents (CodeAct) | LiteLLM 全球支持 | Event Stream | 预设 Microagents | 强制 Docker |
| **Goose** | CLI | Recipes 步骤流转 | 接口兼容即可 | 本地 Log | Recipes (可连 Git) | 依赖 MCP 本身 |
| **Cline** | VS Code 插件 | Modes 角色切换 | 插件配置，随配随用 | 插件本地存储 | Modes / MCP 配置 | 人类审批 |
| **Aider** | CLI | Architect/Editor 双构 | 双模型（O1+Sonnet） | Git 提交历史 | Watch / Architect | 自动 Git 提交 |

---

## 3. 框架三选一 (LangGraph / CrewAI / AutoGen)

如果考虑使用现成多智能体框架替代极简内核：
1. **CrewAI**: 主打 “Role-based” 团队协作，极度易用但封装较死，相当于高级黑盒，难以在“双环视觉验收”时进行精准的收敛阻断。
2. **LangGraph**: 主打状态机，底层图结构对状态的控制最精准，没有“隐藏魔法”，非常适合生产级。
3. **AutoGen**: 主打自由对话式协商，容易陷入死循环，不适合严密的工程开发流水线。

**论据结论**: **维持自研极简内核。** 
因为 openBIMAgent 具有特殊的“视觉双环检测”和“强制快照回滚”机制，若采用 LangGraph，我们依然需要编写大量的 Graph 边控制逻辑和补丁状态；若采用 CrewAI，过度抽象会剥夺我们控制 VLM 视觉评分回传的能力。借鉴 `pi` 的数十行 loop 代码，自行维护外置状态是“成本最低、透明度最高”的做法。

---

## 4. 专题：多模型沟通

**现状**：
目前真正支持“不同角色用不同厂家模型”且经过生产验证的是 **Aider (Architect/Editor)**，它通过强模型（O1）输出规划文本，再直接将文本和代码喂给弱模型（Sonnet）执行。而 Claude Code 虽然支持多代理并发，但本质上是同厂模型的不同 Size 之间基于文本互传结果。

**对 openBIMAgent “Artifact-mediated (工件作为共享内存)” 方案的评价与建议**：
1. **评价**: 我们的方案（把通讯变为 `PLAN.md`、`references.md` 等实体文件落盘）比 Aider 的直接提示词拼接**更健壮**。由于文件可独立被人类审查、修改，完美打通了 AI 与人类的协作界面，这在长周期的 3D 建模中至关重要。
2. **改进建议**: 
   - 增加 **Schema 门禁**：对模型生成的 `TODO.md` 或 `JSON` 工件强制施加 JSON Schema 校验，防止“工件格式漂移”导致下一个模型无法解析。
   - 子代理不仅要回传工件路径，还应回传一段小于 200 字的“核心提示/警告”给 Orchestrator。

---

## 5. 专题：Playbook / 模式系统

目前业界有四种主流机制：
1. **Recipes (Goose)**: 线性工作流配方，简单但缺乏分支。
2. **Modes (Cline)**: 简单的系统提示词预设切换。
3. **Subagent Markdown (Claude Code)**: 文件即代理，包含工具白名单、权限和角色，支持隔离执行。
4. **AGENTS.md / Customizations**: 全局准则。

**对 openBIMAgent Playbook Schema 的改进建议**：
- **融合 Claude Code 的 YAML 头**: 在我们原本的 `playbook` schema 中，为每个 phase 明确指定“该阶段使用的 subagent 继承的权限 (allowlist) 和工具范围”。
- 必须包含 **“产出契约 (deliverables expected)”** 校验。

---

## 6. 对 openBIMAgent 设计决策更新建议 (按价值排序)

1. **[价值极高] 在组件设计中引入 Aider 的“Architect/Editor”双模型标配概念**。  
   *影响文档*: `COMPONENTS.md` §3。  
   *修改建议*: 明确将 Planner 固定为大模型（Gemini Pro / O1），将 Modeler 乃至 SCAD 修复执行器固定为快速模型（Gemini Flash / Claude 3.5 Sonnet），形成官方推荐搭档。
2. **[价值高] 全面吸收 Claude Code 的 Subagent 配置语法**。  
   *影响文档*: `ARCHITECTURE.md` §6 和 `COMPONENTS.md` §2.4。  
   *修改建议*: 统一采用 Markdown 文件形式，顶部包含 `tools`, `model`, `permissions` 的 YAML 配置，下半部分是 system prompt。
3. **[价值中] 引入工件 Schema 漂移阻断器**。  
   *影响文档*: `ARCHITECTURE.md` §2 与 §6。  
   *修改建议*: 在交接工件（如 PLAN.md 或 IR.json）给下一个代理前，通过校验器强制格式正确，格式错误直接报 FIX 原地修改。
4. **[价值中] 支持远程拉取 Playbook**。  
   *影响文档*: `ARCHITECTURE.md` §4。  
   *修改建议*: 像 Goose 那样，在 CLI 中支持 `--playbook https://github.com/xxx` 快速应用分享的 3D 生成剧本。

---

## 7. 入库检查单

- [x] 产出文件: `docs/research/05_gemini_agent_landscape.md` (已完成)
- [x] 涵盖 Claude/OpenClaw/Goose/OpenHands/Cline/Aider 等项目的详调。
- [x] 提供了多模型沟通及 Playbook 深度对标分析。
- [x] 列出了影响架构设计的可执行建议。
- **下一步建议**: 等待用户回复“005 完成”，然后将本文档的精髓合入 `ARCHITECTURE.md` 和 `COMPONENTS.md` 进行修订。
