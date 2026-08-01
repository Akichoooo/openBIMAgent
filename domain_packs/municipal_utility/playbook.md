---
name: municipal_utility
version: 1
description: 市政管网特优生成(毕设主线,第一个 Domain Pack)
targets: [blender, vectorworks]
slots:
  - { id: network_type, question: 管网类型?,        default: 雨污分流(可叠加给水/燃气/电力套管) }
  - { id: scale,        question: 服务范围?,        default: 一个街区(约 200m x 200m) }
  - { id: depth_spec,   question: 埋深/覆土标准?,    default: 按 knowledge/ 内规范条文默认 }
  - { id: deliverables, question: 交付物?,          default: IFC 构件 + 剖切图 + 漫游视频 }
phases:
  - id: research
    agent: researcher
    tools: [web_search, fetch, write]
    output: references.md        # 规范条文 + 既有管网案例 + 材质板
  - id: route_planning
    agent: utility_planner       # 领域角色(包内 agents/ 覆盖)
    solver: municipal-straight-gravity-solver
    solver_version: 0.1.0
    input_schema: utility_solver_input.schema.json
    output: compiled_utility_ir.json # Solver 输出；不再把坐标/坡度留给 LLM 猜测
    acceptance: [diameter_in_spec, slope_in_spec, cover_depth_in_spec, manhole_spacing_in_spec]
  - id: clash_check
    agent: clash_checker
    input: compiled_utility_ir.json
    per_batch: [scad_check]      # 管网用 SCAD 环做三维碰撞/坡度快检
  - id: bim_build
    agent: modeler
    per_batch: [vw_component]    # Vectorworks 段:IFC 构件(管/井/附件)
  - id: visual_build
    agent: modeler
    per_batch: [blender_build, render_check]  # Blender 段:地表场景+剖切表现
  - id: deliver
acceptance:
  scad_loop:    { min_score: 8.0, max_iters: 6 }
  blender_loop: { min_score: 8.5, max_iters: 4 }
  domain_gate:  # FAIL/UNKNOWN 均阻断；Solver v0 的 clash_free 仍为 UNKNOWN，不能直接交付
    { diameter_in_spec: true, slope_in_spec: true, cover_depth_in_spec: true,
      manhole_spacing_in_spec: true, clash_free: true }
deliverables: [IFC 构件库, 纵断/剖切图, 汇报漫游视频]
---

# 市政管网特优生成 · 任务书

毕设实证包:验证「通用基座 + Domain Pack 垂直化」在 BIM 交付向领域的成立。

## 领域硬性要求(叠加通用核心)

1. **规范先行**:路由/埋深/坡度/管径必须引用 knowledge/ 内规范条文,违反即 domain_gate 拦截。
2. **碰撞零容忍**:管线交叉、与建筑基础冲突必须经 clash_check 全过才进 BIM 构建。
3. **语义交付**:VW 段构件必须带 IFC 语义(IfcPipeSegment/IfcDistributionSystem 等),C2/C5 铁律适用。
4. **并行生成路径**:两个 MCP 是并行的生成工具——VW 段出 IFC 语义构件(M1.5 交付主线),Blender 段出地表场景/剖切动画/汇报漫游;Blender 经 Bonsai 出 IFC 为后续备选路径,架构不锁死分层。

## 继承资产(openBIMForge mep_agent)

- `forge_core/mep_agent/`:排污规划(fixture/stack/3D 路由/sizing/IFC 导出/VW 脚本)→ 迁入本包 knowledge/ 与保底模板。
- 迁移清单与适配说明见本包 README.md(待 M1.5 填充)。

## 附录 A · Blender(表现层)

地表街区场景(可复用街区包资产)+ 地层剖切 + 管网高亮动画 + 汇报漫游。

## 附录 B · Vectorworks(交付层,必做)

管网构件(管段/检查井/附件)带 IFC 语义;纵断面图;过 IFC/IDS 门禁。
