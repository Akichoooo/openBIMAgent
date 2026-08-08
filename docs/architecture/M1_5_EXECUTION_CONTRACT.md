# M1.5 长任务执行契约：市政领域深化

版本：v1.0

生效日期：2026-08-03

状态：**ACTIVE — T1–T5 OFFLINE PASS / T6 NEXT**

> 本文件定义 M1.5 的范围、实施顺序、协议边界、验收证据、权限和停止条件。实时 HEAD、测试数字、阻塞和恢复位置仍以 [`PROJECT_HANDOFF_STATUS.md`](PROJECT_HANDOFF_STATUS.md) 为唯一来源；稳定总路线以 [`PROJECT_MASTER_WORKFLOW.md`](PROJECT_MASTER_WORKFLOW.md) 为唯一来源。
>
> JY 已授权在 Vectorworks G6 GUI 真机验收延期期间先推进 M1.5 代码。该授权不改变 M1 完成标准：Vectorworks 真实 `.vwx`、sidecar、幂等重放和真实双宿主比较仍是独立验证债务，不得用 M1.5 离线测试或模拟器结果替代，也不得把 G6/G7 标记为 PASS。

## 1. 目标

把 M1 的“两井一直管”确定性 benchmark 扩展为可审计的多节点市政管网能力：

```text
工程事实 + 可信 MunicipalRuleSet
→ 版本化网络 Solver 输入
→ 确定性拓扑、路线、标高和水力分层求解
→ CompiledUtilityIR
→ typed Blender / Vectorworks execution plan
→ SemanticSnapshot / IFC / IDS / RuleEvidence
→ benchmark、恢复与不可变交付
```

M1.5 不以“支持数组”或“能生成多个对象”为完成标准，而以多节点网络能够通过规则编译、确定性求解、负向门禁、双宿主表达、开放 BIM 交付和 benchmark 验收为完成标准。

## 2. 不变边界

1. LLM 只能理解需求、补齐受控语义槽位和选择经批准的方案；不得直接计算最终坐标、管底标高、水力结果或规范限值。
2. Solver 只消费显式工程事实和具备生产执行资格的规则；事实、规则或上下文不足时输出 `UNKNOWN` 或失败关闭，不猜测。
3. `CompiledUtilityIR`、Solver 输入、宿主计划和交付工件必须版本化、可 Schema 校验、canonical 序列化并可计算 SHA-256。
4. typed 宿主主链不得回退 `execute_code`、`execute_vs_code` 或自由脚本作为正式证据。
5. 工程语义与表现层分离；双宿主比较只允许协议声明的表现差异。
6. 真实宿主写入仍只允许已批准范围；当前 Vectorworks G6 债务只能由后续真实 GUI 工件关闭。
7. `UNKNOWN` 不得包装为 `PASS`；模拟器、离线 E2E 与真实宿主证据必须明确区分。
8. 每个 Gate 形成边界清晰的本地提交，不推送远端，不混入接管前受保护的未提交内容。

## 3. 范围

### 3.1 包含

- 多井、多管段、分支、汇流和多个互相独立的市政系统。
- 网络引用闭合、连通性、流向、端口占用、无环条件和 canonical identity。
- 已知节点平面坐标条件下的确定性重力标高传播、连续坡度和覆土约束。
- 后续独立的路线候选、平面避障、走廊约束和冲突重算协议。
- 与几何 Solver 分层的水力输入、设计/校核工况、结果和证据协议。
- 水平/垂直净距、道路、轨道、河道、构筑物及减距例外审批扩展。
- typed Blender/Vectorworks 多对象表达、统一语义快照及 IFC/IDS 映射扩展。
- 正常、边界、冲突、证据缺失、规则歧义和中大型网络 benchmark。

### 3.2 首批切片 T1–T3 的明确限制

- 只处理已知节点 XY 和地面标高，不执行通用路线寻优。
- 只处理 DN300 混凝土重力污水管，并沿 `start_port → end_port` 方向下降。
- 网络必须是单个系统内的有向无环图；允许一对多支路和多对一汇流。
- 每个管段使用显式设计坡度；节点连接处各端口可有独立管底标高。
- 首批不宣称完成流量分配、管径优化、满流/非满流计算或泵压系统求解。
- 碰撞上下文缺失或规则未获生产资格时继续保持 `UNKNOWN`。

### 3.3 非目标

- 自动修改或写入真实 Vectorworks/Blender 工程。
- 用 M1.5 工件替代 M1 G6/G7 真实宿主验收。
- 云部署、多人远程控制面、正式 Web 产品或第三宿主主链。
- 未经证据核验直接扩大规范覆盖或把经验值写成 production 规则。
- 对 Subagent Runtime 做与当前领域 Gate 无关的重构。
- 宣称生产级全专业综合管网设计平台。

## 4. 协议与版本策略

### 4.1 `CompiledUtilityIR`

- 保持 `CompiledUtilityIR v1` 作为宿主无关编译边界。
- 现有数组结构已能表达多个 system、node、port 和 segment；T1 优先补足网络级语义门禁，不为相同数据形状创建平行 IR。
- 引用闭合、端口唯一占用、系统一致、几何/坡度一致、重力无逆坡、系统内连通和重力无环属于 v1 已声明语义的失败关闭修复。
- 只有增加消费者必须理解的新字段或改变合法对象语义时才升级 minor；删除/重命名字段或改变单位时才考虑 major。

### 4.2 Solver 输入

- 保留 `StraightGravitySolverInput v0.4` 和 `solve_straight_gravity_utility()`，确保 M1 单段案例与现有 pipeline 不回归。
- 多段网络使用独立的版本化输入和独立 Schema，避免把 `start/end` 单段接口悄然改义。
- 网络输入必须显式提供节点、管段端点、系统、坡度、管径、材质、地表上下文和可选锚定管底标高；禁止携带自由公式或可执行代码。
- 同一 canonical 输入和同一 RuleSet 必须生成同一 canonical IR 与 SHA-256。

### 4.3 宿主与交付

- Blender/Vectorworks typed plan 继续消费同一 `CompiledUtilityIR`，不在宿主侧重新求解拓扑或标高。
- `SemanticSnapshot.source_ir_path`、IFC/IDS 和 Artifact Manifest 如需扩展，必须另设 Gate 并保持旧单段工件可读。

## 5. Gate 顺序

### T0：现状审计与契约

完成标准：

- 审计 IR、Solver、Schema、Builder、Snapshot、IFC/IDS 和现有测试。
- 明确现有多对象承载能力与单段限制位置。
- 建立本执行契约、任务拆分和 Git 保护边界。

### T1：多节点拓扑契约

实施内容：

- 系统、节点、端口和管段引用闭合。
- 每个已声明系统必须拥有参与网络的节点和管段。
- 拒绝孤立节点、断开的系统内子图、端口重复占用和跨系统连接。
- 重力系统拒绝有向环路和逆坡。
- 三度及以上分支/汇流节点必须显式使用 `junction`，且具有兼容的 inlet/outlet 端口。
- 多对象 canonical JSON 与输入集合顺序无关。

最低负向矩阵：

- 未知 system/port/evidence subject。
- 重复 system/node/port/segment/evidence ID。
- 同 node 自环、跨 system 连接、端口方向错误、端口复用。
- 孤立节点、系统内断网、有向环路。
- 分支/汇流使用非 junction 节点或 junction 实际度数不足。
- centerline、长度、管底标高、坡度不一致和重力逆坡。

完成标准：Pydantic 语义门禁、JSON Schema Gate、正负测试和 canonical 测试通过，旧单段 fixture 不回归。

### T2：确定性多段重力网络 Solver

实施内容：

- 定义独立网络 Solver 输入模型与 JSON Schema。
- 对输入节点/边执行 ID、引用、系统、DAG 和连通性门禁。
- 按稳定拓扑序传播节点端口管底标高。
- 支持单源分支、多源汇流和固定上游锚点。
- 逐管段生成坡度、覆土、井距和当前可执行净距证据。
- 约束冲突时返回可定位的确定性错误，不静默调坡或改坐标。

完成标准：同义乱序输入输出 hash 一致；支路/汇流正向案例通过；环路、断网、冲突锚点、覆土冲突、规则缺失等负向案例失败关闭或保持 UNKNOWN。

### T3：typed 双宿主离线集成

实施内容：

- 用同一多节点 IR 生成 Blender/Vectorworks typed plan。
- fake/offline executor 创建全部稳定对象并生成统一快照。
- 严格比较拓扑、坐标、标高、坡度、尺寸、分类和领域属性。
- 扩展多节点 IFC/IDS 与 RuleEvidence 交付。

完成标准：离线双宿主 E2E、语义差异注入、IFC/IDS 和 Manifest 验收通过。该 Gate 不等于真实宿主 PASS。

### T4：路线与复杂标高（OFFLINE PASS）

- `GridRouteSolverInput v0.1` / `GridRouteSolverResult v0.1` 已版本化并通过 JSON Schema Gate。
- 路线只在显式批准的四邻接规则网格中搜索；地表高程逐 cell 给出，障碍物仅使用 SHA-256 绑定且获 production 资格的净距规则膨胀。
- 候选按水平长度、转折数和完整 cell 序列稳定排序，允许人工从未篡改的候选集中选择；适配器会确定性重算并拒绝候选篡改。
- 连续坡度按累计路线长度传播，逐 cell 检查覆土；走廊断裂、障碍封堵和覆土冲突返回结构化无解原因。
- 搜索预算耗尽返回 `UNKNOWN / search_limit_exceeded`，不得包装为已证明无可行路线。
- 路线接入网络 Solver 前校验 source IR、CRS、管径、材质、坡度、表面上下文、端点 XY/地面高程和起点锚点一致；终点锚点首版明确拒绝。
- 当前规模门禁：批准走廊最多 10,000 cells，搜索最多 250,000 expansions，最多返回 10 个候选。

### T5：水力 Solver（OFFLINE PASS）

- `HydraulicSolverInput v0.1` / `HydraulicSolverResult v0.1` 已版本化并通过 JSON Schema Gate。
- 输入绑定不可变 `CompiledUtilityIR` canonical SHA-256，逐段显式提供流量、Manning `n`、来源和 source reference；必须同时包含 design/check 工况。
- 使用 `manning_uniform_open_channel_si` 计算满流能力、容量裕量、圆管部分充满度、水力半径和流速；所有内部节点必须满足流量守恒。
- 结果不修改几何，`geometry_mutated=false`；路线→网络→水力组合 E2E 证明计算前后 IR hash 不变。
- 物理容量证据可形成 PASS/FAIL；`MU-DRAIN-007` 缺少结构化 production verification，最小流速与总体规范合规在容量通过时保持 `UNKNOWN / REVIEW_REQUIRED`。
- 水力 `Domain Gate` / `RuleEvidence` 独立关联结果 hash 与源 IR，不写回 `CompiledUtilityIR.evidence`，避免形成隐式几何—水力循环。

### T6：规范、例外和宿主表达扩展

- 可信规则覆盖、证据漂移检测和减距例外审批。
- Vectorworks/Blender/IFC 表达扩展及表现层隔离。

### T7：领域 benchmark 与总验收

- 小型、中型和大规模网络案例。
- 正常、边界、冲突、证据缺失和规则歧义。
- 正确性、确定性、恢复性、性能和宿主一致性指标。
- 验收报告、文档同步和本地边界提交。

## 6. Benchmark 最低集合

| 编号 | 类型 | 最低内容 | 预期 |
|---|---|---|---|
| B1 | 正常 | 3 井 2 段串联 | PASS 或仅非必需项 UNKNOWN |
| B2 | 分支 | 1 入 2 出 junction | 拓扑和标高确定性通过 |
| B3 | 汇流 | 2 入 1 出 junction | 拓扑和标高确定性通过 |
| B4 | 冲突 | 固定锚点导致覆土或连续标高不可满足 | FAIL CLOSED |
| B5 | 断网 | 同 system 两个不连通子图 | REJECT |
| B6 | 环路 | 重力有向环 | REJECT |
| B7 | 证据缺失 | collision/hydraulic context 缺失 | UNKNOWN，不得 PASS |
| B8 | 规则歧义 | 多条规则无法唯一选择 | UNKNOWN / REVIEW REQUIRED |
| B9 | 中型 | 至少 25 节点、24 段且含分支/汇流 | 正确性和稳定 hash 通过 |
| B10 | 较大 | 至少 100 节点 | 有明确性能基线，不设伪造阈值 |

实际性能数字只能由测试运行产生并写入验收报告，不在契约中预填。

## 7. 权限与连续执行

可自主执行：

- 修改 `src/`、`tests/`、`schemas/`、`domain_packs/` 和项目文档。
- 新建 M1.5 所需模型、Schema、fixture、离线 benchmark 和验收报告。
- 运行测试、Ruff、compileall、Schema Gate、离线 E2E 和 Git diff 检查。
- 在既定边界内修复回归、抽取内部函数和维护向后兼容接口。
- Gate 通过后创建本地提交，不推送。

必须先审批：

- 写入真实宿主工程或扩大 `D:\devloop\G6_Test` 之外的宿主范围。
- 改变核心权限模型、降低 Schema/证据/安全标准或放宽 `UNKNOWN`。
- 使用凭据、付费服务、非公开外部系统、系统级安装、发布或推送远端。
- 删除/覆盖重要项目文件、重写 Git 历史或混入接管前受保护内容。

## 8. 暂停条件

仅在以下情况暂停并向 JY 请求决策：

1. 需要改变本契约的目标、协议语义或完成标准。
2. 需要真实宿主、外部凭据、付费调用或扩大文件写入授权。
3. 规则证据不能支持 production 执行且继续会把 `UNKNOWN` 包装为确定结论。
4. 继续执行存在不可逆数据风险、会覆盖受保护工作或降低既定门禁。
5. 出现两个均合理但产品语义不同、无法由现有架构裁决的方案。

普通测试失败、局部重构和可逆实现选择不构成暂停理由。

## 9. Git、证据与成熟度纪律

- 接管前修改和未跟踪文件保持隔离；提交前逐文件审计 staged diff。
- 每个 Gate 只提交该 Gate 的源码、Schema、测试和必要文档。
- 不提交缓存、临时宿主文件、凭据或可再生运行垃圾。
- 每份验收结论必须列出真实命令结果、测试数量、已知限制和提交号。
- T1/T2/T3 通过只能表述为对应能力切片通过；M1.5 只有 T1–T7 和完成定义全部满足后才能关闭。
- 在 Vectorworks G6 债务回补前，M1 仍保持 `DEFERRED / IN PROGRESS`，G7 保持最终阻塞。

## 10. 当前启动点

从 **T6：规范、例外和宿主表达扩展** 继续：先为 `MU-DRAIN-007` 等水力规则补齐标准状态、官方副本 SHA-256、条款定位、适用性和 production verification 协议，再扩展水平/垂直净距、道路、轨道、河道、构筑物及减距例外审批。T5 水力结果继续保持独立证据，不得隐式改写几何 IR。