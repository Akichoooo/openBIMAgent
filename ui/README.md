# openBIMAgent UI

**权威源已迁移（2026-09-07 起）= `frontend/`（React 19 + TS + Vite + Tailwind + shadcn 风格组件），`pnpm build` 产出 `frontend/dist/`，后端检测到 dist 即优先伺服 SPA。**

`ui/workbench.html`（经 `python tools/build_web_ui.py` 生成 `src/openbimagent/server/web_ui.py`）保留为**无 dist 时的回退**与历史参照，不再新增功能；`prototype-j-franken.html` 等 A–L 原型仅为历史留档。

启动：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000` → 打开 http://127.0.0.1:8000/

## 布局原则（2026-09-03 收敛，对齐 ZCode/Codex 的"页面=任务态、设置=配置态"分工）

- **页面（高频任务态）**：单侧栏（折叠图标+Ctrl+B / 新任务 / 会话按 playbook 文件夹分组折叠，单行截断 + 悬浮详情卡）、中央 3D 视口、右侧纯对话栏（图标折叠，检查器已迁出）、composer 模型芯片（provider ▾ 模型 两级下拉，「管理模型」跳设置）
- **设置（ZCode 式全屏页）**：模型与 API（provider 双栏：左 provider 列表/右 Base URL 只读 + Key + 模型列表「设为基线」，切模型自动同步 base_url 并接 provider env key）｜ 工具集预设（选中即生效）｜ 记忆 ｜ 技能 ｜ MCP 服务器（内置宿主 Blender/VW + 第三方）｜ 数据与审查（规则树/插件与能力/IR 查看/上传/用量/归档——原右栏检查器迁入，`openInspector(p)` 重定向到对应分区）
- **已删除**：左侧图标栏、侧栏底部模型行与宿主芯片、`setPop` 假数据弹层、视口时间线大播放键（与「自愈回放」重复）、右栏检查器 tab 与重复的 ⟨ 折叠按钮

## 风格一致性规范（2026-09-05 全局审计后确立）

- **圆角 token**：全局唯一入口 `--r:8px`（此前 `--r:var(--radius)` 引用了未定义 token，所有 `var(--r)` 圆角静默变直角——已修复并在浏览器实测全部解析为 8px）；药丸/圆点一律 999px；小徽章 4px。
- **主行动按钮**：一律 `.btn-pri`（`var(--acc)` 主题色、8px 圆角、12.5px/600 字重）。原 5 处 Tailwind 类按钮（HITL 批准/新任务启动/能力控制台运行/审批卡批准/确认执行）已全部收编。
- **模型设置分区**：`.ms-btn-save`/`.ms-add-mdl-head-btn` 从黑白硬编码改为 `var(--acc)` 主题色；启用/禁用 toggle 从 Tailwind emerald 改 `var(--grn)`；`arch-tag` 同步。**页面内不再有绕过主题 token 的颜色字面量**。
- **空态/占位**：统一 `.emptybox` 组件（dashed 边框卡 + 标题/说明双行），替代 11 处内联 style 空态（用量/上传/记忆/会话/交付产物/模型列表/加载中占位/选择供应商引导）。
- **表单提示**：统一 `.hint` 组件（11px ink3 + `code` chip 样式）；模型设置的 Base URL/API 格式/API Key/名称四个字段都有带真实示例的 hint 文案。
- **分区标题**：`insp-sec-t`/`usg-sec-t`/`.sp-t` 统一 10.5px mono + uppercase + letter-spacing .08em。
- **API 格式选项**：三项都带协议路径后缀说明（Chat Completions · OpenAI 兼容 / OpenAI Responses · /v1/responses / Anthropic Messages · /v1/messages），保存前校验 Base URL（http/https 开头）与 API Key 非空。
- **视口三视图口径（2026-09-05 修复）**：平面图按「节点+绕行折线+障碍物净距圈」全景包围盒居中适配（此前宽场景顶贴、网格硬编码 0..150/-20..40、碰撞注记死坐标 X(24)/Y(16) 全部修正，网格步长 10/25/50m 自适应）；空态按视图绘制（平面=正交网格、纵断面=标尺横线，此前平面空态误用 3D 相机投影呈歪斜网格）；纵断面实现**真垂直夸大**（工程制图口径：纵向 px/m = 横向 ×20 开 / 1:1 关，比例尺标注实时切换）；垂直夸大/自旋/网格按钮按视图语义启停（平面下夸大置灰）；初始时间线态=收敛终态（此前 tlStep=0+bendT=1 标签与几何互相矛盾）。
- 图标尺寸规范：行内 SVG 一律 13-16px 级（cbtn/ms-eye/close/del 16→14 归一）。

## 功能打通清单（本轮新增，均有测试）

| 功能 | 端点 | 说明 |
|---|---|---|
| **新建任务（真跑 Agent）** | `POST /api/v1/runs` · `GET /api/v1/runs/active` | 后台真跑 pipeline（Clarify→Planner→Orchestrator→Deliver；单并发锁 409；离线确定性模板 + MockCritic）→ 会话落 `out/sessions/index.json`（demo app 已改真实索引） |
| **审批中心（P0）** | `GET /api/v1/approvals` · `POST /api/v1/approvals/{id}/decide` | pipeline 触审批门（execute_code/deliver 前）**挂起运行线程**；前端 3s 轮询，对话 tab 琥珀角标 + 线程内 HITL 卡（参数可见/挂起时长/**附带指令输入** → 写入决策回执，steer 语义）；30min 超时失败关闭。**已撤掉 yes=True 自动放行** |
| **SSE 实时流（P1）** | `GET /api/v1/sessions/{id}/events/stream` | 回放既有事件后 0.6s 间隔持续推送新增（keepalive），运行结束自动 drain+关闭；前端 EventSource，断开自动回退轮询；10min 连接上限 |
| **素材归档（P2 · 事件溯源+资产沉淀）** | `GET /api/v1/archive` | 每次运行结束自动把关键工件（IR/规则集/门禁报告/PLAN/manifest）**只增不改**拷入 `domain_packs/<pack>/assets/auto_archive/<session>/` + sha256 index.json（gitignored）；检查器「归档」面板可见 |
| **用量仪表盘（P3 · 2026-09-05 重做）** | `GET /api/v1/usage` | **真实调用流水账** `out/usage_log.jsonl`（append-only；chat 端点每笔真实 LLM 调用 + CLI pipeline 调用都落账，含 latency/来源/会话；`OPENBIMAGENT_USAGE_LOG` 可隔离）。聚合返回总量/按日(14天)/按模型/最近明细；前端图形化：4 统计卡 + 14 日双色柱状图（上=输入/下=输出，hover 明细）+ 模型占比堆叠条 + 最近调用表。空账时回退旧 `usage_summary.json` 快照；账本不记消息内容与 key |
| **会话分支（P4）** | `POST /api/v1/sessions/{id}/fork` | 会话项 ⑂ 按钮 → `/tree` branch/fork 主干链到新会话（fork 写 forked_from 元数据供续跑检测）；审批附带指令为 steer 语义在审批门生效（运行时中途 steer 属 Subagent Runtime 路径，assembly 顺序流不接） |
| **会话事件** | `GET /api/v1/sessions/{id}/events` | 点会话 → 线程渲染真实事件（clarify 问答 / tool_call / 子代理 custom）；运行中页面 2.5s 轮询实时追加 |
| **设置 · 模型与 API** | `GET/PUT /api/v1/settings/llm` | 基线 model/base_url/api_key 写 `config/llm_baseline.local.toml`（gitignored）；管道角色 provider keys（GLM/GEMINI/AGENTROUTER/FREETOKENFAUCET）写进程环境（即时生效）+ `.env`（持久化）；**key 只写不回显**，GET 仅返回 key_set 布尔 |
| **上传附件** | `POST/GET /api/v1/uploads` | composer 回形针真实上传，落盘 `out/uploads/` + sha256 manifest（`index.json`）；检查器「上传」面板实时列表；64MB 上限，文件名消毒 |
| **Composer 调度** | `GET /api/v1/demo/municipal-pipeline` | 普通文本回车 → 真实调度自愈求解器 → 追加工具块（converged/iterations/resolved）+ 3D 场景刷新 |
| **能力控制台** | `POST /api/v1/plugins/invoke` | 检查器内选能力 + payload JSON + 真实 invoke，结构化结果渲染 |
| **HITL 导出** | `POST /api/v1/demo/export-blender` | 批准 → `{confirm:true}` 走 prompt 策略门 → 真实回执（objects/bytes/elapsed/output_path/plan_sha256） |
| **3D 视口** | `GET /api/v1/demo/municipal-pipeline` | 真实 nodes/segments 驱动 canvas 渲染器，动态取景；自愈时间线回放；3D/平面/纵断面三视图 |
| **对话主循环（流式）** | `POST /api/v1/chat/stream` | composer 普通文本 → 基线 LLM **真流式**（SSE：meta → reasoning*/delta* → usage? → done\|error）。dialect 层新增 `stream_openai_completions` 逐行生成器（复用 WAF 检测/delta 解析）；仅连接期瞬时故障有界重试（正文一旦流出不重投）；中断保留已生成内容并成对落盘（对齐 Codex/ZCode）；reasoning 思维链流式展示但不入会话。前端 fetch-ReadbleStream 逐字渲染 + 光标 + ⏹ 停止按钮接管（`/api/v1/chat` 非流式保留为兼容口径） |
| 规则树 / 模型芯片 / 会话 | `demo/rule-tree` · `demo/runtime-info` · `sessions` | 真实数据填充 |
| **技能库（P0-1）** | `GET /api/v1/skills` · `POST /skills/invoke` · `POST /skills/candidates/approve` | 斜杠 `/skills` 或 `#skills` 深链：SKILL.md 目录（渐进披露，调用才给正文）；自蒸馏候选列表 + 人工批准转正 |
| **会话全文检索（P0-2）** | `GET /api/v1/sessions/search` | 斜杠 `/recall 关键词` 或 `#recall=词` 深链：FTS5 + CJK bigram，命中卡可溯源跳会话 |
| **宿主 Supervisor（P0-3）** | `GET /api/v1/hosts` · `POST /hosts/{id}/restart` | 侧栏宿主芯片真实状态（up/down/restarting/external）；Blender down 且配置 exe/cmd 时显示「重启」（有界退避）；VW 恒 external 不伪探测 |
| **工具集预设（P0-3）** | `GET/PUT /api/v1/toolset` | 设置弹层切换 minimal（仅 solver）/modeling/full；清单可见面 + invoke 调用门双层过滤 |
| **长期记忆（P0-4）** | `GET /api/v1/memory` · `POST /memory/record` | 设置弹层查看 MEMORY/USER 末 N 条；写入弹确认（prompt 策略门 confirm 语义），片段注入新任务上下文 |
| **模型切换（composer）** | `GET /api/v1/settings/models` · `PUT /settings/llm` | composer 模型芯片点击出下拉：models.toml 真实清单（provider/vision 标注 + 当前 ✓），点选即切基线模型；「管理模型」跳设置弹层配 key/base_url |

端点实现：`src/openbimagent/server/workbench_io.py`（设置/上传/宿主/工具集/记忆/技能）、`src/openbimagent/server/runs.py`（运行/事件/检索/归档）、`src/openbimagent/server/approvals.py`（审批中心）、`src/openbimagent/server/chat.py`（对话主循环）、`src/openbimagent/server/usage_ledger.py`（用量流水账）；测试：`tests/test_workbench_io.py` + `tests/test_runs.py` + `tests/test_approvals.py` + `tests/test_skills.py` + `tests/test_session_search.py` + `tests/test_host_supervisor.py` + `tests/test_memory.py` + `tests/test_chat.py` + `tests/test_usage_ledger.py`，均 tmp 隔离不碰真实配置。

> 已知边界：municipal_utility 已补 pack 内默认入参 `solver_input.default.json`，Web 运行可越过 domain_gate 抵达 deliver 审批门。

> 端口注意：若 8000 被本机其他程序占用（Windows Hyper-V 保留段常见），换 `--port 8001`。

## 终版技术栈（方案 J）

- **Franken UI 2.1.2**：shadcn zinc-dark 官方 HSL token 皮肤 + 工具类主按钮
- **Motion**：stagger/rise-in/scale-fade 微动效
- **全部 vendor 本地**：`src/openbimagent/server/static/vendor/`（franken + motion，MIT），`/static` 挂载，**零外网依赖**
- 3D 视口为自绘 canvas 渲染器（无 Three.js）

## 历史方案（审核留档）

- `prototype-i-codex3d.html` 方案 I：vanilla + Motion 零依赖基线
- `prototype-k-shoelace.html` 方案 K：Shoelace 组件版（CDN）
- `prototype-l-integrated.html` 方案 L：K 组件 + J 皮肤合并版（本地 vendor；**注意 file:// 打开会因 CORS 拦截本地 ES module 而不渲染组件，需经 HTTP**）
- 旧 A–H 八套已删除（git 历史可回溯）

## 二次修改 UI 的流程

1. 改 `ui/workbench.html`（布局/组件/渲染器/接线脚本全部在这一个文件里）
2. 跑 `python tools/build_web_ui.py` 重建 `web_ui.py`
3. `uv run pytest tests/test_m2_fastapi.py tests/test_workbench_io.py -q` + 起服目检

业务接口背景见 `API_INVENTORY.md`。
