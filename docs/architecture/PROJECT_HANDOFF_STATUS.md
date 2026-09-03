# openBIMAgent 阶段交接状态

版本：v3.5
更新时间：2026-09-02（Asia/Shanghai）
维护状态：**ACTIVE**
工作区：`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`
远程仓库：`https://github.com/Akichoooo/openBIMAgent.git`

> 本文档是跨会话恢复的唯一实时入口。只保留当前可复核事实、未完成债务、受保护内容和唯一下一动作。历史过程详见 Git 提交、专项验收报告与 `.workbuddy/memory/`，不要把历史测试数字当作本轮新证据。

## 1. 当前阶段结论

```text
M1 G1–G5     = PASS
M1 G6        = PASS（Blender 5.2.0 LTS 真实 + Vectorworks 2024 GUI 验收通过）
M1 G7        = PASS（全仓质量门禁）
M1.5 T1–T7   = PASS（多节点、确定性路线、水力、规则证据、B1-B10 Benchmark）
M2 P0–P5     = PASS（FastAPI 只读/写控制、SSE 流、Web 数字化 3D 工作台）
微内核跃升   = PASS（registry.invoke 承重调度、UI-Slots 动态标签、HTTP 插件端点）
规则自愈强化 = PASS（规则集驱动净距核验 + RouteConstraintReport 覆土 + 自适应膨胀半径）
Benchmark 真实化 = PASS（M1.5 T7 真跑 + 直插基线 + SH 消融电池；LLM 行显式占位）
Profile 补丁层   = PASS（CapabilityOverride + 激活/停用还原 + profile.ablation.no_self_healing）
外部插件加载 = PASS（openbimagent-plugin.toml manifest 约定 + OPENBIMAGENT_PLUGINS_DIR 发现加载）
LLM-Direct 真实基线 = PASS（gpt-5.6-terra 实测 60% 合规；B10 超时 ×3 = 真实扩展性上限发现）
3D 视口真实化 = PASS（/api/v1/demo/municipal-pipeline 端点 + 自愈时间线接真实求解输出）
Codex 机制吸收 = PASS（规则自检样例 self_tests + invoke 三态策略门 + 健康探针/背压）
M3 Blender 通路 = PASS（cad_host:blender.execute 正式能力 + 默认 prompt 治理 + Web 导出按钮 + HTTP/真机 3 测全绿）
M3 VW 通路 = PASS（真机验收 2026-08-23:1 passed in 8.22s;m3_registry_e2e.vwx 44.6KB + completed 回执落盘;runner 日志 consumed_total=4 全链路证据）
前端全真实化 = PASS（中栏执行流/规则树/3D 视口/自愈时间线全部接真实数据;HITL 卡驱动真实策略门导出;唯 VLM 六维评分区仍为演示数值）
当前状态     = 全栈工程落地 + 双真机基线复绿，D:/G6_Test/m3_invoke_e2e.blend 为微内核全链路产物
```

## 2. 恢复坐标

```text
分支：main
HEAD：以 `git rev-parse HEAD` 实测为准
全仓测试：1088 passed, 6 skipped, 2 warnings（2026-09-03 实测；6 skipped 为 opt-in 真机/真 LLM 测试，由 OPENBIMAGENT_RUN_REAL_* 环境变量门控，默认不跑）
代码规范：Ruff check 100% checks passed
手动测试：参考根目录下 MANUAL_TESTING_GUIDE.md
```

## 3. 已完成核心能力矩阵

### M1 G1–G7：双宿主与核心确定性管道

- **双宿主 typed 执行**：Blender 5.2 + Vectorworks 2024，同一份 `CompiledUtilityIR v1` 确定性生成，双宿主语义一致性验证。
- **不可变工件交付**：`ArtifactManifest v1.1`、`IfcOpenShell IFC4X3`、`buildingSMART IDS 1.0`、`MunicipalRuleEvidenceBundle` 签名证据。

### M1.5 T1–T7：市政管网求解器矩阵

- **四大确定性 Solvers**：StraightGravitySolver / NetworkGravitySolver / GridRouteSolver (A*) / HydraulicSolver (Manning)。
- **B1–B10 Benchmark 体系**：覆盖串联、汇流、分流、高程冲突、断网、有向环、规则歧义与 102 节点复杂管网。

### M2：产品化服务与 Web 控制台

- **FastAPI / SSE 服务**：只读路由 `/api/v1/sessions|attempts|approvals|lineages|artifacts` + SSE 事件流 + 写控制 `/api/v1/control`（`tree`/`export` 为 CLI 子命令，无对应 HTTP 端点）。
- **现代化 3 栏数字化工作台 (`web_ui.py`)**：左栏领域包与会话树；中栏执行流卡片与 HITL 审批；右栏 WebGL 3D 视口 + 六标签工作台（标签栏由插件 `declared_slots` 动态组装）。

### 微内核与 DSH 对标机制（2026-08 本会话）

1. **承重能力调度**：全部 7 个内置插件在 setup() 绑定真实 Handler，经 `registry.invoke()` 调度；HTTP 端点 `/api/v1/plugins`、`/api/v1/ui/slots`、`/api/v1/plugins/invoke`。
2. **Profile 补丁层**（对标 DSH Cordis patch layer）：`CapabilityOverride` 声明能力重定向，`activate_profile` 失败关闭校验后应用补丁，`deactivate_profile` 自动还原；`plugin.ablation.direct_solver` + `profile.ablation.no_self_healing` 为内置消融示范。
3. **规则自愈强化**：净距核验由规则集 `select_clearance_rule` 驱动（MU-CLEAR-001 分级碰撞/缓冲判定），覆土核验消费求解器 `RouteConstraintReport`；膨胀半径按规则净距自适应（ceil(2.5m/分辨率)），起终点检查井网格受保护。
4. **Benchmark 数据诚信契约**：`run_academic_benchmark` 五行对比表——openBIMAgent (M1.5 T7 真跑)、自愈 ON/OFF（SH-1–SH-6 电池，OFF 行经 Profile 补丁）、Heuristic 直插基线（StraightGravitySolver 逐段真跑）、LLM-Direct（**UNMEASURED 占位**，Markdown/LaTeX 强制携带禁止引用警告）；每行携带 `measured` + `provenance`。
   - 当前实测基线：T7 判定正确 10/10；SH 电池 ON 收敛 5/6 (83.3%) vs OFF 2/6 (33.3%)；直插基线合规 0%。
5. **外部插件发现**：`openbimagent-plugin.toml` manifest 约定 + `OPENBIMAGENT_PLUGINS_DIR` 目录发现加载（失败关闭：manifest 与插件事实不符即拒绝注册）；生态索引约定 GitHub topic `openbimagent-plugin`。

### M3 真机 E2E 预演（2026-08-22，tools/m3_real_e2e_blender.py）

全链路每步经微内核调度、不绕过 registry：`invoke("solver:self_healing")` 真实收敛（2 轮/5 段/1 违规自愈）→ 策略门把 `cad_host:*` 设为 prompt（无 confirm 拒绝、confirm=True 放行，Codex execpolicy 语义在真机生效）→ `invoke("cad_host:blender")` 生成 typed plan → headless Blender 5.2 + fork addon `execute_plan` 受控执行：receipt=completed、semantic snapshot 与 IR 哈希绑定、22 对象、120KB .blend + sidecar 落盘授权根。前置修复：真机集成测试的临时相机/灯改走 `__OBMCP_` 作用域豁免（scope lock 语义正确，原测试自违其锁）；v1.2 前的过期受控产物已重生成。

### Codex 机制吸收（2026-08-22 会话，调研见 docs/research/02_opensource_landscape.md §6）

1. **规则自检样例（self_tests，对标 execpolicy "加载即单测"）**：MunicipalRuleSet 升 v1.2 / 编译器 v0.3.0；12 条净距规则全部携带 match/not_match 样例（合计 33 例，覆盖边界值/跨界命中兄弟规则/缺属性失败关闭），`compile_municipal_rule_set` 编译期对全规则集重放验证，任一失效即拒绝整个规则集；**production 规则两类样例缺一不可**（治理失败关闭）；`run_rule_self_tests` / `validate_rule_self_tests` 公共 API；schema 与 T7 冻结哈希同步重冻结。
2. **能力策略门（对标 execpolicy 三态决策）**：`CapabilityPolicyRule`（精确/`前缀:*` 通配 + 最长前缀获胜 + justification 必填进拒绝信息）；`registry.invoke` 执法——forbidden 直接拒绝、prompt 需显式 `confirm=True`（人工确认语义）、无策略默认放行（开放内核，治理显式开启）；策略表经 `set_capability_policies` 失败关闭装载并暴露于 inventory。
3. **服务加固（对标 app-server）**：`/healthz` `/readyz` 探针；`/api/v1/plugins/invoke` 有界并发背压（满载 503 + error code -32001 "Server overloaded; retry later."）+ 求解移入线程池不再阻塞事件循环；Web Console 增"确认执行"复选框走 prompt 全链路。

## 4. 最新有效质量证据

```text
全仓 pytest：1066 passed, 6 skipped, 2 warnings（2026-09-02 实测；skipped = opt-in 真机/真 LLM 测试）
规则自检：真实知识源 33/33 样例重放通过（test_rule_self_tests）
Ruff 静态检查：All checks passed!
消融电池确定性：test_self_healing_ablation 跨运行逐字节一致
```

## 5. 新会话与快速启动

- 启动 Web 工作台：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000`（模块级 `app` = 演示装配：空只读 service + 默认微内核；生产装配走 `build_m2_readonly_app(adapter)`）
- 运行全量测试：`uv run pytest tests/ -q`
- 生成论文对比表：`uv run python -c "from openbimagent.benchmark.academic_bench import run_academic_benchmark; print(run_academic_benchmark().to_markdown_table())"`
- 自愈消融电池：`uv run python -c "from openbimagent.benchmark.self_healing_ablation import run_self_healing_ablation; on, off = run_self_healing_ablation(); print(on); print(off)"`

## 6. 未完成债务与唯一下一动作

- **2026-09-02 UI 集成收官（方案 J · 功能打通）**：`web_ui.py` 已由"Codex × 3D"终版替换（Franken shadcn zinc 皮肤 + Motion 动效；1540 行旧三栏巨石下线）。库文件 vendor 到 `src/openbimagent/server/static/vendor/`（franken+motion，MIT），`/static` 挂载，**完全离线**。功能全部打通非演示：设置页真实读写 `config/llm_baseline.local.toml` + provider keys 入环境/`.env`（`GET/PUT /api/v1/settings/llm`，key 只写不回显）；附件真实落盘 `out/uploads/`（`POST/GET /api/v1/uploads`，sha256 manifest）；composer 文本真实调度自愈求解器追加回合；HITL 批准 → 真实 POST export-blender 回填回执；3D 视口由真实 IR 驱动动态取景。**Agent 主链路打通**：新建任务 → `POST /api/v1/runs` 后台真跑 pipeline（单并发锁、离线模板安全、yes 自动放行）→ 会话落 out/sessions/index.json（demo app 已改为真实索引）→ `GET sessions/{id}/events` 线程渲染真实事件（clarify 问答/plan/子代理），页面轮询 runs/active 实时追加；脚本化首回合与自动播放已剔除。新增 `server/workbench_io.py` + `server/runs.py` + `tests/test_workbench_io.py`（5 测）+ `tests/test_runs.py`（4 测，真跑一次 pipeline）。
- **审批中心已打通（P0 完成，撤掉 yes=True）**：`server/approvals.py` 阻塞式 Web 审批门——pipeline 触门（execute_code/deliver 前）挂起运行线程，`GET /api/v1/approvals` 轮询票据，前端 HITL 卡人工批准/拒绝 → `POST …/decide` 放行；approval_requested/decided 事件落 Session JSONL（对齐 decision_receipt）；30min 超时失败关闭。`tests/test_approvals.py`（3 测 E2E：真跑 single_asset_hero → deliver 门挂起 → 批准 → 放行 → 事件落盘）。municipal_utility Web 运行已补 `solver_input.default.json`（pack 内默认入参，含完整碰撞上下文），现可越过 domain_gate 抵达 deliver 审批门。
- **P1–P4 全部完成**：P1 SSE 实时跟随 `GET sessions/{id}/events/stream`（回放+持续推送+运行结束自动关闭，前端 EventSource，断开回退轮询）；P2 素材归档（交付工件只增不改写 `domain_packs/*/assets/auto_archive/<session>/` + sha256 index，gitignore，`GET /api/v1/archive`）；P3 用量面板（`GET /api/v1/usage` 读 usage_summary.json，检查器「用量」页）；P4 会话分支（`POST sessions/{id}/fork` → SessionStore branch/fork，会话项 ⑂ 按钮）+ 审批附带指令（decide 携带 instruction 写入决策回执，steer 语义在审批门生效；运行时中途 steer 属 Subagent Runtime 路径，assembly 顺序流不接）。`tests/test_workbench_p124.py`（5 测）。旧原型 A–H 已清理，ui/ 保留方案 I/J/K/L 与装配脚本 `scratch/build_web_ui.py`。
- **2026-09-02 审查修复（已提交）**：pyproject 补 `lxml>=4.9`；清理过期 TODO/docstring；VLM 评分区加 DEMO 水印。
- **仓库卫生（已完成）**：`docs/学术材料/`、`开题报告.*`、`architecture.*`、`outputs/` 经 filter-repo 从全部历史清除并强推；本地文件保留且被 gitignore；权威备份 `scratch/git-backup/pre-rewrite-20260902.bundle`。
- **已知风险（未修）**：`fastapi_app.py` 演示 app 的 `/api/v1/plugins/invoke` 与 export 端点无鉴权（confirm 仅为 body 布尔值）；绑 127.0.0.1 使用风险可控，**勿绑 0.0.0.0 暴露**。生产鉴权待 M2 后续落地（`authentication.py` 目前仅契约类）。
- **运维备注**：VW runner 侧"未响应"为脚本线程被轮询循环占用的预期形态，实测待命 5.5h 零错误；runner 已具备固定 IPC 根 + 心跳 + 文件日志。
- **前端状态**：新工作台全部区块接真实数据（VLM 评分演示值已剔除出交付叙述）；Three.js 依赖随旧 UI 移除，3D 视口为自绘 canvas 渲染器。
- **论文侧**：B10 LLM 超时 ×3 与 LLM 行多次运行方差待写入 limitations；execpolicy 吸收可作 rule-driven 可验证性论据。
- **2026-09-03 独立审核四项修复（已验证）**：🔴 控制面鉴权——`server/auth.py` Bearer token 守卫（/api/v1/** 变更方法 401，GET 开放；token 自动生成 `config/workbench.local.toml`（gitignored）或 `OPENBIMAGENT_WORKBENCH_TOKEN` 覆盖；页面注入 `window.__WB_TOKEN`，前端全请求携带）；🟡 前端清污——删除原型假函数 runTurn/replayAll/假 sendMsg/假 doExport 与 mock SESS/PLUGINS/RULES/IR_JSON，irPre/插件面板/面包屑/宿主芯片全部接真实端点（宿主=Blender TCP 实探，VW 未探测如实灰显），/solve 斜杠命令改真实调度；🟡 归档沙箱——`OPENBIMAGENT_ARCHIVE_DIR` 覆盖，test_workbench_p124 改 tmp 不再污染仓库；🔵 网关兜底——只读 GET 缺 X-Request-ID 自动补全（test_health 断言同步更新）。全量 1084 passed。
- **2026-09-03 架构评审六缺陷处置（全部落地）**：① Trace"自进化"正名+闭环——术语修正为"不可变事件溯源与设计资产增量沉淀"（docs/architecture/LIMITATIONS.md），归档反哺实装：新任务检索 Top-3 相似交付注入会话首条用户消息（runs.py `_retrieve_exemplars`，机制=In-Context Retrieval，非权重更新）；② SCAD 语义断层/③ 宿主强耦合/⑤ 规则泛化天花板 → 三项结构性边界写入 LIMITATIONS.md（L1/L2/L3，论文 Limitation 表述建议）；④ 并发死局破解——有界多并发（`OPENBIMAGENT_MAX_CONCURRENT_RUNS` 默认 2，超额 409；每运行独占 `out/runs/<sid>/`）+ 审批票据落盘 `out/pending_approvals.json`（重启后列为 expired，批准 410、拒绝作废，`OPENBIMAGENT_PENDING_APPROVALS` 可覆盖）；⑥ 视口流式生长——`GET /api/v1/runs/artifact`（白名单+sha256+mtime），前端运行中轮询 sha，变化即重渲染 CompiledUtilityIR。新增 `tests/test_runs_p456.py`（4 测）。全量 1088 passed。
- **2026-09-03 终审通过（独立多轮复验）**：两轮处置（24bb77e 鉴权与前端去伪 / 538aa24 六缺陷）经独立审核全量复验确认真实通过，无新增缺陷项。成熟度矩阵更新：Agent Core 95% / 市政专项 95% / SCAD 环 85% / 多 MCP 85% / Trace 资产沉淀 85% / Web 控制台 88%。测试基线：**1088 passed, 6 skipped**。系统已达论文写作与答辩演示状态。
- **2026-09-03 上下文预算与压缩落地（COMPONENTS §5 最后一块 TODO 清零）**：`core/loop.py` 新增 `_maybe_compact`——估算 token 超 context_window×0.6 即压缩（保留 system+任务锚点+最近 12 条，中段经 clarify 角色凝练摘要，离线失败回退确定性骨架），硬上限 0.92 保底；压缩标记 + digest_sha256 写 session 树可审计；context_window 取自 models.toml ModelConfig（兜底 131072）。机制对齐 Codex auto-compaction / pi 滑窗。既有纪律本就在线：system prompt 2000 token 挂载检查、工具结果 llm/ui 双视图、read/bash 输出截断、子代理 isolated context + 工件介质交接、截图降采样。`tests/test_context_compaction.py`（3 测：触发/锚点保留/预算内调用/离线骨架回退/预算内零干预）。
- **2026-09-03 Agent Core 六项增强全部落地（方案 docs/architecture/AGENT_CORE_ENHANCEMENT.md §5 台账）**：P0-2 会话 FTS5 全文检索（`session/search.py`，CJK bigram 展开，水位线增量，`GET /sessions/search`，前端 `/recall`）；P0-1 Skill 系统（`skills/registry.py`，SKILL.md frontmatter 失败关闭校验，渐进披露目录注入上下文，运行成功自动蒸馏候选入 `skills/_candidates/` 且**永不自动生效**须人工批准转正，`GET/POST /api/v1/skills*`，前端 `/skills`；内置 municipal-gravity-brief / ir-inspection 两真实技能）；P0-3 宿主 Supervisor（`mcp_clients/supervisor.py` 状态机+有界退避重启，VW 恒 external 诚实标记，`POST /hosts/{id}/restart`）+ 工具集预设（`core/toolset.py` minimal/modeling/full，`/api/v1/plugins` 清单过滤 + invoke 403 门双层生效，设置弹层可切换）；P0-4 记忆层（`core/memory.py` MEMORY.md/USER.md 追加式，`memory:record` 走 prompt 策略门 409 need_confirm，记忆片段注入新任务上下文，设置弹层查看+确认写入）；P1-1 通用 MCP client（`mcp_clients/external.py`，`OPENBIMAGENT_MCP_SERVERS` env JSON 挂载第三方 server，工具映射 `mcp:<server>:<tool>` 默认 prompt 策略，失败 server fail-closed 跳过）；P1-2 Hooks 总线（`core/hooks.py` pre_tool 可否决 fail-closed + post_tool/turn_end/run_end 观测，registry.invoke 与 runs finally 接线，ring buffer 200）。横切修复：`_REPO_ROOT` 定位 bug ×2（skills/memory 曾误写 src/）已修并加回归测试；4 个 pipeline 测试补 SKILLS_ROOT 沙箱；构建脚本迁 `tools/build_web_ui.py`（相对路径，产物字节级一致）首入版本控制。新增 5 测试文件 56 测。全量 **1148 passed, 6 skipped**。
- **唯一下一动作**：论文正文写作（素材已备齐：docs/学术材料/实验数据与limitations草稿_2026-08-23.md，LLM 行已升级为 n=3 均值±标准差 60.0±0.0 / 7105±330ms / 10447±294 tok）。
