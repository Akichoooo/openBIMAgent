# Relay 002 · Gemini 3.1 Pro 产品全景调研提示词

用法:把下面代码块**整段**贴给 Gemini 3.1 Pro 执行(可与 001 并行跑,额度无限),完成后告诉主会话「002 完成」。

```text
你是 openBIMAgent 项目的调研子代理。只做调研和写报告,不修改任何项目代码、不安装依赖。

先读并严格遵守调研协议:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
(五段式方法论、报告模板、入库检查单)。中间产物放 relay_workspace/002_agent_landscape/{logs,scripts,raw,notes.md}。

# 背景

openBIMAgent 是新开仓的开源项目(设计阶段):「自研 Agent Core + vectorworks-mcp + blender-mcp + 双环视觉自检 + 可切换 playbook」。先读这两个文件,你的调研要为其决策服务:
- D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\ARCHITECTURE.md
- D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\COMPONENTS.md
已有的 opencode / pi / blender-mcp / vwx-mcp 调研在 docs/research/02_opensource_landscape.md,不要重复,要互补。

# 任务:逐产品解剖 + 横向对比

调研以下产品(全部联网核实最新状态,以官方文档/源码为准):

1. **Claude Code**(Anthropic):subagents 机制、hooks、CLAUDE.md 分层记忆、headless/SDK 模式、上下文压缩、权限模型。重点:它的「多 agent」是不是多模型?子代理之间怎么通信?
2. **OpenClaw**(原 Clawdbot/Moltbot,Peter Steinberger):Gateway 控制面架构、多渠道路由、agent 工作区、多模型支持与模型切换、与 pi 的关系(pi SDK 集成)。重点:多模型通信/路由怎么做。
3. **Goose**(Block):MCP-first 设计、recipes 机制、subagent。重点:recipes 与我们 playbook 的异同。
4. **OpenHands**(原 OpenDevin):multi-agent 委托、microagents、CodeAct。
5. **Cline / Roo Code**(VS Code):modes(角色切换)机制、MCP marketplace。重点:modes 与 playbook 的异同。
6. **Aider**:architect/editor 双模型分工、repo map、edit format。重点:这是「多模型分工」的鼻祖设计,细拆。
7. **Gemini CLI**(Google,开源)与 **Codex CLI**(OpenAI,开源):各自架构一句话 + 有没有独有机制值得抄。
8. **框架三选评**:LangGraph / CrewAI / AutoGen——如果自研极简内核是对的,给出论据;如果某个框架能省我们 50% 工作量,也要诚实指出。

# 每个产品的输出格式(统一)

- 现状一句话(版本/stars/活跃度)
- 架构形态(进程模型/客户端形态)
- agent 循环与多 agent 机制
- 上下文管理(压缩/预算/handoff)
- 模型抽象与多模型支持(单 provider 还是多 provider?角色-模型绑定?)
- 权限与安全
- session/trace 存储格式
- playbook 类似物(recipes/modes/subagent 文件)
- **对 openBIMAgent 的可借鉴点**(最多 5 条,要具体)

# 全局对比与专题

1. 横向对比大表(上述维度为列)。
2. **专题:多模型沟通**——哪些产品真支持「不同角色用不同厂家的模型」?模型之间怎么交接上下文(直接传消息 / 共享文件 / 结构化返回值 / 摘要)?哪种最健壮?对我们「artifact-mediated(工件为共享内存)」方案的评价与改进建议。
3. **专题:playbook/模式系统**——recipes、modes、subagent markdown、AGENTS.md 四类机制的优劣,对我们 playbook schema 的改进建议。

# 输出要求

- 全部中文;事实与推断严格分开标注;外部事实附来源(URL)。
- 写入:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\05_gemini_agent_landscape.md
- 开头 ≤200 字 TL;DR;结尾给「openBIMAgent 设计决策更新建议」清单(按价值排序,每条注明影响哪个文档哪个章节)。
```
