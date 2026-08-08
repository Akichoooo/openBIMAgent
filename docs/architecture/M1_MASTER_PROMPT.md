# M1 总控长任务提示词

版本：v1.0 · 2026-08-02

> 状态：**SUPERSEDED / 历史原始总控提示词**。本文件保留 M1 G1–G7 的完整原始契约提示；当前 G1–G5 已完成，G6 权限与恢复位置已变化。新会话必须优先复制 `PROJECT_HANDOFF_STATUS.md` 中的最新接管提示词，不得直接按本文从 G1 重跑。
>
> 原用途：将下方“可复制提示词”完整粘贴到一个新的 WorkBuddy 会话。新会话应从当前仓库实际状态接管，连续执行 G1–G7；不要重做已经通过门禁并提交的阶段。真实宿主操作在 G6 前必须暂停并获得 JY 明确批准。

## 使用方法

1. 在 WorkBuddy 中打开工作区：
   `D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`
2. 切换到 Craft 模式。
3. 新建会话，将下方代码块全文粘贴并发送。
4. 后续无需反复回复“继续”。执行者应在权限范围内持续推进，仅在阶段门禁或实质阻塞时汇报。
5. 如果会话因网络或上下文限制中断，新建会话后再次粘贴同一提示词。执行者必须通过 Git、执行契约、测试和项目记录判断第一个未完成门禁，从该处恢复，禁止从 G1 盲目重做。

---

## 可复制提示词

```text
你现在接管 openBIMAgent 的 M1 长任务，请直接进入 Craft 执行模式，并持续推进，直到：

1. G1–G5 全部通过；
2. 到达 G6 真实宿主审批门并等待 JY 授权；或
3. 遇到执行契约定义的实质阻塞。

获得 G6 授权后，继续完成 G6–G7。不要在普通实现步骤后询问“是否继续”，不要只输出计划而不实施。

【工作区】
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent

【总目标】
把项目从“工程 Alpha”推进到“受控 Beta 候选”，建立以下可复核闭环：

市政需求与工程事实
→ MunicipalRuleSet / Solver
→ 版本化 CompiledUtilityIR
→ BlenderBuilder + VectorworksBuilder
→ 双宿主语义一致性验证
→ IFC/IDS + RuleEvidence
→ Artifact Manifest + 审计记录 + 可恢复执行状态

M1 不以“写完代码”为完成标准，而以至少一个正式市政案例能够确定性生成、校验、恢复、审计和交付为完成标准。

【第一原则：先接管实际状态，不得重做】
开始时必须：

1. 阅读并遵循：
   - docs/architecture/M1_EXECUTION_CONTRACT.md
   - docs/architecture/ARCHITECTURE.md
   - docs/architecture/COMPONENTS.md
   - README.md
   - docs/README.md
   - domain_packs/municipal_utility/knowledge/constraints.yaml
2. 查看 Git status、最近本地提交和当前 diff，保护已有未提交工作；不得擅自丢弃、覆盖或回滚他人改动。
3. 检查 .workbuddy/memory/ 中最近的项目记录，但不得删除 .workbuddy/。
4. 搜索 G1–G7 的实现、测试、验收报告和提交证据，判断每个门禁为：NOT_STARTED、IN_PROGRESS、PASSED、BLOCKED。
5. 已有本地提交且门禁证据完整的阶段视为已完成，不重写、不重跑无必要的旧任务；从第一个未完成门禁继续。
6. 只有当旧门禁是当前改动的依赖，或全量回归验收需要时，才重跑相关测试。
7. 建立任务列表跟踪当前门禁的设计、实现、测试、文档、验收和本地提交；每项完成后立即更新状态。

历史参考基线不是当前事实，只能用于对照：2026-08-01 曾达到 489 passed、3 skipped，Ruff/compileall/Markdown 链接/git diff --check 通过；本地提交包括 bcad777 和 466afba，未推送。你必须以当前仓库实测结果为准，不得伪造或沿用旧输出。

【持续执行授权】
你可以自主执行：

- 阅读、检索和分析项目文件及公开技术资料；
- 修改 src/、tests/、schemas/、domain_packs/ 中与当前门禁直接相关的代码，以及项目文档；
- 新建 M1 必需的源码、Schema、测试、fixture、模拟器和正式交付工件；
- 运行测试、静态检查、编译检查、Schema 校验、离线 E2E 和本地模拟执行；
- 诊断并修复当前阶段缺陷和关联回归；
- 进行不改变产品边界的内部接口调整、测试重构和文档同步；
- 添加确有必要的项目级依赖并更新 pyproject.toml/uv.lock，但不得全局或系统级安装；
- 每个 G1–G5、G7 门禁验收通过后创建边界清晰的本地 Git 提交，不推送。

以下事项必须先停下并征得 JY 明确批准：

- 改变既定核心架构、协议语义、权限模型或 M1 完成标准；
- 降低测试、RuleEvidence、Domain Gate、Schema、幂等性、安全或审计标准；
- 扩大 M1 范围到路线寻优、自动避障、水力计算、新领域包或 Web 产品化；
- 启动、连接或写入真实 Blender/Vectorworks 工程；
- 使用真实凭据、非公开外部服务或付费模型/API；
- 推送远端、创建 PR、发布、部署或对外发送内容；
- 删除、覆盖、迁移重要文件，修改 `mcp_servers/` 宿主集成代码，安装系统软件，修改系统配置，或执行不可逆操作。

始终禁止：

- 绕过测试、审批、Schema、Domain Gate 或安全边界；
- 自动确认真实宿主中的不可逆操作；
- 静默重放可能产生副作用的旧工具调用；
- 将密钥、token、个人数据写入仓库、日志或交付工件；
- 强推、重写 Git 历史、擅自发布；
- 删除 .workbuddy/；
- 为让测试通过而弱化断言、篡改证据或把 UNKNOWN 强行改成 PASS。

【产品边界】
必须保持：

- LLM 负责需求理解、规划和受控决策；宿主建模主链必须消费显式、版本化、可 Schema 校验的 IR/Execution Plan。
- Vectorworks/Blender 宿主不得重新解释自然语言。
- 调用方不能提高角色 capability ceiling；子代理禁止嵌套。
- 重启后不得自动重放具有副作用的历史工具调用。
- RuleEvidence 必须可追溯；只有 SELECTED 规则可形成 PASS/FAIL，REVIEW_REQUIRED、UNSUPPORTED、AMBIGUOUS 均保持 UNKNOWN。
- 市政水平净距采用 XY 平面实体表面距离；Z 高差不能掩盖平面净距不足。
- production 规则必须满足现有完整核验证据门禁，不能仅修改 confidence 晋级。
- 真实执行必须具备审批、范围锁、稳定 ActorRef、attempt 谱系和幂等语义。

【G1：Vectorworks 确定性 Builder】
目标：实现 CompiledUtilityIR 到版本化 Vectorworks Execution Plan 的确定性映射，并先用 fake/mock executor 闭环。

实施要求：

1. 先审计现有 assembly pipeline、Vectorworks client/executor、utility IR、schemas 和测试，复用现有协议，不另造平行主链。
2. 显式约束对象类型、图层、分类、记录字段、单位、坐标、尺寸、拓扑和命名。
3. 每个领域对象具有稳定 ID；Execution Plan 具有 canonical hash、协议版本和幂等键。
4. 宿主命令只能来自 allowlist/typed operation，禁止自由自然语言和任意脚本进入确定性建模主链。
5. 执行前验证版本、字段、引用、坐标单位和能力；未知对象、未知操作、缺字段、越权操作失败关闭。
6. 支持部分成功记录、补偿/回滚信息和幂等重试；不得重复创建已确认对象。
7. 将 Builder 接入现有 Pipeline 的注入边界；缺 Builder 时继续失败关闭。
8. 补齐 Schema、单测、集成测试、负向测试和架构文档。

G1 门禁：

- 同一 IR 重复编译得到 canonical-equivalent Execution Plan；
- 缺字段、非法版本、未知对象类型和越权命令稳定失败；
- 重试不重复创建已确认成功对象；
- Pipeline 模拟执行闭环通过；
- 相关测试、Ruff、compileall、Schema 和文档检查通过；
- 更新 M1 状态并创建一个本地提交，不推送；
- 门禁通过后直接进入 G2，不等待“继续”。

【G2：Deliver 与 Artifact Manifest】
目标：完成通用 Loop 的 deliver 接线，建立不可变、可验证、可恢复的交付登记。

实施要求：

1. 审计 core/loop.py::_tool_deliver()、现有 artifacts/runtime manifest 和 Pipeline 输出，统一复用工件协议，避免第二套 Manifest。
2. Manifest 至少记录：artifact ID、相对/规范化路径、媒体类型、SHA-256、大小、生成者、来源 request/attempt/ActorRef、协议版本、创建时间、依赖关系和状态。
3. 统一登记宿主模型、Execution Plan、语义快照、IFC、IDS、规则报告、RuleEvidence 摘要、截图和审计日志。
4. Domain Gate 未通过、必要工件缺失、路径越界、hash 不匹配、Schema 不合法时，deliver 必须失败关闭。
5. deliver 使用稳定幂等键；同键同语义复用，同键不同语义冲突；历史不可变工件不得覆盖。
6. 部分完成、失败、取消和恢复状态必须持久化且可审计。
7. 补齐单测、集成测试、路径/篡改/重复提交负向测试、文档和示例。

G2 门禁：

- 离线案例生成可 Schema 校验的 Manifest；
- 所有登记工件存在且 hash 可复算；
- 重复 deliver 不产生重复副作用或覆盖历史；
- Gate/缺件/hash/路径攻击均被阻止；
- 相关测试和质量检查通过；
- 创建本地提交，不推送；通过后直接进入 G3。

【G3：双宿主语义一致性 E2E】
目标：证明同一 CompiledUtilityIR 在 Blender 与 Vectorworks 路径中保持领域语义一致。

实施要求：

1. 建立版本化、宿主无关的 Semantic Snapshot Schema。
2. 快照至少覆盖：稳定对象 ID、对象类型、拓扑、端点/坐标、尺寸、坡度/标高、分类、关键属性和来源 IR 引用。
3. 为 Blender 与 Vectorworks 模拟执行器输出同一快照协议；真实宿主内部句柄不得作为跨宿主身份。
4. 实现确定性比较器，区分必须一致字段、容差字段和明确允许差异字段。
5. 比较失败必须定位到对象、字段、期望值、实际值、容差和来源 IR。
6. 使用一个固定市政 benchmark case 做离线 E2E，并注入坐标、尺寸、拓扑、属性偏差验证失败检测。
7. 将一致性报告纳入 Domain Gate 和 Artifact Manifest。

G3 门禁：

- 基准案例双宿主语义一致性通过；
- 四类人为偏差均稳定失败并给出可定位报告；
- 输出具有 canonical hash 且可重复；
- 测试、Schema、文档和质量检查通过；
- 创建本地提交，不推送；通过后直接进入 G4。

【G4：IFC/IDS 最小交付切片】
目标：为当前市政案例形成最小但真实可验证的 IFC/IDS 交付闭环。

实施要求：

1. 先审计项目现有 IFC、Vectorworks、openBIMForge 可复用资产和 pyproject 依赖；优先使用标准库或成熟项目级依赖，不手写不完整的通用 IFC 引擎。
2. 明确 IFC 版本、实体映射、稳定标识、单位、分类、属性集和必要关系。
3. 从可信 IR 或 Semantic Snapshot 生成/导出 IFC；不得由自然语言临时决定语义。
4. 为当前案例建立 IDS 要求，覆盖必要分类、属性、标识和关系。
5. 生成机器可读验证报告，并将结果关联回 IR object ID 和交付要求。
6. IFC/IDS 未通过时 Domain Gate 不得进入交付成功态。
7. 增加删除属性、错误分类、断开关系等负向 fixture。

G4 门禁：

- 基准案例生成 IFC 与 IDS 并通过自动验证；
- 三类负向 fixture 稳定失败；
- 验证报告可回溯到 IR 对象；
- 工件登记和 Gate 接线完成；
- 测试、Schema、文档和质量检查通过；
- 创建本地提交，不推送；通过后直接进入 G5。

【G5：失败恢复与副作用安全】
目标：验证长链路在拒绝、超时、中断、部分成功和重启后可以恢复，且不重复副作用。

实施要求：

1. 覆盖宿主拒绝、超时、部分成功、进程中断、审批暂停、Runtime 重启和 deliver 中断。
2. 复用现有 RuntimeState、rehydrate、Approval Broker、ActorRef、request/attempt lineage 和 idempotency 机制。
3. 重启后只能根据持久化 receipt/状态决定继续动作；禁止静默重放旧工具调用。
4. 验证已确认对象、已登记不可变工件和已决审批不会重复创建或覆盖。
5. 每个场景必须有明确终态、恢复动作、审计事件和自动测试。
6. 建立 M1 离线恢复 E2E，包含至少一次中断后跨 Runtime 实例恢复。

G5 门禁：

- 所有规定失败场景均可审计且有可恢复终态；
- 无重复建模、重复 deliver、历史工件覆盖或审批漂移；
- 跨 Runtime 恢复自动测试通过；
- 全量离线 E2E、相关测试和质量检查通过；
- 创建本地提交，不推送；
- 然后到达 G6，必须暂停并向 JY 请求真实宿主授权。不得擅自启动或写入真实宿主。

【G6：真实宿主审批门】
未获得 JY 明确授权时，只能输出一份审批请求，不得操作真实 Blender/Vectorworks。

审批请求必须列出：

- 将启动的应用和版本；
- 将读取和写入的每个工程副本路径；
- 保证不会覆盖原文件的措施；
- 预计执行的宿主操作类型；
- 审批门、范围锁、回滚/恢复方式；
- 预期产物路径；
- 若 Vectorworks 不可用时的可归责阻塞判定方式。

获得授权后：

1. 先创建并核验工程副本，只在副本中写入。
2. 记录 Blender、Vectorworks、插件/MCP 版本和 describe_capabilities。
3. 使用同一 benchmark case 运行真实双宿主链路。
4. 生成真实 Semantic Snapshot，并与 IR、模拟快照对比。
5. 记录每次审批、命令、receipt、日志、工件 hash 和失败恢复过程。
6. 不得把真实工程原件、凭据或本机敏感路径提交到 Git。

G6 门禁：

- 至少一个真实市政案例完成双宿主验证；或得到证据充分、责任边界明确的外部阻塞结论；
- 模拟器未掩盖真实宿主 API 差异；
- 所有宿主写操作均有审批和范围锁；
- 真实产物、语义报告和审计证据齐全；
- 根据授权范围决定是否创建本地提交，绝不推送；
- 通过后直接进入 G7。

【G7：受控 Beta 候选总验收】
目标：形成可独立复核的最终工程结论，不夸大成熟度。

必须完成：

1. 运行完整 pytest、Ruff、compileall、Schema 校验、Markdown 相对链接检查和 git diff --check。
2. 运行正式市政 benchmark 的完整 E2E；区分模拟结果与真实宿主结果。
3. 核验一个正式交付清单至少包含：
   - 输入需求/工程事实；
   - MunicipalRuleSet 与 canonical hash；
   - CompiledUtilityIR；
   - Vectorworks/Blender Execution Plan 或执行记录；
   - 双宿主 Semantic Snapshot 和一致性报告；
   - IFC 与 IDS；
   - RuleEvidence 与 Domain Gate 结果；
   - Artifact Manifest；
   - Runtime 审计和失败恢复证据。
4. 更新 README.md、docs/README.md、ARCHITECTURE.md、COMPONENTS.md 和 M1_EXECUTION_CONTRACT.md 状态。
5. 在 outputs/ 生成一份 M1 完整实施与验收报告，明确：实现范围、测试数字、真实/模拟边界、剩余风险、未完成项和成熟度判断。
6. 仅在所有门禁满足后，将项目标记为“受控 Beta 候选”；若真实宿主受外部条件阻塞，应保持“工程 Alpha / Beta 候选待真机验证”，不得虚报完成。
7. 创建最终本地语义化提交，不推送。

【每个门禁的工作方法】
对于当前门禁，循环执行：

A. 审计现状和依赖边界；
B. 写出最小、明确、可验证的内部设计；
C. 先补契约/负向测试，再实施主路径；
D. 运行聚焦测试，修复到通过；
E. 运行相关回归和静态检查；
F. 同步 Schema、架构文档和使用说明；
G. 复核 Git diff，确认没有凭据、临时文件、越界修改和弱化断言；
H. 运行门禁验收；
I. 门禁通过后立即创建本地提交并记录 commit hash；
J. 自动进入下一门禁。

如果发现测试失败，不得只报告失败：先定位根因并在权限范围内修复。若失败来自当前改动，应修复；若是确认存在的历史缺陷且阻断当前门禁，也应做边界清晰的关联修复并在门禁报告说明。

【提交规则】

- 每个门禁一个边界清晰的本地提交；必要时可拆成“实现 + 验收文档”，但不要产生大量碎片提交。
- 提交前必须检查 git status、git diff、git diff --check，并运行该门禁要求的测试。
- 不得提交 .workbuddy/、.env、token、运行时缓存、真实工程原件或可再生临时目录。
- 不得 amend、force push 或跳过 hooks。
- 不推送远端。

【汇报规则】
采用“仅门禁与阻塞汇报”。门禁通过后的汇报必须简洁但自包含，至少给出：

- 门禁名称和 PASS/PARTIAL/BLOCKED；
- 关键实现与具体变更文件；
- 聚焦测试与完整验收的真实结果；
- 新增协议/Schema 版本；
- 本地 commit hash；
- 已知限制和下一门禁。

普通实现步骤不要等待 JY 回复。如果达到工具或会话上下文限制，应先完成以下恢复记录再结束：

1. 保证工作树处于可解释状态；
2. 将已完成工作、未完成项、当前测试结果和准确下一步追加到当日 .workbuddy/memory/YYYY-MM-DD.md；
3. 若当前门禁已通过则提交；未通过不得伪造完成或创建“完成”提交；
4. 最终回复明确写出“重新粘贴 M1_MASTER_PROMPT.md 后会从当前未完成门禁恢复”。

【最终行为要求】
现在立即开始：读取必读文件、检查 Git 与项目记录、判断 G1–G7 实际状态，从第一个未完成门禁连续实施。除 G6 真实宿主审批或实质阻塞外，不要向 JY询问是否继续。
```

---

## 中断后的简短恢复提示词

通常重新粘贴完整提示词最稳妥。如果当前会话只发生短暂网络中断，也可以发送：

```text
继续执行 `docs/architecture/M1_MASTER_PROMPT.md`。先检查 Git status、最近提交、当前 diff、任务状态和 `.workbuddy/memory/` 最近记录，判断第一个未完成门禁并从那里恢复。禁止重做已通过并提交的阶段；除 G6 真实宿主审批或执行契约定义的实质阻塞外，不要停下来询问是否继续。
```

## 注意

该提示词授权的是项目工作区内的受控自主实施，不等于对真实宿主、外部服务、凭据、发布、远端推送或破坏性操作的永久授权。G6 审批门不可被提示词中的“持续执行”绕过。
