---
name: single_asset_hero
version: 1
description: 单资产英雄镜头——最小闭环验证模板(M0 冒烟用)
targets: [blender]
slots:
  - { id: asset,      question: 做什么资产?,  default: 一台日式自动售货机 }
  - { id: style,      question: 风格锚点?,    default: 江户x赛博(同街区包) }
  - { id: wear_level, question: 磨损程度(0-10)?, default: "6" }
phases:
  - id: research
    agent: researcher
    tools: [web_search, fetch, write]
    output: references.md        # 真实照片参考 3-5 张 + 材质板
  - id: asset_batches
    batches: [主体]
    per_batch: [scad_check, blender_build, render_check]
  - id: lighting_render
    agent: lighter
  - id: deliver
acceptance:
  scad_loop:    { min_score: 8.0, max_iters: 6 }
  blender_loop: { min_score: 8.5, max_iters: 4 }
deliverables: [.blend 工程, 英雄镜头渲染 x1]
---

# 单资产英雄镜头 · 任务书

最小端到端闭环:追问 → 调研 → SCAD 检 → Blender 建 → 渲染检 → 出图。
用于 M0 冒烟验证与新人 onboarding;全部硬性要求与街区包一致(调研先行/统一色调+磨损/渲染检查返工),只是批次 = 1。

## 附录 A · Blender

- 英雄镜头一盏主光 + 一盏轮廓光 + 地面反射平面;turntable 连拍 4 视角供 critic 全角度评分。
