# openBIMAgent

> 自研 Agent + Vectorworks MCP + Blender MCP 的生成式 BIM/场景构建系统。

自然语言需求 → 追问澄清 → 领域规则与 Solver → 版本化 Compiled IR → Blender/Vectorworks typed 执行 → 双宿主语义验证 → IFC/IDS/RuleEvidence → 不可变 Artifact Manifest 与可恢复审计。

架构一句话：**Agent Core（Python）+ Domain Pack/确定性 Solver + 两个 typed MCP server（`vectorworks-mcp` 自研、`blender-mcp` fork 改造）+ 语义/视觉双重验证 + 可审计交付与恢复**。

## 特性

- **双宿主 typed 执行**：Blender 5.2 + Vectorworks 2024，同一份 `CompiledUtilityIR` 确定性生成，双宿主语义一致性验证
- **确定性 Solver**：重力流、管网拓扑、路线、水力计算确定性求解，LLM 只出语义、Solver 出坐标
- **开放 BIM 交付**：IFC4X3、IDS 1.0、RuleEvidence、不可变 Artifact Manifest
- **失败恢复**：checkpoint/resume、幂等重放、审计追踪
- **M2 产品化**：FastAPI 只读服务、SSE 事件流、受控写控制、Web 管理界面
- **可复现评测**：B1-B10 benchmark，确定性 canonical hash 可复算

## 安装

```bash
# 克隆
git clone https://github.com/Akichoooo/openBIMAgent.git
cd openBIMAgent

# 安装依赖 (需要 uv)
uv sync
```

### 环境变量

在仓库根目录创建 `.env`（**禁止提交到 git**）：

```bash
# 选填:official / test / faucet；缺省 official
OPENBIMAGENT_PROFILE=faucet

# 只配置实际使用的 provider；禁止提交真实 key
GLM_API_KEY=...
GEMINI_API_KEY=...
AGENTROUTER_API_KEY=...
```

> 无 `.env` 也可跑：planner / builder 走确定性模板（离线冒烟），critic 走 MockCritic；只是不调真实 LLM。

## 快速开始

### 跑全流程

```bash
# 单资产英雄镜头（最小闭环；需 Blender 在跑并装好 blender-mcp addon）
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md

# 离线冒烟（不连 Blender，走确定性模板）
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md \
  --no-blender --no-hitl --yes
```

### 启动 M2 服务

```bash
# 启动只读 API + Web 管理界面
uv run python -m openbimagent server --sessions-dir out/sessions

# 浏览器打开 http://127.0.0.1:8765/
```

### 运行测试

```bash
# 全仓测试
uv run pytest tests/ -q

# 单个测试
uv run pytest tests/test_cli.py -q -x

# 代码检查
uv run ruff check src/
```

## 目录结构

```
src/openbimagent/
├── assembly/          # 装配管线：Blender/Vectorworks typed plans, semantic snapshot
├── utility/           # 市政 Solver：管网拓扑、路线、水力、规则证据
├── orchestrator/      # Subagent Runtime, IPC, control plane, approvals
├── deliver/           # IFC/IDS 交付, ArtifactManifest, deliver gate
├── vision/            # VLM critic, SCAD/render loops, rubric
├── session/           # Session JSONL store, checkpoint/resume
├── providers/         # 多厂家 LLM registry
└── server/            # M2 服务：FastAPI, SSE, 受控写, Web UI
mcp_servers/
├── blender_mcp/       # Blender MCP server (fork 改造)
└── vectorworks_mcp/   # Vectorworks MCP server (自研)
domain_packs/          # 领域专家包
agents/                # 子代理角色定义
schemas/               # JSON Schema 协议
tests/                 # 测试
docs/                  # 文档
```

## 文档

- **实时状态**：`docs/architecture/PROJECT_HANDOFF_STATUS.md`
- **架构总览**：`docs/architecture/ARCHITECTURE.md`
- **组件详设**：`docs/architecture/COMPONENTS.md`
- **项目任务流**: `docs/architecture/PROJECT_MASTER_WORKFLOW.md`
- **M1 执行契约**：`docs/architecture/M1_EXECUTION_CONTRACT.md`
- **M1.5 执行契约**：`docs/architecture/M1_5_EXECUTION_CONTRACT.md`
- **M2 执行契约**：`docs/architecture/M2_EXECUTION_CONTRACT.md`

## 里程碑

| 里程碑 | 状态 | 说明 |
|---|---|---|
| **M1 G1-G5** | ✅ | typed plan、Manifest、SemanticSnapshot、IFC/IDS、RuleEvidence、Domain Gate、恢复 |
| **M1 G6** | ✅ | 真实 Vectorworks 2024 + Blender 5.2 双宿主验收通过 |
| **M1 G7** | ✅ | 全仓工程验收 |
| **M1.5** | ✅ | 市政管网 Solver、B1-B10 benchmark |
| **M2 P0-P7** | ✅ | FastAPI、SSE、受控写、Web UI、远程 Playbook 安全 |
| **M3** | ✅ | 实验报告、可复现评测 |

## License

MIT License