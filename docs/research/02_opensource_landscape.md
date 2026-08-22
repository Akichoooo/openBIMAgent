# 开源对标调研(opencode / pi / blender-mcp / Vectorworks 生态 / 视觉自检 / Codex)

调研日期:2026-07-21(§6 Codex 为 2026-08-22) · 每条标注「事实」(官方文档/源码)或「推断」。

## 1. opencode(TypeScript,~188k stars)

- 事实:仓库已从 `sst/opencode` 迁至 **`anomalyco/opencode`**;核心 CLI 最新稳定 **v1.18.4**(2026-07)。「2.0」实为 Desktop 应用的 v2 UI 迁移,不是核心 2.0。
- 事实:**server/client 分离**——启动即 TUI + HTTP server,TUI 只是客户端之一;`opencode serve` 可 headless,暴露 **OpenAPI 3.1**(`/doc`)+ **SSE 事件流**(`/event`),官方生成 SDK;Desktop/IDE 插件都是这套 API 的客户端。来源:https://opencode.ai/docs/server/
- 事实:agent 体系 = primary(`build` 全权限 / `plan` 只读)+ subagent(`general`/`explore`/`scout`)+ 隐藏系统 agent(`compaction`/`title`/`summary`);自定义 agent = **Markdown 文件 + frontmatter**(model/permission/正文即 system prompt),项目级 `.opencode/agents/`。来源:https://opencode.ai/docs/agents/
- 事实:权限三态 `ask/allow/deny`,按工具/bash glob 细分;`doom_loop` 卡死恢复提示;v1.18.2 起默认**禁止 subagent 再嵌套**。
- 事实:`/undo`、`/redo` **内部用 git 快照**管理文件变更;75+ provider 基于 Vercel AI SDK + models.dev;MCP 配置 `type: local|remote`,remote 自动 OAuth;`instructions` 数组可挂远程 URL 规则。来源:https://opencode.ai/docs/mcp-servers/ 等

**借鉴**:server/client 分离 + SSE 是前端通信的正解;Markdown 定义子代理;权限三态;「快照回滚」思想映射为「每批资产前存 .blend/IR 快照」。

## 2. pi(Mario Zechner,~74k stars,0.80.10)

- 事实:仓库迁至 **`earendil-works/pi`**(原 `badlogic/pi-mono`),npm `@earendil-works/pi-coding-agent`,官网 pi.dev。来源:https://github.com/earendil-works/pi
- 事实:**极简内核**——默认仅 4 工具 `read/write/edit/bash`,**system prompt + 工具定义 < 1000 token**;刻意不做 MCP/subagent/plan mode/内置 todo/权限弹窗,替代方案:TODO.md、PLAN.md、tmux、`pi` 用 bash 再拉起 `pi`、「CLI 工具 + README」渐进披露。作者反 MCP 理由:Playwright MCP 21 工具占 13.7k token、Chrome DevTools MCP 26 工具占 18k token。来源:https://mariozechner.at/posts/2025-11-30-pi-coding-agent/
- 事实:四包架构 `pi-ai`(统一 4 种 API 方言:OpenAI Completions/Responses、Anthropic、Google GenAI;**跨 provider context handoff**,换模型时 thinking 转 `<thinking>` 文本;全程可 abort 且返回部分结果)**/ `pi-agent-core` / `pi-tui`(差分渲染)/ `pi-coding-agent`**;运行模式含 **JSON 事件流 / RPC(stdio JSONL)/ SDK**。
- 事实:**Session = JSONL,每条记录带 `id`+`parentId`,单文件内天然树结构**:`/tree` 跳任意历史点续写、`/fork` 复制分支、`/clone`;compaction 有损但全史可溯。格式公开:`docs/session-format.md`。
- 事实:**工具结果分 LLM 视图与 UI 展示视图两份**;Extensions 是 TS 模块(`registerTool/registerCommand/on`);Skills 遵循 agentskills.io;Prompt templates = Markdown + `{{变量}}`。

**借鉴**:这是自研 agent 最有价值的决策参考——极简内核、状态外置文件、JSONL 会话树(trace 的正解)、工具结果双视图(前端流式丑的病根疗法)、对 MCP 工具数量保持警惕。

## 3. blender-mcp(ahujasid,24.5k stars,PyPI 1.6.4)

- 事实:架构两段式——MCP client ↔ MCP server(`uvx blender-mcp`,Python `mcp[cli]`)↔ **TCP socket(localhost:9876,简单 JSON `{type, params}`)** ↔ **Blender 进程内 addon(`addon.py` 起 socket server)**;`BLENDER_HOST/PORT` 可指远程。来源:https://github.com/ahujasid/blender-mcp
- 事实:工具含 `get_scene_info`、`get_object_info`、**`get_viewport_screenshot`(返回 MCP Image,视口截图直接进模型上下文)**、**`execute_blender_code`(执行任意 bpy 代码,官方警告 powerful but dangerous)**、材质、Poly Haven 资产、Sketchfab 搜索下载、Hyper3D Rodin/Hunyuan3D。
- 事实:**默认开 telemetry**,收集匿名 prompts、代码片段和截图;`DISABLE_TELEMETRY=true` 关闭。复杂操作需拆小步(socket 超时);同时只能跑一个 server 实例。

**处置**:fork 改造(遥测默认关、AST allowlist、批量渲染/相机轨迹/多角度连拍、headless 支持)。它的「宿主内 addon + 外部桥」形态与 Vectorworks 各 MCP 收敛一致,证明这是 DCC 接 agent 的通用解。

## 4. Vectorworks MCP 生态(极年轻,无事实标准)

- 事实:**`vicquick/vwx-mcp`**(10 stars,2026-04,活跃)——同构度最高:**248 个 MCP 工具 + 3071 个 `vs.*` 函数签名索引(`vs_index.json`)**;fastmcp HTTP server ↔ 文件 IPC(jobs/results JSON)↔ 原生 C++ palette 插件 ↔ VW 内 Python menu command 执行;核心约束:**VW 里只有脚本 runner 上下文才能安全改文档**,故拆 trigger/executor/work 三层;**`VWX_TOOLSET` 工具集预设**(full/gis/modeling/minimal)对抗 context 膨胀;自带给 agent 读的 AGENTS.md(VW2026 API 坑清单)。来源:https://github.com/vicquick/vwx-mcp
- 事实:`chronista-club/vectorworks-mcp`(9 stars):Rust MCP server ↔ Unix Domain Socket ↔ C++ SDK 插件;少工具 + `run_script` 任意脚本逃生门。
- 事实:Vectorworks 自带内嵌 Python 引擎(`vs` 模块);官方 C++ SDK 在 `Vectorworks/developer-sdk`。
- 推断:生态全部 ≤10 stars、2026 年才出现,**架构已收敛**(MCP server ↔ IPC ↔ 宿主内插件 ↔ vs.*/SDK + run_script 逃生门),做成即是差异化。

**处置**:我们已有跑通的单体(handoff + `vectorworks_execute.py` + `vs.py` 绑定),拆成 MCP 时吸收 vicquick 三件套:**vs 签名索引、工具集预设、API 坑 AGENTS.md**;不需要 C++ 插件,沿用现有 Python runner 方案。

## 5. 视觉自检环开源先例(生态位基本空白)

- 事实:**SceneCraft**(arXiv:2403.01248,Google Research/Caltech):文本 → scene graph → Blender Python 约束代码 → 渲染 → **GPT-4V 分析渲染图迭代 refine** + **library learning**(成功脚本函数沉淀进可复用代码库)。最对口的学术参考。来源:https://arxiv.org/abs/2403.01248
- 事实:**Anthropic computer-use demo**(`anthropics/claude-quickstarts`,17k+ stars):screenshot → 模型决策 → 执行 → 再截图 的 canonical 工程模板。
- 事实:`colinjoylobo/blender-vision-agent`(0 star):"Vision-LLM writes Blender Python, renders, verifies, iterates"——小型直接实现。
- 反例(事实):BlenderGPT(`gd3kr/BlenderGPT`,4.9k stars)一次性 codegen 无视觉环,2024-06 停更;3D-GPT(arXiv:2310.12945)多 agent 纯文本规划无视觉环——无自检环的方案早就触顶。
- 推断:成熟、活跃、专门做「Blender 视觉自检」的开源项目**不存在**;可组合 = blender-mcp 截图 + computer-use loop 模板 + SceneCraft 图先行/库学习。

## 6. Codex(openai/codex,~112k stars,Apache-2.0,Rust)

调研日期:2026-08-22。核验方式:GitHub API(仓库元数据、`codex-rs/` crate 清单)+ raw README 原文。**developers.openai.com 在调研网络下不可达**(WebFetch/curl 均 TLS 失败),文档站正文未能核验的均已标注。

- 事实:仓库定位「Lightweight coding agent that runs in your terminal」;**111,946 stars、Apache-2.0、主语言 Rust、活跃推送(当日)**。核心为 Rust workspace `codex-rs`(70+ crate)。来源:api.github.com/repos/openai/codex
- 事实(crate 清单经 GitHub API 核验):关键子系统独立成 crate——**`app-server`(+client/daemon/protocol/transport)**、**`code-mode`(+host/protocol/runtime)**、**`core`(+api/`core-plugins`)**、`plugin`、`skills`、`hooks`、`memories`、**`execpolicy`**、`mcp-server`/`codex-mcp`/`rmcp-client`、`rollout`(+trace)、`state`、`thread-store`、`secrets`/`keyring-store`(OS 钥匙串)、沙箱栈(`linux-sandbox`/`windows-sandbox-rs`/`network-proxy`/`process-hardening`)、本地模型(`ollama`/`lmstudio`)、`v8-poc`、`agent-graph-store`、`context-fragments`。
- 事实(`app-server/README.md` 原文核验):富客户端(VS Code 扩展为旗舰)后端,**双向 JSON-RPC 2.0(官方自述 modeled on MCP,线上省略 jsonrpc 头)**;传输 stdio JSONL(默认)/websocket(实验性,带 `/readyz` `/healthz` 探针)/unix socket;**有界队列背压,过载返回 -32001 "Server overloaded; retry later."**;数据模型三原语 **Thread/Turn/Item**;API 面覆盖 thread start/resume/**fork**/archive、turn streaming+steering、compaction、**plugins/marketplaces**、skills、realtime voice、config。
- 事实(`execpolicy/README.md` 原文核验):命令审批策略引擎,**Starlark 语法** `prefix_rule(pattern=[...], decision?, justification?, match?, not_match?)` + `host_executable(name=..., paths=[...])`;decision 三态 **`allow|prompt|forbidden`**(默认 allow);`justification` 人类可读理由并会呈现在审批提示/拒绝信息中;**`match`/`not_match` 为规则自带示例,加载时强制验证——README 原话 "think of them as unit tests"**;多规则命中取最严(forbidden > prompt > allow);`codex execpolicy check` CLI 输出 JSON 评估结果;官方标注 preview、API 可能破坏性变更。
- 事实:仓库 `docs/` 下 agents_md/config/execpolicy/sandbox/skills/slash_commands 等均为重定向壳,正文迁至 developers.openai.com;**skills/plugin 的清单与目录格式本次未能核验**。
- 推断:`code-mode` 三 crate 与 pi-dynamic-workflows 的 code-mode subagent 为同一思想(agent 写代码编排工具调用)的**独立收敛**,OpenAI 侧佐证该范式;`v8-poc` 表明其探索内嵌 JS 运行时做同方向扩展。

**借鉴**:① **execpolicy 模式 = 「规则即声明式代码 + 每条规则自带可执行测试 + 最严获胜」**,与本系统 rule-driven 哲学完全同构——可迁移为:GB50289 规则(constraints.yaml)每条附 match/not_match 自检样例、加载时验证;`registry.invoke` 与宿主插件 `execute_*_code` 前置 per-capability 三态策略门(现为「信任目录」二态);② app-server 的 -32001 背压语义 + `/readyz` `/healthz` 探针是现有 FastAPI/SSE 服务的低成本加固项;③ code-mode 收敛现象写入论文相关工作(Rust 化、OS 沙箱栈、marketplace 分发不吸收)。

**处置(2026-08-22 已落地 ①②)**:MunicipalRuleSet v1.2 self_tests——12 条净距规则 33 个 match/not_match 样例编译期重放,production 规则缺任一极性样例即拒绝整个规则集(tests/test_rule_self_tests.py);`CapabilityPolicyRule` 三态策略门(最长前缀获胜 + justification 进拒绝信息 + prompt 需显式 confirm,tests/test_plugin_registry.py 策略门 6 例);`/healthz` `/readyz` 探针 + invoke 有界并发背压(503 / -32001);③ 待论文写作引用。详见 PROJECT_HANDOFF_STATUS v3.4 §3。

## 7. 汇总:openBIMAgent 设计决策溯源

| 决策 | 出处 |
|---|---|
| server/client 分离 + SSE/OpenAPI | opencode |
| Markdown 定义子代理 + child session + 禁嵌套 | opencode |
| 极简内核、plan/todo 外置文件 | pi |
| 工具结果双视图(LLM/UI) | pi |
| Session JSONL id/parentId 树(trace/回放) | pi |
| MCP 工具数量控制 + 工具集预设 | pi 警告 + vwx-mcp 实践 |
| 宿主内 socket addon + 外部 MCP 桥 | blender-mcp(与 vwx-mcp 收敛) |
| `execute_*_code` 逃生门 + AST 白名单 + 快照 | blender-mcp + openBIMForge blender_mcp_lab |
| 双环视觉自检(SCAD 结构 + Blender 美学)+ 收敛治理 | openBIMForge v1 + SceneCraft + computer-use |
| 声明式策略引擎(三态决策 + 规则自带单元测试 + 最严获胜) | Codex execpolicy |
| 背压语义(-32001)+ 健康探针(/readyz /healthz) | Codex app-server |
| code-mode 编排范式的行业收敛佐证(论文引用) | Codex code-mode + pi-dynamic-workflows |
