# 07 Gemini Trace & Observability 调研报告

**TL;DR**: 建模 Agent 轨迹需支持多模态（截图）、树状分支与复杂工具链。评测基准普遍采用 JSONL 轨迹格式。建议以 `pi` 的树状 JSONL 为基石，融入 OpenTelemetry GenAI 语义扩展，构建我们的 session 事件规范。观测后端建议**纯文件原地 JSONL（主）+ 按需离线导出**，以保持极简，避免过早引入 Langfuse 增加部署成本。VLM 评分需强制 CoT 和 Few-shot 锚点以防分数飘移。

## Langfuse (https://langfuse.com/ · 5.5k+ · 高频活跃 · MIT/核心服务闭源)
- 一句话定位：开源大模型观测平台，支持 Prompt 跟踪与 Trace 分析。
- 架构形态：独立 Server（Postgres + Next.js）+ 多语言 SDK。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | SDK | `@langfuse/client` | API Wrapper | 网络通信 | 低 | 高 |
- 相关机制：支持 Attachments（可挂载截图图片事件），支持 trace 下钻。`openBIMForge` 中通过 `lib/langfuse.ts` (行13) 集成，主要挂载在 `chat` 请求级别。
- 价值评级：C (仅参考)
- 建议动作：忽略。自托管较重，且纯本地 CLI/后台运行 Agent 没必要强依赖。

## AgentOps (https://agentops.ai/ · 3k+ · 活跃 · Apache-2.0)
- 一句话定位：专注于自主智能体的 "Time-travel" 调试与状态可观测性平台。
- 架构形态：SDK 侧植入 Agent 生命周期，后端为 SaaS 服务。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | Agent Instrumentation | `agentops` SDK | Python 装饰器 | 网络通信 | 中 | 高 |
- 相关机制：能录制 Agent 的思考循环与工具调用，提供回放 UI。
- 价值评级：C (仅参考)
- 建议动作：仅参考其 "Time-travel" 概念，我们通过 session 树实现。

## Traceloop OpenLLMetry (https://traceloop.com/ · 3k+ · 活跃 · Apache-2.0)
- 一句话定位：纯原生的 OpenTelemetry (OTel) GenAI instrumentation 实现。
- 架构形态：Python/JS SDK 补丁包，数据直接发向任意 OTel Collector。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | Instrumentation | `traceloop-sdk` | Python SDK | OTel Backend | 中 | 高 |
- 相关机制：遵循 OTel `gen_ai.*` 语义协定，如 `gen_ai.system`, `gen_ai.usage.prompt_tokens`。
- 价值评级：B (改造可用)
- 建议动作：仅参考其 Schema 标准，我们不接 OTel Collector。

## Helicone (https://www.helicone.ai/ · 4.5k+ · 活跃 · Apache-2.0)
- 一句话定位：AI API Gateway 代理，改 base_url 即获得监控与成本控制。
- 架构形态：反向代理服务 (Cloudflare Workers / 独立部署)。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | Proxy Layer | N/A | HTTP URL 替换 | 网络通信 | 极低 | 高 |
- 相关机制：无代码侵入，但无法看到 Agent 内部复杂调度结构。
- 价值评级：C (仅参考)
- 建议动作：忽略。

## 横向对比表

| 方案 | 专注场景 | OTel 支持 | 多模态支持 | 集成成本 | 对 openBIMAgent 的价值 |
|---|---|---|---|---|---|
| **Langfuse** | LLM 应用/Chat | 支持导出 | 附件形式支持 | 需架设后端 | 高频快检成本太高 |
| **AgentOps** | Agent 回放 | 有限 | 弱 | SDK 侵入重 | 理念契合，实现弃用 |
| **OpenLLMetry** | 基础设施对齐 | 纯原生 | 弱 | 中 | Schema 字典参考价值大 |
| **Helicone** | 计费与代理 | 需透传 | 原图可见 | 极低（改URL） | 无法记录本地工具副作用 |
| **pi Session** | 本地代码 Agent | 无 | 支持 JSONL 扩展 | 原地单文件 | **完美契合本地迭代与回溯** |

## 对 openBIMAgent 的建议

### 1. 观测后端决策建议：纯文件 session JSONL 树
**论据：**
1. **轻量与隔离**：建模任务单次周期长、截图大。将大块数据直接落入本地 `~/.pi/agent/sessions/` 样式的 JSONL 文件，不依赖网络，不引入 Langfuse 的部署成本（Postgres+容器）。
2. **完美支持回滚与分支**：Pi 的 `id` / `parentId` JSONL 设计天然支持状态树。Blender 材质崩了，直接 `/tree` 切回快照父节点继续跑，这是 SaaS OTel 无法直接提供给 Agent 的能力。
3. **架构影响**：影响 `COMPONENTS.md` §2.6 Session 的确立，强化无状态 loop，状态即文件。

### 2. Session JSONL 事件 Schema 建议 (对齐 OTel GenAI)
扩展 Pi 的 `AgentMessage` (见 `pi session-format.md`)，并引入 `gen_ai.*` 规范字段。每条记录为 `{id, parentId, timestamp, type, payload}`：
- `type: message` -> `payload.role = user | assistant`, 包含 `gen_ai.request.model`, `gen_ai.usage.prompt_tokens` 等 OTel 指标。
- `type: tool_call` -> `payload.toolCallId`, `payload.toolName`, 对应 OTel span。
- `type: custom` (专用于建模特定事件)：
  - `customType: screenshot` -> `payload.camera_view`, `payload.image_path` (本地绝对路径/base64)，`payload.phase = scad | blender`。
  - `customType: score` -> `payload.rubric_scores`, `payload.fix_instruction`, `payload.critic_model`。
  - `customType: patch` -> `payload.target_file`, `payload.diff`。
  - `customType: snapshot` -> `payload.blend_file_path`, `payload.hash` (参考 Golden Trace 设计)。

### 3. VLM 评分留痕与防飘移建议
VLM-as-judge 不稳定，`temperature=0` 不足以防飘。我们 Critic 评分落盘字段需包含：
- **CoT (Chain of Thought)**：必须先推理后打分，落盘 `payload.reasoning`。
- **Few-Shot 锚点记录**：记录评分时使用了哪个参考图/锚点词（`payload.anchor_ref`）。
- **返工指令**：当某维度低于阈值时，强约束输出可执行返工指令（`payload.actionable_feedback`）。

### 4. BIMBench 后继评测导出格式草案
论文评测时，从 session JSONL 离线提炼。参考 SWE-Bench 与 Terminal-Bench：
```json
{
  "instance_id": "playbook-edo-cyberpunk-01",
  "model_name_or_path": "gemini-3.1-pro",
  "trajectory": [
    { "role": "user", "content": "playbook requirement..." },
    { "role": "tool_call", "tool_name": "bash", "arguments": "..." },
    { "role": "tool_result", "content": "..." }
  ],
  "final_artefacts": {
    "blend_hash": "a1b2c3...",
    "screenshots": ["path/1.png", "path/2.png"]
  },
  "critic_scores": {
    "geometry": 8.5,
    "material": 7.0
  }
}
```
*架构影响*：完善 `ARCHITECTURE.md` §6 trace 评测导出。

## 入库检查单
- [x] 调研中间笔记已完成并在正式报告入库后清理
- [x] 产出 `docs/research/07_gemini_trace_observability.md`
- [x] 覆盖了 OTel 映射、Langfuse/AgentOps 对比、Pi Session 拆解、评测格式与 VLM 留痕五大目标。
- [ ] 等待主会话评审并更新 Wiki。建议下一步：开发 `session.py` 实现基于 JSONL 的读写与分支操作。
