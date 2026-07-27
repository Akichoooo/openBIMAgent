---
name: edo_cyberpunk_district
version: 2
description: 江户x赛博朋克x拟洋风混合街区(解构自 GitHub 高赞提示词)
targets: [blender]              # 需要 BIM 交付时改 [blender, vectorworks]
slots:
  - { id: style_mix,   question: 三种风格的混合比例/主导风格?, default: 江户 50% + 赛博朋克 35% + 拟洋风 15% }
  - { id: scale,       question: 街区规模(栋数/地块)?,        default: 6-8 栋 + 一个十字路口 }
  - { id: palette,     question: 主色调?,                      default: 靛蓝 + 锈红 + 暖黄霓虹 }
  - { id: wear_level,  question: 磨损程度(0-10)?,              default: "7(明显岁月痕迹,局部破损)" }
  - { id: deliverables,question: 交付物(渲染张数/漫游时长)?,   default: 英雄镜头 x3 + 漫游 10s }
phases:
  - id: research
    agent: researcher
    tools: [web_search, fetch, write]
    output: references.md        # 真实建筑/神社/高层结构 + 材质板 + 风格锚点词
  - id: asset_batches
    batches: [路面, 建筑xN, 路灯, 自动售货机, 电线, 招牌/道具]
    per_batch: [scad_check, blender_build, render_check]
  - id: lighting_render
    agent: lighter
  - id: deliver
acceptance:
  scad_loop:    { min_score: 8.0, max_iters: 6 }
  blender_loop: { min_score: 8.5, max_iters: 4 }
deliverables: [.blend 工程, 英雄镜头渲染 x3, 漫游视频]
---

# 江户x赛博朋克x拟洋风混合街区 · 任务书

## 风格融合规则(防元素堆砌)

- **结构层(江户)**:木构架、町屋比例、瓦屋顶、格子门窗——街区骨架。
- **氛围层(赛博朋克)**:霓虹招牌、电线密网、全息广告、雨夜反光——光影表皮。
- **细节层(拟洋风)**:拱窗、铸铁栏杆、洋式山墙——点缀在 15% 的立面上,不得主导。
- 三者在同一资产上最多叠加两层;风格冲突由 critic_render 按「风格一致性」维度裁决。

## 硬性要求(通用核心,所有包继承)

1. **调研先行**:开工前必须有 references.md(真实建筑/神社/高层结构 + 常见材质),否则不得建模。
2. **禁止一次性糊完整城**:按批次逐资产创建,每批资产是一个渲染检查单位。
3. **统一色调 + 经年磨损**:全场景色调服从 palette 槽位;磨损走预置材质库/GeoNodes 参数(wear_level),严禁手写节点树。
4. **子代理分工**:调研/建模/材质/灯光分角色执行,orchestrator 调度。
5. **每批渲染检查,不通过就返工**:返工指令必须可执行(哪个资产、改什么),禁止空泛重试。
6. 时间与 credit 不是约束,质量优先。

## 附录 A · Blender(必做)

- 交付:.blend 工程 + 英雄镜头 x3(清晨/雨夜/黄昏各一)+ 漫游视频(沿主街 10s)。
- 截图自检走视口 screenshot,验收走正式渲染(Cycles 或 EEVEE 按质量档)。
- 电线/招牌等高密度细节资产最后一批做,避免早期遮挡评分视野。

## 附录 B · Vectorworks(targets 含 vectorworks 时启用)

- 已验收建筑资产编译为 BIM 构件(墙/楼板/屋顶带 IFC 语义),过 IFC/IDS 门禁。
- C2/C5 铁律:LLM 出语义、solver 出坐标;deliver 只接 accepted plan。
