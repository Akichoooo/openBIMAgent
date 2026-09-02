# openBIMAgent UI 原型（审核稿 · Codex × 3D 三方案）

日期：2026-09-02 · 状态：**待审核** —— 审核通过前不动 `web_ui.py`。
（旧 A–H 八套方案已清理，git 历史中可回溯；本目录只保留同一布局的三种组件实现）

## 布局（三方案共用）：Codex 风格 × 3D 视口英雄区

- 左图标轨（任务/视口/规则/插件/设置）→ Codex 式任务列表（会话状态点 + 宿主连接 + 模型芯片）
- **中央 3D 视口为英雄区**：零依赖自绘 canvas 渲染器（拖拽旋转 / 滚轮缩放 / Shift 平移 / 垂直夸大 ×3），真实渲染 6 井 5 段 IR 几何 + 建筑/燃气障碍 + 净距圈；3D / 平面 / 纵断面三视图
- 视口底部**自愈时间线回放**：红色碰撞路径（净距 1.6m<2.5m）→ 膨胀半径重路由 → 绿色收敛，4.2s 动画可点步
- 右侧 408px Codex 式线程（可收起）：流式工具块 → IR 工件卡 → HITL 琥珀审批（prompt 策略门）→ 确认弹层 → 交付回执；检查器 = 规则树 12 条 / Compiled IR / 插件 + 能力控制台
- 数据诚信：VLM 评分等演示值显式标注；深链 `#plan` / `#prof` / `#step2` 直达指定视图（审核截图用）

## 三方案差异 = 组件/动效技术栈（业务与布局完全一致）

| 文件 | 技术栈 | 许可 | 离线可用 | 特点 |
|---|---|---|---|---|
| `prototype-i-codex3d.html` **I · vanilla + Motion** | 纯手写 CSS + [Motion](https://motion.dev/)（CDN ESM） | MIT | ✅（Motion 失败自动降级，无感） | 零依赖基线；动效=列表 stagger / 展开高度动画 / 弹层 scale-fade |
| `prototype-j-franken.html` **J · Franken UI** | [Franken UI](https://franken-ui.dev/) 2.1.2 core+utilities（CDN） | MIT | ❌ 需联网 | **shadcn zinc-dark 官方 token 皮肤**（HSL 变量直取），主按钮为 shadcn 白色 primary；最像 shadcn 观感 |
| `prototype-k-shoelace.html` **K · Shoelace** | [Shoelace](https://shoelace.style/) 2.20.1 autoloader + dark 主题（CDN） | MIT | ❌ 需联网 | **真组件行为**：sl-select / sl-switch / sl-tab-group / sl-drawer（设置）/ sl-dialog（HITL 确认）/ sl-alert（审批与回执）/ sl-tooltip |

> J/K 的库均可 vendor 到本地（MIT 允许再分发），迁入 `web_ui.py` 时由 FastAPI 本地伺服即可摆脱 CDN；I 完全不依赖网络。

## 审核步骤建议

1. 三个文件各打开一遍（J/K 需联网加载 CDN）：等首轮流式回合跑完（约 5s，自动播自愈动画）
2. 逐项体验：拖转 3D 视口 → 切 3D/平面/纵断面 → 点「批准导出」走 HITL 全链路 → 右栏切「检查器」→ 输入 `/` 试斜杠命令
3. 对比重点：**J 看皮肤质感**（shadcn zinc 白按钮 vs I 的蓝调）· **K 看组件交互**（下拉/开关/抽屉/对话框的开合动效）
4. 结论 + 批注发我（如「K 的组件 + J 的 token 皮肤」混搭也可——两者不冲突，可合并）

## 审核截图（scratch/ui-review/）

- `shot_i2.png` 方案 I · `shot_j.png` 方案 J · `shot_k2.png` 方案 K（均 1680×1000 实渲）

## 审核通过后的集成步骤（预计一次会话完成）

1. 选定方案的 HTML/CSS/JS 整体迁入 `web_ui.py`（保留既有端点接线函数名 `loadSelfHealingDemo/loadRuleTree/exportHost/handleChat`）；若选 J/K 则先把库文件 vendor 进 `src/openbimagent/server/static/`
2. mock 全部替换为真实端点调用（映射表见 `API_INVENTORY.md` §二）
3. canvas 3D 渲染器改由真实 IR 数据驱动（nodes/segments/resolved_violations/timeline 端点字段已对齐）
4. 跑全量测试 + 起服验收
