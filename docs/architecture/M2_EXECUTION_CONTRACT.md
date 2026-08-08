# openBIMAgent M2 产品化服务与客户端执行契约

版本：v1.0

日期：2026-08-08

状态：**ACTIVE**

前置状态：M1 G6 `PASS`，M1 G7 `PASS`，M1.5 `OFFLINE PASS`

> M1 G6/G7 已关闭。本契约从 DRAFT 升级为 ACTIVE。

边界提交：`25586b1`（feat: complete M1 G6 real Vectorworks acceptance, G7 quality gates）

## 1. 契约目的

M2 将现有单机 Agent Core、Subagent Runtime、Session、Control Plane 和不可变工件能力，产品化为共享版本化协议的服务与客户端：

```text
CLI / TUI / Web 技术验证
→ FastAPI / OpenAPI
→ 版本化 API 与 SSE data-* 事件
→ 认证、授权、幂等、限流与错误协议
→ ReadOnlyControlPlane + Runtime IPC 写代理
→ LocalSubagentRuntime
→ Session / Artifact / Approval / Receipt / Recovery
→ typed BIM 主链与宿主边界
```

M2 解决“如何安全、稳定、可恢复地对外提供现有能力”，不重新发明 Solver、Compiled IR、宿主 Builder 或交付协议。

## 2. 启动条件与阶段状态

### 2.1 当前允许的准备工作

M1 G6/G7 未关闭期间，只允许：

1. 审计现有 M2 底座和差距。
2. 冻结本契约、威胁模型和 Gate。
3. 起草 API、SSE、错误、身份、授权和幂等协议。
4. 设计失败关闭的负向测试和 E2E 矩阵。
5. 做不获取 Runtime lease、不扩大宿主权限的只读协议 spike。

### 2.2 正式启动条件

必须同时满足：

- M1 G6 已由真实 Vectorworks 工件和真实双宿主比较关闭。
- M1 G7 总验收完成。
- `PROJECT_HANDOFF_STATUS.md` 明确将当前 Gate 切换为 M2 P0。
- 本契约从 DRAFT 升级为 ACTIVE，并记录边界提交。
- M2 的部署模式、信任边界和身份来源得到明确批准。

### 2.3 停止条件

出现以下任一情况必须停止当前 Gate 并失败关闭：

- 需要扩大真实宿主、工作区或文件写入范围但未获批准。
- 需要外部凭据、付费服务、系统服务、公开网络监听或云部署。
- 认证、租户、数据保留或远程控制模式存在不可逆产品取舍。
- API 或客户端可以绕过 Runtime lease、Approval Broker、capability ceiling 或 typed host boundary。
- SSE、日志或错误响应可能泄露 token、原始敏感参数、指令正文或内部路径，且无法在当前 Gate 内确定性收敛。
- M1 G6 真实工件到位；此时应立即暂停 M2 准备并优先关闭 G6/G7。

## 3. 当前基线与不可重复建设

### 3.1 已实现底座

| 能力 | 当前实现 | M2 处理 |
|---|---|---|
| SSE 类型草案 | `src/openbimagent/core/events.py` 的 `SSEEventType` / `DataPart` | 版本化并与 Session/server 接线，不复制第二套事件模型 |
| 多会话 | `SessionStore`、每会话 JSONL、`sessions/index.json` | 作为 API/TUI/Web 会话数据源 |
| 只读控制面 | `ReadOnlyControlPlane` | 封装为只读服务，不获取 Runtime lease |
| Runtime 写控制 | loopback `RuntimeIpcServer/Client` | server 只能代理到唯一 lease owner，不得重建 Runtime |
| 操作者身份 | `ActorRef v1` | 认证主体映射为稳定 ActorRef，显示名不是授权身份 |
| 控制操作 | approval/resume/steer/cancel/ping | 复用现有语义与幂等约束 |
| 本机界面 | `OperatorConsoleServer` | 保持 loopback 管理工具，不直接发布为远程产品 |
| 恢复与审计 | RuntimeState、Session、receipt、checkpoint、Manifest | API/SSE 必须投影这些持久事实，不制造旁路状态 |

### 3.2 明确缺口

当前仓库没有：

- FastAPI、Uvicorn 或 OpenAPI server 实现/依赖。
- 正式远程认证和授权中间件。
- 多租户数据隔离协议。
- SSE 连接、游标、重连、回放和终态确认实现。
- 正式 TUI 客户端。
- 远程 Web 产品和对应 E2E。
- 远程 Playbook 供应链协议实现。

`core/events.py` 仍明确标记 `TODO(M2)`；因此现有 Operator Console 和 Runtime IPC 只能称为 M2 底座。

## 4. 信任边界

### 4.1 进程与通道

```text
[不可信客户端]
CLI / TUI / Browser
        │ HTTPS/SSE 或本机连接
        ▼
[M2 Server 安全边界]
认证 → 授权 → 限流 → Schema → 幂等 → 审计
        │
        ├── 只读：ReadOnlyControlPlane / Session / Manifest
        │
        └── 写控制：server-side RuntimeIpcClient
                          │ 仅 127.0.0.1 + bearer token
                          ▼
                  [唯一 Runtime lease owner]
                          │
                          ▼
                LocalSubagentRuntime / Approval
                          │
                          ▼
                typed host plans / deliver gate
```

### 4.2 不变量

1. 远程客户端永远不获取 Runtime IPC discovery token、宿主凭据或内部 capability profile。
2. Server 不获取 Runtime lease，不在请求进程中自行 rehydrate 第二个 Runtime。
3. 所有写控制必须经过唯一 lease owner 的 Runtime IPC 或后续等价受控协议。
4. ReadOnlyControlPlane 只投影持久事实，不能把查询变成隐式恢复或重放。
5. 外部身份必须映射为稳定 `ActorRef`；客户端不能声明 `runtime` 或 `legacy` actor。
6. 远程角色、Playbook 或请求不能提高受信任角色 profile 的 capability ceiling。
7. 宿主写入仍受 M1 typed plan、Approval Broker、范围锁和交付门禁约束。

## 5. 部署模式

M2 P0 必须在以下模式中显式选择一种，未选择前不得实现远程写控制：

- **单用户本机模式**：server 和 Runtime 同机，仅本机用户访问；作为第一正式切片的推荐模式。
- **单租户远程模式**：受信任 server 代理到专属 Runtime；需要正式认证、TLS、审计和数据保留策略。
- **多租户模式**：需要租户级 Session、Artifact、Runtime、密钥和配额隔离；不属于首个 M2 切片，除非另行批准。

默认契约：先完成单用户本机模式。任何公开监听、云部署或多租户实现均需独立审批和威胁模型升级。

## 6. 版本化 API 草案

### 6.1 版本规则

- REST 基础路径：`/api/v1`。
- SSE 协议版本：`1.0`。
- OpenAPI 文档必须由运行时代码生成并纳入契约测试。
- Pydantic 模型和 JSON Schema 默认 `extra="forbid"`。
- 未知 API major、事件类型、操作类型或字段失败关闭。
- 新增 optional 字段可在同一 major 演进；删除、改义或必填化必须升 major。

### 6.2 资源边界

| 资源 | 只读操作 | 写操作 | 事实来源 |
|---|---|---|---|
| sessions | list/get/events | create、fork、rename（分 Gate 实现） | `SessionStore` / `index.json` |
| attempts | list/get/lineage | dispatch、cancel、resume、steer | RuntimeState + Runtime IPC |
| approvals | list/get pending | approve/reject | Session facts + Runtime IPC |
| artifacts | list/get metadata/download | 无直接覆盖；仅由 Runtime/deliver 提交 | Artifact Manifest / immutable store |
| events | SSE subscribe/replay | 无客户端直写 | Session + Runtime lifecycle |
| runtime | health/capabilities | 不暴露 start/rebuild/lease | Runtime IPC `ping` / server 配置 |

### 6.3 建议端点

首个契约切片：

```text
GET  /api/v1/health
GET  /api/v1/sessions
GET  /api/v1/sessions/{session_id}
GET  /api/v1/sessions/{session_id}/events
GET  /api/v1/attempts
GET  /api/v1/attempts/{request_id}
GET  /api/v1/lineages/{lineage_id}
GET  /api/v1/approvals
GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/events
POST /api/v1/approvals/{approval_id}/decisions
POST /api/v1/attempts/{request_id}/resume
POST /api/v1/attempts/{request_id}/steer
POST /api/v1/attempts/{request_id}/cancel
```

首切片不提供：任意代码执行、任意文件路径读取、Runtime 创建/重启、lease 操作、token 读取、宿主自由脚本、工件覆盖或删除。

## 7. 身份、认证与授权

### 7.1 身份

认证完成后由 server 构造：

```text
ActorRef {
  protocol_version,
  actor_id,
  actor_type,
  display_name?
}
```

- `actor_id` 是授权和幂等域的一部分，必须稳定且不可由请求体覆盖。
- `display_name` 只用于展示。
- API 请求体禁止出现可替换 server actor 的字段。

### 7.2 认证

P0 必须选择并记录认证方式。首个本机切片建议使用本机生成、受限保存、可轮换的 server token 或操作系统本地身份；不得复用 Runtime IPC token。

认证秘密：

- 不进入 URL、Session、RuntimeState、Artifact Manifest、错误响应或普通日志。
- 只以哈希或固定长度摘要用于审计。
- 必须支持轮换和失效。

### 7.3 授权

至少区分：

- `viewer`：读取隐私收敛的 Session、Attempt、Approval 和 Artifact 元数据。
- `operator`：在 viewer 基础上执行 approve/reject/resume/steer/cancel。
- `admin`：仅用于 server 配置；不得自动获得宿主写入批准。

授权不能替代 Approval Broker。即使 actor 具备 operator 权限，具体工具副作用仍需遵守原有审批和 capability ceiling。

## 8. 幂等与并发

### 8.1 写请求

所有写端点必须要求：

- `Idempotency-Key`，长度和字符集与 Runtime IPC 兼容。
- server 认证后的 `actor_id`。
- 规范化资源标识和语义 fingerprint。

幂等域：

```text
actor_id + endpoint_operation + idempotency_key
```

规则：

- 同键同义返回原响应和原 receipt，不重复副作用。
- 同键异义返回稳定 `409 IdempotencyConflict`。
- Server 重启后仍能从持久 receipt/Session/RuntimeState 对账；不能只依赖进程内缓存。
- 客户端超时不代表操作未发生，必须先按幂等键查询/重试。

### 8.2 并发

- 请求、SSE 连接和 Runtime 控制分别设置显式上限。
- 并发上限不扩大 Runtime 当前最多 4 个子代理的限制。
- 同一 approval、attempt 或 lineage 的冲突写操作按版本/终态串行化并失败关闭。
- Server 不使用无界队列。

## 9. SSE 事件协议

### 9.1 事件信封

建议统一为：

```json
{
  "protocol_version": "1.0",
  "event_id": "stable-event-id",
  "event_type": "data-progress",
  "session_id": "...",
  "request_id": "...",
  "lineage_id": "...",
  "attempt_number": 1,
  "sequence": 1,
  "occurred_at": "RFC3339",
  "terminal": false,
  "data": {}
}
```

初始 `event_type` 至少兼容现有：

- `data-progress`
- `data-vision-scorecard`
- `data-clarify-form`

M2 还应以版本化方式补充 attempt、approval、artifact、error 和 terminal 事件；不得把 Session 原始内部事件不加筛选地直接暴露。

### 9.2 双视图

- LLM 工具结果：短文本/结构化结果，供模型继续推理。
- UI data part：面向渲染的隐私收敛结构。
- 两个视图来自同一持久事实并可独立演进；前端不得通过字符串解析修补协议。

### 9.3 重连和回放

- 支持 `Last-Event-ID` 或等价游标。
- 事件按单个 session/attempt 范围内的稳定 sequence 排序。
- 断线重连必须从持久事实回放，不依赖内存队列完整性。
- 重复事件由 `event_id` 幂等消费。
- 必须发送明确 terminal 事件；客户端确认终态前不得仅凭连接关闭推断完成。
- 游标过旧或无法回放时返回结构化错误，不能静默跳到最新状态。

### 9.4 隐私

SSE 禁止包含：

- token、API key、cookie、IPC bearer token。
- 工具原始敏感参数或自由代码正文。
- 未脱敏的 instruction/task 原文。
- 未授权绝对路径和内部异常堆栈。

## 10. 错误协议

所有非流式 API 错误使用稳定信封：

```json
{
  "ok": false,
  "error": {
    "code": "StableErrorCode",
    "message": "safe human-readable summary",
    "retryable": false,
    "request_id": "correlation-id",
    "details": {}
  }
}
```

最低错误类别：

- `InvalidRequest`
- `UnsupportedVersion`
- `Unauthorized`
- `Forbidden`
- `NotFound`
- `Conflict`
- `IdempotencyConflict`
- `RateLimited`
- `PayloadTooLarge`
- `RuntimeUnavailable`
- `ApprovalRequired`
- `TerminalStateConflict`
- `ReplayCursorExpired`
- `InternalError`

要求：

- HTTP 状态和错误 code 稳定映射。
- 错误响应不回显 token、请求正文、内部堆栈或 Pydantic `input_value`。
- 只有明确可安全重试的错误才标 `retryable=true`。
- Runtime 原始异常必须映射为白名单错误，不能直接把 `type(exc).__name__` 当远程协议。

## 11. Artifact 与下载边界

1. 客户端只按 Manifest 中的 artifact identity 访问，不提交任意文件路径。
2. Server 解析真实路径时必须限制在不可变工件根目录内并拒绝 `..`、绝对路径、链接逃逸和设备路径。
3. 下载前重算或按策略抽检 SHA-256；metadata 返回 size、media type、SHA-256、status 和 source attempt。
4. `partial/failed` 工件默认不可作为完成交付下载；如提供调试访问，必须单独授权和明确标记。
5. API 不提供覆盖、重命名或删除不可变历史的端点。
6. Range、缓存和 Content-Disposition 必须防止内容嗅探和文件名注入。

## 12. 远程 Playbook 供应链

该工作包不进入首个 server 切片。启用前必须具备：

- HTTPS 与允许的可信源策略。
- 内容 SHA-256、版本锁和可选签名。
- 缓存身份、离线恢复和过期策略。
- 下载大小、媒体类型和重定向限制。
- Schema 与 capability ceiling 校验。
- 远程内容不得修改本地角色权限、工具 allowlist、宿主路径或审批策略。
- 同一 URL 内容漂移必须生成新身份，不能静默覆盖缓存。

## 13. 客户端边界

### 13.1 CLI/TUI

- 与 Web 共用 API/SSE，不直接读取 Runtime 私有文件或 IPC token。
- 支持会话列表、切换、搜索、审批、进度、工件和错误展示。
- 斜杠命令映射到版本化 API，不在客户端复制业务状态机。
- 断线重连后从 server snapshot + SSE cursor 恢复。

### 13.2 Web 技术验证

- 只在 P5 后开始。
- 不复用 Operator Console 的本地 CSRF 模型冒充远程认证。
- 浏览器不得直接连接 Runtime IPC 或宿主 MCP。
- 首先验证会话、事件、审批和工件预览；不以视觉完成度替代安全/E2E Gate。

### 13.3 Operator Console

Operator Console 保持：

- 仅 `127.0.0.1`。
- 无 CORS。
- server-side ActorRef 和 Runtime IPC token。
- Host、Origin、CSRF、Content-Type、请求大小、并发和 CSP 限制。

它是本机故障处理与管理界面，不是 M2 远程前端代码基座。

## 14. Gate 与完成标准

### P0：契约、部署模式与威胁模型

交付：

- 本契约升级为 ACTIVE。
- 部署模式和身份来源决议。
- 数据分类、信任边界、威胁模型和停止条件。
- API/SSE/错误/幂等初始版本。

门禁：所有不可逆产品选择已明确；M1 G6/G7 已关闭。

### P1：协议模型与 Schema

交付：

- API request/response、错误、SSE、cursor、artifact metadata 模型。
- JSON Schema 和 canonical 示例。
- OpenAPI 基线与兼容性规则。

门禁：未知字段/版本、非法 actor、敏感字段、越权操作和 schema 漂移负向测试通过。

### P2：FastAPI/OpenAPI 只读服务

交付：

- health、sessions、attempts、lineage、approvals、artifact metadata。
- ReadOnlyControlPlane/Session/Manifest 适配。

门禁：server 不获取 Runtime lease；路径和隐私边界通过；OpenAPI 契约测试通过。

### P3：受控写控制

交付：

- approval decision、resume、steer、cancel。
- server-side ActorRef、认证授权和 Runtime IPC 代理。
- 持久幂等对账。

门禁：任何客户端均不能重建 Runtime、读取 IPC token、提升权限或绕过 Approval Broker。

### P4：SSE 恢复

交付：

- 版本化 data parts。
- Session/Runtime 事实到 SSE 的投影。
- cursor、回放、重复消费、terminal confirmation。

门禁：断线、server 重启、重复事件、游标过期和慢消费者测试通过；无敏感泄露。

### P5：CLI/TUI

交付：

- 会话侧边栏、控制操作、事件流和工件展示。
- 斜杠命令 API 映射。

门禁：客户端断线和 server 重启后状态一致；不直接访问 Runtime 私有状态。

### P6：Web 技术验证

交付：

- 会话、事件、审批、工件预览和宿主状态技术验证。
- 浏览器安全与 E2E。

门禁：远程认证/CSRF/CORS/CSP/下载边界通过；不能把 Operator Console 直接发布。

### P7：远程 Playbook 与可选宿主评估

交付：

- 供应链协议和缓存身份。
- Bonsai/第三宿主 benchmark 评估。

门禁：不扩大 capability ceiling；只有语义和 benchmark 达标的候选可进入后续主链决策。

### P8：M2 总验收

最低完成定义：

- API、SSE、TUI 和至少一个 Web 技术验证共享同一版本化协议。
- 审批、权限、会话恢复和工件交付在客户端断线与服务重启后保持一致。
- 认证、授权、并发、限流、请求上限、路径、重放和敏感信息测试通过。
- 本地 Operator Console 与远程产品控制面边界明确。
- 全量自动化、E2E、报告、文档、memory 和本地边界提交完成；不推送远端。

## 15. 负向测试矩阵

| 类别 | 必测失败场景 |
|---|---|
| 版本/Schema | 未知 major、未知字段、类型错误、超长字段、非法枚举 |
| 认证 | 缺失、失效、伪造、轮换后旧 token、凭据出现在 URL |
| 身份 | 请求体覆盖 actor、声明 runtime/legacy、显示名冒充 identity |
| 授权 | viewer 写操作、跨资源访问、admin 绕过 Approval Broker |
| 幂等 | 同键异义、客户端超时重试、server 重启后重试、并发相同 key |
| Runtime | IPC 不可用、token/hash 不一致、第二 lease、Runtime 重启 |
| 控制 | 已终态 cancel/steer、错误 attempt/lineage、审批冲突、resume 重放 |
| SSE | Last-Event-ID 非法/过期、重复事件、乱序、慢消费者、无 terminal |
| 隐私 | token、instruction、工具参数、内部路径、异常堆栈泄露 |
| 文件 | `..`、绝对路径、符号链接逃逸、设备路径、文件名注入、hash 漂移 |
| 资源 | 超大请求、连接耗尽、无界队列、SSE 并发和速率限制 |
| Playbook | 非可信源、重定向逃逸、内容漂移、签名/hash 错误、权限升级 |

## 16. 测试与验收纪律

每个 Gate 至少执行：

1. focused 单元和负向测试。
2. Schema/OpenAPI 契约测试。
3. 受影响链路测试。
4. 断线、重启、幂等和并发 E2E。
5. 全仓 pytest、Ruff、compileall、`git diff --check`。
6. 敏感信息扫描和路径边界检查。
7. 正式工件/报告存在、可读、SHA-256 可复算。
8. 仅暂存当前 Gate 自有文件，审查 staged diff 后创建本地提交；禁止推送。

测试数字、HEAD 和当前 Gate 只维护在 `PROJECT_HANDOFF_STATUS.md`，不在本契约长期复制。

## 17. 非目标

M2 首轮不包含：

- 生产级多租户 SaaS。
- 公网部署、云编排或 Kubernetes。
- 绕过真实宿主审批的远程 BIM 写入。
- 任意代码执行或宿主自由脚本 API。
- 复制一套新的 Runtime、Session 或 Manifest。
- 大规模 M3 学术实验。
- 未经 benchmark 的第三宿主主链。
- 以 Web UI 完成度代替协议、安全和恢复验收。

## 18. 当前下一动作

在 M1 G6 工件仍缺失的前提下，M2 只执行准备态动作：

```text
冻结本契约 v0.1
→ 形成协议/威胁模型验收报告
→ 保持 DRAFT / PRE-G7 PREPARATION ONLY
→ 等待真实 Vectorworks G6 工件
→ G6/G7 关闭后将本契约升级为 ACTIVE 并进入 P0
```

若真实 G6 工件到位，立即停止 M2 准备，优先执行 G6/G7。
