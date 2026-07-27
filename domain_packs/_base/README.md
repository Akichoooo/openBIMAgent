# Domain Pack 模板族与创作指南(_base)

Domain Pack 是 openBIMAgent 的一等概念(ARCHITECTURE §4)。**任何范例包都不是唯一版本**——江户街区包只是「风格场景类」的第一个实例。本目录是模板族的通用核心与创作指南。

## 通用核心(所有包继承,不得删除)

1. **调研先行**:references.md(真实参考 + 材质板)存在才允许建模。
2. **禁止一次性糊完整场景**:按批次逐资产创建,每批 = 一个渲染检查单位。
3. **统一色调 + 经年磨损**:服从 palette/wear_level 槽位;只调预置材质库/GeoNodes 参数,严禁手写节点树与 boolean 破损。
4. **子代理分工**:调研/建模/材质/灯光分角色,orchestrator 调度。
5. **每批渲染检查,不通过就返工**:返工指令必须可执行。
6. 质量优先;时间与 credit 不是约束。

## 三类模板

| 类型 | 范例 | 什么时候以它为底本 |
|---|---|---|
| 风格场景类 | `edo_cyberpunk_district` | 做视觉表现向场景(街区/室内/道具陈列) |
| BIM 交付类 | `municipal_utility` | 做带 IFC 语义的工程交付(管网/建筑/结构) |
| 冒烟类 | `single_asset_hero` | 验证链路/ onboarding / 单资产英雄镜头 |

## 写一个新包(7 步)

1. 复制最近类型的范例包目录,改 `name`。
2. 填 frontmatter:`targets`(blender / vectorworks / 两者——两 MCP 是并行生成路径,按包选用)。
3. 定义 `slots`:把「用户必须拍板的变量」全部槽位化(风格/规模/色调/交付物),每个给 default。
4. 排 `phases`:阶段引用角色(可用包内 `agents/` 覆盖全局角色),声明每阶段 tools 与 output 工件。
5. 定 `acceptance`:SCAD/Blender 双环阈值;有确定性硬指标(规范/碰撞/尺寸)的在 `rubric_overlay.md` 配 domain_gate 规则,**硬指标不走 VLM**。
6. 填 `knowledge/`(规范/坑清单,机器可读优先)、`tools/`(已验证领域工具)、`assets/`(材质板/预设/黄金截图集)。
7. 写正文任务书:风格/工程规则、禁止事项、两个 MCP 附录(按需)。

## 校验

- playbook frontmatter 过 `schemas/plan.schema.json`;knowledge 中的约束文件在包 README 注明来源与核实状态。
- 新包第一次跑用 `single_asset_hero` 规模冒烟,确认双环与 domain_gate 都生效再放大。
