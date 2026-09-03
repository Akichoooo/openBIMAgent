---
name: ir-inspection
description: 解读 CompiledUtilityIR / 规则证据包 / domain_gate 报告，定位不合规项并给出修复方向
when_to_use: 当任务在 domain_gate 被阻断、规则核验出现 FAIL/UNKNOWN、或用户询问 IR 内容时
---

# IR 与规则证据解读流程

## 读取顺序
1. `compiled_utility_ir.json`：核对 nodes（检查井坐标/地面/管内底标高）与 segments（管径/坡度/长度/中心线）
2. `domain_gate_report.json`：逐项检查 PASS / FAIL / UNKNOWN；UNKNOWN = 证据缺口（不是违规），需补 solver 证据键
3. `municipal_rule_set.json`：确认规则集 canonical_sha256 与证据包一致（防篡改）

## 常见阻断模式 → 修复方向
- clash_free UNKNOWN → collision_context 缺 coverage=complete 或障碍物清单；补全后重跑
- 净距 FAIL → 触发自愈重路由（膨胀半径）；仍 FAIL 则调整走廊或管位
- 坡度/覆土 FAIL → 检查 design_slope 与 ground/invert 关系；按 GB 50014 修正

## 输出要求
- 引用规则必须带 rule_id 与 source_clause；不确定的数值标注"待 Solver 重算"
