# openBIMAgent 文档治理与 K3 历史映射

版本：v1.0  
更新时间：2026-08-03  
状态：**ACTIVE**

> 本文件解释 K3/Kimi 早期文档体系与当前项目文档的关系，并定义 Wiki 的权威来源、更新责任和历史材料边界，避免同一事实散落在多份文档后继续漂移。

## 1. K3 的真实含义

K3/Kimi 是项目早期的主会话/模型身份，主要承担：

- 总体架构设计与裁决。
- 多模型 Relay 任务拆分和验收。
- Wiki 首页与架构文档同步。
- `agents/*.md` 角色规格和 artifact-mediated 协作原则。
- M0/M1 阶段任务规划。

**K3 不是项目里程碑，不等于 M3，也不是 M0/M1/M1.5/M2/M3 之外的新阶段。**

可复核历史依据：

- Git 提交 `2bacf81`：Relay 012/013 的 K3/GLM 5.2 工作规格。
- Git 提交 `0603b71`：M0 验收与 Wiki 文档同步。
- Git 提交 `466afba`：阶段报告合并、Relay 001–018 一次性提示词清理。
- `openBIMAgent项目与AgentCore实现详解.md`：记录 K3/Kimi 既有 `agents/*.md` 与 artifact-mediated 原则。

## 2. K3 规划成果的当前落点

| K3 早期成果 | 当前权威落点 | 处理方式 |
|---|---|---|
| 总体系统架构 | `ARCHITECTURE.md` | 持续维护稳定架构和里程碑 |
| 组件、Agent、模型和上下文设计 | `COMPONENTS.md` | 持续维护组件事实与状态 |
| 选型和设计裁决 | `DECISIONS_DRAFT.md` | 保留历史决策；新重大决策追加 ADR/附录 |
| M0 任务规划与验收 | `M0_PLAN.md` | 历史阶段记录，不改写为当前进度 |
| M1 G1–G7 契约 | `M1_EXECUTION_CONTRACT.md` | 当前 M1 完成标准 |
| 跨会话总控提示词 | `PROJECT_HANDOFF_STATUS.md` | 使用其中最新提示词；旧 `M1_MASTER_PROMPT.md` 保留为原始契约提示词 |
| 项目全路线 | `PROJECT_MASTER_WORKFLOW.md` | M0–M3 稳定任务流与完成定义 |
| Wiki 导航 | `docs/README.md` | 唯一文档首页和变更日志 |
| 多模型接力 | `relays/RELAY_WORKFLOW.md` | 保留方法，不再绑定单一模型品牌或额度假设 |
| 研究接力协议 | `relays/RESEARCH_PROTOCOL.md` | 保留调研质量门 |
| Relay 001–018 一次性任务书 | Git 历史 | 不恢复到工作树，避免噪声和过时命令 |
| 阶段验收报告 | `outputs/` | 每阶段保留合并后的正式报告 |

## 3. 文档四层模型

### L1：入口与实时状态

- `README.md`：产品定位、当前一句话状态、快速开始和 Wiki 入口。
- `docs/README.md`：Wiki 首页、阅读顺序、全量索引、状态矩阵和变更日志。
- `PROJECT_HANDOFF_STATUS.md`：实时门禁、HEAD、测试、工件、阻塞和下一动作。

更新频率：高。不得在 L1 长篇复制底层实现。

### L2：稳定架构与路线

- `PROJECT_MASTER_WORKFLOW.md`
- `ARCHITECTURE.md`
- `COMPONENTS.md`
- `DECISIONS_DRAFT.md`
- `M1_EXECUTION_CONTRACT.md`

更新频率：中。只有架构、协议、阶段范围或完成标准变化时更新。

### L3：专题设计与验收证据

- `docs/research/`
- `outputs/`
- `M0_PLAN.md`
- 学术材料

更新频率：按专题或阶段。历史报告完成后保持不可变，必要时在顶部标注“历史状态”并链接最新入口，不重写历史证据。

### L4：临时接力与运行材料

- `relay_workspace/`
- 一次性 Relay 提示词
- 临时日志、克隆和原始摘录

这些内容不属于长期 Wiki。任务收口后删除或保持 gitignored；需要追溯时使用 Git 历史和正式验收报告。

## 4. 单一事实来源

| 事实 | 唯一权威来源 |
|---|---|
| 当前门禁、HEAD、测试数字、下一动作 | `PROJECT_HANDOFF_STATUS.md` |
| M0–M3 工作包与顺序 | `PROJECT_MASTER_WORKFLOW.md` |
| 系统总体结构和主链 | `ARCHITECTURE.md` |
| 组件职责、协议和模块状态 | `COMPONENTS.md` |
| M1 完成标准和权限边界 | `M1_EXECUTION_CONTRACT.md` |
| 设计裁决及延后项 | `DECISIONS_DRAFT.md` |
| 文档入口、状态分类、变更日志 | `docs/README.md` |
| 阶段实测证据 | 对应 `outputs/*验收报告.md` |
| 日常跨会话恢复补充 | `.workbuddy/memory/YYYY-MM-DD.md` |

其他文档引用这些来源，不再复制易变化的测试数字、提交号和进度结论。

## 5. 状态标签

Wiki 索引中的文档统一使用：

- `CURRENT`：当前权威，必须与实现同步。
- `ACTIVE`：正在执行的契约或计划。
- `REFERENCE`：仍有参考价值，但不是当前状态来源。
- `HISTORICAL`：阶段历史证据，不再按当前实现更新。
- `SUPERSEDED`：已被新文档替代，保留仅为追溯。
- `DRAFT`：尚未正式拍板。

禁止用“当前有效”描述明显包含过时状态矩阵的文档。

## 6. 同步事务

### 实时进度变化

同一回合更新：

1. `PROJECT_HANDOFF_STATUS.md`
2. 必要时 `README.md` 的一句话状态
3. `.workbuddy/memory/YYYY-MM-DD.md`

### 架构或路线变化

同一变更集更新：

1. `PROJECT_MASTER_WORKFLOW.md`
2. `ARCHITECTURE.md`
3. `COMPONENTS.md`
4. `DECISIONS_DRAFT.md` 或新 ADR
5. `docs/README.md` 变更日志

### 阶段验收完成

同一阶段收口：

1. `outputs/` 正式验收报告
2. `PROJECT_HANDOFF_STATUS.md`
3. `README.md` 和 `docs/README.md`
4. 相关执行契约状态
5. 边界清晰的本地 Git 提交

## 7. 防重复和防漂移规则

1. 不在多份文档重复维护测试总数、HEAD 和工件 hash。
2. 历史报告不改写为当前报告；在 Wiki 标记历史状态并链接最新入口。
3. 一次性 Relay 提示词不回填到 Wiki 正文。
4. 新会话提示词只维护一份最新版，放在 `PROJECT_HANDOFF_STATUS.md`；当前版本覆盖 M1 收口并按阶段契约连续推进 M1.5、M2、M3。
5. 架构图必须标明版本、更新日期和事实边界。
6. 文档中的“已实现、未实现、待验证”必须能由代码、测试、Git 或宿主工件复核。
7. 任何包含真实路径的文档不得包含 token、凭据或个人敏感信息。
8. 更新后至少运行 `git diff --check` 和 Markdown 相对链接检查。

## 8. 当前收口结论

- K3 早期整体规划没有丢失，核心结论已吸收到架构、组件、决策、角色、M0/M1 和 Wiki 文档。
- Relay 001–018 的清理是有意的文档治理，不是资料缺失；完整内容仍可从 Git 历史恢复。
- 当前项目不需要恢复旧 Relay 文件，也不需要新建第二套 Wiki。
- 从 2026-08-03 起，以 `docs/README.md` 为唯一 Wiki 首页，以交接状态和总流程两份文档解决跨会话状态漂移。
