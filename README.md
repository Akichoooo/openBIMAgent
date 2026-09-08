# openBIMAgent: 生成式三维设计与市政工程智能体平台

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![React 19](https://img.shields.io/badge/React-19-61dafb.svg)](https://react.dev/)
[![TailwindCSS 3.4](https://img.shields.io/badge/TailwindCSS-3.4-38b2ac.svg)](https://tailwindcss.com/)
[![Three.js](https://img.shields.io/badge/Three.js-r185-black.svg)](https://threejs.org/)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Standard-purple.svg)](https://modelcontextprotocol.io/)

**基于多模态大模型与物理自愈微内核的工业级生成式 BIM / 市政基础设施自主设计智能体系统**

[项目特点](#-核心特性) • [系统架构](#-系统架构) • [快速开始](#-快速开始) • [现代工作台-ui](#-现代工作台-ui开发) • [学术评测基准](#-学术评测与基准套件) • [技术文档](#-文档与学术支撑)

</div>

---

## 📖 项目简介

**openBIMAgent** 是一套面向建筑信息模型（BIM）与市政基础设施三维正向设计的生成式智能体（Generative Agent）平台。

传统生成式大模型在三维空间几何、高程水力放样与刚性工程规范（如国标 GB 50289）中存在不可避免的**空间幻觉与物理失真**。openBIMAgent 采用**神经-符号协同（Neuro-Symbolic）**范式，践行 **“LLM 规划设计语义，确定性 Solver 计算物理与几何坐标”** 的铁律，打造集**多模态长思考推理、刚性规则护栏、双宿主 CAD 执行与闭环视觉质检（VLM Critic）**于一体的生产级设计系统。

---

## ✨ 核心特性

- 🧠 **多模态与统一思考抽象（Unified Reasoning）**
  - 原生适配小米 **MiMo-v2.5**（多模态视觉 + `thinking.type` 自适应思考控制）、**SenseNova 6.8-flash-lite**、**DeepSeek-R1 / V4**、**GLM-5.2**、**Qwen3.8-max**、**Kimi-k3** 等前沿模型；
  - 封装统一 5 档思考抽象（`off` / `low` / `medium` / `high` / `max`），自动适配各家 Wire 协议（如 `thinking.type`、`reasoning_effort`、`thinking_budget` 等）。
- 📐 **确定性符号物理与水力求解微内核（Deterministic Solver）**
  - 重力流雨污水管网、高程坡降标高、覆土防冻计算、避让寻路算法全程由符号水力微内核确定性求解；
  - 彻底规避纯黑盒大模型的浮点数漂移与几何穿透幻觉。
- 🖥️ **现代全栈工作台 UI（Modern Full-Stack Workbench）**
  - 基于 **React 19 + TypeScript + TailwindCSS + Radix UI + Three.js** 构建的现代化工程工作台；
  - 集成实时 3D 几何投影、多工作空间隔离切换、审批中心（HITL 人机协同审批门）、真流式 SSE 思维链打字机渲染、MCP 服务与模型统一管理。
- 🔌 **标准双宿主 typed 执行（MCP Ecosystem）**
  - 基于 Anthropic **Model Context Protocol (MCP)** 标准总线；
  - 支持 **Blender 5.2**（几何装配、物理渲染）与 **Vectorworks 2024**（BIM 实体交付）双宿主 typed 执行与语义一致性比对。
- 🔬 **学术级基准评测套件（Academic Benchmark Suite）**
  - **架构消融（Ablation）**：严格验证确定性微内核、反思闭环与领域护栏对任务成功率的贡献；
  - **Pass@k 可靠性**：长时序多步工程设计任务下的成功率评估；
  - **动态学习曲线（Learning Curve）**：度量自蒸馏经验注入后的自愈加速效应；
  - **VLM 六维打分卡（VLM Scorecard）**：多模态视觉模型对三维生成结果的工程规范合规性与视觉美学打分。
- 📦 **开放 BIM 工业资产交付**
  - 交付标准 **IFC4X3** 数据模型、**IDS 1.0** 规范检验契约、RuleEvidence 决策证据链及不可变 Artifact Manifest。

---

## 🏗️ 系统架构

```
┌────────────────────────────────────────────────────────────────────────┐
│                        前端交互层 (Modern Web UI)                       │
│  React 19 SPA · Three.js 3D 视口 · 审批中心 · 轨迹时间轴 · 真流式 SSE   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / SSE / A2A
┌───────────────────────────────────▼────────────────────────────────────┐
│                    服务端运行时 (Server / FastAPI)                     │
│   Workspaces 多工作空间 · Usage 计量账本 · Approvals 门禁 · SSE 调度   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                       智能体调度中枢 (Agent Core)                       │
│ ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────┐ │
│ │  Clarify (槽位确认)  │ │   Planner (分步规划) │ │ Deliver (交付门) │ │
│ └──────────────────────┘ └──────────────────────┘ └──────────────────┘ │
│  Reflexion 反思闭环 · Anti-Doom-Loop 三振断路器 · 3-Tier 上下文折叠    │
└───────────────────────┬──────────────────────────┬─────────────────────┘
                        │                          │
┌───────────────────────▼───────┐  ┌───────────────▼─────────────────────┐
│ 神经符号微内核 (Symbolic Core) │  │   多模态模型层 (Providers Registry) │
│ • 确定性重力流管网 Solver     │  │ • MiMo-v2.5 (Vision + Reasoning)    │
│ • GB 50289 国标刚性规范检查   │  │ • SenseNova-6.8-flash-lite (Multimodal)
│ • OpenSCAD 几何编译沙箱       │  │ • DeepSeek / GLM / Qwen / Kimi / Gemini
└───────────────────────┬───────┘  └─────────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────────────────────┐
│                   双宿主执行总线 (MCP Servers)                         │
│   • Vectorworks MCP (自研)          • Blender MCP (Fork 改造)          │
│   • IFC4X3 / IDS 1.0 资产交付       • 不可变 Artifact Manifest 归档    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 1. 环境准备

确保本机已安装 Python 3.11+ 以及 [uv](https://docs.astral.sh/uv/)（推荐）：

```bash
# 克隆仓库
git clone https://github.com/Akichoooo/openBIMAgent.git
cd openBIMAgent

# 同步 Python 运行环境
uv sync
```

### 2. 配置环境变量

在仓库根目录下复制或创建 `.env` 文件（**注意：禁止提交包含真实 Key 的配置文件至 Git**）：

```bash
# 激活预设 Profile (official / sensenova / faucet_vlm)
OPENBIMAGENT_PROFILE=official

# 根据实际调用的模型配置对应 API Key
SENSENOVA_API_KEY="your-sensenova-key"
FREETOKENFAUCET_API_KEY="your-token"
GLM_API_KEY="your-glm-key"
QWEN_API_KEY="your-qwen-key"
KIMI_API_KEY="your-kimi-key"
```

> **无 Key 离线体验**：系统支持内置纯确定性离线仿真与测试桩，即使未配置真实大模型 Key 也可通过本地微内核运行全流程测试。

### 3. 启动现代化 Web 服务

```bash
# 启动本地服务（自动优先挂载已编译的现代前端）
uv run python -m openbimagent server

# 浏览器访问：http://127.0.0.1:8765/
```

---

## 💻 现代工作台 UI 开发

前端代码位于 `frontend/` 目录，采用 Vite + React 19 技术栈：

```bash
cd frontend

# 安装依赖
pnpm install

# 启动开发服务器（支持热重载并反向代理后端 8765 端口）
pnpm dev

# 运行前端测试
pnpm test

# 打包生产静态资源（打包至 frontend/dist，后端将自动感应并优先伺服）
pnpm build
```

---

## 🔬 学术评测与基准套件

openBIMAgent 提供开箱即用的学术评测套件，覆盖大模型在空间物理工程中的核心维度：

```bash
# 1. 运行核心架构消融实验 (Ablation Study)
uv run pytest tests/test_architecture_ablation.py -v

# 2. 运行 Pass@k 可靠性与鲁棒性度量
uv run pytest tests/test_reliability_passk.py -v

# 3. 运行自蒸馏学习曲线评测
uv run pytest tests/test_learning_curve.py -v

# 4. 运行 VLM 六维多模态视觉打分卡验证
uv run pytest tests/test_vlm_scorecard.py -v

# 5. 全套自动化核心模块集成验证 (95 项测试)
uv run pytest tests/test_a2a_adapter.py tests/test_architecture_ablation.py tests/test_chat.py tests/test_chat_stream.py tests/test_learning_curve.py tests/test_reasoning.py tests/test_reliability_passk.py tests/test_trace_export.py tests/test_trajectory_metrics.py tests/test_usage_ledger.py tests/test_vlm_scorecard.py tests/test_web_ui_prefers_dist.py tests/test_workspaces.py tests/test_schema_gate.py tests/test_dialects.py -q
```

---

## 📂 核心代码目录拓扑

```
openBIMAgent/
├── config/                  # 模型能力表、提供商映射 (models.toml)
├── domain_packs/            # 行业领域专家包 (如 municipal_utility, single_asset_hero)
├── frontend/                # 现代 React 19 + Tailwind + Three.js 全栈前端源码
│   ├── src/
│   │   ├── components/      # UI、聊天、设置、视口、审批与轨迹组件
│   │   └── services/        # 统一 API 客户端与 SSE 流式解析器
│   └── package.json
├── mcp_servers/             # MCP 跨进程协议服务 (Blender & Vectorworks)
├── schemas/                 # 轨迹度量、编译 IR 的标准 JSON Schema
├── src/openbimagent/
│   ├── benchmark/           # 学术基准：消融实验、学习曲线、Pass@k、VLM 打分
│   ├── core/                # 智能体主调度循环与生命周期 Hook
│   ├── providers/           # 大模型方言转换与 Reasoning 统一抽象层
│   ├── server/              # FastAPI 服务、A2A 适配、审批中心与账本
│   ├── utility/             # 确定性水力与几何求解微内核
│   └── vision/              # VLM 视觉多模态评估体系
├── tests/                   # 全面自动化测试套件 (1,200+ 项单测与集成测试)
└── ui/                      # 离线回退工作台 (Franken UI)
```

---

## 📚 文档与学术支撑

- **核心技术报告**：`papers/2026秋季开题与学术汇报全套资料/` 目录下提供详尽的理论支撑与架构解析：
  - *现代 AI Agent 前沿教程与工业级架构实战精粹*（含 Karpathy 认知计算隐喻、Anthropic 生产级工程法则、Reflexion 反思架构与上下文折叠算法）；
  - *openBIMAgent vs 顶尖 Agent 框架深度调研对比与核心架构补强报告*（横评 `claude-code`, `codex`, `pi`, `opencode`, `deepseek-harness`）；
- **系统架构详设**：`docs/architecture/ARCHITECTURE.md`
- **组件执行契约**：`docs/architecture/COMPONENTS.md`

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议开源。
