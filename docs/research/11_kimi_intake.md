# 11 · 主架构师评审与对齐(006/007 入库)

日期:2026-07-21 · 作者:主会话(主架构师)· 输入:09 报告 + constraints.yaml + ifc_mapping.md + 10 报告(Grok)
注:接力方建议的 008 评审提示词不另派——评审方就是主会话,本文档即产出。

## 1. 质量门评审结论

| 产物 | 结论 | 说明 |
|---|---|---|
| 10 社区情报(Grok) | **通过,质量优秀** | 引用扎实(issue/PR/论坛帖均带链接日期),噪音剔除透明;blender-mcp 五大坑、vs 幻觉共识、VLM-judge 工程化做法、设计院质疑五条,全部可直接转设计决策 |
| 09 市政管网(Gemini) | **通过,带两条保留** | A/B/C 节方向正确但偏薄:① constraints.yaml 只有 5 条且 `source_clause` 未逐条给准确条文号——M1.5 前必须对照规范原文二轮核实扩充(检查井间距/净距/雨污分流缺失);② D 节模型价格/上下文自标「推测」——按未核实占位处理,动工前以官方控制台为准 |
| constraints.yaml | 草案通过 | 结构正确(rule_id/category/parameter/value/unit/source_clause),可作 domain_gate 数据源起点 |
| ifc_mapping.md | 草案通过 | 三层映射方向对(尤其「IR 只负拓扑,几何由 VW 侧生成」符合 C2),M1.5 细化 |

## 2. 产品假设验证表(社区证据 ↔ 现有决议 ↔ 处置)

| 我们的假设/决议 | 社区证据(10 报告) | 判定 | 处置 |
|---|---|---|---|
| 双环视觉自检是差异化 | 从业者厌 one-shot、要「可编辑场景内迭代」(@irinatoxi 等);「有视觉反馈的迭代没那么容易做好」 | 成立,但有前提 | 前提=截图可靠+评分不放水,见变更 V1/V4 |
| fork blender-mcp 改造 | 五大坑:连接脆弱/超时冷启动/**截图全黑(PR #266)**/execute_code 无沙箱/**Agent 越界改场景** | 原改造清单不够 | fork 清单 5 项扩 8 项(V1) |
| AST allowlist + 快照 | execute_code 安全担忧 + 越界投诉 | 不足 | 加**范围锁**(可编辑集合白名单)(V1) |
| vs_index.json 防崩溃 | 行业共识:LLM 会编造不存在的 vs 函数(Pat Stanford:「ChatGPT will lie to you all day」) | 升级为双门禁 | vs_index 同时做**防幻觉白名单**(V2) |
| VLM 六维评分 | VLM-judge 系统性放水、位置偏置;有效=分维 rubric+锚点+**关键维 pass/fail**+judge 与生成分家+黄金集回归 | 基本成立,需加固 | 防放水三件套扩五件套(V4) |
| 确定性检查先于 VLM | 社区:几何/规范可验证维应走确定性检查 | 新启发 | **评分分层**:domain_gate(机器)管硬指标,VLM 只管外观/语义/布局(V4) |
| 市政包交付物 | 设计院要「检核表」:每条可引规范条款;质疑责任/规范/数据/兼容/ROI | 成立 | 交付叙事=「约束生成→双环检核→人审签」,不讲「AI 设计师」(V5) |
| 观测/话术 | 专业社区(Blender Artists)冷淡,要看到约束+检核+可回滚 | 成立 | README/答辩话术=副驾/检核/减重复(V5) |

## 3. Domain Pack 断层评估(Gemini 补充任务 2)

机制本身与架构兼容(决议 v1 已并入),但有 **5 个断层**,本轮全部修复:

| # | 断层 | 处置(已落 ARCHITECTURE v0.3) |
|---|---|---|
| G1 | **模板族缺失**:江户包是一个模板范例,不是唯一版本——用户明确指示 | 新增 `domain_packs/_base/`(通用核心+创作指南);三类模板定型:风格场景类(江户)/BIM 交付类(市政)/冒烟类(单资产) |
| G2 | **「表现层」措辞错误 + 预览未分线**:两 MCP 是**并行生成路径**(Blender 不止表现,VW 不止交付;Blender BIM 如 Bonsai 留作后续评估);预览要分「给模型看」与「给人看」 | §4 targets 语义改为并行路径;新增 §6.5 预览双线:模型=SCAD 白模+离屏视口截图(降采样)+正式渲染;人=**每批 HTML 验收页**(截图+评分+对比+返工指令)+ turntable 序列帧,M2 后 Web UI 实时化 |
| G3 | **HITL 基座能力未单列**:用户要求人能随时打断/回退 | 新增 §6.5 基座能力清单:abort 中断+部分结果、消息排队、审批门(MCP 写/execute_code/deliver 三态 ask)、回退(session /tree + 快照恢复)、断点续跑、doom_loop 检测——M0 必备 |
| G4 | **包内无工具封装概念**:社区证据「预封装 Domain 工具 > 通用 execute_code」 | 包结构加 `tools/`(已验证领域工具,如管网避让算法、破损 GeoNodes 挂载器),LLM 优先调工具,逃生门兜底 |
| G5 | fork 基座版本:用户指定 ahujasid 官方**最新验证好用版** | §5 注明:动工时取最新稳定 release,与 openBIMForge 实验 pin(6641189)对比后定 |

## 4. models.toml 更新建议(Gemini 补充任务 1,已执行)

按 09 报告 D 节同步 `config/models.toml` 与 COMPONENTS §4,**全部标注「2026-07-21 调研值,未经官方控制台确认」**:

- 三模型 context_window 均标 1M(GLM-5.2 原占位 200k 已上调,待确认)。
- GLM-5.2 capabilities 加 `vision`(报告称支持)——若属实,glm 可作 critic 的降级链,视觉环成本结构更灵活。
- 价格登记(调研值):GLM-5.2 ~$0.84/$2.64、3.1-pro $2/$12、3.5-flash $1.5/$9(每百万 token 入/出)。
- **策略维持不变**:Pro 管规划/建模/验收,Flash 管高频,GLM 管调度;理由是质量优先而非成本敏感。若控制台确认 GLM-5.2 有 vision 且价格显著低,再把 critic_scad 降级链指过去。

## 5. 架构变更清单(v0.3 已落)

- V1:blender-mcp fork 清单 5→8 项(+连接健康检查/+截图非黑断言/+范围锁);基座=最新验证版
- V2:vs_index 白名单双门禁(防幻觉+防崩溃);Domain 工具封装优先于 run_script
- V3:两 MCP 并行生成路径表述;municipal_utility playbook「表现与交付分离」改为「并行路径,M1.5 主线 VW 交付+Blender 表现,Bonsai 备选」
- V4:评分分层(确定性 domain_gate vs VLM 软评分)+ 防放水五件套(+关键维 pass/fail 硬门禁、+judge 与生成模型分家、+黄金截图集校准回归)
- V5:对外叙事=副驾/检核/人审签(进 README 与毕设答辩稿)
- V6:§6.5 HITL 基座 + 预览双线(见 G2/G3)
- V7:`domain_packs/_base/` 模板族指南;包结构加 `tools/`

## 6. M0 实施待办(从本评审流出)

1. constraints.yaml 二轮核实扩充(对照 GB 50014-2021 原文,可再派 Gemini)。
2. FORK_NOTES.md 同步基座版本决策(V1/G5)。
3. blender-mcp 离屏截图路径 spike(PR #266 已合并,验证非前台截图可靠 + 非黑断言)。
4. models.toml 控制台核实(GLM-5.2 vision/价格)。
