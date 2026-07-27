# M0 实施计划(单资产端到端冒烟)

版本:v1 · 2026-07-21 · 依据:ARCHITECTURE v0.3.1 / COMPONENTS v0.3 / 11 号评审
目标:**真连 Blender,把 `single_asset_hero`(日式自动售货机)端到端跑通**——追问→规划→SCAD 检→Blender 建→渲染检→HTML 验收页→交付,HITL 基座全程可用。

> **状态:M0 已收官(2026-07-27,附条件通过)。** 冒烟报告:`relay_workspace/m0_smoke/report.md`(主会话已逐项自验)。
> 六道结论:a/d PASS;b/e/f PARTIAL PASS(产物真实、四事件齐、通道跑通,受 agentrouter 额度阻断);c NOT DEMONSTRATED(代码路径已验,待补 key 实演)。
> 测试基线:229 passed + 1 skipped;ruff/compileall 干净。
> M1 待办:①clarify 问答落 message 事件 ②usage_summary 异常退出也落盘(atexit)③modeler prompt 加风格锚点 ④critic fallback 顺序复核 ⑤补 key 后实演中断-续跑 + 六维收敛至 ≥8。

## 验收标准(六道,M0 完成的定义)

1. 追问一问一答带默认值可用,答错可 `/tree` 回改。→ **PASS**(三槽位一问一答,completion_score=100)
2. 售货机 `.blend` 生成;HTML 验收页含六维评分(目标 ≥8,未达则记录评分曲线与返工轨迹)。→ **PARTIAL PASS**(.blend 真实 38 对象可打开;六维评分曲线 4.0→4.33→4.17 + patch 返工轨迹完整记录,符合"未达则记录"条款)
3. 建模中可 ESC 打断 → checkpoint → 续跑。→ **NOT DEMONSTRATED**(代码路径 cli.py:152-174 已验,额度耗尽未实演)
4. `/tree` 回退到任一批次前 + 快照恢复 `.blend`。→ **PASS**(分支会话 019fa446 含快照事件,blend_file_path+hash 可恢复)
5. trace JSONL 五类事件齐全(message/tool_call/screenshot/score/snapshot)。→ **PARTIAL PASS**(四类齐 + patch 增强类;message 缺——clarify 走 stdout 未落 session,M1 修)
6. 全程 `profile=test`(agentrouter 通道)跑通,token 消耗有记录。→ **PARTIAL PASS**(3 轮真实评分跑通;token 估算 ~53,200;401 额度耗尽为外部 blocker,精确计量待 usage_summary 落盘)

## 阶段 0 · 环境与凭证 spike(0.5 天,主会话)

| # | 任务 | 验收 |
|---|---|---|
| 0.1 | git init;用户填 `.env`(AGENTROUTER_API_KEY) | 仓库可提交 |
| 0.2 | agentrouter 探测脚本:方言判定(OpenAI/Anthropic)、列模型、**glm-5.2 vision 真机验证**(发图问描述) | 方言与 vision 结论回填 models.toml 注释 |
| 0.3 | **Blender 5.2 headless spike**:`--background --python` + socket + GPUOffScreen 离屏截图 + 非黑断言原型 | 离屏截图非黑(5.x 兼容是最大未知数,上游验证于 4.x) |
| 0.4 | OpenSCAD CLI spike:scad→三视角 png | 三张 png 产出 |

产物:spike 笔记落 `relay_workspace/m0_spikes/`。

## 阶段 1 · 核心链路(主会话写)

| # | 任务 | 验收 |
|---|---|---|
| 1.1 | `session/store.py` + `schema.py` 实现(JSONL 树、/tree、快照、sessions/index.json 多会话) | 单测过(派工) |
| 1.2 | `providers/dialects.py` + `registry.py`(openai-compatible 先行;重试/熔断/abort/部分结果) | agentrouter 一次 chat 成功 |
| 1.3 | `schema_gate/gate.py`(接入 schemas/ 四个 JSON Schema) | 脏工件被拦 |
| 1.4 | `clarify/slots.py`(规则抽取 + 一问一答) | 追问冒烟 |
| 1.5 | `core/loop.py` + `events.py` + `permissions.py`(≤8 工具、三态审批门) | 冒烟:追问→PLAN→门禁→session 可查 |

## 阶段 2 · blender-mcp fork(主会话写改造点,派工测试样板)

| # | 任务 | 验收 |
|---|---|---|
| 2.1 | 复制上游最新稳定版进 `mcp_servers/blender_mcp/`,记录上游 commit | 基线可连 |
| 2.2 | 八项改造:遥测关/headless/快照+AST/工具精简+3 新工具/健康检查/非黑断言/范围锁/预置库占位 | 逐项过 |
| 2.3 | `describe_capabilities` 实现(server/宿主版本/工具集/限制/坑) | agent 开工首调成功 |
| 2.4 | `mcp_clients/blender.py` | execute 建方块 + 截图非黑 + 越界被范围锁拦 |

## 阶段 3 · 双环(主会话写核心,派工测试)

| # | 任务 | 验收 |
|---|---|---|
| 3.1 | SCAD 环:移植 openBIMForge `forge_core/vision_loop`(json2scad/三视角/patch/收敛,裁剪) | 故意错的 IR 被检出并 patch |
| 3.2 | `vision/rubric.py` 实现 + critic 提示词(防放水五件套) | 评分含 reasoning/anchor_ref |
| 3.3 | HTML 验收页生成器(contact sheet:截图+评分+对比+返工指令) | 每批一页,CLI 打路径 |

## 阶段 4 · 端到端 + HITL(主会话)

| # | 任务 | 验收 |
|---|---|---|
| 4.1 | orchestrator 简化版(M0 顺序执行,并发 M1) | 批次流转 PASS/FIX |
| 4.2 | single_asset_hero 全流程 | 六道验收全过 |
| 4.3 | HITL:abort/排队/审批门/undo/redo/sessions 命令(CLI 先行,侧边栏 M2 TUI) | 验收 3/4 过 |

## 分工(主会话 = 编排 + 核心;relay 提示词由主会话出)

| 方 | 干什么 |
|---|---|
| 主会话 | 阶段 0-4 全部核心代码;fork 改造点;双环核心;冒烟 |
| GLM 5.2(relay) | 单测批量、ruff/mypy 清理、**constraints.yaml 二轮核实**(对照 GB 50014-2021 原文扩充检查井间距/净距/雨污分流) |
| Gemini 3.5 Flash(relay) | README 使用说明、命令帮助文档、HTML 验收页 CSS 美化、测试数据整理 |

## 风险与注意

- **Blender 5.2 兼容**(最大未知数):spike 不过则 pin 可工作提交自行移植 addon。
- **agentrouter 方言未确认**:阶段 0.2 探测;若 Anthropic 方言,dialects 加映射。
- **额度低**:critic_scad 若 glm-5.2-ar vision 不通切 claude-opus-4-6;全程 tracking token。
- VW 侧 M0 不碰(M1 才拆 vectorworks-mcp)。
