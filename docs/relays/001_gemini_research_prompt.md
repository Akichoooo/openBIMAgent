# Relay 001 · Gemini 3.1 Pro 调研提示词

用法:把下面代码块**整段**贴给 Gemini 3.1 Pro 执行,完成后告诉主会话「001 完成」。

```text
你是 openBIMAgent 项目的调研子代理。只做调研和写报告,不修改任何项目代码、不安装依赖。

先读并严格遵守调研协议:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
(五段式方法论、报告模板、入库检查单)。中间产物放 relay_workspace/001_mcp_vision/{logs,scripts,raw,notes.md}。

# 背景

openBIMAgent 是一个新开仓的开源项目(设计阶段),形态为「自研 Agent Core + vectorworks-mcp(自研)+ blender-mcp(fork 改造)」,核心差异点是「模型自己看截图自己纠正」的双环视觉自检。架构文档在:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\ARCHITECTURE.md
(先读它,你的调研要为其中的 M0/M1 里程碑服务。)

前身项目 openBIMForge 在:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMForge
可读,不可改。

# 任务 A:blender-mcp 源码级解剖(为我们 fork 改造出规格)

对象:https://github.com/ahujasid/blender-mcp (重点读 src/blender_mcp/server.py 与 addon.py)

输出:
1. 全工具清单表:工具名 / 参数 / 返回结构 / 超时与限制 / 我们 fork 时应保留还是改造。
2. socket 协议细节:消息格式、分块/大小限制、错误返回形态、并发约束(为什么只能单实例)。
3. get_viewport_screenshot 实现细节:分辨率/格式怎么定、视口 vs 渲染的区别、headless(--background)下能否工作(如不能,给出 headless 截图的替代实现思路,如 bpy 离屏渲染)。
4. telemetry 收集哪些字段、代码位置、彻底关闭的改法。
5. 评审我们的 fork 改造清单(遥测默认关、execute_blender_code 加 AST allowlist、执行前自动存 .blend 快照、新增 batch_render/camera_turntable/camera_path_render、headless 支持):逐条给可行性、实现要点、估计改动位置(文件+函数)。

# 任务 B:vwx-mcp 源码级解剖(为我们自研 vectorworks-mcp 出规格)

对象:https://github.com/vicquick/vwx-mcp

输出:
1. 三层架构(trigger/executor/work)各层职责与代码位置。
2. 文件 IPC 格式:jobs/results JSON 的 schema(字段、状态机、错误形态)。
3. VWX_TOOLSET 工具集预设机制:预设清单、切换方式、对 context 占用的实测/估计。
4. vs_index.json 的 schema:3071 条 vs.* 签名怎么组织、怎么被工具描述/检索使用。
5. 它 AGENTS.md 里记录的 Vectorworks API 坑:挑出最关键的 20 条,逐条翻译/解释。
6. 「VW 里只有脚本 runner 上下文才能安全改文档」这一约束的技术细节与它的事故案例(如有)。
7. 对照:openBIMForge 的现有实现(RUN_IN_VECTORWORKS_START_FRONTEND.py、vectorworks_execute.py、forge_core/design_agent/vs.py)与 vicquick 方案逐点对比,给出「我们拆 MCP 时该吸收什么、我们的 handoff/hash/approval 机制怎么保留」的建议。

# 任务 C:VLM 视觉评分 rubric 调研(为双环自检出评分规格)

必读:SceneCraft https://arxiv.org/abs/2403.01248 ;另检索 2024-2026 年「VLM/MLLM as judge for rendered 3D scenes / text-to-3D evaluation」相关工作 3-5 篇。

输出:
1. SceneCraft 的 VLM refine 循环细节:prompt 怎么写、反馈怎么结构化、library learning 怎么沉淀。
2. 为我们设计六维评分 rubric(几何正确性/风格一致性/材质贴图真实感/经年磨损破损/灯光氛围/镜头构图):每维给定义、0-10 分的锚点描述(0/5/10 各长什么样)、常见失效模式、低分时对应的可执行返工指令模板。
3. 防放水机制:锚点参考图对、两两比较、评分一致性校验的具体做法,给出可直接写进系统提示词的段落。
4. 适用场景区分:SCAD 白模三视角评分 vs Blender 写实渲染评分,rubric 应如何分别裁剪。

# 输出要求

- 全部中文;事实与你自己的推断严格分开标注。
- 每个外部事实附来源(URL + 文件路径 + 行号或 commit)。
- 报告写入:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\03_gemini_mcp_vision_report.md
- 结构:任务A/任务B/任务C 三节,每节末尾给「对 openBIMAgent 的建议」小节。
- 报告开头写一段 ≤200 字的执行摘要(TL;DR)。
```
