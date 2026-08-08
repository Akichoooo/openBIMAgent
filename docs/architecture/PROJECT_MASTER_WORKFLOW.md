# openBIMAgent 项目整体任务流程

版本：v1.0  
更新时间：2026-08-03  
状态：**ACTIVE ROADMAP**  
配套进度文档：[`PROJECT_HANDOFF_STATUS.md`](PROJECT_HANDOFF_STATUS.md)  
文档治理：[`DOCUMENTATION_GOVERNANCE.md`](DOCUMENTATION_GOVERNANCE.md)

> 本文档定义项目从需求、规则、IR、双宿主建模、开放 BIM 交付，到产品化服务、评测和学术交付的整体任务流。它回答“接下来做什么、先后依赖是什么、每阶段如何验收”。实时进度、测试数字和恢复位置不在此重复维护，统一查看 `PROJECT_HANDOFF_STATUS.md`。

## 1. 最终目标

建立一条确定性、可审计、可恢复、可扩展到多领域的生成式 BIM 主链：

```text
需求与工程事实
→ 领域语义澄清
→ 可信规范与规则编译
→ Solver 确定性求解
→ 版本化 Compiled Domain IR
→ typed Host Execution Plan
→ Blender / Vectorworks / 可选第三宿主
→ 宿主语义反投影与一致性验证
→ IFC / IDS / RuleEvidence / Domain Gate
→ 不可变 Artifact Manifest
→ 审批、trace、失败恢复与可追溯交付
→ CLI / TUI / API / Web 产品入口
→ 标准 benchmark、实验与论文交付
```

### 不变原则

1. LLM 负责需求理解、规划和受控决策；Solver 负责确定性坐标与工程计算。
2. 宿主只消费版本化 IR/Execution Plan，不重新解释自然语言。
3. typed 主链不得回退任意脚本作为正式证据。
4. 能机器校验的工程规则不得交给 VLM 主观裁决。
5. `UNKNOWN` 不得被包装成 `PASS`。
6. 所有副作用必须具备审批、范围锁、幂等键、receipt 和恢复事实。
7. 工件不可变、可 hash、可审计；deliver 只接 accepted 产物。
8. 真实宿主结果、模拟器结果和外部阻塞必须明确区分。
9. 每个阶段以可复核门禁为完成标准，不以“代码写完”作为完成标准。
10. 本地边界提交，不自动推送远端。

## 2. 项目层级与口径

### 2.1 产品工程路线

```text
M0 原型
→ M1 工程化双宿主
→ M1.5 市政领域深化
→ M2 产品化服务与客户端
→ M3 评测、基准与学术输出
```

### 2.2 当前专项路线

M1 内部使用 G1–G7 受控执行契约：

```text
G1 typed Vectorworks Builder
→ G2 immutable delivery
→ G3 cross-host semantics
→ G4 IFC/IDS
→ G5 recovery safety
→ G6 real hosts
→ G7 controlled beta acceptance
```

当前专项提前实现了部分原本归入 M1.5 的市政能力，因此“里程碑名称”与“代码完成顺序”存在交叉。验收时按实际能力和证据判断，不按名称重复建设。

### 2.3 学术路线

学术材料中的 M2/M3 是更高层概括：

- 学术 M2：系统完全体与实验完成。
- 学术 M3：论文投递与毕业答辩准备。

它们与产品 M2/M3 不完全同义，项目管理时必须分别追踪。

## 3. 全局任务生命周期

每个用户任务或 benchmark 按以下主流程执行：

### P0：需求与授权边界

输入：用户需求、工程事实、目标宿主、交付格式、授权范围。

动作：

- Clarify 检查 slots，缺失则逐项追问。
- 固化工作区、目标文件、真实宿主写入范围和审批要求。
- 识别是否需要外部凭据、付费服务、系统修改或破坏性操作。

门禁：需求完整度达到阈值；权限边界明确；缺失事实不得猜测。

### P1：领域规则与证据

输入：受信任规范源和工程事实。

动作：

- 编译版本化 RuleSet。
- 核验标准身份、状态、条款、表号、来源 hash、适用条件和复核证据。
- 将规则选择结果分类为 selected、review required、unsupported 或 ambiguous。

门禁：只有完整核验的 selected production 规则可进入确定性 PASS/FAIL。

### P2：Solver 与 Compiled IR

动作：

- Solver 仅消费工程事实和受信任 RuleSet。
- 生成确定性几何、拓扑、尺寸、标高和 RuleEvidence。
- 编译严格版本化的 Compiled Domain IR。
- 生成 canonical JSON 与 SHA-256。

门禁：Schema、引用闭合、单位、数值一致性、Domain Gate 全部满足；UNKNOWN 失败关闭。

### P3：typed 宿主计划

动作：

- 为每个宿主生成 typed Execution Plan。
- 计划携带协议版本、source IR hash、stable ID、幂等键和 capability 要求。
- operation 必须来自 allowlist。

门禁：版本、Schema、引用、单位、能力、范围和计划身份校验通过。

### P4：宿主执行

动作：

- 先 fake/offline executor，再真实宿主。
- 每个操作返回 receipt。
- 执行前写 checkpoint/sidecar；部分成功后只补执行未确认操作。
- 保存到授权目标，不覆盖无关文件。

门禁：所有写操作在审批和范围锁内；完成态必须有真实工件和 completed receipt。

### P5：语义反投影与双宿主比较

动作：

- 从实际宿主对象反投影统一 `SemanticSnapshot`。
- 对 stable ID、坐标、尺寸、坡度、拓扑、分类和领域属性严格比较。

门禁：只允许协议声明的宿主表现差异；任何工程语义差异必须定位并失败。

### P6：开放 BIM 与规则交付

动作：

- 从可信快照生成 IFC。
- 生成 IDS 和确定性关系规则。
- 生成验证报告、RuleEvidence 和 Domain Gate 报告。

门禁：IFC 可重开；IDS 过官方 Schema；必要实体、属性和关系全部通过。

### P7：不可变交付

动作：

- 登记 Execution Plan、模型、快照、比较报告、IFC、IDS、Evidence、日志和截图。
- 为每项工件记录路径、媒体类型、大小、SHA-256、生成者和 attempt lineage。

门禁：路径受控、hash 可重算、工件齐全、Gate 状态允许；同键异义冲突。

### P8：恢复、审计与人审签

动作：

- 保存 Session JSONL、审批、receipt、checkpoint、resume、steer 和失败终态。
- 重启后只恢复持久事实，不自动重放副作用。
- 人审签最终交付清单。

门禁：失败和恢复路径可复核；没有重复建模、重复交付或历史覆盖。

## 4. M0：基础原型

### 目标

形成可运行的最小 Agent Core 和单资产 E2E。

### 主要任务

- 极简 Agent Loop 与受控工具集。
- Session JSONL、树状历史、HITL、abort、queue、回退和 checkpoint。
- Schema Gate。
- Provider 抽象与模型 profile。
- Domain Pack 基座和模板族。
- OpenSCAD 结构快检。
- Blender MCP fork 基础安全改造。
- 单资产建模和 HTML 验收页。

### 当前处理方式

M0 已形成基础，不再单独重做。后续发现缺陷时只做与当前阶段相关的边界修复。

### 完成定义

单资产需求能经过 clarify、plan、Schema、建模、检查、交付和 trace 完成离线 E2E。

## 5. M1：工程化与双宿主受控 Beta

### 目标

让同一份可信 IR 在 Blender 和 Vectorworks 中确定性执行、验证、恢复和交付。

### 已完成压缩

- typed Vectorworks plan。
- 不可变 Artifact Manifest。
- 双宿主统一语义协议和离线比较。
- IFC4X3 / IDS 1.0 / RuleEvidence。
- 审批、checkpoint、resume 和副作用安全。
- Blender/Vectorworks typed host adapter。
- Blender 真实 G6 执行。

实时提交、测试和宿主工件见 `PROJECT_HANDOFF_STATUS.md`。

### 当前剩余

1. Vectorworks 2024 GUI 真实 typed 执行。
2. Vectorworks 幂等重放。
3. 真实双宿主严格语义比较。
4. 更新 G6 报告。
5. G7 全量质量、E2E、文档和最终交付验收。

### M1 完成定义

- 同一版本化 IR 驱动两个真实宿主，或形成责任边界明确的外部阻塞结论。
- 正式市政案例具备模型、快照、IFC、IDS、RuleEvidence、Manifest 和审计记录。
- 中断、拒绝、超时和重启可恢复且不重复副作用。
- 全量自动化与文档门禁满足。
- 状态最多标记为“受控 Beta 候选”，不得夸大为生产级平台。

## 6. M1.5：市政领域深化

### 目标

把当前“两井一直管”的受控 benchmark 扩展为可用于真实方案辅助和毕业设计实验的市政管网能力。

### 工作包 A：网络拓扑扩展

- 多井、多管段、支路和汇流。
- 节点与端口类型扩展。
- 网络连通性、方向性和闭合检查。
- 多系统和系统间关系。

### 工作包 B：路线与标高求解

- 路线候选生成。
- 平面避障和走廊约束。
- 纵断面与复杂标高协调。
- 重力流连续坡度和覆土约束。
- 冲突后的可解释重算。

### 工作包 C：水力 Solver

- 流量、流速、充满度或压力相关输入协议。
- 设计工况与校核工况。
- 水力规则证据和 UNKNOWN 边界。
- 与几何 Solver 分层，禁止 LLM 直接计算结果。

### 工作包 D：规范覆盖扩展

- 完整水平和垂直净距规则。
- 构筑物、道路、轨道、河道和其他市政设施。
- 安全措施减距例外审批协议。
- 规范版本升级和证据漂移检测。

### 工作包 E：宿主构件与表达

- 更丰富 Vectorworks BIM 构件。
- Blender 剖切、漫游和施工表达。
- IFC 实体映射扩展。
- 表现层材质与工程语义继续分离。

### 工作包 F：领域 benchmark

- 正常、边界、冲突、证据缺失、规则歧义案例。
- 小型、中型和大规模网络。
- 正确性、恢复性、性能和宿主一致性指标。

### 完成定义

至少一组多节点市政网络能完成规则编译、求解、双宿主生成、IFC/IDS、失败恢复和 benchmark 评测；不得只靠单段演示宣称生产级市政能力。

## 7. M2：产品化服务与客户端

M2 启动前应先建立独立执行契约，不能直接沿用 M1 宿主验收契约。

### 工作包 A：FastAPI server

- 明确本地 Runtime 与远程 server 的信任边界。
- 定义会话、任务、审批、工件、事件和控制 API。
- FastAPI 实现与 OpenAPI 契约。
- 认证、授权、速率限制、请求大小和错误协议。
- 多租户或单租户模式必须显式选择。

### 工作包 B：SSE 事件流

- 采用版本化 `data-*` parts。
- 工具结果分离 LLM 视图与 UI 视图。
- 支持断线重连、游标、幂等消费和终态确认。
- 事件不得泄露 token、原始敏感参数或内部凭据。

### 工作包 C：CLI/TUI

- Textual/Rich 或等价技术。
- Codex CLI 风格交互。
- 会话侧边栏、命名、切换和搜索。
- `/compact`、`/tree`、`/fork`、`/undo`、`/redo`、`/retry`、`/sessions`、`/model`、`/diff`、`/export`。
- 审批、进度、工件和错误的结构化展示。

### 工作包 D：远程 Playbook

- `--playbook <url>` 获取协议。
- 内容 hash、签名或可信源策略。
- 缓存、版本锁、离线恢复和供应链安全。
- 远程内容不能提高本地 capability ceiling。

### 工作包 E：Web UI 技术验证

- 评估 AI SDK v6、assistant-ui、ai-elements。
- 会话、事件流、审批、工件预览和双宿主状态。
- 与 TUI 共用协议，不复制业务逻辑。
- Operator Console 仅是本机管理界面，不得直接当远程 Web 产品发布。

### 工作包 F：Bonsai 与第三宿主评估

- Blender Bonsai IFC 路径可行性、语义完整性和版本兼容。
- 可选 build123d 作为精确 CAD 第三环。
- 评估必须有 benchmark，不因“能生成文件”就进入主链。

### M2 完成定义

- API、SSE、TUI 和至少一个 Web 技术验证共享同一版本化协议。
- 审批、权限、会话恢复和工件交付在客户端断线及服务重启后保持一致。
- 安全测试、并发测试、契约测试和 E2E 通过。
- 本地 Operator Console 与远程产品控制面边界清楚。

## 8. M3：评测、基准与学术输出

### 工作包 A：trace 评测导出

- 从 Session JSONL、Artifact Manifest 和 receipts 导出标准评测数据。
- 记录任务、attempt、工具调用、审批、错误、恢复、token 和时延。
- 匿名化与敏感字段清理。

### 工作包 B：BIMBench 对接

- 映射任务格式、工件格式和评价指标。
- 建立可重复运行的适配器。
- 区分工程正确性、宿主一致性、视觉质量和过程安全。

### 工作包 C：系统实验

- 多模型对比。
- 单/多代理对比。
- typed plan 与自由脚本边界对比。
- 有/无规则编译、有/无恢复机制、有/无双宿主比较的消融实验。
- 成功率、语义准确率、重复副作用率、恢复率、耗时和成本。

### 工作包 D：论文与答辩

- 研究问题和贡献边界。
- 方法、系统架构和协议描述。
- benchmark、基线、消融和误差分析。
- 威胁有效性与局限性。
- 论文投递、答辩 PPT、演示案例和复现实验包。

### M3 完成定义

实验结果可从版本化数据和工件复算；论文结论不超出证据范围；演示案例和统计均可独立复核。

## 9. 阶段启动模板

每个新阶段必须先完成：

1. 读取总体架构、组件、交接状态和最近 memory。
2. 审计 Git、代码、测试、Schema、文档和已有工件。
3. 建立该阶段执行契约：目标、范围、非目标、门禁、授权和停止条件。
4. 将阶段拆成设计、契约/Schema、负向测试、实现、集成、E2E、文档、验收和提交任务。
5. 先建立失败关闭边界和负向测试，再实现主路径。
6. 每个门禁通过后创建边界提交，不推送。
7. 更新交接进度文档。

## 10. 通用门禁矩阵

每个功能原则上应覆盖：

| 维度 | 最低要求 |
|---|---|
| 协议 | 有版本、Schema、canonical identity |
| 正向测试 | 主路径和重复执行通过 |
| 负向测试 | 缺字段、未知版本、越权、篡改、路径攻击失败 |
| 幂等 | 同键同义复用，同键异义冲突 |
| 恢复 | 部分成功和重启后不重复副作用 |
| 权限 | capability ceiling、审批和范围锁不可绕过 |
| 审计 | attempt、ActorRef、receipt、hash 和终态可追溯 |
| 工件 | 存在、可打开、hash 可复算、不覆盖历史 |
| 文档 | 架构、使用说明、状态与实现一致 |
| Git | diff 边界清楚、无凭据、无临时工件、不推送 |

## 11. 文档与状态维护机制

### `PROJECT_HANDOFF_STATUS.md`

维护实时事实：

- 当前门禁和下一动作。
- HEAD、关键提交。
- 最新测试基线。
- 授权和阻塞。
- 真实宿主工件及 hash。
- 新会话接管提示词。

更新触发：门禁、提交、测试、授权、阻塞、工件或下一动作变化。

### `PROJECT_MASTER_WORKFLOW.md`

维护稳定路线：

- 里程碑目标和先后依赖。
- 工作包和完成定义。
- 通用生命周期和门禁矩阵。
- 路线优先级与范围变化。

更新触发：产品路线、阶段范围、协议边界、完成标准或优先级变化。不要在这里堆积每次测试数字。

### `.workbuddy/memory/YYYY-MM-DD.md`

追加当日有长期恢复价值的工作事实；不代替交接文档或验收报告。

### 验收报告

每个阶段结束后在 `outputs/` 形成独立报告，保留实现范围、实测证据、已知风险和成熟度结论。

## 12. 当前推荐执行顺序

```text
1. 完成 G6 Vectorworks GUI 真机执行
2. 验证 Vectorworks receipt、工件和幂等重放
3. 完成真实 Blender / Vectorworks 语义比较
4. 更新 G6 报告并关闭 G6
5. 完成 G7 全量验收与文档收口
6. 建立 M1.5 扩展执行契约和 benchmark 规划
7. 优先扩展多节点拓扑、复杂标高和净距规则
8. 再建设水力 Solver 与大规模领域 benchmark
9. 建立 M2 独立执行契约，先协议与 server，再 TUI/Web
10. 建设 M3 trace 导出、BIMBench、实验与论文交付
```

## 13. 明确非目标与防止范围漂移

在 M1/G7 完成前，不得把以下任务混入当前门禁：

- 通用路线寻优。
- 完整水力计算。
- 新建筑领域包。
- 云部署、多人远程控制面。
- 正式 Web 产品。
- 第三宿主主链。
- 大规模论文实验。

只有当前门禁的安全、正确性或可恢复性确实依赖时，才允许做最小关联修复，并在交接状态中记录原因。
