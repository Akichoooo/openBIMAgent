# Procedural 材质库（Infinigen 风格）

## 设计原则

1. **纯 procedural**：全部基于 Blender Shader Nodes，零外部贴图（无 PNG/JPG 依赖）
2. **Infinigen 风格**：参数化节点组（Node Group），LLM 只调参数（颜色/粗糙度/金属度），不从零写节点树
3. **PBR 标准**：Principled BSDF 为核心，物理正确
4. **非破坏性**：节点组封装内部逻辑，参数暴露为 node group interface，调用方不触碰内部节点

## 材质清单

| 材质节点组 | 输入参数 | 默认值 | 用途 |
|-----------|---------|--------|------|
| `MetalWorn` | Color, Roughness, Metallic, WearAmount | (0.55,0.55,0.6,1) / 0.65 / 1.0 / 0.4 | 磨损金属（管道/支架/外壳） |
| `WoodOak` | Color, Roughness, GrainScale | (0.45,0.30,0.18,1) / 0.6 / 8.0 | 橡木（家具/地板/标牌） |
| `ConcreteRough` | Color, Roughness, BumpScale | (0.62,0.60,0.58,1) / 0.85 / 0.5 | 粗糙混凝土（墙体/地面） |
| `PlasticMatte` | Color, Roughness | (0.20,0.20,0.22,1) / 0.55 | 哑光塑料（外壳/容器/按钮） |
| `GlassFrosted` | Color, Roughness, IOR | (0.85,0.90,0.95,1) / 0.25 / 1.45 | 磨砂玻璃（窗户/瓶罐） |

## 优先级清单（materialist 必须遵守）

按资产类别选择预置材质：

- 金属 → `MetalWorn`
- 木材 → `WoodOak`
- 混凝土 → `ConcreteRough`
- 塑料 → `PlasticMatte`
- 玻璃 → `GlassFrosted`

## 使用方式（Blender Python）

```python
import bpy

# 1. Append 材质节点组（从 materials.blend）
bpy.ops.wm.append(
    filepath="//mcp_servers/blender_mcp/assets/materials/materials.blend/NodeTree/MetalWorn",
    directory="mcp_servers/blender_mcp/assets/materials/materials.blend/NodeTree/",
    filename="MetalWorn",
)

# 2. 应用到对象（材质槽挂节点组）
mat = bpy.data.materials.new("MyMetal")
mat.use_nodes = True
mat.node_tree.nodes.clear()
group_node = mat.node_tree.nodes.new('ShaderNodeGroup')
group_node.node_tree = bpy.data.node_groups["MetalWorn"]
out = mat.node_tree.nodes.new('ShaderNodeOutput')
mat.node_tree.links.new(group_node.outputs[0], out.inputs[0])

# 3. 调参数（只调参数，不动内部节点）
group_node.inputs["Color"].default_value = (0.8, 0.75, 0.7, 1.0)
group_node.inputs["Roughness"].default_value = 0.7
group_node.inputs["Metallic"].default_value = 0.9
group_node.inputs["WearAmount"].default_value = 0.5
```

## 重新生成 .blend

```powershell
D:/devloop/blender/blender.exe --background --factory-startup --python mcp_servers/blender_mcp/assets/materials/generate_materials.py
```

生成产物：`materials.blend`（含 5 个 NodeTree）。

## Blender 5.2 兼容要点

- node group interface 用 `group.interface.new_socket()`（5.x API），**非**旧版 `group.inputs.new()`
- 节点组类型 `'ShaderNodeTree'`（材质），GeoNodes 用 `'GeometryNodeTree'`
- Principled BSDF 5.x 输入名："Base Color"/"Metallic"/"Roughness"/"IOR"；Transmission 改为 "Transmission Weight"
