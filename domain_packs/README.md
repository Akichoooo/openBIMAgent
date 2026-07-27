# Domain Pack(领域专家包)

通用基座 + 垂直领域包;先做到极致再泛化(ARCH §0 原则 7、§4)。Domain Pack 是 playbook 的上级概念:
把「流程剧本 + 领域角色 + 知识 + 资产 + 评分细则 + 评测用例」打包,注入 Agent Core 跑垂直任务。

## 包结构

```
domain_packs/<name>/
├── playbook.md          # 流程剧本(YAML frontmatter + 任务书正文)
├── agents/              # 领域角色覆盖(可选,覆盖全局 agents/)
├── knowledge/           # 领域知识/规范/坑清单
├── assets/              # 材质板、GeoNodes 预设、typology 模板
├── rubric_overlay.md    # 领域评分细则(叠加通用六维)
└── benchmark_cases.json # 领域评测用例
```

## playbook schema 摘要

playbook.md = Markdown + YAML frontmatter(全文示例见 ARCH §4):

- `name` / `targets`:`[blender]` 或含 `vectorworks`(启用附录 B)。
- `slots`:Clarify 槽位(`{id, question, default}`),问齐且 completion_score ≥ 85 才放行。
- `phases`:阶段序列;每阶段可指定 `agent` / `tools`(阶段级工具权限范围)/ `output`;`asset_batches` 给批次清单(批次粒度 = 一次渲染检查单位)。
- `acceptance`:双环阈值,如 `scad_loop: {min_score: 8.0, max_iters: 6}`、`blender_loop: {min_score: 8.5, max_iters: 4}`;超限 ESCALATE 不死循环。
- `deliverables`:Deliver 门禁(C5)强校验清单。
- 正文 = 任务书:风格定义、硬性要求(调研先行/分资产/统一色调+磨损/子代理分工/每批渲染返工)、禁止事项;附录 A Blender 必做,附录 B Vectorworks 按 targets 启用。

## 与全局 agents/ 的覆盖关系

- 全局 `agents/` 定义 10 个内置角色(COMPONENTS §3);包内 `agents/` **可选**,同名角色文件覆盖全局(同为 Markdown + YAML frontmatter 格式)。
- 覆盖只在该包任务内生效;包未提供覆盖时使用全局角色。
- `rubric_overlay.md` 叠加(不替换)通用六维 rubric(ARCH §3)。

## 现有三个包(先读它们再写新包)

- `edo_cyberpunk_district` —— 江户 x 赛博朋克 x 拟洋风街区,Blender 表现极(M0)。
- `single_asset_hero` —— 单资产英雄镜头,快速验证全链路(M0)。
- `municipal_utility` —— 市政管网,毕设主线,继承 openBIMForge `mep_agent` 资产(M1.5)。

远程拉取(Goose 式 `--playbook <url>`)列为 P1。
