# Limitations（系统边界与学术诚实声明）

> 本文档汇总架构评审确认的三项**结构性天花板**（非代码缺陷，属设计边界），供论文写作与答辩引用。
> 原则：对外克制表述，对内持续改造；不包装、不掩盖。

## L1. OpenSCAD 视觉环与真实 BIM 构件之间的语义断层

- **事实**：SCAD 环消费极简图元（cube/cylinder/sphere/cone，scad_loop.py），而真实市政构件有流槽、沉泥室、偏心井圈、承插口、砂石垫层。SCAD 环只能筛"粗暴碰撞/体量级错误"，**不能证明覆土、跌水倒坡、管口标高契合**。
- **风险**：VLM Critic 对三视图白模的视觉高分不等于工程合规（"视觉合规 ≠ 工程合规"）；JSON patch 无法修复拓扑断裂（需增删节点时必达 hard_limit）。
- **现有缓解**：工程合规由 C2 约束兜底——**确定性判定全部交给 Solver + domain_gate，不走 VLM**；SCAD 环定位是"极速过滤漏斗"而非验收器。
- **论文表述建议**：作为 Limitation 主动提出；将 SCAD 环描述为"粗筛层（coarse funnel）"，工程正确性证据链 = CompiledUtilityIR + RuleEvidence + IDS。

## L2. 多 MCP 架构的重型宿主强耦合

- **事实**：Blender 5.2 + Vectorworks 2024 + OpenSCAD 均为本地物理机重型依赖（VW 需 GUI 环境，无法 Linux Docker 化）；真机集成测试在 CI 中只能 opt-in skip（6 skipped）。
- **风险**：Vectorworks File-IPC 长时间轮询在 Windows 下可能被判定"应用无响应"（实测待命 5.5h 零错误，但属已知形态而非根治）。
- **现有缓解**：typed execution plan + scope lock + 语义快照使宿主失败可检测、可隔离；双宿主互为证据。
- **论文表述建议**：作为 Limitation 提出——"交付正确性优先于部署弹性"；云原生路径 = 无头 Blender + Bonsai IFC（备选，未实现）。

## L3. 规则库的"手工编译孤岛"与领域泛化天花板

- **事实**：自愈与规则编译围绕 GB 50289 净距规则深度定制（市政雨污水重力管主线）；超出范围（综合管廊、电力通信同沟、热力补偿）即失能；未知障碍物必须人工录入规则并重编译（编译期 33 自检样例 + 哈希冻结是刻意设计）。
- **风险**：规则维护成本依赖领域专家；规则动态提取（从 Trace 归纳新规则）属研究开放问题。
- **现有缓解**：Domain Pack 模板族（_base 7 步）使新领域可系统性扩展；规则受信任源地位保证生成可复核。
- **论文表述建议**：把"人工专家规则编译 + 编译期自验证"作为**特性**而非缺陷陈述（fail-closed 治理），泛化列为 future work。

## 术语修正（重要）

- ~~"Trace 自进化"~~ → **"不可变事件溯源与设计资产增量沉淀机制"（Traceable Event-Sourcing & Continuous Asset Archiving）**。
- 归档反哺的实现机制 = **In-Context Retrieval**（启动新任务时检索 Top-3 相似交付注入 brief，runs.py `_retrieve_exemplars`），**不涉及任何权重更新 / RL / DPO**。论文中如遇"如何改变下一轮行为"之问，标准答案：仅通过上下文范例注入影响 LLM 条件分布，模型权重与受信任规则集全程不变。
