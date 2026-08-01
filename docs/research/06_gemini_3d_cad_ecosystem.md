# 06_gemini_3d_cad_ecosystem

**TL;DR**
本次调研覆盖了基于 LLM 和过程化生成的主流 3D/CAD 场景项目。结论：纯 LLM 单次生成（BlenderGPT、3D-GPT）容错率极低，必须依赖类似 SceneCraft 的视觉-语言闭环。在资产生成上，Infinigen 的纯规则节点树是无价之宝，可直接提取其材质和磨损节点组。对于精确 CAD 内核，build123d 的显式语法比 CadQuery 更适合 Agent 编写。社区已大量涌现 CAD 类 MCP Server，验证了工具链外置方向。资产 API 方面，需建立本地缓存以应对 Sketchfab 等平台的下载配额限制。

---

## SceneCraft (未见官方仓, 仅论文 2403.01248 · N/A · N/A)
- 一句话定位：基于 LLM 代理，通过场景图（Scene Graph）和 VLM 反馈将文本转为 Blender 场景的代码综合系统。
- 架构形态：LLM Agent 规划 -> Blender 执行 -> VLM (GPT-4V) 视觉检查打分 -> 迭代 refine，并包含代码库学习（Library Learning）机制。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 视觉迭代环 | 论文思想提取 | Prompt 级循环 | GPT-4V | 低 | N/A |
  | 空间布局 IR | 论文图表 | Scene Graph Schema | 无 | 低 | N/A |
- 相关机制：论文中基于视觉的渲染图评价循环，与 openBIMAgent 的 Blender 美学精检环高度同构。
- 价值评级：C (仅参考，因无官方源码)
- 建议动作：仅参考其 Scene Graph 语义设计与迭代评分 prompt 思路。

## Infinigen (https://github.com/princeton-vl/infinigen · 3.5k+ · 活跃 · BSD-3-Clause)
- 一句话定位：完全由数学规则驱动、基于 Blender 的极致写实程序化 3D 场景生成器。
- 架构形态：Python 封装的 Blender 几何节点与材质节点网络，无 AI 参与。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 过程化材质库 | `infinigen/assets/materials` | Python AST/Blender Nodes | Blender | 中 | 极好 (BSD) |
  | 过程化资产生成器 | `infinigen/assets/objects` | Python AST/Blender Nodes | Blender | 高 | 极好 (BSD) |
- 相关机制：利用几何节点控制形变（如破坏、风化）和利用着色器节点（Noise/Curvature）构建复杂材质堆叠。
- 价值评级：A (直接可用)
- 建议动作：fork 改造 / 抽取局部模块，将优质节点组（尤其是建筑和道具类）转为 openBIMAgent 建模子代理的引用库。

## Holodeck (https://github.com/allenai/Holodeck · 300+ · 2024 中旬活跃 · Apache 2.0)
- 一句话定位：基于语言引导，利用大模型查询 Objaverse 资产库以程序化构建 3D 室内环境的框架。
- 架构形态：LLM 规划布局 -> Objaverse API 检索资产 -> AI2-THOR 场景组装。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 资产检索代理 | `holodeck/retrieval` | Python / Prompt | Objaverse API | 中 | 极好 (Apache) |
  | 空间约束求解 | `holodeck/layout` | 空间规则校验 | 无 | 高 | 极好 |
- 相关机制：其“先从真实资产库检索，再进行布局约束检查”的思路。
- 价值评级：B (改造可用)
- 建议动作：抄设计重写其资产 API 查询（Objaverse）逻辑和布局 IR 解析逻辑。

## 3D-GPT (https://github.com/Chuny1/3DGPT · 600+ · 低活跃 · Apache 2.0)
- 一句话定位：基于 LLM 的文本到 Blender Python 代码的 3D 建模系统。
- 架构形态：三代理架构（任务分发、概念化补充描述、建模生成 Python 代码）。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 概念化扩写 Prompt | 概念化代理相关 prompt | 文本 | 无 | 低 | 极好 |
- 相关机制：其将抽象需求拆解为具体建模步骤，利用 LLM 直接生成 `bpy` 代码（但在缺少视觉反馈时极易崩溃）。
- 价值评级：C (仅参考)
- 建议动作：仅参考其分工代理的设计，不复用代码。

## BlenderGPT (https://github.com/gd3kr/BlenderGPT · 4.8k+ · 停滞 · GPLv3)
- 一句话定位：一个直接将 GPT 集成在 Blender 面板内用于生成 `bpy` 脚本的早期插件。
- 架构形态：简单的单轮 LLM 对话 -> 代码提取 -> exec()。
- 可拆走模块表：无（架构过于原始，且 GPLv3 有传染性问题）。
- 相关机制：提供了一个反面教材，展示了如果不做 AST 白名单和视觉闭环，一次性 codegen 会导致多少运行时异常和错误。
- 价值评级：C (仅参考)
- 建议动作：忽略。

## LayoutGPT / SceneVerse (https://github.com/UCSB-AI/LayoutGPT · 300+ · 低活跃 · MIT)
- 一句话定位：利用 LLM 作为视觉规划器生成 3D 室内场景布局的框架。
- 架构形态：文本到结构化布局约束（CSS/3D 坐标），再喂给下游生成器。
- 可拆走模块表：
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  |---|---|---|---|---|---|
  | 布局 IR | 论文及示例中的 JSON/CSS 语法 | 文本描述 | 无 | 低 | 极好 |
- 相关机制：非常适合引入到我们 openBIMAgent 的 Planner 子代理的输出格式 `PLAN.md` 中，规范坐标和资产关系约束。
- 价值评级：B (改造可用)
- 建议动作：抄设计重写。

## build123d vs CadQuery (CAD内核候选)
- 一句话定位：两者皆为基于 Open Cascade (OCCT) 的 Python 编程式 CAD 库。
- 架构形态：
  - CadQuery 使用链式调用 (Fluent API)。
  - build123d 使用上下文管理器 (`with` blocks)，状态更显式。
- 可拆走模块表：两者本身即为第三方依赖包。
- 相关机制：由于 LLM 生成链式长调用容易出错且难以调试中间态，build123d 显式的上下文块更利于 Agent 分步编写和检查。
- 价值评级：build123d 评 A(直接可用)；CadQuery 评 B(改造可用)。
- 建议动作：未来若引入“精确 CAD 实体”作为第三 MCP，推荐直接依赖 build123d。

## MCP for CAD 生态先例
- 现状调查：GitHub 已存在大量 CAD/3D 软件的 MCP Server，例如 `freecad-mcp` (neka-nat), `kicad-mcp` (lamaalrajih), `openscad-mcp-server` (fboldo), `fxhoudinimcp`。
- 结论：这证明了“LLM + MCP 控制底层 DCC 软件”是高度可行且被广泛验证的技术路线。工具设计普遍趋同：提供组件查询、树结构导出、AST 隔离下的脚本执行、以及视口截图（png/base64）用于视觉反馈。这为我们的 `blender-mcp` 和 `vectorworks-mcp` 提供了坚实的定心丸。
- 建议动作：参考这些现有项目中的 `execute_script` 沙箱和错误截获设计。

---

## 专题：写实「经年磨损与局部破损」

为实现江户/赛博朋克写实街区的岁月痕迹，调研确立 2 条可行技术路线，可封装为 openBIMAgent `materialist` 的工具集：

### 路线 1：材质节点级风化 (Material Shader Nodes)
- **原理**：利用大尺度噪波 (Musgrave/Perlin) 与曲率/尖锐度 (Pointiness/Bevel) 节点相乘，提取资产的边缘和缝隙，用 ColorRamp 控制磨损程度并混合两种 BSDF（如锈迹与干净金属）。
- **代表实现**：各类 Blender YouTube procedural weathering 教程（如 Erindale），以及 Infinigen 材质库。
- **集成成本**：极低。可以要求 `materialist` 代理直接输出相应的 `bpy.data.materials` 构建代码，渲染时开销小。
- **优点**：性能极佳，不会增加网格面数，非常适合作为底层的统一样板。

### 路线 2：几何节点级物理破损 (Geometry Nodes)
- **原理**：利用几何节点 (Geo Nodes) 进行布尔体积扣削 (Mesh Boolean) 模拟缺口，并在缺口边缘撒点生成碎石/废渣。
- **代表实现**：Alex Martinelli 的 Procedural Damage 库，cgvirus/blender-geometry-nodes-collection。
- **集成成本**：中等。建议在 `blender-mcp` 中内置一个隐藏的 "Damage_GeoNodes_Setup" 资产库文件，让建模代理调用 `append` 挂载该修改器，而非由 LLM 当场从头写庞大的节点树。
- **缺点**：计算开销大，大量对象使用会导致视口卡顿。

---

## 资产源 API 调研
- **Sketchfab (Fab)**：API 请求限制较严，有约 300 次下载/Key 的隐性配额，容易触发 429 错误。
- **Poly Haven**：提供开源、CC0 资产的 API，配额限制极宽，非常适合用于 HDRI、基础材质的获取。
- **Objaverse**：本质是研究型数据集（通过 Hugging Face 等托管），无传统按次调用的限制，受带宽和平台限制。
- **结论**：我们的 `researcher` 代理或 `blender-mcp` 的 fetch 工具，必须建立本地资产 Cache 层并支持指数退避（Exponential Backoff）处理 429。

---

## 横向对比表

| 项目 / 技术 | 核心价值 | 对 openBIMAgent 的意义 | 落地难度 | 许可证风险 |
|---|---|---|---|---|
| **Infinigen** | 极致过程化规则 | 直接提供材质与资产的 Blender 节点树写法库 | 中 | 无 (BSD) |
| **Holodeck** | 检索与布局结合 | 提供 Objaverse 检索到组装的 pipeline 参考 | 低 | 无 (Apache) |
| **SceneCraft** | 视觉反馈循环 | 佐证我们“双环自检”架构的前瞻性 | 低 | 无 (无源码) |
| **BlenderGPT** | 单向 LLM 生成 | 提供反面教材（需防范 AST 和运行时崩溃） | 极低 | 高 (GPLv3) |
| **build123d** | Python 编程式 CAD | 未来作为高精度 CAD 实体的核心生成库 | 低 | 无 (Apache) |

---

## 对 openBIMAgent 的建议

1. **确立 Infinigen 为材质/生成参考金标准 (价值极高)**
   - 影响：`ARCHITECTURE.md` §3 (Blender 精检环) 和建模代理工具集。
   - 建议：不用让 LLM 从零开始写 Blender Python 来构建材质。我们应该在 `blender-mcp` 挂载一套类似 Infinigen 剥离出来的基础 procedural 材质库，让 `materialist` 只需传入参数（老化度、颜色），以极大降低大模型下发代码的 token 数和失败率。
2. **规范化几何破损的实现方式**
   - 影响：`COMPONENTS.md` §3 (内置 agent 规格) - `modeler`。
   - 建议：物理破损（缺角、断裂）一律采用预置的 Geometry Node 修改器，通过 Agent 控制暴露在修改器面板上的 factor 参数，严禁 Agent 手写 boolean 破坏逻辑。
3. **引入基于 build123d 的精确 CAD 第三环 (备选方案)**
   - 影响：`ARCHITECTURE.md` 路线图 (M2+)。
   - 建议：如果 OpenSCAD 环表现出语法表达瓶颈，可以直接用 build123d 替换它，其显式的 Python `with` 语法更适合 Pro 级模型输出。
4. **资产下载层的本地缓冲设计**
   - 影响：`COMPONENTS.md` §2。
   - 建议：引入 `asset_cache` 目录，所有检索到的 Poly Haven / Objaverse 资产通过 hash 校验后落盘，切断高频反复抓取导致的 429 报错。

---

## 入库检查单

- [x] 调研中间笔记已完成并在正式报告入库后清理
- [x] 产出文件：`docs/research/06_gemini_3d_cad_ecosystem.md` (本正式报告)
- [ ] 未完成项：无。所有要求的必查项目及专题均已覆盖并评估价值与传染性。
- [ ] 建议下一步：主会话审阅本报告，并将基于预置 Geometry Nodes 实现破损、Infinigen 材质提取等动作写入后续的实现 TODO 中。用户回复「NNN 完成」即可。
