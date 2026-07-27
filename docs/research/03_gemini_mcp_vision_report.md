# 调研协议(RESEARCH PROTOCOL) - openBIMAgent MCP与视觉自检模块

**执行摘要 (TL;DR)**
本次调研深入解剖了 `blender-mcp` 与 `vwx-mcp` 的核心机制，并结合 SceneCraft (arXiv:2403.01248) 等最新文献，为 openBIMAgent 的“双环视觉自检”和工具集成制定了规格。针对 Blender，指出了在 Headless 模式下的改造路径及工具精简方案；针对 Vectorworks，拆解了三层架构与 `vs_index.json` 索引，确定了与现有 handoff 机制的融合点。同时，设计了一套包含防放水机制的六维 3D 渲染 VLM 评分基准，为高质量场景生成闭环奠定基础。

---

## 任务 A: blender-mcp 源码级解剖

### ahujasid/blender-mcp (https://github.com/ahujasid/blender-mcp)
- **一句话定位**: 将 Blender 功能暴露为 MCP 工具集的 Socket 服务端插件。
- **架构形态**: 单实例多线程 Socket 监听 -> `bpy.app.timers.register` 将执行委托给主线程 -> 返回 JSON。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  | --- | --- | --- | --- | --- | --- |
  | Socket 核心服务 | `src/blender_mcp/server.py` | TCP JSON 通信 | 无 | 低 | MIT |
  | Headless 截图 | `addon.py` (get_viewport_screenshot) | 返回图片元数据 | bpy, gpu | 中 | MIT |
- **相关机制**:
  - **工具清单与改造**: 提供 `get_scene_info`, `get_object_info`, `get_viewport_screenshot`, `execute_code`。需要移除 Polyhaven/Hyper3D 等冗余工具。超时限制可通过 Socket timeout 配置（默认 180s）。
  - **Socket 协议**: 监听 `localhost:9876`，单连接循环 `json.loads(b''.join(chunks).decode('utf-8'))`，错误以 `{"status": "error", "message": "..."}` 抛出，并发请求依赖锁避免数据流交错。
  - **截图实现细节**: 使用 `gpu.types.GPUOffScreen.draw_view3d` 在内存渲染视口，摆脱 OS 窗口依赖；如果失败退化到 `bpy.ops.screen.screenshot_area`。
  - **遥测代码**: `src/blender_mcp/telemetry.py`，收集 tool_name, prompt 等。
- **价值评级**: B(改造可用)
- **建议动作**: fork 改造

**对 openBIMAgent 的建议 (Blender 改造)**:
1. **彻底关闭遥测**: 硬编码 `telemetry.py` 中 `self.config.enabled = False`，或移除 `telemetry_decorator`。
2. **Headless 支持**: 移除 `addon.py` `start()` 方法中对 `bpy.app.background` 的阻断检查。
3. **AST Allowlist 与快照**: 在 `execute_code` 的 `exec()` 之前注入 `bpy.ops.wm.save_as_mainfile`，并引入 `ast` 模块进行白名单校验。
*(影响 ARCHITECTURE.md §5)*

---

## 任务 B: vwx-mcp 源码级解剖

### vicquick/vwx-mcp (https://github.com/vicquick/vwx-mcp)
- **一句话定位**: 针对 Vectorworks 2026 的防假死异步 MCP Server。
- **架构形态**: 三层架构。Trigger (C++ Native Palette) -> Executor (MenuCommand) -> Work (Python MCP Server via File IPC)。
- **可拆走模块表**:
  | 模块 | 文件/包 | 接口形态 | 外部依赖 | 集成成本 | 许可证兼容 |
  | --- | --- | --- | --- | --- | --- |
  | vs.* 签名索引 | `vs_index.json` | 静态 JSON 词典 | 无 | 低 | MIT |
  | 工具标签预设 | `mcp-server/tool_tags.py` | Python Dict | fastmcp | 低 | MIT |
- **相关机制**:
  - **三层架构代码位置**:
    - Trigger: `native/VwxBridge2026.vcxproj` (响应 OnIdle 不冻结界面)
    - Executor: `vwx-plugin/BridgeStart_MenuCommand.py` (运行于安全文档上下文中)
    - Work: `vwx-plugin/vwx_pump.py` 与 `commands.py`。
  - **File IPC**: 以 `ipc/jobs/<ts>-<cid>.json` 写入，`vwx_pump` 消费后写入 `ipc/results/<cid>.json`。
  - **VWX_TOOLSET 机制**: 预设白名单 (`modeling`, `gis` 等) 配合 fastmcp 的 `tags` 与 Visibility API，有效限制上下文中工具的数量 (248 减至 40~100)。
  - **vs_index.json**: 结构包括 `args`, `arity`, `ret`, `doc`，使工具不仅能在发送前校验 Arity 避免引擎崩溃，也能支持智能代码补全。
  - **Vectorworks 坑点**: `AGENTS.md` 记录了 `vs.ArcByCenter` 损坏需替换为 `Oval`, `Arc` 的第六个参数为 Sweep, 禁用旧版 `GetClassName` 转用 `GetClass` 等关键差异。
  - **文档约束与事故**: 仅脚本 runner 上下文可安全修改，偏离此约定可能导致几何变异或直接崩溃。
- **价值评级**: A(直接可用，需剥离出我们需要的 API)
- **建议动作**: 结合 openBIMForge 现有 Handoff 重写。

**对 openBIMAgent 的建议 (Vectorworks 拆分)**:
对比 openBIMForge 的轮询锁，应吸纳 vicquick 的 **C++ Palette 闲置通知机制**以提升响应。原有的 `handoff/hash/approval` 应植入到 Executor 层 (`BridgeStart_MenuCommand.py` 的执行前门禁)。
*(影响 ARCHITECTURE.md §5)*

---

## 任务 C: VLM 视觉评分 rubric 调研

### 核心论文: SceneCraft (arXiv:2403.01248) 及其扩展
- **一句话定位**: 整合 VLM 作为 Critic 对 3D 渲染和结构脚本进行闭环优化的方案。
- **Refine 循环细节**:
  - **Prompt**: 传递 Scene Graph 与约束条件，附带最新渲染图，要求 VLM 分析不满足的约束。
  - **结构化反馈**: 输出必须指出「失效位置(Failure Location)」「当前观测状态(Observed State)」「可接受的替代参数(Admissible Alternatives)」。
  - **Library Learning**: 遇到重复的错误布局，VLM 优化后的 Python 函数将被提取并缓存在持久化“技能库”中，防止下一批资产犯同样错误。

### 评分规格 (Rubric)
1. **几何正确性 (Geometry)**: 定义: 结构比例与边界无穿插。锚点: 0(严重漂浮/交错)，5(有轻微重叠)，10(完全遵循物理空间)。
2. **风格一致性 (Style)**: 定义: 资产是否符合设定的流派(如江户赛博)。锚点: 0(出戏，如古代出现现代垃圾桶)，5(元素堆砌)，10(浑然天成)。
3. **材质贴图真实感 (Material)**: 定义: 纹理分辨率、法线贴图及反光正确性。锚点: 0(缺失贴图/纯色)，5(低分辩率/重复纹理)，10(PBR 真实)。
4. **经年磨损破损叙事 (Weathering)**: 定义: 做旧、划痕和环境互动。锚点: 0(一尘不染/塑料感)，5(均匀的噪声脏迹)，10(自然的水渍、磕碰、边缘磨损)。
5. **灯光氛围 (Lighting)**: 定义: 色温、阴影和层次感。锚点: 0(全白无阴影)，5(有光源但死板)，10(体积光、层次分明的 GI)。
6. **镜头构图 (Composition)**: 定义: 焦段与主体突出度。锚点: 0(主体被遮挡/跑焦)，5(居中平庸)，10(有前景遮挡、英雄机位)。

### 防放水机制 (System Prompt 段落)
> "你是一个苛刻的 3D 艺术总监。必须执行两两比较 (A/B Swap consistency)：将生成的图片与上一版本的快照对比。对于每个维度，如果得分 < 8，你必须强制输出具体的 `actionable_rework_command`（例: 'Object A 缩放 0.8 并沿 Z 轴下降 0.2'）。禁止输出空泛的建议。分数需与提供的金标准锚点图严丝合缝地对齐。"

### 适用场景
- **SCAD 白模环**: 仅评测「几何正确性」和基础构图。裁剪掉材质、灯光、磨损等不可见维度，以追求毫秒级的验证速度。
- **Blender 渲染环**: 全维度激活，以真实感、磨损和灯光氛围的闭环验收为核心。

**对 openBIMAgent 的建议 (双环自检)**:
严格落实结构化反馈。SCAD 快检失败时重写生成坐标；Blender 精检失败时侧重于更新着色器节点与光源。
*(影响 ARCHITECTURE.md §3)*

---

### 入库检查单
- [x] 完成 `blender-mcp` 分析及改造清单
- [x] 完成 `vwx-mcp` IPC 及工具预设分析
- [x] 完成 VLM 评分 Rubric 设计
- 未完成项: 无。
- 建议下一步: 主会话提取架构变更，开发人员启动 `blender-mcp` fork 的改造。
