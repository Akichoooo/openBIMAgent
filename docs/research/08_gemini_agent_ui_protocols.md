# 调研报告:Agent UI 协议与渲染层
依据:`docs/relays/RESEARCH_PROTOCOL.md`

**TL;DR**
openBIMForge 的前端之所以崩溃，是因为其自定义 SSE 事件与 Vercel AI SDK v6 严格的 `UIMessage.parts` 结构不兼容。为了实现未来的 Web UI，openBIMAgent 需要设计一套严格映射至 AI SDK v6 `data-*` 和 `tool-result` 的 Server SSE schema。前端选型上，推荐使用 **assistant-ui**（提供开箱即用的 shadcn 风格流式与工具组件）结合 **Vercel ai-elements** 作为底层渲染模块。关于工具结果双视图，协议层需利用 MCP 的 `_meta.ui.resourceUri` 或自定义 data 字段，实现「大模型看文本/数值评分，UI 渲染带截图和进度条的卡片」。A2A 协议主要用于跨厂商代理协同，对我们目前 in-process 的子代理体系意义不大，可暂忽略。

---

## Vercel AI SDK v6 (https://sdk.vercel.ai/ · ~15k · 活跃 · Apache 2.0)
- **一句话定位**: 现代化的大模型前端全栈集成 SDK。
- **架构形态**: 客户端 `useChat` + `DefaultChatTransport` 拦截流，服务端 `streamText` 处理事件。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | UIMessage Parts | `ai` package | 联合类型 | 无 | 低 | 兼容 |
  | DefaultChatTransport | `ai` package | 传输拦截器 | 无 | 低 | 兼容 |
- **相关机制**: SDK v6 废弃了纯 string stream，所有流内容必须符合严格的 schema：`text` / `tool-call` / `tool-result` / `data`。openBIMForge 的自定义事件（如 `step_start`）因为没被包装在 `data` part 内而直接导致前端报错。
- **价值评级**: A (直接可用)
- **建议动作**: 直接依赖。作为 Web UI 的底层数据传输流层。

## AG-UI / CopilotKit (https://ag-ui.com/ · ~7k · 活跃 · MIT)
- **一句话定位**: 标准化的 Agent-User 交互事件协议。
- **架构形态**: 基于事件流的协议（Message / Tool Call / State Patch / Lifecycle）。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 状态同步协议 | `@copilotkit/shared` | JSON Schema | 框架无关 | 中 | 兼容 |
- **相关机制**: 提供了 Human-in-the-Loop（如我们的 Clarify 追问）和 Shared State（任务状态同步）的标准通信事件格式。
- **价值评级**: B (改造/参考可用)
- **建议动作**: 抄设计重写。我们的 Server SSE Schema 应大量参考其 State Patch 和 Lifecycle 事件的设计思想。

## assistant-ui (https://www.assistant-ui.com/ · ~2k · 活跃 · MIT)
- **一句话定位**: 基于 shadcn/ui 的 React AI 聊天组件库。
- **架构形态**: Composable Radix-like UI primitives (Thread, Message, Composer)。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | Thread/Message | `assistant-ui` | React Components | shadcn/ui, Tailwind | 低 | 兼容 |
- **相关机制**: 直接集成 Vercel AI SDK v6 的流，完美处理多轮对话、流式自动滚动和 Generative UI 渲染。
- **价值评级**: A (直接可用)
- **建议动作**: 直接依赖。作为我们 Web UI 的基础脚手架，免去自己手写消息列表的麻烦。

## Vercel ai-elements (https://sdk.vercel.ai/docs/ai-elements · ~N/A · 活跃 · Apache 2.0)
- **一句话定位**: 针对 AI 交互场景的预构建组件库（如 Reasoning, PromptInput）。
- **架构形态**: 源码级复制入项目的 shadcn-like 组件。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | Reasoning/Shimmer | CLI add | React Components | Tailwind | 低 | 兼容 |
- **相关机制**: openBIMForge 里的 `components/ai-elements/` (`model-selector`, `reasoning`) 就是它。非常适合用来渲染思考过程或加载动画。
- **价值评级**: A (直接可用)
- **建议动作**: 直接使用 CLI 引入需要的高级组件。

## MCP-UI (https://mcpui.dev/ · ~N/A · 活跃 · MIT)
- **一句话定位**: 基于 MCP 的远程交互 UI 标准（MCP Apps / SEP-1865）。
- **架构形态**: Server 返回 UI 资源 URI，Host 采用沙箱 iframe 渲染。
- **相关机制**: 将工具调用与其展示的 UI 绑定。
- **价值评级**: C (仅参考)
- **建议动作**: 仅参考。由于我们拥有极强的内部协议定制自由，且需要 UI 与项目深度绑定（如 BIM 图表），过度沙箱化可能会增加没必要的开销，更推荐 Native 组件渲染 JSON 的形式。

## Agent2Agent (A2A) (Google · ~N/A · 草案期)
- **一句话定位**: 跨厂商的 Agent 发现与协作协议。
- **相关机制**: 通过 `/.well-known/agent.json` 声明能力。
- **价值评级**: C (仅参考)
- **建议动作**: 忽略。它主要解决跨厂异构 Agent 互联。我们的子代理全在进程内/同一项目内编排（Markdown 定义角色），属于单一厂商系统的细分任务分发，无需引入 A2A 的发现复杂性。

---

## 横向对比表

| 项目 | 适用层级 | 与 Vercel SDK 兼容性 | 我们项目价值 | 引入方式 |
|---|---|---|---|---|
| AI SDK v6 | 传输协议层 | 原生 | 核心传输基石 | npm install |
| AG-UI | 事件协议层 | 高 (CopilotKit 支持) | 协议设计灵感 | 参考 Schema |
| assistant-ui | 基础 UI 组件 | 原生无缝集成 | 免手写 Thread/Message | npm install |
| ai-elements | 高级 UI 组件 | 原生 | 提供 Reasoning 等 | npx add |
| MCP-UI | MCP 远端 UI | 弱 (偏独立沙箱) | 提供 GenUI 思路 | 仅参考 |

---

## 额外必答: 针对 openBIMAgent 的建议

### 1. openBIMAgent Server SSE 事件 Schema 草案
**目标**: 完全对齐 AI SDK v6，禁止顶层乱发自定义事件。
所有服务端向外推送的结构均应被包装进 `data` 数组或标准的 `tool-call`/`tool-result` part。

```json
// 事件类型枚举化映射
{
  "type": "data",
  "data": {
    "type": "progress",            // 映射: 资产建模进度条
    "payload": { "batch": "路灯", "status": "SCAD 快检中" }
  }
}
{
  "type": "data",
  "data": {
    "type": "vision_scorecard",    // 映射: Blender 精检环六维评分卡
    "payload": { "asset": "建筑A", "rubric": { "geometry": 9, "lighting": 5 }, "screenshot": "url" }
  }
}
{
  "type": "data",
  "data": {
    "type": "clarify_form",        // 映射: Clarify 追问表单
    "payload": { "missing_slots": ["palette", "scale"] }
  }
}
```

### 2. 工具结果双视图（LLM 视图 / UI 视图）在协议层的落实
在 `tool-result` 的部分，同时提供文本结果供大模型消费，并通过 `data` 附带 UI 渲染素材：
- **LLM 视图**: `tool-result` 中的纯文本或轻量 JSON。如 `{"status": "fix_needed", "reason": "too dark"}`。由于大模型 token 敏感，这里不放 base64 图片或冗余的 CSS 标记。
- **UI 视图**: 在同一次 SSE tick 中，向 `data` stream 发送带有资源链接的 JSON。前端 `assistant-ui` 的工具渲染组件（`ToolCall` component override）会同时读取 `tool-result` 状态和 `data` 流中的截图书签，渲染出“包含缩略图和雷达图的精美验收卡片”。大模型看不到这张卡片，但用户看到了。

### 3. 未来 Web UI 技术选型建议
**推荐组合**: **Vercel AI SDK v6 + assistant-ui + ai-elements**。
- **理由**:
  1. **无缝对接**: `assistant-ui` 原生使用 Vercel SDK 的 `useChat`，彻底规避了 openBIMForge 时代的协议报错问题。
  2. **高定制度**: 基于 `shadcn/ui` 的 Primitive 设计使得我们可以随时把 BIM 特色的 3D Viewer 塞进 Chat Thread，而不需要对抗深层封装的黑盒。
  3. **生态现成**: `ai-elements` 弥补了复杂的交互态（如 AI 思考流、工具加载状态），减少 60% 的基础搬砖量，且风格统一。

### 入库检查单
- [x] 调研中间笔记已完成并在正式报告入库后清理
- [x] 产出 `docs/research/08_gemini_agent_ui_protocols.md` 正式报告
- [x] 回答 SSE Schema 设计
- [x] 回答工具双视图落实
- [x] Web UI 选型推荐

建议下一步：主会话收到完成指令后，根据本报告的 SSE 结构，开始规划 `agent_core/server` 层的协议实现。
