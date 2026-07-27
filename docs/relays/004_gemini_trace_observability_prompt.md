# Relay 004 · Gemini 3.1 Pro:trace/观测/评测生态

用法:整段代码块贴给 Gemini 3.1 Pro,完成后告诉主会话「004 完成」。可与其他 relay 并行。

```text
你是 openBIMAgent 项目的调研子代理。先读并严格遵守:
- 调研协议:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
- 架构背景:ARCHITECTURE.md(§6 trace 设计)与 COMPONENTS.md(§2.6 session)
中间产物放 relay_workspace/004_trace/{logs,scripts,raw,notes.md}。正式报告写到:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\07_gemini_trace_observability.md

# 调研目标

我们的 trace 基线是 pi 式 session JSONL 树(id+parentId,截图/评分/patch/工具调用全落盘)。现在要为它选:观测后端(要不要接、接谁)、事件 schema 对齐标准、评测导出格式(论文 BIMBench 要用)。前身项目 openBIMForge 已有 Langfuse 接入(lib/langfuse.ts)和 Golden Trace(forge_core/build_agent/trace_recorder.py,1893 行,JSONL+SQLite+脱敏+TrainingExport),可读不可改。

# 必查项目

1. **OpenTelemetry GenAI semantic conventions**:gen_ai.* span/事件标准现状——我们的 JSONL 事件类型怎么对齐它,给映射表。
2. **Langfuse**(开源,可自托管):trace 数据模型、OTel 兼容度、对「图片(截图)事件」的支持、自托管成本。值不值得接,还是只做导出格式。
3. **AgentOps / Traceloop OpenLLMetry / Helicone**:各一句话定位 + 有没有 Langfuse 给不了的能力。
4. **pi session 格式**(github.com/earendil-works/pi 的 docs/session-format.md):逐字段拆,评估我们直接兼容的可行性。
5. **Agent 轨迹数据集/评测格式**:SWE-bench 轨迹、AgentBench、Terminal-Bench 的记录格式;哪套最适合改造成「建模 agent 轨迹 + 视觉评分」的论文评测格式(BIMBench 后继)。
6. **VLM 评分留痕**:搜索 VLM-as-judge 的可复现性实践(评分锚点、温度、多次采样一致性),给我们 critic 评分事件的落盘字段提建议。

# 输出

按 RESEARCH_PROTOCOL §4 契约。额外必答:
1. 给我们 session JSONL 的完整事件 schema 建议(逐 type 列字段:message/tool_call/screenshot/score/patch/snapshot)。
2. 观测后端决策建议:自研只读 viewer / 接 Langfuse / 纯文件,三选一给论据。
3. BIMBench 后继评测导出格式草案(与论文需要衔接)。
```
