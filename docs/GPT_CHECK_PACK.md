# openBIMAgent GPT 检查包

将本文件与以下文件一起交给 GPT 进行完整检查。

## 检查范围

| 类别 | 文件/目录 | 说明 |
|---|---|---|
| 架构文档 | `docs/architecture/ARCHITECTURE.md` | 总体架构与设计原则 |
| 组件详设 | `docs/architecture/COMPONENTS.md` | 每个组件的详细规格 |
| 执行契约 | `docs/architecture/M1_EXECUTION_CONTRACT.md` | M1 完成标准 |
| 执行契约 | `docs/architecture/M1_5_EXECUTION_CONTRACT.md` | M1.5 市政深化 |
| 执行契约 | `docs/architecture/M2_EXECUTION_CONTRACT.md` | M2 产品化 |
| 实时状态 | `docs/architecture/PROJECT_HANDOFF_STATUS.md` | 当前进度与测试 |
| 项目路线 | `docs/architecture/PROJECT_MASTER_WORKFLOW.md` | M0-M3 整体任务流 |
| 审计提示词 | `docs/AUDIT_PROMPT.md` | 完整审计检查清单 |
| 核心代码 | `src/openbimagent/` (86 个 .py) | Agent Core + M2 服务 |
| 测试 | `tests/` (72 个 test_*.py) | 全仓测试 |
| Schema | `schemas/` (46 个 .json) | 协议 Schema |

## 核心架构摘要

### 1. 项目规模

```
src/openbimagent/    86 个 Python 模块
tests/               72 个测试文件
schemas/             46 个 JSON Schema
mcp_servers/         2 个 MCP 服务器
domain_packs/        3 个领域包
agents/              10 个子代理角色定义
```

### 2. 分层架构

```text
用户层: CLI / Web UI / Operator Console
    │
M2 产品化层: FastAPI + SSE + 受控写 + 认证
    │
Agent Core 层:
    ├── AgentLoop (read/write/edit/bash/mcp_call/vision_check/subagent/deliver)
    ├── Provider Registry (GLM/Gemini/agentrouter/faucet)
    ├── Clarify (中英 slot 抽取)
    ├── Planner (Scene Graph IR)
    ├── Schema Gate (JSON Schema 校验)
    └── Session (JSONL 树 + checkpoint/resume)
    │
确定性 Solver 层:
    ├── StraightGravitySolver (两井一直管)
    ├── NetworkGravitySolver (多节点拓扑)
    ├── GridRouteSolver (路线搜索)
    ├── HydraulicSolver (Manning 水力)
    └── RuleEngine (GB 50289-2016, 12 条 rules)
    │
双宿主 MCP 执行层:
    ├── blender-mcp (fork 改造, 8 项安全改造)
    └── vectorworks-mcp (自研, ForEachObject 白名单)
    │
视觉自纠错层:
    ├── SCAD 快检环 (三视角白模, 毫秒级)
    └── VLM 精检环 (六维评分, 返工指令)
    │
交付层:
    ├── IFC4X3 (IfcOpenShell 0.8.5+)
    ├── IDS 1.0 (buildingSMART XSD)
    ├── RuleEvidence (PASS/FAIL/UNKNOWN/REVIEW_REQUIRED)
    └── ArtifactManifest (不可变交付)
```

### 3. 核心设计原则

1. **C2 原则**：LLM 出语义，Solver 出坐标
2. **极简内核**：Loop + ≤8 工具，状态外置文件
3. **工件即协议**：模型间只经工件文件交接，过 Schema 门禁
4. **typed 主链**：禁止回退 execute_code/execute_vs_code
5. **失败关闭**：UNKNOWN 不包装为 PASS
6. **双宿主语义一致性**：仅允许 host_handle/presentation_material 差异

### 4. 关键协议

| 协议 | 版本 | 约束 |
|---|---|---|
| CompiledUtilityIR | v1 | 冻结、禁止额外字段、canonical SHA-256 |
| SemanticSnapshot | v1 | 双宿主语义比较 |
| MunicipalRuleSet | v1.1 | 12 条 production 规则 |
| RuleEvidence | v1.0 | 四态裁决 |
| ArtifactManifest | v1.1 | 不可变交付 |
| M2 API | 1.0 | REST + SSE + 幂等 |

### 5. 测试基线

```
全仓 pytest:           991 passed, 4 skipped, 3 warnings
M2 专项:               283 passed
M3 Benchmark 专项:     150 passed
Runner 聚焦:           24 passed
Ruff:                  All checks passed
compileall:            PASS
```

### 6. 完成状态

```
M1 G6: 真实 Vectorworks 2024 + Blender 5.2 双宿主验收通过
M1 G7: 全仓工程验收通过
M1.5:  市政管网 B1-B10 benchmark 通过
M2:    P0-P7 产品化服务完成
M3:    实验报告、论文材料完成
```

## 检查重点

请 GPT 重点检查以下方面：

1. **架构一致性**：代码实现是否与文档描述一致？
2. **安全边界**：M2 服务是否不泄露敏感信息？Vectorworks 运行器句柄操作是否安全？
3. **协议完整性**：Schema 是否覆盖所有关键字段？幂等状态机是否完整？
4. **测试覆盖**：关键失败分支是否有负向测试？
5. **已知问题**：GetPolyPt3D 垃圾值、SAFE_DELETE_FAIL_CLOSED 等是否合理？

## 审计输出格式

请按以下格式输出：

```
## 审计结果

### 通过
- [项1] — 说明

### 警告
- ⚠️ [项1] — 严重程度:低/中/高 | 说明

### 失败
- ❌ [项1] — 严重程度:低/中/高 | 修复建议

### 建议
- [项1] — 说明
```