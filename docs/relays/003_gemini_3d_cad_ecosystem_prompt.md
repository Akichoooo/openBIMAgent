# Relay 003 · Gemini 3.1 Pro:3D/CAD/场景生成开源生态

用法:整段代码块贴给 Gemini 3.1 Pro,完成后告诉主会话「003 完成」。可与 001/002/004/005 并行。

```text
你是 openBIMAgent 项目的调研子代理。先读并严格遵守:
- 调研协议:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
- 架构背景:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\ARCHITECTURE.md 与 COMPONENTS.md
中间产物放 relay_workspace/003_3d_cad/{logs,scripts,raw,notes.md}。正式报告写到:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\06_gemini_3d_cad_ecosystem.md

# 调研目标

我们要做「Agent + Blender MCP + Vectorworks MCP + SCAD/Blender 双环视觉自检」的写实街区生成。凡是有可拆走模块的 3D/CAD/场景生成开源项目都要过一遍,重点提取:过程化资产生成、材质/磨损生成、场景图→约束、资产检索 API、CAD 内核。

# 必查项目(发现阶段可再补充)

1. **SceneCraft**(arXiv:2403.01248,找官方代码仓):scene graph schema、GPT-4V refine 循环实现、library learning 代码——我们双环的学术原型,模块级细拆。
2. **Infinigen**(Princeton,过程化 Blender 资产生成):生成器组织方式、材质/几何节点程序化程度、能否单独拆「建筑/街道/道具」生成器、许可证。
3. **Holodeck**(AI2,语言引导 3D 场景生成):Objaverse 资产检索管线、约束求解布局——「先检索真实资产再建模」机制的参照。
4. **3D-GPT**(arXiv:2310.12945):多 agent 纯文本规划接 Blender 的做法与失效点。
5. **BlenderGPT**(gd3kr/BlenderGPT)与类似 Blender LLM 插件:一次性 codegen 模式的教训;有没有仍在维护的 fork。
6. **LayoutGPT** 及 2024-2026 街区/城市级生成工作(如 CityGen、SceneVerse 等,发现阶段核实):布局 IR 设计。
7. **build123d / CadQuery**:Python 编程式 CAD 内核——若 openBIMAgent 未来加「精确 CAD 实体」第三 MCP,选谁;与 GenCAD cadlib(DeepCAD 命令+OCC 重建,见 D:\devloop\workSpace\app_codex\GenerativeBIM\GenCAD-main\cadlib\)对比。
8. **trimesh / OpenSCAD**:工具链角色定位;OpenSCAD 有没有 MCP server 先例(搜索核实)。
9. **资产源 API**:Objaverse、Poly Haven、Sketchfab 的检索/下载 API 现状与配额——researcher 子代理和 blender-mcp 资产工具要用。
10. **MCP-for-CAD 先例**:FreeCAD / KiCad / OpenSCAD / Houdini 的 MCP server(搜索核实存在性、stars、工具设计)。

# 专题:写实「经年磨损与局部破损」

检索 procedural weathering / aging / damage 的开源实现(Blender 几何节点、材质节点、贴图库),给出可落地的技术路线 2-3 条,各配代表项目与集成成本。这是我们 playbook 硬性要求之一。

# 输出

按 RESEARCH_PROTOCOL §4 契约:每项目固定模板(含可拆走模块表、价值评级、建议动作、许可证传染性),末尾横向对比表 + 对 openBIMAgent 的建议 + 入库检查单。
```
