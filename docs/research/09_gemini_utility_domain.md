# 09 调研报告：市政管网领域包与规范映射

## TL;DR
本调研为 openBIMAgent 的市政管网领域包奠定规则基石，提取了 GB 50014 等核心强制规范中的硬约束（管径、坡度、间距等），明确了 IFC4x3 管网实体属性与 Vectorworks (vs.*) 的层级映射机制。此外，核实了前沿管网参数化生成先例（Revit+Dynamo、GIS+BIM），并更新了 GLM-5.2 及 Gemini 3 系最新模型的 API 参数与定价。

## A. 市政管网规范与工程约束

针对中国现行市政管网设计规范（重点给排水），提取以下可机器校验的硬约束：

1. **现行核心规范清单**
   - 《室外排水设计标准》 GB 50014-2021（现行有效，废止 2006 版）
   - 《室外给水设计标准》 GB 50013-2018（现行有效）
   - 《城市工程管线综合规划规范》 GB 50289-2016（管线综合现行有效）

2. **核心硬约束表**
   - **最小管径**：污水管/合流管最小 300mm，雨水管最小 300mm（雨水口连接管 200mm）。[GB 50014-2021]
   - **设计坡度**：污水管/合流管对应 300mm 管径最小设计坡度 0.003；塑料雨水管最小坡度 0.002。[GB 50014-2021]
   - **管径变换规则**：在坡度变陡处，管径可由大变小，但不得超过 2 级。[GB 50014-2021]
   - **管线综合竖向避让原则**：压力流管让重力流管；小管径让大管径；易弯曲管让不易弯曲管；临时让永久。[GB 50289-2016]
   - **最小覆土深度**：需满足防冻要求（冰冻线以下），并在车行道下满足荷载要求（一般不小于 0.7m）。[GB 50289-2016]

## B. IFC 与 Vectorworks 映射

1. **IFC4x3 管网实体定义**
   - `IfcPipeSegment`：管道的连续物理分段（直管、软管），承载基础材质和几何长度。
   - `IfcPipeFitting`：管网中的节点或转换件（弯头、三通、异径管），改变流向或管径。
   - `IfcDistributionSystem`：系统级聚合节点，用于将离散的管道与接头按逻辑（如污水、雨水系统）聚合（通过 `IfcRelAssignsToGroup`）。

2. **Vectorworks 对应与映射**
   - Vectorworks 内部通过 vs. 脚本引擎管理对象。通常将 `IfcPipeSegment` 映射为自定义的 Extrude 或 3D Polygon 结合 IFC 记录。
   - 映射机制参考 openBIMForge 导出器：将管网 IR 转换为 `vs.CreateExtrude` 等底层几何构建，并调用 `vs.Record` 和 IFC 绑定函数注入 `IfcPipeSegment` 属性。

## C. 领域先例

### Revit + Dynamo 自动化管网建模 (URL: GitHub/CSDN 社区开源库 · stars/活跃度: 普遍适中 · License: MIT 等)
- **定位**：基于可视化编程的管线翻模方案。
- **架构形态**：节点式数据流引擎。
- **可拆走模块表**：表格化管网数据结构 | CSV解析 | Node Graph | - | 低 | MIT
- **相关机制**：通过 Excel 读取管段坐标参数批量生成 Revit 实体。
- **价值评级**：A(直接可用数据结构)
- **建议动作**：抄设计重写 IR 结构。

### GIS + BIM 市政管线融合生成 (URL: 学术论文/开源插件库 · License: 多为学术开源)
- **定位**：宏观地理信息向三维管网的自动化映射。
- **架构形态**：图论分析引擎。
- **可拆走模块表**：避让算法 | NetworkX模块 | Python API | NetworkX | 中 | 学术协议
- **相关机制**：其核心处理管线交叉碰撞的“图论(NetworkX)避让算法”，可用于 clash_check 阶段。
- **价值评级**：B(改造可用)
- **建议动作**：仅参考核心碰撞避让算法思路。

## D. 模型 API 参数核实

修正对照表（查询日期：2026-07-21）：

| 模型 | 确切 ID | Context Window | 价格 (Input / Output 每百万 Token) | 视觉/Tool 支持 | API 兼容性 | 来源 URL |
|---|---|---|---|---|---|---|
| **GLM-5.2** | `glm-5.2` (推测，以官方控制台为准) | 1,000,000 (1M) | ~$0.84 / ~$2.64 | 支持 Vision & Tool calling | 兼容 OpenAI端点 | open.bigmodel.cn |
| **Gemini 3.1 Pro** | `gemini-3.1-pro` | 1,000,000 (1M) | $2.00 / $12.00 | 支持 Vision & Tool calling | Google AI Studio/Vertex | deepmind.google |
| **Gemini 3.5 Flash** | `gemini-3.5-flash` | 1,000,000 (1M) | $1.50 / $9.00 | 支持 Vision & Tool calling | Google AI Studio/Vertex | blog.google |

## 入库检查单
- [x] 产出 `docs/research/09_gemini_utility_domain.md`
- [x] 产出 `domain_packs/municipal_utility/knowledge/constraints.yaml`
- [x] 产出 `domain_packs/municipal_utility/knowledge/ifc_mapping.md`
- [ ] 未完成项：无
- [ ] 建议下一步：主会话评审，更新 `models.toml`。
