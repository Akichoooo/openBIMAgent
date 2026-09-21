# openBIMAgent：Agent 对标与 Python 工程提升实施方案

日期：2026-09-17  
性质：调研与实施设计，尚未执行修复。  
范围：当前工作树，HEAD 为 `7e3764d46b509d420552cb5b1cb5cc5d330c87dc`，存在大量既有修改；该提交号不能单独复原本次工作树。  
目标：优先恢复可复现的市政管网设计—求解—执行—验证—交付闭环，再提升通用 Agent 工程质量。不是改写整个项目，也不是模仿其他产品的功能清单。

## 1. 结论与证据边界

### 1.1 不是简单的“本机没有 uv”

上一轮实测：

- `uv run pytest tests/ -q` 未启动，原因是 Git Bash 找不到 uv。
- 改用 `.venv/Scripts/python.exe -m pytest tests/ -q` 后，测试收集出现两个 ImportError。
- 使用 `--continue-on-collection-errors` 后：1122 passed、77 failed、9 skipped、14 errors、2 warnings，434.35 秒；14 errors 包含两个收集错误及测试准备阶段错误。
- `.venv/Scripts/python.exe -m ruff check src/` 通过。
- 本轮没有重跑全量测试，没有改业务代码，没有调用真实 LLM 或重新做双宿主验收。

本轮环境核验：

- 当前 PATH 与检查过的常见安装位置未找到 uv 可执行文件，不等于证明磁盘任意位置都没有它。
- `.venv/pyvenv.cfg` 记录该环境曾由 uv 0.12.5 建立。
- 实际解释器为 Python 3.13.14；`pyproject.toml:6` 与 `uv.lock:3` 的约束均为 `>=3.11`；项目没有 `.python-version`。
- 抽查的十二项关键依赖安装版本与当前锁文件一致：fastapi 0.141.1、fastmcp 3.4.4、google-genai 2.13.0、httpx 0.28.1、ifcopenshell 0.8.5、jsonschema 4.26.0、pydantic 2.13.4、pytest 9.1.1、python-docx 1.2.0、pyyaml 6.0.3、ruff 0.15.22、uvicorn 0.51.0。
- 这不是完整依赖图、所有平台分支和二进制 ABI 都已经验证的声明，但足以否定“关键依赖明显没装或版本任意漂移”的初步猜测。

uv 是解释器与环境管理工具，不是项目运行时必需导入的业务库。恢复 uv 有利于后续一致安装，不会自动补出缺失函数、注册 HTTP 路由或修复错误的合规判定。

### 1.2 已证实的跨平台问题：XSD 换行与哈希

`src/openbimagent/deliver/ifc_ids.py:39` 固定的哈希：

`528d0969f0ba16bb211a77c431f450f6b4ca788e0839ed45929b285c81c6aa30`

当前 `schemas/buildingsmart_ids_1_0.xsd` 为 LF 字节，哈希：

`e48683c303203305ac16731df8f9fe883c43a756aeac6cf8e8dcfe06d389a684`

将同一文件在内存中由 LF 转为 CRLF 后，哈希恰等于代码固定值。文件实测 13147 字节、285 个 LF、0 个 CRLF。没有 `.gitattributes`，当前 `core.autocrlf=true`。

结论：这组 IFC/IDS 失败的直接原因已确定为字节表示不一致，不是 XSD 内容发生任意语义变化，也不是缺 uv。不能把该结论扩大到所有失败。

推荐处理：明确供应商资源的字节契约；建议采用仓库内固定 LF 的规范副本，`.gitattributes` 针对资源钉住 LF；重新核对来源后更新该规范副本的固定摘要，保留原始来源/原始摘要与规范化方法。验证时继续对明确规定的实际字节做严格校验。若采用运行时换行规范化，应独立命名“规范摘要”和“原始摘要”，增加转换测试，不得让任意校验失败都通过重写 expected 值消失。

不推荐：直接关闭摘要检查；不核对来源便更新哈希；在当前大量修改的工作树上执行全仓 renormalize 或强制清理。

### 1.3 其他失败应独立分类

| 类别 | 当前证据 | 处理原则 |
|---|---|---|
| 源码接口缺失 | COMPACT_KEEP_RECENT、REASONING_LEVELS 导入失败；业务 chat.py 也导入缺失 reasoning_payload | 先决定并实现公开契约，不能只加空常量骗过收集 |
| HTTP 契约漂移 | 测试没带 X-Request-ID，返回 400 | 保留协议要求，修测试构造器和对应契约测试 |
| 应用装配缺口 | 部分业务端点 404/405；默认入口未注册模块 | 统一 app factory，不为测试另造一套特殊 app |
| 脆弱测试 | Schema 数量写死 45，实际 46 | 检查必需集合、版本及内容，而非无意义总数 |
| 资源字节漂移 | IDS XSD LF/CRLF 差异 | 修跨平台契约并回归交付链 |
| 语义缺陷 | 合规通过计数、评分/快照版本错位 | 先补能发现问题的回归测试，再修实现 |
| 真实环境未验证 | Blender/VW、模型服务、前端交互 | 单列验收，不借离线 Fake PASS 宣称真机通过 |

## 2. 对标原则：借机制，不堆框架

公开源码与文档为本轮检索时所见；多数链接指向可变化的 main/master，不是已冻结的可复现源码快照。进入实际改造前，为采用的机制补记具体 commit、许可证及引用文件。只读公开代码，不执行外部仓库代码。

Claude Code 的公开仓库使用商业条款，不能把核心实现作为普通开源代码研究或复用。其公开文档可用于机制对比。开源产品的源码可见、工程成熟和学术创新也是三件不同的事。

### 2.1 Codex：借边界分离和协议约束

本轮核实 `codex-rs/Cargo.toml`：Rust workspace 包含 core、execpolicy、app-server-protocol、state、linux-sandbox；还存在 Windows sandbox 相关成员/依赖，测试依赖包括 assert_cmd、insta。仓库许可证为 Apache-2.0。

应吸收：

- 核心运行时、策略判定、协议模型、状态存储与执行隔离分层。
- 协议模型变更要有快照/兼容性验证，不能靠前后端手工同步字段。
- 审批策略与底层执行限制是两层，不用字符串 confirm 取代认证与授权。
- 错误报告保留版本、平台、可重复步骤、预期/实际行为与脱敏诊断。

映射到项目：`core/plugin.py` 只负责能力分发；策略成为统一服务；`server` 适配认证主体；`orchestrator` 管状态；MCP adapter 执行已经授权的 typed plan。

不搬：Rust workspace、整个 CLI/TUI/产品形态；不能由模块名称推断已有相同保证。官方 security 页面本轮 403、app-server README 抓取超时，未将其细节作为已验证结论。

### 2.2 Claude Code：借权限与生命周期语义，不照抄闭源内核

公开权限文档描述 allow/ask/deny，以及 deny 优先、跨作用域限制和 workspace trust；权限决定是否允许工具调用，沙箱约束实际文件/网络访问。

公开 hooks 文档描述 PreToolUse、PostToolUse、PostToolUseFailure、SessionStart/End 等生命周期。重点不是 hook 数量，而是处理顺序、阻断语义与失败行为必须明确；后置 hook 不能撤销已发生的操作，异步 hook 不适合做强授权边界。

映射到项目：统一事件总线；PreTool 失败是否阻断由明确契约决定；授权必须在实际执行入口校验，不能仅由一个可配置 hook 承担。无模型回调/插件返回值能够自行批准宿主写入。

不搬：把“产品有这功能”当成源码验证；把任何 hook 都设计成自动修复/递归触发；默认启用高权限绕过模式。

### 2.3 PydanticAI：Python 类型与测试规范参考

本轮查看公开 pyproject/Makefile：uv workspace、Ruff、Pyright、pytest 严格警告、xfail_strict；还有小型 mypy 类型冒烟测试、Pydantic Evals 与 OpenTelemetry/Logfire 生态。

应吸收：

- 核心输入输出是类型模型，外部 JSON 在入口验证，而不是全链路 dict[str, Any]。
- Type checker 与运行时 Pydantic 校验互补：前者发现代码接口断裂，后者处理不可信输入。
- 真实行为评测与单元测试分离。
- warnings 有治理，不让弃用与接口漂移持续积累。

不搬：为了使用类型检查而更换 Agent 内核；照抄整套 monorepo；同时对所有代码启用多种严格类型检查器。

### 2.4 LangGraph：借持久执行与 checkpoint 抽象

本轮核实公开包配置与可插拔 checkpoint 包结构、pytest strict 标记/配置及快照测试工具。存储实现与编排层分离，值得借鉴。

映射到项目：为现有状态持久化制定窄接口和一致的事务边界，运行态存储与 JSONL 审计导出分工；恢复区分读取已有状态、重试幂等动作与需要人工判断的外部副作用。

不搬：仅为持久化就把自研编排改成 LangGraph；把 checkpoint 当成任意外部工具 exactly-once 的证明。SQLite 是否采用应由并发和一致性需求决定，不为对标而引入 Postgres。

### 2.5 OpenHands Python SDK：借执行边界与工作空间契约

本轮 Python 侧重点是 `OpenHands/software-agent-sdk`，不是只看主产品仓库的前端。公开配置包含 SDK/tools/workspace/agent-server 包边界、Ruff/Pyright、pytest timeout/异步/并行支持及依赖冷却、约束配置。

映射到项目：将 agent 决策与工具执行上下文分开；明确工作空间根、超时、可取消性、日志、工件身份和返回契约。依赖升级走专门 PR 与测试，不随开发启动自动刷新。

不搬：多包结构、Electron 或完整容器平台。Vectorworks GUI 与 Blender 宿主有各自线程/进程限制，不能假设一个通用 Docker runtime 可以代替两种真实宿主。

### 2.6 SWE-agent / SWE-ReX：借可复现实验和独立执行器

公开配置包括 pytest marker、xfail_strict、Ruff/pre-commit；SWE-ReX 将执行环境与 Agent 分开，支持容器/远端执行场景。

映射到项目：离线测试与有副作用的宿主测试明确隔离；每个测试/任务有独立工作目录和资源预算；真实任务保留输入、工具轨迹、输出与评测原因。

不搬：其完整 ACI 或缺少统一锁文件的方式。容器适合纯 Python 求解、协议和多数服务测试，GUI 宿主保留专门机器与验收脚本。

### 2.7 pi 与 DSH

**pi**：作者 Mario Zechner 的官方文章指向 `badlogic/pi-mono`，介绍 TypeScript/Node.js 实现、read/write/edit/bash 四个默认工具，以及模型适配、Agent 状态/事件、终端渲染、CLI 装配分层。本轮未核验本地文档声称的 `earendil-works/pi` 迁移（访问超时），因此不报告其当前版本、许可证或新功能。

应吸收小型核心与低上下文开销：工具定义只描述必要契约，完整领域知识按需读取；模型适配与执行循环分开。不能照搬默认不受限执行；BIM 宿主必须保留审批、工作区限制、typed plan 和回滚。

**DSH** 已确认是 `deepseek-ai/deepseek-harness`，官方公开仓库标示 MIT。官方 README/架构文档描述 Cordis 可替换插件、共享上下文、类型化事件及卸载撤销注册。配置顺序为 bundles → profile patch → home patch → CLI overlay；patch 是行配置替换/追加，不应误述成任意深合并。

最有价值的规则是“Model-visible means logged”：模型请求应能由持久会话事实重建。映射到项目：对每次模型调用保存可追溯的消息投影和配置身份，并区分持久事件、实时通知与能力 hook；含敏感内容时采用受控本地保存和脱敏导出，不把完整私密提示公开上传。插件卸载必须撤销 handler/subscription；profile 激活失败原子回滚，不能半应用。

限制：DSH 文档标注 developer preview，可能破坏兼容；未结算的流片段崩溃时可能不保留，不能宣称字节级全恢复；有 Python SDK 不等于核心是 Python，也不等于 Python 端具备全部自定义插件能力。不开启完整插件生态迁移，只借有价值的契约。

## 3. 目标结构：小步收敛现有系统

保留现有目录主干，不进行一次性大迁移：

- `utility/`：纯确定性计算与明确的物理/几何语义；不依赖 HTTP/UI/LLM。
- `core/`：Agent loop、能力注册、预算、策略调用，不承担宿主细节。
- `orchestrator/`：任务/审批/attempt 状态机、恢复、取消、执行调度。
- `providers/`：统一模型请求与响应契约，方言适配，能力探测与错误分类。
- `assembly/`：编译结果到 typed plan 的转换、宿主回执、语义比较。
- `deliver/`：绑定已验证字节、发布与 Manifest，不自行发明 PASS。
- `session/`：持久化接口与审计投影，事务边界清楚。
- `server/`：认证、协议适配、统一装配，不复制另一套业务状态机。
- `frontend/`：消费正式契约；demo 数据有显著标签，不能混入生产验收。

跨层只允许三类承诺：类型明确的请求/响应、版本化事件、内容绑定的工件。一个名为 Gate 的模块如果未被真实入口调用，不算完成了门禁。

## 4. Python 工程基线

### 4.1 解释器、依赖与构建

1. 暂以当前 3.13 系列作为待验证开发基线，不因失败盲目降级；通过 wheel 安装与 CI 后固定具体补丁版和 uv 版本。
2. 新增 `.python-version`、`.gitattributes`；Python 宣称支持范围与 CI 实际覆盖保持一致。不经讨论直接把 >=3.11 改为只支持 3.13。
3. 每个开发和 CI 环境用同一锁文件。`uv sync --locked` 校验元数据与锁一致并精确同步；`--frozen` 只是使用现有锁且不检查其是否过期，两者不要混淆。
4. 干净复现使用单独环境目录，例如 PowerShell 的 `$env:UV_PROJECT_ENVIRONMENT='.venv-repro'`，避免覆盖正在使用的 `.venv`；该命令是未来执行建议，本轮未执行。
5. 区分 runtime/dev/test/host-test 依赖，按实际需要增量增加 pytest-cov、类型检查和属性测试工具；不一次加满所有工具。
6. 单独验证 wheel/sdist 安装后查找 schema/模板/知识包资源，不能只在仓库根目录依赖相对路径跑通。
7. 禁止在全量运行过程中隐式升级 provider SDK。依赖更新独立进行，保留锁文件 diff 和兼容性测试结果。

### 4.2 格式、静态检查、类型

保留现有 120 行风格。分阶段增加 Ruff 的 I、UP、B、ASYNC、RUF100、PGH003 等规则；是否启用取决于锁定 Ruff 版本支持，不进行未经审阅的全仓自动修复。vendor/生成代码单列边界。

先对 contracts、utility、providers 公共接口启用 Pyright；再覆盖 orchestrator/server。新增文件严格检查，旧代码用逐模块收敛而非全局 ignore。

必须满足：

- 导入时不能依赖模型 key、宿主在线或网络。
- 所有字符串文件读取明确 UTF-8；哈希另有字节契约。
- 核心类型不使用 Any 逃避错误；第三方动态接口只在 adapter 边界豁免。
- `# type: ignore` 必须具体、局部且可清理。
- 生成协议若有三份实现（Pydantic/JSON Schema/宿主轻量验证），保留共享有效/无效 fixture 做一致性测试，不要求宿主内嵌 Python 安装所有后端依赖。

### 4.3 测试分层

建议显式标记 unit、integration、contract、property、host_blender、host_vectorworks、llm_live、frontend_e2e、benchmark。仅增加 marker 不会自动阻止真实调用：collection hook/运行选项需要默认拒绝真实宿主和网络测试，显式开关才允许，CI 要核对实际收集集合。

- 快速门禁：collect-only、导入冒烟、lint/type、unit、HTTP 契约。
- 离线集成：真实求解器/IFC 库/持久化，不调用模型和 GUI 宿主。
- 真机验收：专用环境、授权输出根、串行宿主写操作、有超时、保留日志和产物。
- 学术实验：单独套件，不把慢 LLM 实验塞进每次提交测试。

改进断言：

- 不删失败测试来换绿；先判断是测试过期还是产品违约。
- 不写 schema 文件总数等脆弱常量；写必需 schema、版本与兼容性。
- Fake 实现必须通过真实 adapter 同样的契约 fixture，避免 fake 过于宽松。
- 恢复测试覆盖崩溃窗口，而不只是正常写完后重新加载。
- warnings 分期治理：项目自身弃用先升级为错误；第三方警告有精确豁免、原因与到期条件。
- 收集 branch coverage，先建立基线，再定分模块目标；变更代码建议 >=85% 分支覆盖作为目标而非当前事实。关键授权、交付和规则判定需逐条负例，不能拿覆盖率替代正确性。

### 4.4 运行时工程标准

- 结构化日志字段：request_id/session_id/attempt_id/tool_call_id/host_job_id/artifact hash；默认不记录 key、完整用户文件或模型隐私内容。
- 异常分为配置、输入、鉴权、上游限流/暂时不可用、宿主未知状态、验证失败、内部错误。
- 重试仅对可安全重试的失败；宿主超时先查询回执，不能盲目重复执行。
- 取消是明确状态机；线程内同步调用不可强杀的限制应对 UI 可见，不承诺“立即停止”。
- 有总任务预算、模型调用预算、单工具超时和有界队列；不要无限追加 history 或递归自愈。
- 统一 hook 生命周期，PostToolFailure 也留下终态；策略是不可绕开的独立边界。

## 5. 分阶段工作包与验收

### P0：恢复可重复的质量基线（建议 2–4 人日）

工作：记录当前 diff 与依赖；修 XSD 字节契约；完成缺失的 provider reasoning 与 context compaction 契约或明确撤回未实现功能；导入所有业务模块；区分 HTTP fixture 过期与路由缺失。新增 wheel 安装冒烟。只处理能解释现有失败的变更。

重点文件：`pyproject.toml`、`uv.lock`、`.gitattributes`、`providers/dialects.py`、`core/loop.py`、`server/chat.py`、`deliver/ifc_ids.py`、相关测试。

验收：0 收集错误；固定 LF/CRLF 场景有测试；原有失败按根因有归档；同一锁文件的干净环境复现通过选定基线。不将“1122 passed”当全绿，缺少的功能标记明确。每个根因独立补丁，避免混进全仓格式化。

### P1：统一应用入口与前后端契约（3–5 人日）

工作：建立统一 `create_app(settings, services)` 装配，CLI、模块入口、测试从它创建；显式分 demo/test/local-production 模式；统一注册 chat/runs/workspaces/approvals/settings 等正式路由；不可用功能返回结构化不可用状态，而不是 UI 有按钮但根本没路由。

events 路径拆分 JSON 分页查询和 SSE 订阅；定义 cursor/Last-Event-ID、心跳、断开释放、有界缓冲、重放语义，不承诺网络 exactly-once。前端依照 OpenAPI/正式模型生成或校验类型；整合请求 ID 与错误处理。

验收：正式 app 的 route inventory 快照；前端使用的所有 API 有契约测试；会话创建—发送—流式接收—取消—历史重放最小闭环；frontend typecheck/build/test 通过；实际浏览器验证生产构建而非静态 demo。认证相关验收与 P2 共同通过后才允许开放访问。

### P2：认证、授权、审批统一（3–5 人日，可与 P1 合并实现）

工作：身份来自服务端认证，不接受 body.actor 自我声明；统一 allow/ask/deny，deny 最高优先；所有副作用入口包括插件调用、MCP、导出均落同一授权链。审批记录绑定 actor/session/tool/参数摘要/工件或计划哈希、有效期和单次消费状态；参数改变即重新审批。

本地模式明确仅绑定 loopback，也不要将 localhost 等同绝对安全。任意网络访问需要正式认证配置；为本地浏览器访问制定 Origin/Host 约束与凭据存储策略。工作区路径检查需解析后验证，不能只靠字符串前缀。配置与日志统一脱敏；补 local provider JSON 忽略规则。

验收：缺认证、身份不匹配、过期/已消费审批、参数变更均不能产生副作用；权限在真实 handler 前校验。测试仅在受控本机 fixture 内进行，不提供或运行外部利用链。明确文档中“审批”和“执行隔离”是不同保证。

### P3：把合规和交付变成可信结论（4–6 人日）

工作：统一 EvaluationReport；每条实际规则结果必须有 rule identity、对象、输入哈希、observed/required 值、单位、状态和证据。规则目录条数不能用作通过条数；PASS/FAIL/UNKNOWN/REVIEW_REQUIRED 分开。任何必须满足而未验证的约束不能整体 PASS。

自愈收敛要求路由、网络、适用水力/几何和 domain gate 共同满足该任务明示的验收策略。区分“本轮没有局部冲突”和“完整合规”。限定 CDCL 等术语，不宣称未实现算法。

视觉链分 pre_exec rollback snapshot 与 post_exec accepted snapshot，图片、评分、计划、场景文件通过同一版本身份绑定。生产验收不允许 MockCritic 9 分进入 PASS；离线演示仍可用，但携带 simulated/evidence_kind。

交付先将字节固定到 staging，对固定内容验证并发布同一内容，避免旧 report 绑定新文件；Manifest 应标应用层不可覆盖/可检测漂移，不声称不可篡改。

验收：构造局部无冲突但网络 FAIL/UNKNOWN 的案例，整体不得 PASS；检查每个 pass_count 来自真实结果；一次建模首轮成功后的交付文件含新对象而非初始场景；校验后内容变化被拒；hash 与语义报告指向同一产物。

### P4：持久化、恢复和宿主可靠性（4–6 人日）

工作：明确 task/attempt/tool operation 状态与允许转换；采用单一权威状态存储。若现有 JSONL 多写者和事务问题无法局部解决，优先本机 SQLite 事务+约束；JSONL 保留为审计投影，不作为第二个独立真相。是否启用 WAL 需验证实际文件系统/部署模式，不放共享网络盘后默认认为安全。

审批、幂等 reservation 与终态结果必须持久化；实现 compare-and-swap，而不是只生成 mutation 描述。JSONL 提供序号/parent 完整性检查；fork 只复制选定祖先链。宿主采用 operation id、回执查询与恢复对账。无法确定是否产生副作用时标 UNKNOWN/REVIEW_REQUIRED，不能盲重放。

验收：进程重启、终态事件未写、save 后 ack 前、同键不同参数、重复请求、取消与完成竞态；同操作重试不产生重复对象。不得宣称对所有外部程序都有 exactly-once。

### P5：领域求解与 IFC 互操作（4–7 人日）

工作：先统一地表/管底/中心线坐标和单位，修空间图语义；明确平面净距与真实三维距离的适用范围；网络平行边/环检测有测试。路线优化应先基准和 profile，若状态含方向/历史约束，不能只按 cell 加 closed set；定义完整状态、可采纳启发式和搜索预算后再优化。

IFC 补必要单位、定位、标准 Body/ShapeRepresentation 和管段几何，限定毕设支持的构件种类。IDS 明确支持子集，未支持 facet 返回 UNSUPPORTED/UNKNOWN 或拒收，不能悄悄当通过。使用独立校验器/查看器检查导出，而不是只用同一函数生成并验证。

验收：小图与独立穷举参考比较、几何边界/单位变换属性测试；至少一个完整管网 IFC 在独立查看器中几何可见、实体/属性/端口关系正确；未支持 IDS 约束不能 PASS。

### P6：工程 CI、独立证据与论文实验（4–6 人日）

CI：每次变更做格式/lint/type、导入、离线单元/契约、前端 build/test；定期 Windows/Linux 与宣称支持的 Python 版本矩阵；宿主 job 独立且明示是否运行。先不把 Matrix 扩到所有 Python 版本，应与支持声明一致。

学术实验：冻结任务集合与规则版本，区分开发集和最终评测集；B1–B10 留作回归集，不伪称独立真实工程样本。建议另建约 30 个分层任务、包含有效和无效输入；每个随机配置至少 5 次作预算起点，不代表自动满足统计充分性。

统一输入、模型、工具预算、约束与成功定义。比较 LLM-Direct、语义+solver、完整系统、自愈消融；自愈 ON/OFF 必须使用同一组任务。人工设计的 adversarial/边界案例独立报告，不混入真实工程案例名义。

指标：需求解析、可行实例生成、无效实例正确拒绝、完整规则通过、宿主执行、语义一致、IFC独立验收、恢复正确性、耗时/调用量/成本；不能把这些合成一个无法解释的成功率。报告分母、超时处理、置信区间与相关样本结构；温度0不保证绝对确定。

每次保留脱敏输入、代码/工作树身份、锁文件哈希、模型配置、规则/schema 哈希、原始工具轨迹、工件、逐案例判定、汇总脚本版本。禁止只留下五行均值表。密钥和受限用户文件不进入公开数据包。

验收：从独立目录使用冻结材料重算论文表；一条失败可追溯到具体输入和产物；真实宿主与 offline compatibility 分栏；表中 UNMEASURED 不可引用为实测结果。

## 6. 排期与范围控制

以上估算是有效人日，不是保证交付时间；顺序完成约 24–39 人日，单人全时约 5–8 周，真机环境或论文数据准备会额外影响工期。不能把所有包平行推进，因为 P3/P5 的验收依赖 P0/P1 的稳定基线。

若毕设时间紧：P0 → P1/P2 最小闭环 → P3 → 一个管网案例的 P5 → P6 最小可复现实验。P4 只修确实影响该闭环的事务问题，不急着做完整存储迁移。

明确暂缓：更换 Agent 框架、多租户云平台、Postgres/Redis/Kubernetes、复杂插件商店、更多 UI 主题、额外模型数量、自动自改代码、形式上“支持所有 CAD”。这些不会解决现有证据不一致。

每个变更采用：明确契约 → 失败测试 → 最小实现 → 局部回归 → 跨层验收 → 更新事实文档。格式化与功能改动分开；不覆盖用户既有改动，不擅自 commit/push，不直接清理当前工作树。

## 7. 建议新增的交付物（未来实施，不是本轮已创建）

- `.python-version` / `.gitattributes`：解释器与字节契约。
- 精确测试分层和类型配置：pyproject 与 CI。
- 统一 app factory 与 route contract 清单。
- 工程报告模型与运行态/审批状态迁移说明。
- 跨适配器共享的有效/无效 fixture。
- `scripts/verify_release` 类统一验收入口，失败保留真实退出码，不用管道末端 tail 的成功掩盖 pytest 失败。
- 机器可读 environment/evidence manifest。
- 论文 experiment manifest、逐案例结果和汇总脚本。

这些名字是建议，不预设必须拆成新的文件；优先复用现有恰当模块，避免“为了架构再加一层”。

## 8. 公开来源与核验范围

1. Codex README / LICENSE：https://github.com/openai/codex  
   Rust workspace：https://github.com/openai/codex/blob/main/codex-rs/Cargo.toml  
   问题报告规范：https://github.com/openai/codex/blob/main/docs/contributing.md
2. Claude Code 仓库：https://github.com/anthropics/claude-code  
   商业条款文件：https://github.com/anthropics/claude-code/blob/main/LICENSE.md  
   权限：https://code.claude.com/docs/en/permissions  
   Hooks：https://code.claude.com/docs/en/hooks
3. PydanticAI：https://github.com/pydantic/pydantic-ai  
   工程配置：https://github.com/pydantic/pydantic-ai/blob/main/pyproject.toml  
   文档：https://ai.pydantic.dev/
4. LangGraph：https://github.com/langchain-ai/langgraph  
   工程配置：https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml
5. OpenHands Python SDK：https://github.com/OpenHands/software-agent-sdk  
   工程配置：https://github.com/OpenHands/software-agent-sdk/blob/main/pyproject.toml
6. SWE-agent：https://github.com/SWE-agent/SWE-agent  
   工程配置：https://github.com/SWE-agent/SWE-agent/blob/main/pyproject.toml  
   SWE-ReX：https://github.com/SWE-ReX/SWE-ReX
7. uv sync/locked/frozen 官方语义：https://docs.astral.sh/uv/concepts/projects/sync/
8. pi 作者的一手设计说明：https://mariozechner.at/posts/2025-11-30-pi-coding-agent/  
   该文章链接的仓库：https://github.com/badlogic/pi-mono
9. DSH：https://github.com/deepseek-ai/deepseek-harness  
   官方架构参考：https://deepseek-harness.github.io/deepseek-harness/en/reference/

## 9. 第一批最小补丁清单与验收命令

以下是实施时使用的命令，本轮没有据此修改业务源码或重跑套件：

1. **资源契约补丁**：只修改 XSD 的字节规范、相关 `.gitattributes`、固定摘要与来源记录；新增 LF/CRLF 与 Windows/Linux 检出测试。验收先运行 `tests/test_ifc_ids_delivery.py`，再运行 `tests/test_network_utility_integration.py` 和 `tests/test_m1_5_t7_benchmark.py`。本轮内存转换断言已通过，证明 CRLF 摘要与旧 pin 对应，但不冒称补丁后的集成测试已经通过。
2. **Provider 契约补丁**：核对 `tests/test_reasoning.py` 中每家模型参数的真实 provider 文档/能力配置，不把测试里可能过期的模型名当作 API 事实；实现或迁移 reasoning 契约、导入链和降级策略。运行该文件与 `tests/test_dialects.py`、`tests/test_providers_registry.py`；无需真实 key 的导入冒烟必须通过。
3. **上下文契约补丁**：实现或迁移预算、保留近期消息、摘要失败回退、审计与取消语义；不能只补一个常量。运行 `tests/test_context_compaction.py` 与 `tests/test_loop.py`，检查模型请求确实不超预算且工具调用/结果配对完整。
4. **应用装配补丁**：正式 app factory 统一挂载认证和路由；修测试请求 ID 生成器，不放松认证/协议去迎合旧测试。先运行 `tests/test_chat.py`、`tests/test_chat_stream.py`、`tests/test_workspaces.py`、`tests/test_workbench_io.py`，再做前端契约和浏览器验收。

现有环境执行模板：`.venv/Scripts/python.exe -m pytest <上述测试路径> -q`。每个补丁保持真实退出码；进入下一补丁前记录通过/失败/跳过和日志。恢复 uv 后，在独立复现环境执行 `uv sync --locked`，随后 `uv run --locked python -m pytest --collect-only -q` 与分层测试。安装 uv、同步依赖、实施补丁及真机副作用都不属于本轮已完成事项。

限制：未逐仓库运行其测试；未核验全部历史版本；官方项目采用某项配置不等于它最适合本项目。未取得的页面不会被列为已证实细节。对公开项目的总体好坏、代码原创性或安全性不作审计结论。
