# openBIMAgent Web UI — 接口业务梳理（UI 原型依据）

日期：2026-08-23 · 用途：`ui/prototype.html` 原型的数据与业务依据；审核通过后按此映射集成进 `src/openbimagent/server/web_ui.py`。

## 一、业务全景（UI 要呈现什么）

系统 = **微内核插件化的市政管网生成 Agent**：用户一句工程指令 → 确定性求解器（含规则自愈）→ GB 50289 规则核验 → 人工确认 → 真实 CAD 宿主（Blender/Vectorworks）受控写盘交付。

```text
[用户指令] ──▶ solver:self_healing ──▶ rules:gb50289 核验 ──▶ HITL 确认(prompt 策略)
                    │                                              │
                    ▼                                              ▼
            CompiledUtilityIR (6 井/5 段)              cad_host:blender.execute
            + 自愈时间线 (2 轮收敛)                     cad_host:vectorworks.execute
                                                           │
                                                           ▼
                                                 .blend / .vwx + sidecar 回执
```

## 二、接口清单（真实端点 → UI 映射）

| # | 端点 | 方法 | 输入 | 输出要点 | UI 挂载位置 |
|---|---|---|---|---|---|
| 1 | `/api/v1/demo/municipal-pipeline` | GET | — | converged / iterations_spent / nodes[6]{x,y,invert_z,ground} / segments[5]{points,diameter_mm,slope,length_m} / resolved_violations{rule_id,required,actual,description} / timeline[] | 线程主 turn 的工具块 + 工件卡（平面图/纵断面 SVG 由 nodes/segments 绘制） |
| 2 | `/api/v1/demo/rule-tree` | GET | — | 12 条规则 {rule_key, required_clearance_m, obstacle_category, clause, table, enforcement, self_test_match/not_match} + canonical_sha256 | 工作台「规则树」标签；线程内 rules 工具块摘要 |
| 3 | `/api/v1/demo/runtime-info` | GET | — | llm{model=gpt-5.6-terra, base_url, configured} + registry{plugins=7, capabilities=16, policies=2}（**永不含 key**） | 左栏底部模型芯片 + 设置弹层 |
| 4 | `/api/v1/demo/export-blender` | POST | `{confirm: true}` | receipt{status=completed, objects=22, output_bytes, elapsed_ms, output_path, plan_sha256}；confirm 缺失→策略门拒绝 | HITL 工件卡的「批准导出」按钮（浏览器 confirm → POST） |
| 5 | `/api/v1/demo/export-vectorworks` | POST | `{confirm: true}` | 同上（applied_operations/confirmed_objects 口径）；需 VW 宿主 runner 运行中 | 同上第二个按钮 |
| 6 | `/api/v1/plugins` | GET | — | plugin_count / capabilities_map(16) / profiles(3) / capability_policies(2) / ui_slots | 工作台「插件清单」标签（DSH Slots 视图） |
| 7 | `/api/v1/ui/slots` | GET | — | 声明式 UI 插槽（工作台标签动态装配源） | 工作台标签生成 |
| 8 | `/api/v1/plugins/invoke` | POST | `{capability, payload, confirm}` | 任意微内核能力调度结果（结构化） | 「能力控制台」（高级面板：选能力+payload+confirm 运行） |
| 9 | `/api/v1/sessions` | GET | — | Session JSONL 会话列表（id/标题/时间） | 左栏会话树 |
| 10 | `/api/v1/sse`（事件流） | GET | — | 运行事件流（cursor 续读） | 线程实时事件（M3 接入中，原型先静态） |
| 11 | `/healthz` `/readyz` | GET | — | 存活/就绪 | 设置弹层连接状态 |

## 三、关键业务数据形状（原型 mock 采用真实值）

**自愈求解结果（端点1）**：`converged=true, iterations_spent=2`；节点示例 `{node_id:"source", x:0, y:0, invert_z:10.0, ground:11.0}`；管段含 centerline points；已消解违规示例 `{rule_id:"MU-CLEAR-001", required:"2.5m", actual:"<2.5m(障碍物)", description:"建(构)筑物水平净距碰撞…"}`。

**规则集（端点2）**：MU-CLEAR-001 建筑 2.5m；MU-CLEAR-005 给水 d≤200→1.0m / d>200→1.5m；MU-CLEAR-006 燃气五档 1.0~2.0m；通信 1.0m；电力 0.5m。全部 enforcement=production，自检样例合计 33。

**导出回执（端点4/5）**：Blender `{status:"completed", objects:22, output_bytes:120031, elapsed_ms:2187, output_path:"D:/devloop/G6_Test/…blend"}`；VW 同构（8.22s 验收实测）。

## 四、UI 分区与数据绑定（原型的骨架）

| 分区 | 内容 | 数据源 |
|---|---|---|
| 顶栏 | logo + 工作台开关 | 静态 |
| 左栏·上 | 会话树（Session JSONL） | 端点 9 |
| 左栏·下 | 模型芯片（gpt-5.6-terra）+ 设置弹层（模型/连接/运行时） | 端点 3 |
| 中栏·线程 | 用户气泡 → 工具块(solver/rules, 可折叠) → 工件卡（**平面布置图 SVG、纵断面 SVG**、HITL 审批、交付回执） | 端点 1/4/5 |
| 中栏·底部 | Composer（斜杠命令 /tree /rules /export /capabilities；普通文本→真实调端点1追加 turn） | 端点 1 |
| 右·抽屉(可开合) | 规则树 / 空间图谱&自愈 / 交付工件 / Compiled IR / 插件清单 / 能力控制台 | 端点 2/6/7/8 |

## 五、交互规则（原型必须可演示）

1. 工具块：点头部折叠/展开；状态三态（running… / ✓ / ✗）
2. HITL：点「批准导出」→ 原型内模拟 confirm 弹层 → 按钮转「执行中…」→ 1.5s 后交付卡填充回执（真实集成时换端点 4/5）
3. Composer：输入 `/` 弹出命令菜单；输入普通文本回车 → 追加用户气泡 + running 工具块 → mock 结果填充
4. 抽屉：顶栏按钮或工具块内链接打开；六标签可切换
5. 模型芯片：点开设置弹层；点外部关闭

## 六、集成计划（审核通过后）

原型 HTML/CSS/JS 整体迁入 `web_ui.py` 单文件结构（保持现有端点接线函数名：`loadSelfHealingDemo` / `loadRuleTree` / `exportHost` / `handleChat` 等），mock 数据全部替换为端点真实调用；3D 视口按原型工件位（平面/纵断面 SVG 先行，Three.js 3D 作为可选增强保留在线程工件位）。
