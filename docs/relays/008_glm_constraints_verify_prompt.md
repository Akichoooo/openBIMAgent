# Relay 008 · GLM 5.2:市政管网 constraints.yaml 二轮核实与扩充

用法:整段代码块贴给 GLM 5.2,完成后告诉主会话「008 完成」。

```text
你是 openBIMAgent 项目的领域核实子代理。先读并严格遵守调研协议:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\relays\RESEARCH_PROTOCOL.md
中间产物放 relay_workspace/008_constraints/{logs,scripts,raw,notes.md}。

# 背景

openBIMAgent 的市政管网 Domain Pack(毕设主线)有一份工程约束草案:
D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\domain_packs\municipal_utility\knowledge\constraints.yaml
它是前序调研(09 报告)产出的,只有 5 条规则,且 source_clause 未经规范原文核实。
这份 yaml 是 domain_gate(确定性规则校验)的数据源,数值错一条,生成的管网就错一片——你的任务是对照规范原文逐条核实并扩充。

# 任务 A:逐条核实现有 5 条规则

对 constraints.yaml 现有每条规则:
1. 找到规范原文出处(GB 50014-2021《室外排水设计标准》、GB 50013-2018《室外给水设计标准》、GB 50289-2016《城市工程管线综合规划规范》),给出准确条文号。
2. 核对数值是否准确(最小管径 300mm、最小坡度 0.003、管径缩减不超过 2 级、最小覆土 0.7m、压力让重力)。
3. 输出对照表:rule_id / 原值 / 核实值 / 准确条文号 / 置信度(高=见原文,中=权威二手,低=未查到) / 来源 URL。
4. 查不到原文的,明确标「未查到,建议人工核对」,禁止编造条文号。

# 任务 B:扩充规则(至少新增以下类别)

1. 检查井最大间距(按管径分档,GB 50014)。
2. 与其他管线/建筑物的最小水平净距(GB 50289 表格,给排水/燃气/电力/通信 两两组合至少 6 组)。
3. 雨污分流制要求与混接限制(GB 50014)。
4. 常用管径序列(200/300/400/500/600/800/1000 mm)及对应最小设计坡度。
5. 冰冻线埋深确定方法(GB 50013/50014 相关条文)。
6. 雨水口连接管最小管径(200mm)核实。

# 任务 C:更新 constraints.yaml

- 字段保持:rule_id / category / parameter / value 或 range / unit / source_clause;
- 每条新增/修改的规则加两个字段:verified_by(你的核实方式)与 confidence(high/medium/low)。
- 直接改写 D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\domain_packs\municipal_utility\knowledge\constraints.yaml(只动这一个 yaml 和 relay_workspace,其他文件一个字节都不碰)。

# 输出

1. 改写后的 constraints.yaml(核实+扩充后预计 15-25 条)。
2. 核实报告写入 D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent\docs\research\12_glm_constraints_verify.md:任务 A 对照表 + 任务 B 新增规则清单与出处 + 入库检查单。
3. 全部中文;每条事实带来源 URL;事实与推断严格分开。
```
