# openBIMAgent UI（方案 L 已集成上线 · 2026-09-02）

**终版 = `prototype-l-integrated.html`（方案 L），已迁入 `src/openbimagent/server/web_ui.py` 并接全部真实端点。**

启动：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000` → 打开 http://127.0.0.1:8000/

> 注意：方案 L 直接双击打开（file://）会因浏览器 CORS 拦截本地 ES module 而无法加载 Shoelace 组件——**必须经 HTTP 访问**（集成态由 FastAPI 伺服，无此问题）。

## 终版技术栈（J + K 合并）

- **Shoelace 2.20.1**（Web Components）：sl-select / sl-switch / sl-tab-group / sl-drawer / sl-dialog / sl-alert / sl-button 等真组件交互
- **Franken UI 2.1.2**：shadcn zinc-dark 官方 HSL token 驱动整体皮肤
- **Motion**：列表 stagger / 卡片 rise-in / 弹层 scale-fade
- **全部 vendor 到本地**：`ui/vendor/`（评审用副本）+ `src/openbimagent/server/static/vendor/`（生产副本，120 文件 1.6MB，MIT 许可），**零外网依赖**
- 3D 视口为自绘 canvas 渲染器（零依赖，无 Three.js）：真实 IR nodes/segments 驱动、动态取景、自愈时间线回放、3D/平面/纵断面三视图

## 真实数据接线（页尾 bootstrap 脚本）

| UI 区块 | 端点 |
|---|---|
| 3D 视口 + 工具块数值 | `GET /api/v1/demo/municipal-pipeline`（nodes/segments/resolved_violations/iterations_spent） |
| 规则树 | `GET /api/v1/demo/rule-tree` |
| 模型芯片 | `GET /api/v1/demo/runtime-info` |
| 会话列表 | `GET /api/v1/sessions` |
| 能力控制台下拉 | `GET /api/v1/plugins`，运行 → `POST /api/v1/plugins/invoke` |
| HITL 批准导出 | `POST /api/v1/demo/export-blender`（`{confirm:true}` 走 prompt 策略门，回执填充交付卡） |

端点失败时各区块保留内置演示数据兜底（演示值已显式标注）。

## 历史方案（审核留档）

- `prototype-i-codex3d.html` 方案 I：vanilla + Motion，零依赖蓝调基线
- `prototype-j-franken.html` 方案 J：Franken UI shadcn zinc 皮肤（CDN 版）
- `prototype-k-shoelace.html` 方案 K：Shoelace 组件（CDN 版）
- 旧 A–H 八套已删除（git 历史可回溯）

## 二次修改 UI 的流程

1. 改 `ui/prototype-l-integrated.html`（布局/组件/渲染器）
2. 跑 `python scratch/build_web_ui.py`：自动替换 vendor 路径、注入集成接线脚本、重写 `web_ui.py`、同步测试断言
3. `uv run pytest tests/test_m2_fastapi.py -q` + 起服目检

业务接口背景见 `API_INVENTORY.md`。
