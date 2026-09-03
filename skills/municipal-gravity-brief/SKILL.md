---
name: municipal-gravity-brief
description: 市政重力管网新建任务的标准作业流程（槽位→求解→规则核验→双宿主交付）
when_to_use: 用户要求生成污水/雨水重力管网、布置检查井、做管线综合净距核验时
tools: [solver:self_healing, rules:gb50289.verify, cad_host:blender.execute]
---

# 市政重力管网标准作业流程

## 输入槽位（clarify 必须全部确认）
1. 管网类型：雨污分流 / 合流（可叠加给水/燃气/电力套管）
2. 服务范围：街区尺度（默认 200m×200m 走廊）
3. 埋深/覆土标准：默认按 Domain Pack knowledge/ 规范条文
4. 交付物：IFC 构件 + 纵断/剖切图 + （可选）漫游

## 执行步骤
1. `solver:self_healing` 生成 CompiledUtilityIR（确定性几何；碰撞→膨胀半径重路由，目标净距合规）
2. `rules:gb50289.verify` 规则核验（12 条净距 + 覆土 + 坡度；全部 PASS 才进下一步）
3. domain_gate 全 PASS 后才允许交付；UNKNOWN/FAIL 一律阻断并说明证据缺口
4. 交付走 HITL 审批门：`cad_host:blender.execute` / `cad_host:vectorworks.execute`（prompt 策略，confirm=true 才执行）

## 铁律
- 坐标/标高只能由 Solver 输出（C2）；LLM 只出语义，不猜数值
- 规则限值只引用 Domain Pack knowledge/constraints.yaml（受信任源）
- 交付只接受 artifact-manifest 提交的产物（C5）
