# openBIMAgent 前端（React 19 + TypeScript + Vite + Tailwind + shadcn 风格组件）

现代工作台前端，构建产物 `dist/` 由后端优先伺服（`src/openbimagent/server/web_ui.py` 检测到
`frontend/dist/index.html` 时挂载 SPA，否则回退旧单文件 `ui/workbench.html`）。

## 开发

```bash
pnpm install
pnpm dev      # http://127.0.0.1:5173，/api 代理到 127.0.0.1:8001
pnpm build    # tsc -b && vite build → dist/（后端伺服的就是这份产物）
pnpm lint     # oxlint
```

后端启动：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8001`。

## 结构

- `src/services/api.ts` — 类型化后端客户端（唯一 API 入口；GET/变更均带 X-Request-ID 与 Bearer token）
- `src/components/layout/` — Header（视图切换/真机导出 HITL/主题）、Sidebar（工作区选择器+会话列表）、WorkspacePicker（选择项目/打开文件夹/不在项目中工作）、NewTaskDialog
- `src/components/chat/ChatThread.tsx` — 对话流（SSE 事件流优先、断线回退轮询）、HITL 审批卡、停止运行、会话导出（md/jsonl）、真实 IR 查看
- `src/components/viewport/CanvasViewport.tsx` — 3D/2D/纵断面三视图画布（无 IR 数据时为空态，不渲染示例几何）
- `src/components/settings/` — 设置弹层：外观 / 模型供应商 / 工具集与宿主 / MCP 服务器 / 插件与能力 / 长期记忆 / 技能库 / 归档 / 用量 / 规则树 / 上传附件
- `src/components/ui/` — shadcn 风格原语（Radix + Tailwind），新 UI 一律复用这套组件

## 工作区（选择项目）

注册表方案：`POST /api/v1/workspaces` 登记项目文件夹（不存在则创建），
`POST /api/v1/workspaces/current` 切换；会话 index 条目带 `workspace` 字段，
侧栏按当前工作区过滤。注册表落盘 `out/workspaces.json`
（`OPENBIMAGENT_WORKSPACES_FILE` 可覆盖）。数据不做目录级隔离。
