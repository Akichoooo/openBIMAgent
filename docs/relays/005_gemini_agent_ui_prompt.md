# Relay 005 · Gemini 3.1 Pro:agent↔UI 协议与流式前端生态

用法:整段代码块贴给 Gemini 3.1 Pro,完成后告诉主会话「005 完成」。可与其他 relay 并行。

```text
你是 openBIMAgent 项目的调研子代理。先读并严格遵守:
- 调研协议:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
- 架构背景:ARCHITECTURE.md(server 后置、工具结果双视图)与 COMPONENTS.md
中间产物放 relay_workspace/005_agent_ui/{logs,scripts,raw,notes.md}。正式报告写到:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\08_gemini_agent_ui_protocols.md

# 调研目标

openBIMForge 的前端流式既丑又断过(自定义 SSE 事件与 AI SDK v6 DefaultChatTransport schema 不兼容,见 docs/research/01_codebase_audit.md §2)。openBIMAgent 策略是 server/CLI 先行、Web UI 后置,但 server 的 SSE 事件 schema 必须现在就设计对,否则历史重演。本调研为未来 Web UI 和 server 事件协议储备决策。

# 必查项目

1. **Vercel AI SDK v6**:UIMessage parts 体系(text/reasoning/tool-*/data-*/file 等)、DefaultChatTransport 的 chunk schema(合法类型清单)、自定义 data-* 部分的正确用法——把我们当年踩的坑讲透。
2. **AG-UI 协议**(CopilotKit):agent↔用户交互事件协议,事件类型全集、与 MCP/A2A 的关系、适配成本——是不是我们 server 事件协议应该直接对齐的标准?
3. **assistant-ui**(开源 React chat 组件):渲染能力(工具调用/流式/parts)、与 AI SDK 和 AG-UI 的集成路径。
4. **Vercel ai-elements**:组件清单,哪些可直接用于「执行日志/截图/评分卡/追问表单」的渲染(openBIMForge 前端已有 components/ai-elements/ 目录,可读参考用法)。
5. **MCP-UI**(mcpui.dev):MCP 工具返回 UI 片段的标准——blender-mcp 截图/渲染结果能不能以 UI 资源形式直给前端?
6. **A2A**(Agent2Agent,Google)一句话定位:与我们子代理通信有没有关系,没有就明说忽略。

# 输出

按 RESEARCH_PROTOCOL §4 契约。额外必答:
1. **openBIMAgent server SSE 事件 schema 草案**:逐事件类型列字段(text-delta/data-progress/data-screenshot/data-score/data-clarification-question/tool-* …),标注每个事件与 AI SDK v6 parts / AG-UI 的映射关系——目标:未来 Web UI 用现成 SDK 零补丁渲染。
2. 「工具结果双视图(LLM 视图 / UI 视图)」在协议层怎么落。
3. 未来 Web UI 技术选型建议:AI SDK v6 + ai-elements / AG-UI + assistant-ui / 其他,给一个推荐组合与理由。
```
