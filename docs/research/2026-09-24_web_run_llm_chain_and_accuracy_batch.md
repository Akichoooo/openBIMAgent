# 2026-09-24 Web run 真 LLM 链与生成精度批次：收益、代价与证据边界

版本：v1.0
更新时间：2026-09-24（Asia/Shanghai）
维护状态：**CURRENT**（批次收口记录）
事实边界：本文只回答「为什么做 / 不做的后果 / 验证到哪一步」。实时门禁、HEAD 与测试基线数字一律以 [PROJECT_HANDOFF_STATUS.md](../architecture/PROJECT_HANDOFF_STATUS.md) 为准，本文不重复维护。所有"已实现"结论可由文中路径与 `tests/` 复核。

---

## 0. 一句话

这一批把 Web 工作台的"新建任务"从**跑确定性模板却显示得像 AI 在工作**，改成**真走 LLM 规划链、真出 IFC、并且人能在审批门上看懂将要执行什么**；同时补了三处"看起来是绿的、其实错了"的回验缺口，并修掉一个会让 run 永久挂死的生产级状态污染缺陷。

分支 `feat/agent-engineering-absorption`，9 个边界清晰的提交已推送远程。

---

## 1. 逐项：收益 / 不做的后果 / 证据强度

### 1.1 Web run 接工作台真 LLM 规划链

- 代码：`src/openbimagent/server/workbench_llm.py`（新增）、`src/openbimagent/server/runs.py`、`src/openbimagent/session/schema.py`
- 做法：按 pipeline 消费方的鸭子契约 `registry.chat(role, messages)` 适配工作台模型设置；**逐次调用重解析** `_resolve_llm()`，所以设置页改模型即时生效；解析不到时返回 `None`，由 runs 侧降级确定性模板并如实发 `llm_planner` 事件。
- 收益：Web 端第一次真正执行 planner→solver→gate→deliver 全链。
- 不做的后果：**这是本批最严重的一条**。点"新建任务"实际跑模板，界面语义却暗示 AI 在工作——任何评审者打开网页点一次即可证伪"面向工程人员的 LLM 建模 agent"这一核心主张。属于声称与实现不符，不是性能问题。
- 设计约束（用户拍板）：**没有 API key 就用不成，不做假成功**；不做远程 Blender（本机 i5-6200U/12GB 无宿主）。
- 证据强度：**强**。有 key 端到端实测：`llm_planner mode=llm` 事件、20 个真实类别语义资产、56 条空间约束、domain_gate PASS。

### 1.2 无 CAD 主机的离线 IFC/IDS 交付

- 代码：`runs.py::_deliver_offline_ifc`、`_IFC_DELIVERY_FILES` 进归档与工件白名单
- 做法：gate PASS 后 `CompiledUtilityIR → FakeBlenderSemanticExecutor → build_ifc_ids_package`，写 IFC4X3_ADD2 + IDS XML + 校验报告；规则身份用**本 run 落盘工件的 sha256** 钉死（rule set / gate 报告），IFC 每个对象可回溯到本次规则评估。交付失败只发 `ifc_delivery` 事件，**绝不推翻 pipeline 结论**。
- 收益：无 Blender / 无 Vectorworks 的机器也能产出第三方可打开的 IFC，交付闭环不再绑定宿主。
- 不做的后果：无主机时 agent 终点只剩一个 `compiled_utility_ir.json`，工程师拿不到 IFC——"BIM agent"没有 BIM 交付物。而该离线链当时**只被测试和 benchmark 调用**，产品链路里没人接。
- 证据强度：**中强**。真产出 12,818 字节 IFC4X3_ADD2；IDS 校验 `checked_entity_count=6 / findings=64`；工件端点可读、归档 `index.json` 含 4 个 IFC 产物。**未做**：逐条核看那 64 个 finding 的语义、在 IFC 查看器里人眼确认几何。

### 1.3 审批决策门附「将要执行什么」预览（对标 Codex 执行 diff）

- 代码：`src/openbimagent/assembly/approval_preview.py`（新增）、`target_executor.py`、`batch_executor.py`、前端 `ChatThread.tsx`
- 做法：typed plan 给逐操作摘要（总数 / 按类型计数 / 前 20 条明细，截断后计数仍为全量真值）；**自由代码路径不假装能预览**——代码在门后才生成，故改为附本批次资产声明（`assets_preview`）。票据 params 原样流到前端，无需新增端点。
- 收益：HITL 从"看到操作名→点批准"变成"看到将写入哪些对象、多少个、什么类型"。
- 不做的后果：人在回路退化为**橡皮图章**。整套安全论证（fail-closed、人工裁决）依赖"人真的能判断"；审批人看不懂 `operation_count=37` 只能盲签，那审批门只是把责任推给一个没有信息的人。
- 证据强度：**中**。后端字段与事件流核对过，`tsc` 通过；**审批卡在浏览器里未实测**。

### 1.4 dispatch rework 透传进渲染循环

- 代码：`batch_executor.py::agent_fn` → `vision/render_loop.py::run_render_loop(..., dispatch_rework=...)`
- 做法：裁判 FIX 的 rework 指令作为**首轮先验**注入（`iteration==1 且 prev_critique is None` 时构造 `CritiqueResult`）。
- 收益：修掉真实缺陷——裁判已说清"哪里不对、怎么改"，执行器把话吞了，自由代码只能盲改再撞同一个坑。
- 不做的后果：多烧一轮渲染+评判（时间与 token），且失败收敛慢：同一几何错误可能被 FIX→重试→FIX 反复撞。
- 证据强度：**强**（逻辑确定），+2 测试直接断言注入生效。

### 1.5 RAG few-shot 代码语料（降宿主 API 语法幻觉）

- 代码：`src/openbimagent/assembly/code_snippets.py`（新增）、`snippets_seed.json`（新增 5 例）、`builder.py`
- 做法：**只有验收 PASS 的片段入库**（sha256 去重 + 特征索引 `index.json`），检索按批次特征 Jaccard 重合度加权历史得分、零重合不注入；参考实现块插在输出契约与风格锚点**之间**，并附「几何必须按本次批次资产声明重新推导，禁止照抄坐标与尺寸」。
- 收益：`mathutils` / 集合作用域锁定这类契约错误最适合靠示例消除；同时验收门保护语料不被污染代码灌入。种子随包分发，冷启动即有参考。
- 不做的后果：每次从零猜 API，同一语法错误跨会话反复出现，经验不累积。收益是长期的。
- 证据强度：**弱——本批最不满意的一条**。测试只覆盖机制本身（捕获/去重/检索/注入/零重合不注入）；**"减少幻觉"这个主张没有 A/B 量化**，5 个种子是手写而非跑出来的。修过一个真 bug（捕获时误带一次性 asset id，导致新片段排名被种子压过），但那属机制正确性，不等于效果。本机无 CAD 宿主，短期也测不出真实命中率。

### 1.6 spatial_constraints 回验 linter

- 代码：`src/openbimagent/planner/constraint_lint.py`（新增）、`instantiate.py`（并入 `_validate_scene_ir`）+ planner 提示补铁律
- 做法：引用完整性（subject/object 必须是已声明资产；containment/adjacency 必带 object）、间距值合理性（正、有限、≤10km）、同 `(type,subject,object)` 重复声明且数值互斥。
- 收益：LLM 写的约束自相矛盾时在 Scene IR 阶段就拒，而不是漂到 domain_gate 或交付物里。
- 不做的后果：`spacing=-3.5m` 或指向不存在资产的约束被照单全收，最终表现为**几何荒谬但门禁 PASS**——对工程软件这是最坏的一类 bug，因为它看起来是绿的。
- 证据强度：**中**。7 例覆盖各分支；真 LLM 运行的 56 条约束跑出 0 违规，**这只能证明不误报，不能证明抓得住真违规**（缺自然违规样本）。**几何回验故意不做**：scene-IR 与 compiled-IR 之间不存在 id 桥，硬做只会产出假信号。

### 1.7 benchmark 几何 diff 指标

- 代码：`src/openbimagent/benchmark/geometric_diff.py`（新增）、`academic_bench.py`、`llm_direct_baseline.py`
- 做法：以确定性解算器 `solve_network_gravity_utility` 的 invert 为参考，逐节点比对，产出 invert 误差均值/峰值、坡度误差均值/峰值、覆土误差；缺参考解时字段为 `None`，不假装有值。
- 收益：目前唯一能把"生成精度"量化的指标——LLM 直出相对确定性解偏多少米、偏多少坡度。
- 不做的后果：方法基准只能报"通过/未通过门禁"这一二元结果，无法回答"比基线好在哪、好多少"，生成精度的论述缺证据支撑。
- 证据强度：**中强**。数学性质有判别测试（整网均匀平移→坡度误差为 0；单点偏移→坡度误差非 0）；**完整跑一轮 `llm_direct_baseline` 未做**（需真调模型 × 场景矩阵，本机成本高）。

### 1.8 前端模型配置实时联动 + 陈旧覆写修复

- 代码：`ModelsTab.tsx`（派发 `wb-models-change`）、`Header.tsx`/`ChatThread.tsx`（监听重拉）
- 收益：改完模型设置后界面不再显示旧 provider；`handleSelectModel` 先重取最新配置再 PUT。
- 不做的后果：后半条是**真数据风险**——用陈旧快照 PUT 会静默回退用户刚做的配置改动。
- 证据强度：强（代码路径 + `tsc`）；同样**未在浏览器实测**。

### 1.9 审批票据进程级共享状态污染修复

- 代码：`approvals.py::_load_pending` 装载守卫；5 个 run 类测试 fixture 清 `_pending` + 回收 `OPENBIMAGENT_PENDING_APPROVALS` + `OPENBIMAGENT_RUN_LLM=0`
- 根因链（插桩堆栈定位，非推测）：
  1. `fastapi_app.py:627` 模块级 `app = create_app(...)` 在**pytest 收集期导入时**就建 app，`add_approvals → _load_pending` 此刻读**仓库默认路径** `out/pending_approvals.json`（任何 fixture 的 env 覆盖都还没设），把上次失败运行残留的票据装进进程级 `_pending`；
  2. `_load_pending` 用磁盘重放**覆盖活跃票据**，运行线程等待的 `threading.Event` 被换成 expired 副本 → **永不唤醒**；
  3. 该 run 永久占住并发额度（默认上限 2）→ 后续模块 `POST /api/v1/runs` 全被判 409 → 级联十余个失败；且每次失败再持久化更多僵尸票据，**自我强化**。
- 收益：套件从间歇性失败转为稳定全绿且耗时下降（僵尸线程消失，不再烧审批超时与真模型 API）；同时修掉一条生产同型缺陷。
- 不做的后果：
  - **生产语义**：服务器在有人等待审批时重启、再装载到同名票据，那个 run 就永久挂死。测试只是把这个缺陷放大暴露出来。
  - **测试失去意义**：我前三次全量跑出**三个不同的错误组合**，每次结论都不一样——这种套件给出的任何"通过"都不能作为证据。
  - **偷偷烧额度**：单文件 353s、p124 >7min 的真因是测试在真调模型 API，CI 会变成要花钱的东西。
- 证据强度：**强**（堆栈直证 + 全量转绿复跑确认）。

---

## 2. 证据强度汇总

| 强度 | 条目 | 含义 |
|---|---|---|
| 强 | 1.1 真 LLM 链 / 1.4 rework 透传 / 1.9 票据污染修复 | 有端到端实测或确定性逻辑 + 断言测试 |
| 中强 | 1.2 离线 IFC 交付 / 1.7 几何 diff | 产物真实存在，但结论未做完整语义/基准复核 |
| 中 | 1.3 审批预览 / 1.6 约束 linter / 1.8 前端联动 | 后端与类型层已核，**UI 未浏览器实测**；linter 只证明不误报 |
| 弱 | 1.5 RAG few-shot | 机制正确，**效果主张无 A/B** |

---

## 3. 明确未做与未验证（欠账清单）

1. **三档执行模式的承诺与实现仍不符**：本批只把 Ask/Plan/Build/Audit 作了选型建议，并按"宿主侧对象级回滚无法验证即不承诺"的原则**否掉了增量编辑模式**；代码里模式仍只有 `agent`/`yolo` 两个取值生效（`runs.py`），设置页模式字段无对应行为。**一行未动**。
2. RAG few-shot 的降幻觉幅度**无量化**（需要接宿主或至少一个真实 Blender 代码评测集）。
3. IDS 那 64 个 finding 的语义正确性未逐条核。
4. 子代理并发路径 + 多批次场景**未过真模型**（只过了确定性模板）。
5. 约束 linter 对**真违规**的召回未验证。
6. `fastapi_app` 在 import 期建 app（生成 token、读磁盘票据）是本轮所有测试污染的物理根因。本批只加了守卫与测试清表，**没动该设计**：改惰性构建会牵动 uvicorn 入口，属需要单独拍板的决定。
7. 三张审批卡/预览的前端渲染未在浏览器验证（本机仅 `tsc` + `vitest` 层）。

---

## 4. 复核方法

```bash
# 1) 真 LLM 链 + 离线 IFC 交付：起工作台，新建任务，看事件与产物
python -m uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000
#   → GET /api/v1/sessions/<sid>/events?tail=500 应有 customType=llm_planner 与 ifc_delivery
#   → GET /api/v1/runs/artifact?session=<sid>&name=municipal_utility.ifc 应返回 text + sha256

# 2) 无 key 降级语义（fail-closed，不假成功）
#   把工作台模型配置清空后跑一次：llm_planner 事件 mode 必须为 template

# 3) 测试隔离三板斧是否守住（新加 run 类测试必须照做）
pytest tests/test_approvals.py tests/test_workbench_p124.py -q   # 应在十秒级返回，不得真调模型

# 4) 几何 diff 指标的判别力
pytest tests/test_geometric_diff.py -q    # 均匀平移→坡度误差 0；单点偏移→误差非 0
```
