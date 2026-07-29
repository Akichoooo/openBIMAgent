# Damage GeoNodes 修改器资产库

## 设计原则

1. **非破坏性修改器**：保留原始几何体，通过 Geometry Nodes 添加磨损/破损效果（可随时关闭修改器还原）
2. **参数化**：LLM 只调参数（强度/随机种子/范围），不手写节点树
3. **禁止手写 boolean**：磨损/破损一律走预置 GeoNodes（规则写死进 `agents/materialist.md`）
4. **纯 procedural**：基于 Geometry Nodes 内置节点（Noise/Position/Normal），零外部资产

## GeoNodes 修改器清单

| 节点组 | 输入参数 | 默认值 | 效果 |
|--------|---------|--------|------|
| `DamageEdgeWear` | Geometry, Intensity, Seed | 0.4 / 42 | 边缘磨损（按法线方向偏移顶点制造磨圆/缺口感） |
| `DamageRustSpots` | Geometry, Density, Size, Seed | 0.3 / 0.05 / 7 | 锈斑（在表面散布小凹陷实例） |
| `DamageScratches` | Geometry, Count, Length, Seed | 50 / 0.1 / 11 | 划痕（程序化线条刻痕） |

## 使用方式（Blender Python）

```python
import bpy

# 1. Append GeoNodes 节点组（从 damage_geonodes.blend）
bpy.ops.wm.append(
    filepath="//mcp_servers/blender_mcp/assets/geonodes/damage_geonodes.blend/NodeTree/DamageEdgeWear",
    directory="mcp_servers/blender_mcp/assets/geonodes/damage_geonodes.blend/NodeTree/",
    filename="DamageEdgeWear",
)

# 2. 应用到对象（作为修改器，非破坏性）
obj = bpy.context.active_object
mod = obj.modifiers.new("EdgeWear", 'NODES')
mod.node_group = bpy.data.node_groups["DamageEdgeWear"]

# 3. 调参数（只调参数，不手写 boolean）
mod["Intensity"] = 0.5   # 强度
mod["Seed"] = 42         # 随机种子
```

## 重新生成 .blend

```powershell
D:/devloop/blender/blender.exe --background --factory-startup --python mcp_servers/blender_mcp/assets/geonodes/generate_geonodes.py
```

生成产物：`damage_geonodes.blend`（含 3 个 GeometryNodeTree）。

## 为什么用 GeoNodes 而非 boolean

1. **非破坏性**：boolean 改变基础网格，不可逆；GeoNodes 是修改器，可关闭/重调
2. **参数化**：强度/种子一调即可换风格，boolean 要重做
3. **性能**：GeoNodes 求值延迟到渲染，viewport 可降级；boolean 即时改拓扑
4. **质量**：预置节点组经过调参，磨损自然；LLM 手写 boolean 易出穿模/破面

## Blender 5.2 兼容要点

- GeoNodes 节点组用 `bpy.data.node_groups.new(name, 'GeometryNodeTree')`
- interface 用 `group.interface.new_socket()`（5.x API）
- 输入 Geometry socket 必须命名为 "Geometry" 并 `in_out='INPUT'`
- 输出 Geometry socket 同名 `in_out='OUTPUT'`
