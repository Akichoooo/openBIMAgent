# openBIMAgent 阶段交接状态

版本：v3.4
更新时间：2026-08-22（Asia/Shanghai）
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
M3 真机 E2E 预演 = PASS（registry.invoke→自愈求解→策略门 confirm→Blender 5.2 受控执行落盘）
当前状态     = 全栈工程落地 + 双真机基线复绿，D:/G6_Test/m3_invoke_e2e.blend 为微内核全链路产物
```

## 2. 恢复坐标

```text
分支：main
HEAD：以 `git rev-parse HEAD` 实测为准（本会话改动未提交，见 §6）
全仓测试：1055 passed, 4 skipped, 2 warnings（2026-08-22 实测）
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

- **FastAPI / SSE 服务**：`/api/v1/sessions`、`/api/v1/tree`、`/api/v1/export` 等只读与控制端点。
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
全仓 pytest：1055 passed, 4 skipped, 2 warnings
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

- **待提交**：累积 20+ 文件改动（补丁层/自愈核验/benchmark 真实化/外部加载器/LLM 基线/3D 视口/Codex 吸收及全部测试）尚未 commit，建议按机制拆 2–3 个提交。
- **中栏执行流卡与规则树数值**：仍为静态演示数据（M3 范围；3D 视口与自愈时间线已接真实数据）。
- **论文侧**：B10 LLM 超时 ×3 与 LLM 行多次运行方差待写入 limitations；execpolicy 吸收可作 rule-driven 可验证性论据。
- **唯一下一动作**：① 提交本轮修复与 M3 预演脚本（本会话已提交至 22bd453 后的增量）；② 把「build plan + 真机 execute_plan」收敛为正式 capability（如 `cad_host:blender.execute`，prompt 策略默认开启）+ Web UI 宿主写入按钮；③ Vectorworks 侧同构 E2E。
