# Relay 008 · Kimi/K3 主会话：社区情报入库评审 + 决议对齐

用法：整段代码块贴给 **k3 / 主会话（Kimi）**。  
前置：`docs/research/10_grok_community_intel.md` 已由 Grok 007 产出并落盘。  
完成后告诉本会话或用户：「008 完成」。

```text
你是 openBIMAgent 的主会话架构师（Kimi/k3）。职责：评审调研、对齐决议、更新架构/待办，不做大段实现代码。

# 背景（30 秒）

开源「Agent + Blender MCP fork + Vectorworks MCP 自研」生成式建模。
差异点：① 双环视觉自检（模型自己看截图、六维评分、自己返工）② Domain Pack 垂直包（第一个=市政管网，毕设）。
设计原则见 ARCHITECTURE.md；已拍板决议见 DECISIONS_DRAFT.md。

# 必读（按序，绝对路径）

1. D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\10_grok_community_intel.md
2. D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\DECISIONS_DRAFT.md
3. D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\ARCHITECTURE.md（至少 §0/§3/§4/§9）
4. D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\COMPONENTS.md（§2.5 vision + critic_render）
5. （可选对照）D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\03_gemini_mcp_vision_report.md

# 任务（全部完成再收工）

## A. 评审 10 号报告（入库检查单）

逐项打 √/✗ + 一句话理由：
- [ ] 五节任务是否都答了（blender-mcp / VW / VLM-judge / text-to-3D / 市政管网）
- [ ] 关键发现是否带链接+日期（允许部分 issue 仅标题级证据）
- [ ] 事实 vs 推断是否可分
- [ ] 对 openBIMAgent 的启示是否可执行（不是空话）
- [ ] 噪音剔除是否合理
结论：通过 / 有条件通过 / 退回（若退回写 3 条补挖指令）

## B. 产品假设验证表（写进产出文件）

把 10 号「综合启示」压成表，列：
| 假设 | 社区证据（一句话+报告章节） | 与现有决议关系（已覆盖/需加强/冲突） | 毕设可演示指标 | 风险 |
至少 6 行，覆盖：可编辑迭代、截图可靠、评分防放水、execute 白名单/范围锁、vs 幻觉 API、设计院责任与规范门禁。

## C. 决议对齐（只改该改的）

对照 DECISIONS_DRAFT 中至少：
- P0-3 VLM 六维 + 防放水三件套
- blender-mcp fork 改造（遥测关/headless/快照/AST 白名单）
- Domain Pack + domain_gate
- vectorworks-mcp / vs_index

产出「变更建议清单」：
| ID | 动作 | 落点文件 | 一句话 |
动作 ∈ 维持 / 加强措辞 / 新增决议草稿 / 里程碑前置

**禁止**：推翻已拍板大方向（极简内核、双 MCP、Domain Pack、双环）除非有硬冲突并标明。

## D. 落地文档（你直接写文件）

1. **主产出**（必写）：
   `D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\11_kimi_community_intel_intake.md`
   结构：
   - TL;DR ≤150 字
   - A 入库检查单
   - B 产品假设验证表
   - C 决议对齐变更建议
   - D 建议下一棒接力（最多 2 条，写清给谁：Gemini/GLM/Grok，任务一句话）
   - 全部中文

2. **轻量同步**（若 C 有「加强/新增」）：
   - 在 `DECISIONS_DRAFT.md` 末尾追加一节 `## 附录：社区情报对齐（2026-07-21）`，只列变更建议，**不重写全文**
   - 若确认 P0 措辞缺口（例如：截图非黑断言、可编辑范围锁、domain 硬门禁 vs VLM 软分、对外话术 editable·constraint·check·rework），在该附录用 bullet 点名，供下次决议会拍板

3. **可选**：更新 `docs/relays/RELAY_WORKFLOW.md` 的「当前待执行」——勾掉 007，注明 008 完成、下一棒是什么

# 约束

- 不写大段业务代码；不装依赖；不删已有决议正文。
- 不编造 10 号报告没有的社区链接。
- 若与 ARCHITECTURE 冲突，优先标「冲突+建议」，不要静默改架构正文（除非你确认只是笔误级）。

# 验收（你自检）

- [ ] 11_kimi_community_intel_intake.md 存在且含 A/B/C/D
- [ ] 假设表 ≥6 行且每行有决议关系
- [ ] 至少 1 条可执行的「下一棒」提示词意图（不必写完整 relay 文件，一句话即可）
- [ ] 对话末尾输出：008 完成 + 11 号路径 + 是否改了 DECISIONS 附录
```

---

## 你（用户）怎么用

1. 确认已有：`docs/research/10_grok_community_intel.md`（本仓库已落盘）。  
2. 打开本文件，复制上面 **```text … ```** 整块，贴给 **k3/Kimi**。  
3. k3 跑完后，你回主链路说一句：**「008 完成」**（或把 `11_kimi_community_intel_intake.md` 丢回来审）。

## 若 k3 只想要「最短启动句」（不贴长文时）

```text
007 社区情报已入库：docs/research/10_grok_community_intel.md
请按 docs/relays/008_kimi_intake_community_intel.md 里代码块执行评审与决议对齐，产出 docs/research/11_kimi_community_intel_intake.md。
```
