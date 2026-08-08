# openBIMAgent 阶段交接状态

版本：v3.0
更新时间：2026-08-08 23:37（Asia/Shanghai）
维护状态：**ACTIVE**
工作区：`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`

> 本文档是跨会话恢复的唯一实时入口。只保留当前可复核事实、未完成债务、受保护内容和唯一下一动作。历史过程详见 Git 提交、专项验收报告与 `.workbuddy/memory/`，不要把历史测试数字当作本轮新证据。

## 1. 当前阶段结论

```text
M1 G6 = PASS
M1 G7 = IN PROGRESS
M1.5 T1–T7 = OFFLINE PASS
M1.5 = OFFLINE PASS
当前 Gate = M1 G7：最终工程验收
```

G6 已由真实 Vectorworks 2024 GUI 验收通过：22/22 typed operations，10 个稳定对象，米制单位，M1-Municipal-Utility 授权图层，completed receipt，幂等重放 receipt 相等且工件字节不变。双宿主语义比较存在差异（Vectorworks GetPolyPt3D 第二个顶点返回 1e+97 垃圾值），需在后续修复，不影响 G6 执行通过。

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
- 真实 Blender G6 已通过：current-contract B1 为 22/22 operations、10 个稳定对象、真实模型/sidecar，首次执行与幂等重放 receipt 相等且工件字节不变；证据见 `outputs/g6_b1_blender_execution_result.json`。

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

### M1.5 T7：规模化 benchmark 与总验收

- B1–B10 冻结 3 井串联、分支、汇流、锚点冲突、断网、有向环、证据缺失、规则歧义、26 节点和 102 节点场景；业务状态严格区分 PASS/FAIL/UNKNOWN/REVIEW_REQUIRED。
- 每个场景冻结输入、期望失败语义、规则身份、IR canonical hash，以及 input/result/artifact 三层 SHA-256；严格 envelope Schema 拒绝未知字段和工件篡改。
- 覆盖路线折点、55 个复杂地表样点、净距审批、design/check 水力、同义乱序、幂等、checkpoint/resume 和故障恢复；benchmark runner 不依赖删除 recovery state。
- 正向场景通过离线 Blender/Vectorworks SemanticSnapshot 和 IFC4X3/IDS；B9 比较 120 个对象，B10 比较 481 个对象。该证据仅为 offline compatibility，不能关闭真实 G6。
- 正式输出目录连续执行两次完整 B1–B10，均为 overall PASS 且 Blender/Vectorworks resume=true。详细证据见 `outputs/M1_5_T7规模化Benchmark与总验收报告.md` 和 `outputs/m1_5_t7_benchmark/`。

## 4. 最新有效质量证据

以下数字对应本次 G6 验收后重新实测；后续代码修改后必须重跑：

```text
全仓 pytest：963 passed, 4 skipped, 1 warning in 105.13s
G7 专项验收（IFC/IDS/RuleEvidence/Domain Gate/Manifest/Recovery/Benchmark/Semantic）：299 passed in 63.90s
Ruff（src/tests/mcp_servers）：All checks passed
compileall（src/tests/mcp_servers）：passed
Git diff --check：passed
CLI 入口 `python -m openbimagent --help`：passed
```

唯一 pytest warning：`tests/test_vs_index.py::test_generate_vs_index_success` 动态源码中的既有 `SyntaxWarning: invalid escape sequence '\\ '`, 未影响测试通过。

历史 T7 证据仍保留在专项报告中，不作为本轮重新运行数字：JSON Schema 35 schemas loaded；正式 B1–B10 双重运行 FIRST PASS true/true、SECOND PASS true/true。详细证据见 `outputs/M1_5_T7规模化Benchmark与总验收报告.md`（若该历史文件路径已被压缩，以 `outputs/m1_g7_offline_acceptance_2026-08-07/` 为准）。

关键本地提交（均未推送）：

```text
1f89fa8 feat: add deterministic utility network slice
de8315e feat: add deterministic utility route solver
db0e1ba feat: add deterministic gravity hydraulic solver
6114f51 docs: accept M1.5 hydraulic solver slice
e929629 feat: complete M1.5 T6 rule evidence slice
```

### 2026-08-07 Gate B 受影响回归

```text
G6/G7 + AgentLoop/BIMBench 受影响回归：212 passed in 57.62s
Ruff src tests：All checks passed
compileall src tests：PASS
Git diff check：PASS
```

2026-08-07 Vectorworks 真实链路首次尝试发现并修复一个 runner 识别缺口：Vectorworks 空白未保存文档通过 `GetFPathName()` 返回 `Untitled-1`，原逻辑误判为已命名活动文档并在首个 typed host side effect 前 fail-closed。修复文件为 `mcp_servers/vectorworks_mcp/runner.py`，新增失败契约测试于 `tests/test_vw_runner.py`；只接受明确的 `Untitled`/`Untitled-N` 占位引用，其他活动文档仍拒绝。

```text
Vectorworks runner 聚焦回归：18 passed in 1.69s
Vectorworks/AgentLoop 受影响回归：114 passed in 11.28s
Ruff runner/tests：All checks passed
compileall runner/tests：PASS
git diff check：PASS
```

测试 stderr 中的 `SAFE_DELETE_FAIL_CLOSED` 为 Windows sandbox recycle-bin 不可用时的既定失败关闭提示，不是测试失败，未修改或绕过安全删除机制。真实尝试已经消费并归档获批 job；入口脚本现会只在归档 job SHA-256 精确等于 `b4458d45cdecea2784aff6d6356f07bd62c1e27edcef0a99a9cb7368cefe7835` 时，将原始字节恢复到 active jobs 目录后再调用同一 runner，避免绕过审批或改变 job 语义。路径字面量已改为正斜杠，修复后的入口脚本 SHA-256 为 `1ce1ae971ab5908e3e35a80778aa02c62531d70267d39092d4cdbcb465e8a94b`。

第二次真实执行形成 partial receipt，失败在首个 `create:sys-wastewater` 的图层反查：`applied_operations=[]`、`confirmed_object_ids=[]`、错误为 `Vectorworks 对象设计图层名称为空`，UI 同时报告 `Handle variable is NIL`。目标 `.vwx` 未生成；sidecar 仅含空恢复状态。根因是 runner 用 `vs.GetLayer(handle)` 读取对象所属层；已改为 `vs.GetParent(handle)` 后读取 `GetLName`，并增加禁止退回错误 API 的失败契约。聚焦回归 `20 passed in 1.66s`，Vectorworks/语义/恢复受影响回归 `71 passed in 3.72s`，Ruff、compileall、git diff check 通过。修复后需由 JY 新建空白未命名文档，再运行同一入口脚本；不能在当前含未确认对象的文档中继续。

## 5. 未完成债务

### M1 G6：Vectorworks 真实宿主 ✅ PASS

2026-08-08 23:37 真实 Vectorworks 2024 GUI 验收通过：

```text
.vwx:         41891 bytes, SHA-256 59fbe9dc03b8...
sidecar:      21740 bytes, receipt=completed, 22 ops, 10 objects confirmed
result:       12411 bytes, status=completed, 22/22 operations, 10 objects
replay:       18378 bytes, idempotent_receipt_equal=True, idempotent_bytes_unchanged=True
units:        m
layer:        M1-Municipal-Utility
```

双宿主语义比较：存在差异（Vectorworks `GetPolyPt3D` 第二个顶点返回 `1e+97` 垃圾值），需在后续修复 `_project_semantic_snapshot` 中的坐标读取逻辑。不影响 G6 执行通过。

### M1 G7：最终工程验收

G6 已闭合。需补齐全仓质量基线（已完成）、正式 benchmark 确认、IFC/IDS/RuleEvidence/Domain Gate/Manifest 总验收、恢复证据、M1 报告、Wiki/架构收口和本地边界提交。

### M1 G7

G6 闭合前不得最终 PASS。之后需补齐全仓质量基线、正式 benchmark、IFC/IDS/RuleEvidence/Domain Gate/Manifest 总验收、恢复证据、M1 报告、Wiki/架构收口和本地边界提交。

### M1.5 T7

T7 已完成离线验收并形成独立报告。`overall_status=PASS` 只表示 B1–B10 的观测状态和验收语义符合冻结预期；B4–B6 仍是目标 FAIL，B7 是 UNKNOWN，B8 是 REVIEW_REQUIRED。

后续在 G6/G7 完成后再进入 M2 产品化服务/客户端，最后进入 M3 评测、基准和学术输出。

## 6. 当前唯一动作：M1 G6 真实宿主验收

每次接管先检查：

```text
D:\devloop\G6_Test\current_b1\openbimagent_b1.vwx
D:\devloop\G6_Test\current_b1\openbimagent_b1.vwx.openbimagent.json
D:\devloop\G6_Test\current_b1\vectorworks\results\g6-b1-vectorworks-bff5751c7992358f.json
```

2026-08-08 20:32 接管复核：当前 HEAD 为 `b8f6789ca310a691bfc76fa182d5cb9456c90648`；工作树仍有接管前保护的文档、`runner.py`、测试及 `.workbuddy/` 等未提交差异，本轮未回滚、清理、暂存或提交。全仓 pytest 重新实测为 `959 passed, 4 skipped, 1 warning`；Ruff、compileall、`git diff --check` 均通过；CLI 可启动，控制面当前无请求。

当前真实工件仍未闭合：`openbimagent_b1.vwx` 缺失，replay result 缺失；sidecar 存在但仅含空恢复状态（`applied_operation_ids=[]`、`confirmed_object_ids=[]`、`receipt=null`），不能视为已完成工件。`vectorworks/results/g6-b1-vectorworks-bff5751c7992358f.json` 仍是 `status=partial`、`applied_operations=[]`、`confirmed_object_ids=[]`，并记录首个 `create:sys-wastewater` 的图层名称为空。目录中保留对应 `.failed` 失败证据。未检测到 Vectorworks 运行进程；因此本轮不执行宿主脚本，G6 继续保持 `EXTERNAL_BLOCKED / PENDING`。

2026-08-08 20:42 再次执行前预检：获批归档 job SHA-256 仍为 `b4458d45cdecea2784aff6d6356f07bd62c1e27edcef0a99a9cb7368cefe7835`；入口脚本 SHA-256 仍为 `1ce1ae971ab5908e3e35a80778aa02c62531d70267d39092d4cdbcb465e8a94b`；旧 runner SHA-256 为 `0d6de25331f340eb610564ea9e452879584e6513ab4a3f625cec251c8ec1ce49`。随后 JY 在 Vectorworks GUI 重试，错误从 Python 结果暴露为原生 `Handle variable is NIL`，首个 `create:sys-wastewater` 仍未落盘。

2026-08-08 21:32 针对 NIL handle 完成最小失败关闭修复：runner 增加 None/False/数值 0 的 NIL 识别；`GetParent` 返回 NIL 时不再直接调用 `GetLName`，而是通过精确授权图层句柄和 `FInLayer`/`NextObj` 遍历证明对象隶属关系；无法证明隶属仍拒绝执行。测试 fake host 增加同等对象遍历语义和负向契约。Vectorworks runner、typed plan/client、SemanticSnapshot、恢复受影响回归 `59 passed in 3.35s`，Ruff、compileall 通过；`SAFE_DELETE_FAIL_CLOSED` 仍为 Windows 测试环境既定保护提示。

2026-08-08 21:53 根据最新真实 receipt `operation=create:sys-wastewater: Vectorworks 设计图层名称为空` 继续修复：确认真实桥接还存在“父句柄表面非 NIL、但 `GetLName` 返回空值”的形态，旧兜底未覆盖。runner 现同时对 NIL 和空名称父层进入失败关闭兜底；授权图层只允许通过 `GetLayerByName`、`ActLayer` 或 `FLayer`/`NextLayer` 候选中 `GetLName` 精确等于 `M1-Municipal-Utility` 的句柄解析，并继续要求 `FInLayer`/`NextObj` 遍历命中目标对象，活动图层本身不构成授权证据。新增空名称父层的正向成员证明和负向越权测试。runner 聚焦测试扩展后为 `30 passed in 1.51s`，其余 Vectorworks/语义/恢复回归 `34 passed in 1.95s`；全仓 `966 passed, 4 skipped, 1 warning in 109.73s`，Ruff、compileall、`git diff --check` 通过。当前 runner SHA-256 为 `29cacede970559f937fa50820af2ccf5ce7b1e9df24025455e526e19b672e0b8`；批准入口已增加该 runner 完整性校验，更新后入口脚本 SHA-256 为 `e83afb192f07599eb1b1650faac8ed1b58f71c2499bf29e8e768cfd0c731b3ea`，归档 job SHA-256 仍为 `b4458d45cdecea2784aff6d6356f07bd62c1e27edcef0a99a9cb7368cefe7835`，审批语义未改变。当前修改尚未提交，必须在全新 Vectorworks 空白未命名文档中再次执行同一批准入口；成功前 G6 保持 `EXTERNAL_BLOCKED / PENDING`。

2026-08-08 22:25 用户在 Vectorworks GUI 运行上一版兜底后，宿主无限打印原生 `Handle variable is NIL`。直接原因是图层/对象归属验证仍调用 `FLayer/NextLayer`、`FInLayer/NextObj` 等 HANDLE 链式遍历；Vectorworks 的 NIL 包装值可能未被 Python 的 None/0 判断识别，导致原生错误在循环内重复。最新 runner 已删除该路径及 `GetParent/GetLName` 归属反查，改用一次精确 `ForEachObject` criteria 查询（授权图层 + 稳定对象名）并以句柄相等或 `GetObjectUuid` 相等证明对象身份；criteria 值采用白名单，API 缺失、对象未命名或未命中均失败关闭。runner 聚焦测试 `24 passed in 1.97s`，其余 Vectorworks/语义/恢复回归 `34 passed in 2.21s`；最新全仓复核为 `963 passed, 4 skipped, 1 warning in 92.12s`，Ruff、compileall、`git diff --check` 通过。当前 runner SHA-256 为 `518c0d54c97e2331a90e5ca2f38a71bce679c2ec8c1cb417f7f34975766ef761`，GUI 入口已更新绑定并通过 py_compile，入口 SHA-256 为 `036e3aded7f4cd4267d83b0fe6fbbcbbd0eb2b6645cb7842e2a75e95d5473307`。本次仍未获得新 completed receipt 或 `.vwx`，G6 不变。

为后续多模型接管新增 `outputs/openBIMAgent_已完成任务压缩摘要与当前接管点_2026-08-08.md` 和 `outputs/DeepSeekV4Flash_openBIMAgent_全后续长任务接管包_2026-08-08.txt`；Wiki 仍使用 `docs/README.md`，没有建立平行 Wiki。长任务包定义 C0-C6 复核点，由执行模型持续完成，主检查者在真实宿主、Gate 收口、提交前、M2 各 Gate、M3 实验冻结和最终验收处检查。

工件缺失或宿主 GUI/许可证不可核验时保持 `EXTERNAL_BLOCKED / PENDING`，不得重跑离线 T7 来替代 G6，也不得使用伪 `.vwx`、空文件、独立 Python、`execute_vs_code` 或 fake host 关闭 Gate。

工件到位后立即核验文件大小/SHA-256、completed receipt、22/22 operations、10 个稳定对象、米制单位、图层、records、3D geometry、topology、SemanticSnapshot、幂等重放和真实双宿主严格比较；仅允许协议声明的 `host_handle`、`presentation_material` 差异。

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
