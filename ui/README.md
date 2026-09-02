# openBIMAgent UI（方案 J 已集成上线 · 功能打通 · 2026-09-02）

**终版 = `prototype-j-franken.html`（方案 J），已迁入 `src/openbimagent/server/web_ui.py`，全部功能接真实端点（非演示）。**

启动：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000` → 打开 http://127.0.0.1:8000/

## 功能打通清单（本轮新增，均有测试）

| 功能 | 端点 | 说明 |
|---|---|---|
| **新建任务（真跑 Agent）** | `POST /api/v1/runs` · `GET /api/v1/runs/active` | 后台真跑 pipeline（Clarify→Planner→Orchestrator→Deliver；单并发锁 409；离线确定性模板 + MockCritic；`yes=True` 自动放行，C5 交付门不受影响）→ 会话落 `out/sessions/index.json`（demo app 已改真实索引） |
| **会话事件** | `GET /api/v1/sessions/{id}/events` | 点会话 → 线程渲染真实事件（clarify 问答 / tool_call / 子代理 custom）；运行中页面 2.5s 轮询实时追加 |
| **设置 · 模型与 API** | `GET/PUT /api/v1/settings/llm` | 基线 model/base_url/api_key 写 `config/llm_baseline.local.toml`（gitignored）；管道角色 provider keys（GLM/GEMINI/AGENTROUTER/FREETOKENFAUCET）写进程环境（即时生效）+ `.env`（持久化）；**key 只写不回显**，GET 仅返回 key_set 布尔 |
| **上传附件** | `POST/GET /api/v1/uploads` | composer 回形针真实上传，落盘 `out/uploads/` + sha256 manifest（`index.json`）；检查器「上传」面板实时列表；64MB 上限，文件名消毒 |
| **Composer 调度** | `GET /api/v1/demo/municipal-pipeline` | 普通文本回车 → 真实调度自愈求解器 → 追加工具块（converged/iterations/resolved）+ 3D 场景刷新 |
| **能力控制台** | `POST /api/v1/plugins/invoke` | 检查器内选能力 + payload JSON + 真实 invoke，结构化结果渲染 |
| **HITL 导出** | `POST /api/v1/demo/export-blender` | 批准 → `{confirm:true}` 走 prompt 策略门 → 真实回执（objects/bytes/elapsed/output_path/plan_sha256） |
| **3D 视口** | `GET /api/v1/demo/municipal-pipeline` | 真实 nodes/segments 驱动 canvas 渲染器，动态取景；自愈时间线回放；3D/平面/纵断面三视图 |
| 规则树 / 模型芯片 / 会话 | `demo/rule-tree` · `demo/runtime-info` · `sessions` | 真实数据填充 |

端点实现：`src/openbimagent/server/workbench_io.py`（设置/上传）、`src/openbimagent/server/runs.py`（运行/事件）；测试：`tests/test_workbench_io.py`（5 测）+ `tests/test_runs.py`（4 测，含一次真实 pipeline 运行），均 tmp 隔离不碰真实配置。

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

1. 改 `ui/prototype-j-franken.html`（布局/组件/渲染器/功能区块）
2. 跑 `python scratch/build_web_ui.py`：自动替换 vendor 路径、注入集成接线脚本、重写 `web_ui.py`、同步测试断言
3. `uv run pytest tests/test_m2_fastapi.py tests/test_workbench_io.py -q` + 起服目检

业务接口背景见 `API_INVENTORY.md`。
