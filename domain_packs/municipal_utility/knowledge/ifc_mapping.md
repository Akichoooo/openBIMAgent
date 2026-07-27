# 管网 IR → IFC 实体 → VW 对象映射草案

## 映射层级表

| 路由 IR 语义节点 | IFC4x3 实体分类 | Vectorworks (vs.*) 内部实现对应 |
| :--- | :--- | :--- |
| **System/Network** | `IfcDistributionSystem` | VW Layer/Class 逻辑组，通过 `vs.NameClass` 和 IFC 数据附加 `IfcRelAssignsToGroup` |
| **Pipe/Tube (管段)** | `IfcPipeSegment` (SubType: `RIGIDSEGMENT`) | VW 3D Polygon 或 Extrude (`vs.CreateExtrude`) + 绑定 `IfcPipeSegment` 记录 |
| **Joint/Elbow (弯头/三通)**| `IfcPipeFitting` | VW Symbol (`vs.Symbol`) 或 Solid Operations (`vs.AddSolid`) + 绑定 `IfcPipeFitting` |
| **Connection Port (连接口)**| `IfcDistributionPort` | VW 3D Locus (`vs.Locus3D`) 定义拓扑连接点位 |

## 实施备注
1. **语义保留**：路由 IR (`route_ir.json`) 中只负责拓扑和属性，不含绝对坐标（遵循 C2 铁律）。
2. **实体生成**：由 Vectorworks MCP 工具集中的 `vs` 函数负责基于语义属性生成底层 3D 几何构件，最后附加上述相应的 IFC4x3 数据记录。
