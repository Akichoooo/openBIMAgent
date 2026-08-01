# openBIMAgent

> 自研 Agent + Vectorworks MCP + Blender MCP 的生成式建模系统。新独立开源仓库,重构自 openBIMForge。

## 定位

质量优先的建模 agent:

自然语言需求 → 追问澄清 → playbook(剧本)驱动 → 逐资产建模(禁止一次性糊整城) → **模型自己看截图、自己纠正**(双环视觉自检) → 交付 `.blend` / 英雄镜头渲染 / 漫游视频 / BIM 构件。

架构一句话:**Agent Core(Python)+ 两个 MCP server(`vectorworks-mcp` 自研、`blender-mcp` fork 改造)+ 双环视觉自检 + 可切换 playbook 模板**。

## 状态

M1 持续实现中。已具备确定性 Pipeline、Blender/Vectorworks MCP 客户端、Domain Gate，以及 Subagent Runtime v1 的 child Session、不可变 Artifact、background/status/cancel/join、跨进程 Session 索引锁、重启 rehydrate、Approval Broker、P1c `resume/steer`、P1d 控制面基础、P1e 单机 Runtime IPC 和 P1f 本地 Operator Console。`resume` 永远创建新的 request/agent/child attempt，以 `lineage_id + attempt_number` 保持谱系；稳定 `ActorRef`、`instruction_sha256` 和调用方 `idempotency_key` 保证同键同语义重试复用、同键不同语义严格冲突。旧结果只作为不可变只读上下文，禁止静默重放旧工具副作用；`steer` 仅绑定当前 queued/running attempt，在下一轮 Provider 调用前的安全边界应用。只读 `ReadOnlyControlPlane` 与 `control` CLI 不获取 Runtime lease；P1e `runtime-serve` 由唯一 lease owner 启动 loopback-only IPC，`control-write` 经 bearer token 和 ActorRef 提交 Approval/Resume/Steer/Cancel；P1f `operator-console` 用浏览器展示 attempts/approvals/resumes/steers，并在服务端代理写控制，浏览器不接触 IPC token。市政主线已新增 `compiled utility IR v1`、`MunicipalRuleSet v1.1` 和 `municipal-straight-gravity-solver v0.4.0`：Solver v0.4 限定两井一直管 DN300 混凝土污水管，确定性生成坐标、拓扑、坡度/标高/管径、覆土/井距 RuleEvidence；Pipeline 从 Domain Pack 的受信任 `knowledge/constraints.yaml` 编译带 source/canonical SHA-256 和结构化 `RuleVerification` 的规则集，障碍物输入只提交工程事实，不能自行填写或降低净距限值。系统已按 `GB 50289-2016` 第 4.1.9 条/表 4.1.9 的双 PDF 原表交叉核验结果，编译并生产执行建筑物、给水、燃气、电力和通信共 12 条水平净距规则；production 必须同时满足高置信与完整规范核验证据，不能只改 `confidence` 绕过。净距按 XY 平面实体表面水平距离计算，Z 高差不能掩盖平面净距不足；Evidence 可回溯标准身份、表号、规范副本 SHA-256、原表定位和 Rule Set hash。碰撞上下文、规则属性或例外审批事实缺失仍为 UNKNOWN；安全措施减距、路线扩展、全量水平/垂直净距、水力和生产级 VectorworksBuilder 仍待实现。通用 Loop 的 MCP/vision/deliver 接线和双宿主 E2E 也仍在推进。

## 文档地图

**Wiki 首页:`docs/README.md`**(阅读顺序、全量索引、变更日志)

- `docs/architecture/ARCHITECTURE.md` — 总体架构(先读这个)
- `docs/architecture/COMPONENTS.md` — 组件/agent/模型配置/上下文管理详设
- `docs/research/` — 调研报告(openBIMForge 审计、开源对标、GenCAD 盘点、Gemini 接力产出)
- `docs/relays/` — 接力工作流与调研协议；已执行任务提示词已清理，可由 Git 历史恢复
- `outputs/Subagent Runtime v1完整实施与验收报告.md` — Runtime P0–P1f 合并实施与验收报告

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

在仓库根目录创建 `.env`(providers profile 与模型 key;**禁止提交到 git**):

```bash
# 选填:official / test / faucet；缺省 official
OPENBIMAGENT_PROFILE=faucet

# 只配置实际使用的 provider；禁止提交真实 key
FREETOKENFAUCET_API_KEY=...
GLM_API_KEY=...
GEMINI_API_KEY=...
AGENTROUTER_API_KEY=...
```

> 无 `.env` 也可跑:planner / builder 走确定性模板(离线冒烟),critic 走 MockCritic;只是不调真实 LLM。

### 3. 跑全流程

```bash
# 单资产英雄镜头(最小闭环;需 Blender 在跑并装好 blender-mcp addon)
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md

# 离线冒烟(不连 Blender,orchestrator 直接 ESCALATE;验证装配链路)
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md \
  --no-blender --no-hitl --yes

# 跳过所有审批门(自动化场景)
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md --yes
```

P1d 只读控制面示例（可与活跃 Runtime 并行查询，不触发副作用）：

```bash
uv run python -m openbimagent control attempts --sessions-dir out/sessions --json
uv run python -m openbimagent control lineage <lineage_id> --sessions-dir out/sessions --json
uv run python -m openbimagent control approvals --pending-only --sessions-dir out/sessions --json
uv run python -m openbimagent control resumes --sessions-dir out/sessions --json
uv run python -m openbimagent control steers --request-id <request_id> --sessions-dir out/sessions --json
```

P1e 单机 Runtime IPC 写控制示例：

```bash
# 终端 1：启动唯一 Runtime lease owner 与 loopback IPC
uv run python -m openbimagent runtime-serve --sessions-dir out/sessions --artifacts-dir out/subagents

# 终端 2：健康检查、审批、恢复、导向和取消
uv run python -m openbimagent control-write ping --actor-id human:operator --idempotency-key ping-001
uv run python -m openbimagent control-write approve <approval_id> --actor-id human:operator --idempotency-key approval-001 --reason reviewed
uv run python -m openbimagent control-write resume <request_id> --actor-id human:operator --idempotency-key resume-001 --instruction "检查当前状态后继续"
uv run python -m openbimagent control-write steer <request_id> --actor-id human:operator --idempotency-key steer-001 --instruction "下一轮先核对外部状态"
uv run python -m openbimagent control-write cancel <request_id> --actor-id human:operator --idempotency-key cancel-001
```

> IPC v1 仅绑定 `127.0.0.1`，discovery 和 token 位于 `sessions/_runtime`；原始 token 不进入 Session、RuntimeState 或 CLI 参数。IPC 不是远程 API，不应暴露端口或共享 sessions 目录。调用方必须为每个逻辑写操作稳定复用 `actor_id + idempotency_key`。

P1f 本地 Operator Console（终端 1 的 Runtime 保持运行）：

```bash
# 终端 2：启动独立 Console；它不持有 Runtime lease
uv run python -m openbimagent operator-console \
  --sessions-dir out/sessions \
  --actor-id human:operator \
  --display-name "Local Operator"

# 浏览器打开命令输出的 http://127.0.0.1:8765/
```

Console 展示 attempts、pending approvals、resumes 和 steers，并代理 Ping、Approve/Reject、Resume、Steer、Cancel。它只监听 `127.0.0.1`，强制 Host/Origin/CSRF、JSON Content-Type、64 KiB 请求上限和 CSP 安全头，不提供 CORS。ActorRef 在服务端启动时固定；浏览器无法声明 actor，也不会收到 `control-ipc.token`。该 Console 是单机操作面板，不是远程管理 API。

常用参数:

| 参数 | 说明 |
| --- | --- |
| `--playbook <path>` | playbook.md 路径(必填) |
| `--out <dir>` | 产物根目录(默认 `./out`) |
| `--sessions-dir <dir>` | sessions 目录(默认 `<out>/sessions`) |
| `--yes` | 跳过所有审批门(MCP 写操作 / execute_code / deliver) |
| `--no-blender` | 不连 Blender(离线冒烟/测试) |
| `--no-hitl` | run 结束后不进 HITL REPL(脚本场景) |
| `--blender-transport <stdio\|socket>` | Blender MCP 传输层(stdio 主 / socket 回退,默认 stdio) |
| `--blender-port <int>` | Blender MCP socket 端口(默认 9876) |
| `--cameras <name>...` | Blender batch_render 相机列表 |
| `--turntable-target <name>` | turntable 目标对象名 |
| `--turntable-frames <int>` | turntable 帧数(默认 4) |
| `--image-size <int>` | 渲染图尺寸(默认 512) |

每批结束会打印 HTML 验收页路径(`[HTML 验收页] batch=... → <path>`)。

### 4. HITL 斜杠命令

`run` 结束后进入 HITL REPL(除非 `--no-hitl`),接受斜杠命令:

| 命令 | 说明 |
| --- | --- |
| `/sessions` | 列出多会话(按 last_active 倒序) |
| `/tree <event_id>` | 从当前会话分支到该事件(回退续跑) |
| `/export [out_path]` | 导出当前会话 JSONL(默认 `<session_id>.jsonl`) |
| `/help` | 显示斜杠命令列表 |
| `/exit` `/quit` | 退出 HITL REPL |
| `/undo` `/redo` `/retry` `/compact` `/model` | M1 桩(暂未实现) |

中断:`Ctrl+C` 落 checkpoint 事件到 session,可 `/tree` 回退续跑。

### 5. 其他子命令

```bash
# 列出多会话
uv run python -m openbimagent sessions

# 从某会话的某事件分支(回退续跑)
uv run python -m openbimagent tree <session_id> <event_id>

# 导出会话 JSONL
uv run python -m openbimagent export <session_id> [out_path]
```

## 与前身 openBIMForge 的关系

本仓库独立开发。`../openBIMForge` 作为资产参考库:视觉环、Vectorworks 执行链、编排器、trace 等成熟代码按需抽取移植,不整体继承。v2(`agent_core/`)方向已证伪,不继承。
