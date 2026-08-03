# openBIMAgent 阶段交接状态

版本：v2.1
更新时间：2026-08-04 03:24（Asia/Shanghai）
维护状态：**ACTIVE**
工作区：`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`

> 本文档是跨会话恢复的唯一实时入口。只保留当前可复核事实、未完成债务、受保护内容和唯一下一动作。历史过程详见 Git 提交、专项验收报告与 `.workbuddy/memory/`，不要把历史测试数字当作本轮新证据。

## 1. 当前阶段结论

```text
M1 G6 = DEFERRED / IN PROGRESS
M1 G7 = FINAL BLOCKED
M1.5 T1–T6 = OFFLINE PASS
M1.5 = IN PROGRESS
当前 Gate = M1.5 T7：规模化 benchmark 与总验收
```

JY 已明确允许在 Vectorworks GUI 真机验收延期期间继续推进 M1.5 代码、Schema、负向测试、离线 E2E 和文档；该授权不等于 G6/G7/M1 通过。

## 2. 恢复坐标

```text
分支：main
HEAD：以 `git rev-parse HEAD` 实测为准
T6 实现提交：e9296294eb35eb22ecca11a7d3322e94a90588c7（feat: complete M1.5 T6 rule evidence slice）
远端：未推送
```

接管第一步必须重新执行：`git status --short`、当前 HEAD、staged/unstaged/untracked diff、测试与 Schema 基线。不得无条件沿用本文旧数字。

## 3. 已完成能力（压缩版）

### M1 G1–G5

- typed Vectorworks plan、不可变 ArtifactManifest、SemanticSnapshot、离线双宿主比较、IFC4X3/IDS 1.0、RuleEvidence、Domain Gate、checkpoint/resume、审批对账、幂等和恢复安全已形成边界提交。
- 真实 Blender G6 已通过：13/13 operations、6 个稳定对象、真实模型/sidecar、幂等重放 hash 不变。

### M1.5 T1–T3：多节点拓扑与确定性网络

- `CompiledUtilityIR v1` 严格冻结、禁止额外字段/非有限值，校验引用闭合、端口方向和占用、几何/坡度一致、系统连通、孤立节点、junction 语义及重力 DAG。
- `NetworkGravitySolverInput v0.1` / `municipal-network-gravity-solver v0.1.0` 支持多井、多段、支路、汇流、源节点管底锚点和显式跌水；同义乱序输入保持 canonical hash 稳定。
- 同一 IR 离线生成 Blender/Vectorworks typed plan，并通过多节点 IFC4X3/IDS 和语义比较。

### M1.5 T4：路线与复杂标高

- `GridRouteSolverInput/Result v0.1` 在显式批准的四邻接走廊内确定性搜索；按生产规则集 SHA-256 绑定净距规则，逐 cell 检查覆土和连续坡度。
- 候选按长度、转折数、完整 cell 序列稳定排序；适配器会重算候选并拒绝候选、规则集、端点和工程属性篡改。
- 走廊断裂、障碍封堵、覆土冲突结构化返回；搜索预算耗尽为 `UNKNOWN / search_limit_exceeded`，不得包装为确定性无解。

### M1.5 T5：独立水力 Solver

- `HydraulicSolverInput/Result v0.1` 显式绑定源 IR SHA、Manning `n`、来源和 source reference，并要求 `design` 与 `check` 工况。
- 按 `manning_uniform_open_channel_si` 计算满流能力、容量裕量、部分充满度、水力半径和流速；所有内部节点检查流量守恒。
- `geometry_mutated=false`；水力结果独立于几何 IR，输出独立 Domain Evidence/RuleEvidence。

### M1.5 T6：规则证据、例外与宿主表达

- `MunicipalRuleEvidenceBundle v1.0` 和 `ClearanceExceptionApproval v1.0` 对 official/secondary/legacy、production verification、规则/核验/规则包/评估/审批 canonical SHA-256 建立失败关闭协议。
- 统一选择/评估水力、水平/垂直净距、道路、轨道、河道和构筑物规则；缺属性、歧义、证据不足和无法唯一选择不生成伪 PASS。
- `MU-DRAIN-007` 已绑定官方正文 SHA-256 和条款定位；水力容量与最小流速分证据输出，最小流速可确定性形成 PASS/FAIL，无可计算流速保持 UNKNOWN。
- T6 路线包装逐障碍物绑定规则与减距审批，并以相同上下文重算后接入网络；减距结果必须绑定有效审批，未减距不得冒用审批。
- RuleProjectionIdentity 已串联 typed Blender/Vectorworks、SemanticSnapshot、IFC4X3/IDS；`CompiledUtilityIR v1` 模型和 Schema 保持零漂移。

## 4. 最新有效质量证据

以下数字对应本轮 T6 修复后重新执行的真实门禁；后续代码修改后必须重跑：

```text
全仓 pytest：680 passed, 4 skipped, 1 warning in 76.26s
T6 规则/路线/水力/组合链路：70 passed in 12.39s
严格 T6 Schema 与路线：24 passed in 2.85s
JSON Schema 元校验：34 schemas meta-validated
Ruff：All checks passed
compileall：passed
git diff --check：passed
```

唯一 warning：`tests/test_vs_index.py::test_generate_vs_index_success` 动态源码中的 `SyntaxWarning: invalid escape sequence '\\ '`, 未影响测试通过。详细 T6 证据见 `outputs/M1_5_T6规则证据例外与宿主表达离线验收报告.md`。

关键本地提交（均未推送）：

```text
1f89fa8 feat: add deterministic utility network slice
de8315e feat: add deterministic utility route solver
db0e1ba feat: add deterministic gravity hydraulic solver
6114f51 docs: accept M1.5 hydraulic solver slice
e929629 feat: complete M1.5 T6 rule evidence slice
```

## 5. 未完成债务

### M1 G6：Vectorworks 真实宿主

必须由 JY 后续在 Vectorworks 2024 GUI 空白未命名文档中执行，不得用独立 Python、空文件、伪 `.vwx`、`execute_vs_code` 或离线模拟器替代。

执行脚本：

```text
D:\devloop\G6_Test\vectorworks_g6\run_g6_once_in_vectorworks.py
```

预期工件：

```text
D:\devloop\G6_Test\openbimagent_g6.vwx
D:\devloop\G6_Test\openbimagent_g6.vwx.openbimagent.json
```

工件到位后连续验收：文件大小/SHA-256、completed receipt 身份、13/13 operations、6 个稳定对象、米制单位、图层 `M1-Municipal-Utility`、records、3D geometry、topology、SemanticSnapshot、幂等重放及 `.vwx`/sidecar 前后 hash，并与 Blender 真实快照严格比较。仅允许 `host_handle`、`presentation_material` 差异。

### M1 G7

G6 闭合前不得最终 PASS。之后需补齐全仓质量基线、正式 benchmark、IFC/IDS/RuleEvidence/Domain Gate/Manifest 总验收、恢复证据、M1 报告、Wiki/架构收口和本地边界提交。

### M1.5 T7

- 正常/边界/冲突/证据缺失/规则歧义 benchmark；至少 25 节点和 100 节点网络；正确性、确定性、恢复性、性能、宿主一致性和总验收。
- T6 离线验收报告：`outputs/M1_5_T6规则证据例外与宿主表达离线验收报告.md`。

后续再进入 M2 产品化服务/客户端，最后进入 M3 评测、基准和学术输出。

## 6. T7 唯一启动动作

先冻结 T6 已通过协议和规则身份，建立可重复 benchmark 工件与验收脚本：

1. 建立正常、边界、冲突、证据缺失和规则歧义场景集，期望状态显式区分 PASS/FAIL/UNKNOWN/REVIEW_REQUIRED。
2. 建立至少 25 节点和 100 节点网络，覆盖支路、汇流、路线折点、复杂地表标高、净距审批和 design/check 水力工况。
3. 对同义乱序、重复执行、checkpoint/resume 和故障恢复验证 canonical hash、输出和审计身份稳定。
4. 记录正确性、确定性、恢复性、性能和双宿主/IFC 语义一致性；离线 Vectorworks 结果不得替代真实 G6。
5. 运行全仓质量门禁并形成 T7 总验收报告；提交前继续保护接管前差异。

若真实 Vectorworks `.vwx` 和 sidecar 到位，立即暂停 T7，优先执行 M1 G6 真实验收。

## 7. 受保护工作树与提交纪律

接管前未提交内容必须原样保护，不得覆盖、回滚、清理、暂存或误提交：

```text
README.md
docs/README.md
docs/architecture/ARCHITECTURE.md
docs/architecture/COMPONENTS.md
docs/architecture/openBIMAgent_Architecture_Graph.md
docs/relays/RELAY_WORKFLOW.md
openBIMAgent项目与AgentCore实现详解.md
.workbuddy/
docs/architecture/DOCUMENTATION_GOVERNANCE.md
docs/architecture/M1_EXECUTION_CONTRACT.md
docs/architecture/M1_MASTER_PROMPT.md
outputs/M1_G6真实宿主预检与阻塞报告.md
```

每个 Gate 仅可在明确审查 staged diff 后提交本 Gate 自己新增或修改的代码、Schema、测试、验收报告和实时交接状态；禁止远端推送。不要删除或暂存 `.workbuddy/`，不要绕过 Windows 安全删除失败关闭。

## 8. 新会话低上下文入口

完整的后续全阶段执行提示词已独立保存：

```text
outputs/openBIMAgent_后续全阶段低上下文执行提示词.txt
```

新会话直接复制该文件全文。它覆盖 `T7 → Vectorworks G6 → M1 G7 → M2 → M3 → 最终文档压缩`，并要求以本文实时状态为准、重新实测 Git/测试/Schema/宿主工件，不重复加载或输出长历史总结。

最短恢复指令：

```text
读取 docs/architecture/PROJECT_HANDOFF_STATUS.md 和 outputs/openBIMAgent_后续全阶段低上下文执行提示词.txt，按提示词直接接管执行；若真实 Vectorworks 工件已到位先做 G6，否则从 M1.5 T7 benchmark 继续。不要复述长历史，不推送远端。
```

## 9. 维护规则

- 门禁、HEAD、测试、授权、阻塞、真实工件或下一动作变化时更新本文。
- 本文只保留最新有效状态；过程细节写入专项报告或每日 memory。
- 未通过门禁保持 `DEFERRED / IN PROGRESS / BLOCKED / NOT RUN`，不得包装为 PASS。
- 任何实质工作完成后，追加当日 `.workbuddy/memory/YYYY-MM-DD.md`；不记录密钥、临时错误或可再生垃圾。
