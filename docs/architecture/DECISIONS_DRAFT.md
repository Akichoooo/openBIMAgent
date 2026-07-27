# openBIMAgent 全局架构决策决议(Draft v1)

日期:2026-07-21 · 状态:**v1 已拍板,已并入 ARCHITECTURE/COMPONENTS v0.2**

> 拍板记录(2026-07-21):Q1 同意——modeler 维持 gemini-3.1-pro,实现提示词中硬性写明;Q2 市政管网包为毕设主线,GitHub 高赞提示词解构为多套模板(已落地 `domain_packs/` 三包);Q3 确认 C++ palette 延后。§5/§6 修改清单已执行完毕。
依据:5 份 Gemini 接力调研报告(docs/research/03、05、06、07、08)+ 主会话此前 3 份调研(01、02、04)
目的:敲定 M0/M1 最终技术选型,列出 ARCHITECTURE.md / COMPONENTS.md 的章节级修改清单。

## 0. 报告质量门结论

| 报告 | 结论 | 备注 |
|---|---|---|
| 03 MCP+视觉 | 通过 | addon.py/VW 三层解剖有文件级细节;rubric 可直接用 |
| 05 产品全景 | 通过 | OpenClaw 一节较浅(stars/定位待核),但结论(维持自研内核、Claude Code 子代理规范、Aider 双模型)可信 |
| 06 3D/CAD 生态 | 通过 | 增量最大:Infinigen 材质库、磨损两路线、build123d 选型、资产缓存 |
| 07 trace/评测 | 通过 | JSONL 事件 schema 可直接定稿;观测后端结论与极简原则一致 |
| 08 UI 协议 | 通过 | SSE schema 草案对齐 AI SDK v6,正是 openBIMForge 断链的根治方案 |

## 1. 新架构概念:Domain Pack(领域专家包)——用户新想法的落位

**用户思路**:做「某领域的特优生成管道」——流程专门优化 + 素材/知识齐全,像领域专家;毕设先做**市政管网**特优版,以后再做建筑等其他领域。

**定性**:这套思路在战略上叫**垂直化(vertical-first)**:通用基座不追求一次全能,先打一个垂直领域做到可交付质量,再把「打包机制」泛化。业界的相邻概念(Goose recipes / Claude skills / Roo modes)都只覆盖其中一层,我们要的是完整交付包。

**决议:引入一等概念 Domain Pack(领域包),playbook 降为包的组成文件。**

```
domain_packs/
  municipal_utility/            # 市政管网包(毕设实证,第一个)
    playbook.md                 # 流程剧本(原 playbook 全部能力:slots/phases/acceptance)
    agents/                     # 领域角色覆盖(管网规划师/碰撞检查员…)
    knowledge/                  # 领域知识与坑清单(规范条文、管径/埋深约束)
    assets/                     # 素材:材质板、GeoNodes 预设、构件 typology 模板
    rubric_overlay.md           # 领域评分细则(叠加在通用六维上:如坡度/碰撞/覆土厚度)
    benchmark_cases.json        # 领域评测用例(论文数据源)
  edo_cyberpunk_district/       # 街区展示包(Blender 表现向,验证另一极)
```

关键判断:

1. **市政管网包让双 MCP 分工第一次有了真业务**——管网是 BIM 交付向领域:Vectorworks(经 vectorworks-mcp)出带 IFC 语义的构件,Blender 做表现层(剖切、漫游、汇报动画)。江户街区包验证 Blender 极,市政管网包验证 VW 极,两极合起来才证明「Agent + 双 MCP」架构成立。
2. **可直接继承 openBIMForge 的 `mep_agent` 资产**(排污规划:fixture/stack/3D 路由/sizing/IFC 导出/VW 脚本)——这是市政管网包的 knowledge 与保底模板来源,不用从零开始。
3. 毕设叙事成型:「通用极简基座 + Domain Pack 垂直化方法,以市政管网领域包实证」。

## 2. 立即吸收(P0,进 M0/M1 最终选型)

| # | 决策 | 来源 | 落点 |
|---|---|---|---|
| P0-1 | **blender-mcp fork 改造规格定稿**:遥测硬编码关闭、headless 放开(移除 `bpy.app.background` 阻断)、`execute_code` 前自动存 `.blend` 快照 + AST allowlist、工具精简(去除 Polyhaven/Hyper3D 等冗余) | 03 | ARCH §5 |
| P0-2 | **vectorworks-mcp 融合方案**:吸收 vicquick 三层划分,但 M1 不引 C++ 插件——沿用已跑通的 Python runner + 文件 IPC;`handoff/hash/approval` 植入 Executor 层执行前门禁;从 `vs.py` 生成 `vs_index.json`(args/arity/ret/doc,发送前 arity 校验防引擎崩溃);工具集预设(248→40~100) | 03 | ARCH §5 |
| P0-3 | **VLM 六维 rubric 定稿**:几何/风格/材质/磨损/灯光/构图,带 0/5/10 锚点;**防放水三件套**:A/B swap 两两比较、<8 分强制 `actionable_rework_command`(禁空泛建议)、金标准锚点图对齐;**维度裁剪**:SCAD 环只评几何+基础构图,Blender 环全维 | 03 | ARCH §3 |
| P0-4 | **子代理规范 = Markdown + YAML frontmatter**(Claude Code 式:`tools/model/permissions` + 正文 system prompt) | 05 | ARCH §6, COMP §2.4 |
| P0-5 | **工件 Schema 门禁**:PLAN/IR/评分 JSON 强制 JSON Schema 校验,格式漂移直接 FIX 原地改,不进入下一棒 | 05 | ARCH §2 |
| P0-6 | **session JSONL 事件 schema 定稿**:pi 树 `{id,parentId,timestamp,type,payload}` + OTel `gen_ai.*` 字段对齐 + custom 事件(screenshot/score/patch/snapshot)+ VLM 留痕字段(`reasoning` 强制 CoT、`anchor_ref`、`actionable_feedback`) | 07 | COMP §2.6 |
| P0-7 | **观测后端 = 纯文件 JSONL,不接 Langfuse/AgentOps/Helicone**;评测走离线导出(BIMBench 草案见 07) | 07 | ARCH §6 |
| P0-8 | **server SSE 事件 schema 现在定稿**(M2 才实现):一切自定义事件包进 `data-*` part,枚举见 08(progress/vision_scorecard/clarify_form);**工具结果双视图**经 `tool-result`(LLM 文本)+ 同 tick `data` 流(UI 素材)分离 | 08 | ARCH §6 |
| P0-9 | **Infinigen 为材质金标准**:blender-mcp 挂载预置 procedural 材质库,`materialist` 只传参数(老化度/颜色),**禁止 LLM 从零写材质节点树**;物理破损一律用预置 Damage GeoNodes 修改器(控 factor),**严禁手写 boolean** | 06 | COMP §3 |
| P0-10 | **资产缓存层 `asset_cache`**:Poly Haven(CC0 宽配额)/Objaverse 资产 hash 落盘 + 429 指数退避;Sketchfab 隐性配额(~300 次/key)需节制 | 06 | COMP §2 |
| P0-11 | **维持自研极简内核,不用 LangGraph/CrewAI/AutoGen**(双环收敛与快照回滚需要边级控制,框架是负资产) | 05 | 定稿确认 |
| P0-12 | **Planner 输出 = Scene Graph IR**(SceneCraft/LayoutGPT 式 JSON:资产清单+空间约束),SCAD 环直接消费;C2 铁律不变 | 03+06 | ARCH §2 |

## 3. 接受但延后(P1/P2)

| # | 事项 | 延后理由 | 时机 |
|---|---|---|---|
| P1-1 | Web UI 实现:选型定稿 **AI SDK v6 + assistant-ui + ai-elements** | server/CLI 先行;schema 已定(P0-8)防返工 | M2/M3 |
| P1-2 | 远程 playbook 拉取(Goose 式 `--playbook <url>`) | 本地包机制先稳 | M2 |
| P1-3 | vicquick C++ Palette 闲置通知(替代轮询) | 避免编译链复杂度;文件 IPC 轮询已验证,响应优化后做 | VW MCP 性能优化期 |
| P1-4 | build123d 作为「精确 CAD 第三环」备选(显式 with 语法优于 CadQuery 链式) | SCAD 环先用;遇表达瓶颈再换 | M2+ |
| P1-5 | BIMBench 后继评测导出实现 | 格式草案已接受(07 §4);论文需要时实现 | 论文实验期 |
| P1-6 | OpenClaw Gateway 多端分发参考 | M2 server 阶段再回看 | M2 |

## 4. 明确拒绝/忽略

- **A2A 协议**:子代理进程内编排,无需跨厂发现(08)。
- **MCP-UI 深度采用**:沙箱 iframe 开销不值,Native 组件渲染 JSON(08)。
- **BlenderGPT / 3D-GPT 代码复用**:一次性 codegen 无环是反面教材,且 BlenderGPT 是 GPLv3 传染(06)。
- **LangGraph/CrewAI/AutoGen**:见 P0-11(05)。
- **CadQuery**:链式 API 不利于 Agent 分步调试,选 build123d(06)。

## 5. ARCHITECTURE.md 章节级修改清单

| 章节 | 动作 | 理由 |
|---|---|---|
| §0 设计原则 | 加第 7 条「Domain Pack 垂直化」、第 8 条「工件即协议,Schema 门禁」 | §1、P0-5 |
| §2 生命周期 | 步骤 3 后加「Schema 门禁」;researcher 产出明确为 references.md + 资产缓存填充;Planner 输出明确为 Scene Graph IR | P0-5/10/12 |
| §3 双环自检 | rubric 替换为 P0-3 定稿版(锚点+防放水三件套+维度裁剪);引用 03 报告 | P0-3 |
| §4 Playbook | 升级为 **Domain Pack 结构**(§1 目录树);phase 增加 subagent 权限/工具范围字段;`targets` 语义保留 | §1,05 专题 |
| §5 MCP 规格 | blender fork 清单按 P0-1 定稿;VW 按 P0-2 定稿(三层融合、handoff 植入 Executor、vs_index、toolset、C++ palette 移 P1-3) | P0-1/2 |
| §6 子代理与 trace | 子代理 frontmatter 规范引用 P0-4;子代理返回增「<200 字核心提示/警告」;trace 事件 schema 引用 P0-6;观测后端定稿 P0-7;SSE schema 引用 P0-8 | P0-4/6/7/8,05 |
| §8 风险 | 加「资产源 429 → 缓存+退避」;「VLM 评分飘移 → CoT+锚点落盘」;「工件漂移 → Schema 门禁」 | P0-5/6/10 |
| §9 里程碑 | M0 增加:session schema 实现、Schema 门禁、blender fork 四项改造;M1 增加:材质库/GeoNodes 预设、资产缓存;新增 M1.5(毕设线):**市政管网 Domain Pack** | 本决议全部 |

## 6. COMPONENTS.md 章节级修改清单

| 章节 | 动作 | 理由 |
|---|---|---|
| §1 组件总表 | 加 `domain_packs/`、`asset_cache`、材质库/GeoNodes 预设库组件 | §1,P0-9/10 |
| §2.4 orchestrator | frontmatter 字段定稿(tools/model/permissions);子代理返回 = 结构化摘要 + 工件路径 + <200 字核心提示 | P0-4,05 |
| §2.5 vision | rubric 六维定稿、两环维度裁剪、critic 强制 CoT(`reasoning` 落盘) | P0-3/6 |
| §2.6 session | 事件 schema 按 P0-6 全量替换 | P0-6 |
| §3 角色表 | `materialist` 加约束:只调预置材质库/GeoNodes 参数;**`modeler` 维持 gemini-3.1-pro 不降 Flash**——05 报告建议 Editor 用快模型,但用户硬性约束是「质量优先、时间与 credit 非约束」,建模是质量咽喉,保持 Pro;Flash 用于追问/SCAD 评分/高频快检 | P0-9,偏离 05 的理由 |
| §4 模型配置 | 写入 Aider Architect/Editor 搭档为官方推荐(Pro 规划+Pro 建模+Flash 高频) | 05 |
| §5 上下文 | 补「资产缓存与材质库即渐进披露,不进上下文」 | P0-9/10 |
| §7 安全 | AST allowlist 规格引用 03/06(MCP-for-CAD 先例的 execute_script 沙箱设计) | P0-1 |

## 7. M0/M1 最终技术选型表(待拍板)

| 层 | 选型 |
|---|---|
| 内核 | 自研极简 loop(Python 3.11+,uv),无框架 |
| 子代理 | Markdown + YAML frontmatter,禁嵌套,并发 ≤4 |
| 模型搭档 | gemini-3.1-pro(规划/建模/材质/灯光/验收评分)+ gemini-3.5-flash(追问/SCAD 评分/高频快检)+ glm-5.2(编排调度);providers 层集中重试/熔断/降级 |
| blender-mcp | fork ahujasid@6641189,改造四项(P0-1) |
| vectorworks-mcp | 自研 FastMCP + 文件 IPC,handoff/hash/approval 植入 Executor,vs_index + toolset 预设 |
| 视觉自检 | 双环;rubric 六维定稿;SCAD 环评几何/构图,Blender 环全维 |
| IR | Scene Graph JSON(LayoutGPT/SceneCraft 式) |
| trace | pi JSONL 树 + OTel 字段对齐,纯文件,不接 Langfuse |
| SSE 协议 | data-* parts 对齐 AI SDK v6(schema M0 定,实现 M2) |
| 材质/破损 | Infinigen 参考库 + 预置 Damage GeoNodes 修改器,LLM 只调参 |
| 资产源 | Poly Haven / Objaverse + asset_cache 本地缓存 |
| 领域包 | Domain Pack 机制;第一个=市政管网(毕设),演示包=江户街区 |
| Web UI(后置) | assistant-ui + ai-elements + AI SDK v6 |

## 8. 待用户拍板

1. **modeler 维持 Pro 不降 Flash**(偏离 05 建议,理由见 §6)——同意?
2. **毕设主线确认**:M0 仍用江户街区包打通 Blender 极,M1 后市政管网包作为毕设实证主线——还是市政管网提前到 M0/M1 并行?(影响 playbook 开发顺序)
3. **C++ palette 延后**(P1-3)——确认 VW MCP 先用轮询。
4. 决议通过后,我按 §5/§6 清单改写 ARCHITECTURE.md 与 COMPONENTS.md,然后出 M0 实施计划。

## 附录 A · 社区与领域情报对齐(v1.1,2026-07-21)

006(市政管网领域)/007(Grok 社区情报)入库,主会话评审见 `docs/research/11_kimi_intake.md`。在 v1 基础上修订(已全部并入 ARCHITECTURE v0.3):

- V1:blender-mcp fork 改造 5→8 项(+连接健康检查/+截图非黑断言/+范围锁);基座=官方最新验证稳定版
- V2:vs_index 升级为防幻觉+防崩溃双门禁;Domain 工具封装优先于 run_script
- V3:两 MCP 改为并行生成路径表述(Blender 非"表现层";Bonsai IFC 路径留 M2 评估)
- V4:评分分层(确定性 domain_gate vs VLM 软评分)+ 防放水三件套扩五件套
- V5:对外叙事=副驾/检核/人审签
- V6:新增 HITL 基座(abort/排队/审批/回退/续跑/doom_loop)与预览双线(模型=降采样截图;人=每批 HTML 验收页)
- V7:模板族定型(`_base/` + 三类范例);包结构加 `tools/`
- models.toml 按 09 报告同步调研值(全部标注待控制台确认)

## 附录 B · M0 实施期裁决(2026-07-21,阶段 1 产出评审)

1. 工具结果事件 = `tool_call` 事件的 `phase=result` + 双视图字段(result_llm_view/result_ui_view),不新增独立类型;M2 若 viewer 需要再评。
2. checkpoint 事件 = `type=message` + `checkpoint:true` extra(M0 实用主义);M1 再评是否入 schema 第五型。
3. `ModelConfig.context_window` 改可选,补 `cost_per_mtoken`(对齐 models.toml 现状)。
4. google-genai 方言 M1 实现;官方通道 Gemini 优先试 OpenAI 兼容端点(`generativelanguage.googleapis.com/v1beta/openai/`),models.toml 改 provider type 即可零代码。
5. `dialects.chat` 同步实现;M2 server 需要异步时加包装,不预埋。
6. 用户确认:glm-5.2 不支持 vision(控制台);test 通道视觉评判 = gpt-5.5(与 Claude 生成的 modeler 跨厂,合防放水第 5 条);gemini-3.6-flash 进 official 通道顶替 3.5。
7. constraints.yaml 值类型契约(008 入库):value ∈ {scalar, range-string, ordered_list, table(list of dict)};domain_gate 按 category 配类型化求值器,M1.5 实现时照此。
8. SCAD 环消费的是「编译 IR」(solver 出坐标后,assets 带 primitive/size/position),与 planner 产出的「语义 IR」(C2 禁坐标)严格区分;补 `scad_scene_ir.schema.json` 列入阶段 3b。
9. agentrouter 实测(2026-07-22):按 User-Agent 过滤客户端,必须带 `claude-cli/x.x.x` UA 否则 401「unauthorized client detected」;glm-5.2 在该通道是 reasoning 模型(content 可能为空、reasoning_tokens 计费),方言层需兼容 reasoning_content。
10. gpt-5.5 vision 复测(2026-07-22):1×1 红点答错为退化输入;真实 512² 渲染图(立方体+光照)两个模型均准确描述(物体/颜色/光照方向)——critic 通道维持 gpt-5.5,opus-4-8 备选。vision_retest.json 存 relay_workspace/m0_spikes/。
