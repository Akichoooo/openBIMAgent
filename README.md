# openBIMAgent

> 自研 Agent + Vectorworks MCP + Blender MCP 的生成式建模系统。新独立开源仓库,重构自 openBIMForge。

## 定位

质量优先的建模 agent:

自然语言需求 → 追问澄清 → playbook(剧本)驱动 → 逐资产建模(禁止一次性糊整城) → **模型自己看截图、自己纠正**(双环视觉自检) → 交付 `.blend` / 英雄镜头渲染 / 漫游视频 / BIM 构件。

架构一句话:**Agent Core(Python)+ 两个 MCP server(`vectorworks-mcp` 自研、`blender-mcp` fork 改造)+ 双环视觉自检 + 可切换 playbook 模板**。

## 状态

设计阶段。当前仅有调研与架构文档,原型待审核后开工。

## 文档地图

**Wiki 首页:`docs/README.md`**(阅读顺序、全量索引、变更日志)

- `docs/architecture/ARCHITECTURE.md` — 总体架构(先读这个)
- `docs/architecture/COMPONENTS.md` — 组件/agent/模型配置/上下文管理详设
- `docs/research/` — 调研报告(openBIMForge 审计、开源对标、GenCAD 盘点、Gemini 接力产出)
- `docs/relays/` — 接力工作流 + 待执行的 GLM/Gemini 提示词

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

在仓库根目录创建 `.env`(providers profile 与模型 key;**禁止提交到 git**):

```bash
# 选填:指定 providers profile(见 config/profiles/*.yaml;缺省走 default)
OPENBIMAGENT_PROFILE=default

# 模型 API key(按实际使用的 provider 填,只配你用的那个)
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
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
