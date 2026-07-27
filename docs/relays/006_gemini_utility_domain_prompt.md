# Relay 006 · Gemini 3.1 Pro:市政管网领域知识 + 模型 API 核实

用法:整段代码块贴给 Gemini 3.1 Pro,完成后告诉主会话「006 完成」。

```text
你是 openBIMAgent 项目的调研子代理。先读并严格遵守调研协议:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
中间产物放 relay_workspace/006_utility_domain/{logs,scripts,raw,notes.md}。

# 背景

openBIMAgent:开源「Agent + vectorworks-mcp + blender-mcp」生成式建模系统,采用 Domain Pack(领域专家包)垂直化策略,第一个领域包 = 市政管网(毕设主线)。先读:
- D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\domain_packs\municipal_utility\playbook.md
- D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\architecture\ARCHITECTURE.md (§4 Domain Pack、§9 M1.5)
你的任务:为该包填充 knowledge/ 草案,并核实模型配置参数。

# 任务 A:市政管网规范与工程约束(knowledge 主力)

调研中国现行市政管网设计规范(给排水为主,兼顾燃气/电力套管),提取可机器校验的硬约束:
1. 规范清单:现行有效版本(如 GB 50014 室外排水设计标准、GB 50013 室外给水设计标准、CJJ 相关系列),逐条核实是否最新有效。
2. 硬约束表(每条:约束项/数值或区间/出处规范+条文号):最小管径、设计坡度范围、最小覆土深度(冰冻线/荷载)、检查井最大间距、管径变换规则、雨污分流要求、管线综合竖向避让原则、与其他管线/建筑基础的最小水平净距。
3. 产出两份:
   - 报告章节(进 09 报告);
   - 机器可读草案 D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\domain_packs\municipal_utility\knowledge\constraints.yaml,字段:rule_id / category / parameter / value 或 range / unit / source_clause。这是 domain_gate 的校验数据源,务必结构化、数值化。

# 任务 B:IFC 与 Vectorworks 映射

1. IFC4x3 管网相关实体:IfcPipeSegment / IfcPipeFitting / IfcDistributionPort / IfcDistributionSystem / IfcSanitaryTerminal 等的定义、关键属性、与 IFC2x3 的差异(以 buildingSMART 官方文档为准)。
2. Vectorworks 侧对应对象/记录格式:vs.* 中与管道/装置相关的函数,可参考(只读):
   - D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMForge\forge_core\design_agent\vs.py
   - D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMForge\forge_core\mep_agent\ (IFC 导出代码)
3. 给出「管网 IR → IFC 实体 → VW 对象」三层映射草案表,写入 knowledge\ifc_mapping.md。

# 任务 C:领域先例

市政管网 BIM / 参数化 / AI 生成的论文或开源项目 3-5 个,各一段:做了什么、我们能拿什么。

# 任务 D:模型 API 参数核实(models.toml 占位待核,重要)

联网核实以下三个模型的最新官方参数,给修正对照表:
1. GLM-5.2(智谱,open.bigmodel.cn):确切模型 ID、context window、输入/输出价格、tool calling 与 vision 支持、OpenAI 兼容端点路径。
2. Gemini 3.1 Pro:确切模型 ID、context window、价格、vision/tool 能力。
3. Gemini 3.5 Flash:同上。
每项附来源 URL 与查询日期;查不到的明确写「未查到,以官方控制台为准」,禁止编造。

# 输出

- 正式报告:D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\09_gemini_utility_domain.md(TL;DR + A/B/C/D 四节 + 入库检查单)。
- knowledge 草案:constraints.yaml(必做)、ifc_mapping.md(必做)。
- 全部中文;事实/推断严格分开;来源 URL 必填。
```
