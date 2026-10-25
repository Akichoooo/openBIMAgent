# openBIMAgent 阶段交接状态

版本：v4.7
更新时间：2026-10-25（Asia/Shanghai）
维护状态：**ACTIVE**
工作区：`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`
远程仓库：`https://github.com/Akichoooo/openBIMAgent.git`

> 本文档是跨会话恢复的唯一实时入口。只保留当前可复核事实、未完成债务、受保护内容和唯一下一动作。历史过程详见 Git 提交、专项验收报告与 `.workbuddy/memory/`，不要把历史测试数字当作本轮新证据。

## 1. 当前阶段结论

### 2026-10-25 市政管网四大架构升级（本地 main，最新，未提交）

- **范围**：
  - `utility/network_solver.py`：DN300 硬编码取消，能力面 [150, 2000] mm；新增 `lookup_pipe_normative_limits` 动态查表（坡度/井距/覆土/净距分档，DN300-DN600 全覆盖）；逐段证据按管径取限值。
  - `utility/candidate.py`：三级刚柔约束（Tier-2 满包混凝土特批 0.45m）+ 工程造价 `compute_engineering_cost` + CEGIS 五算子拓扑沙箱 `evaluate_repair_sandbox`（顺坡传导 / 跌水井 / 平面绕行 / 自动插井 / 满包特批，含组合分支）+ 帕累托字典序选优；修复锁定节点"一刀切罢工"Bug；`propose_candidate_repairs` 签名与返回类型不变。
  - `server/municipal_workflow.py`：`iterative_refine` 默认 4 轮；回滚门禁升级为新违规码 ∥ 硬违规增加 ∥ obstacle_clearance 碰撞引入即回滚；`state_diff_audit.json` 落每轮沙箱账本/造价/相机轨迹/本体演化（只增不改）。
  - `vision/municipal.py`：`compute_clash_normal_nbv`（异面直线公垂线 NBV）、`render_longitudinal_profile_view`（Matplotlib 工程纵断面 PNG）、SoM 方位罗盘/水流箭头/米制标尺图层、`render_custom_camera_views`（free_orbit / nbv / 纵断面三模式）；`MunicipalVisualReport` 新增 `next_camera_requests` 与 `active_camera_trajectory`。
  - `utility/wiki_ontology.py`（新建）：BIM-Wiki-Ontology 三位一体本体（属性图 + Obsidian 双链词条 + Harness² 探针熔断 `fail_streak>=2`）；`server/runs.py` 归档时同步沉淀 Wiki 案例；`server/municipal_actions.py` 将本体算子优先级注入沙箱。
  - `scripts/run_municipal_ablation_benchmark.py`（新建）+ `tests/test_ultimate_municipal_combo.py`（新增 28 项）。
- **测试**：全仓 `python -m pytest tests/ -q` = **1633 passed / 6 skipped / 1 failed**。唯一失败 `test_ids_schema_dependencies::test_ids_xsd_compiles_in_fresh_process_with_broken_network` 经 `git stash` 基线复核为改动前既有的环境性失败，与本轮无关。消融基准（确定性、无 VLM/宿主）：12/12 场景最终合规（首次 16.7%），本体自进化前后算子命中率 0% → 66.7%，NBV/纵断面触发 12/12；报告在 `output/benchmark/`（gitignored）。
- **兼容性**：既有 72 项市政相关测试零修改通过；`schemas/network_utility_solver_input.schema.json` 仅追加可选字段；1 项"DN400 必须被拒绝"断言按本轮升级指令改为区间外管径（2500）并保留拒绝意图。
- **边界**：未提交、未推送；未操作 Vectorworks/Blender 宿主、未调用 VLM；Wiki 本体目录按 `OPENBIMAGENT_ARCHIVE_DIR` 沙箱惯例 gitignored（`domain_packs/*/wiki_ontology/`、`output/`）。
- **下一动作**：真实页面验收 NBV/纵断面/沙箱账本链路；按 `output/benchmark/ablation_report.md` 核对消融数字后纳入论文实验章节。

### 2026-10-18 origin/main 合并落盘（本地 main，上一轮）

- 以 `origin/main` `95267ce` 为基线，本地 `main` 通过 cherry-pick `7b39523` 落盘（提交 `836fd08`）；旧历史保留在本地备份分支 `backup/local-main-20261018-7b39523` 与标签 `pre-origin-sync-20261018`。
- 合并修复：恢复 `sensenova` provider/model/profile（上游已移除，但本机视觉链路依赖）；web 默认直管输入补充显式内/外径；providers registry/dialects 重新合并 diagnostics/abort/streaming；XSD/JSON 快照统一 LF 并固定（`schemas/w3c/`、`schemas/frozen/`、IDS XSD）；benchmark 当前 schema pin 对齐上游 `befe4f8f…`；前端 App 适配上游 ChatThread props。
- 验证：全仓 `uv run pytest tests/ -q` = **1601 passed / 6 skipped / 0 failed**；前端 46 passed、`npm run build` 通过、lint 0 errors（131 既有 warnings）；Ruff（本轮 Python 改动）通过。
- 状态：**未推送**；远端 `main` 仍为 `95267ce`。真实页面验收与 VLM 预算仍待确认。

### 2026-10-18 IFC 交付小补丁与用户验收准备（上一轮，优先于下方交付收尾）

- **ifc_download 身份读取修正**：按真实生产 IFC stage 的**根部**读取 `revision`/`candidate_sha256`（此前误读 `generation` 内字段，导致真实 stage 的版本校验形同虚设）；缺失或非法（非整数 revision、非 64-hex candidate）明确拒绝；保留首次导出从 `delivery.receipt.ifc` 读取的兼容路径；文件 SHA-256 校验保持。
- **retry_ifc 分层摘要统一**：IFC 状态只存 `delivery.receipt.ifc`（去除顶层重复副本；旧顶层 `ifc` 仅读取兼容），`delivery_layers` 在 delivery 根部与 receipt 内同步重算，消除新旧相反状态；宿主 `execution_id`/`output_path`/文件哈希不变，`topology=unverified` 不变，重试不增加宿主调用。
- **IDS XSD 偶发失败（已定位并修复）**：
  - 首跑：定向回归首次出现 `XMLSchema.xsd` 解析失败；重跑通过（偶发）。
  - 根因：IDS XSD 的 `xs:import` 指向 W3C `XMLSchema.xsd` / `xml.xsd`；lxml/libxml2 在 schema import 阶段仍会网络解析，`no_network=True` 不足以阻断。用不可用代理复现：原实现必然失败（QName 无法解析）；新实现离线编译通过。
  - 修复：本地化官方依赖 `schemas/w3c/XMLSchema.xsd`（SHA-256 `e767a159c179bd6e4dc2bac2a63a62f50cbd027ec19d6b5cc9eb1c2f33e0cb2e`）与 `xml.xsd`（`61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c`），来源 URL 与哈希固定在代码；显式 lxml Resolver 仅从本地文件加载，编译前复核摘要；不删除 import、不关闭校验、不增加重试。
  - 仍不确定：首跑失败未保留完整堆栈，无法逐字证明当时为网络超时；离线依赖修复后该失败模式已消除；第三个 import（XMLSchema-instance 命名空间）在离线测试中不触发网络。
- **文档长度口径**：明确 IFC 属性 `HorizontalLengthM` 为 IR 水平投影长度（工程属性），实体 `IfcExtrudedAreaSolid.Depth` 与几何读回 `length_m` 为真实三维轴线长度，不得互相替代或混报。
- **用户验收准备（未执行）**：新增 `docs/architecture/IFC_USER_ACCEPTANCE_CHECKLIST.md`（首次导出 → 下载 → 刷新恢复 → 受控 IFC 失败 → 仅重试 IFC → 再次下载；记录 execution_id、宿主文件 SHA-256、宿主调用次数以证明未重跑 CAD）。未确认专用 VW 文档与操作范围前不启动/不修改宿主，未触碰既有验收产物。
- **测试**：定向回归 106 passed（含新 XSD 离线/篡改、IFC stage 根部身份、旧 revision/错误 candidate/缺失身份、重试分层一致）；全仓 `uv run pytest tests/ -q` = **1653 passed / 6 skipped / 0 failed**；前端 28 passed、`npm run build` 通过、lint 0 errors（118 既有 warnings）。
- **边界不变**：未提交、未推送；未操作 VW、未调用 VLM、无三态经验/研究实验/论文笔记。
- **下一阶段**：按清单执行真实页面验收（需人工确认与 VW 环境）；外部 IDS/IFC 校验器互操作与完整 IDS 覆盖仍超出当前范围。

### 2026-10-18 IFC 交付收尾（上一轮，优先于下方 IFC 实体闭环）

- **下载统一与校验**：`ifc_download` 同时读取 `delivery.ifc` 与 `delivery.receipt.ifc`（首次导出 IFC 状态在回执内，刷新页面后仍可下载）；下载前校验记录 SHA-256 与实际文件、当前 revision/candidate；文件缺失/内容被修改/旧版本分别给出明确错误。前端下载链接基于回执内 IFC 状态。
- **真实模式校验**：新增 `validate_ifc_schema`（IfcOpenShell 0.8.5 `validate(..., express_rules=True)`），保存 `ifc_schema_validation_report.json`（validator/scope/issue 详情）；显式检查重复 GlobalId 与非法 GlobalId。模式校验纳入交付判定（失败/未检查 → 不通过；`commit_ifc_ids_package` 失败关闭）。只称“模式校验”，不冒充工程合规或完整 IDS。
- **IFC 单独重试收尾**：宿主回执必须携带可验证的 `source_ir_sha256` 且与当前 IR 一致（缺失即拒绝）；每次重试使用独立 `ifc/<execution_id>/attempt-NN/` 目录，旧 IFC/报告/哈希保留；重试后同步更新 `receipt.ifc`、`delivery.ifc` 与 `delivery_layers`；不增加宿主调用，宿主 `topology=unverified` 不变。
- **规则证据收尾**：严格校验 `DeliveryRuleIdentityKind` 与版本；生产资格只使用任务冻结规则上下文（缺失 → `review_required`，绝不重新取当前规则源）；unknown/review_required 保持原状态。合成验收脚本显式声明 `confirmation_claim=none` 并使用 `compiled_ir_evidence_only`，不声称发生过人工确认。
- **验收脚本**：分项判定决定退出码（schema/IDS/geometry 任一失败或未检查 → 非 0）；每次运行创建新的 `attempt-*` 目录，失败也保存 summary/report，重复运行不覆盖旧证据。
- **新真实验收产物**（gitignored）：`out/ifc-acceptance/attempt-20261018T052030Z-01/`（真实 `.ifc`、schema/IDS/几何/规则报告；IFC SHA-256=`5d8c4bf5838c303265607520513abbd6a51c7f9e1eb6bd44fcd22908bd7a48a4`；schema/IDS/geometry=PASS，domain_rules=unknown 未升级；`confirmation_claim=none`）。复现：`uv run python tools/ifc_local_acceptance.py`；旧 evidence 目录保留。
- **测试**：全仓 `uv run pytest tests/ -q` = **1649 passed / 6 skipped / 0 failed**；前端 28 passed、`npm run build` 通过、lint 0 errors（118 既有 warnings）。新增：schema/EXPRESS 校验、重复与非法 GlobalId、规则身份 kind/版本、重试 attempt 目录、首次导出下载/哈希被改/文件缺失、验收分项判定与非覆盖。
- **边界不变**：未操作真实 Vectorworks、未调用 VLM、无三态经验/研究实验/论文笔记；未提交、未推送。
- **下一阶段**：真实宿主链路的用户验收（需人工确认与 VW 环境）、外部 IDS/IFC 校验器互操作、完整 IDS 覆盖；本轮的本地真实文件与后端链路已验证，但不能由测试全绿推断“用户交付链路已完成”。

### 2026-10-18 直管 IFC 实体交付最小闭环（上一轮，优先于下方判定/真机轮次）

- **生成物**：每个直管段为真实空腔实体 `IfcCircleHollowProfileDef`（Radius=外径/2，WallThickness=(外径−内径)/2）+ `IfcExtrudedAreaSolid`（Depth=真实三维轴线长度）；`IfcAxis2Placement3D` 承载起点/轴向/参考方向；`Body/SweptSolid` 表示；项目级 SI 单位（m/m²/m³）、Model/Body 几何上下文、精度 1e-6。节点/检查井标注为示意（`GeometryRepresentation=schematic_none`）。来源显式记录为 `compiled_utility_ir`（非 Vectorworks 原生导出）。
- **输入边界**：必须是 CompiledUtilityIR 1.1；DN/内径/外径严格分字段（`NominalDiameterMM`/`InnerDiameterMM`/`OuterDiameterMM`/`WallThicknessMM`）；复用 `resolve_dimensions`/`execution_geometry`，不另写换算；斜向带坡管使用真实三维轴线长度，不拿水平长度。
- **读回验证**：对保存后的 IFC 重新 `ifcopenshell.open` 并经 `ifcopenshell.geom.create_shape`（world coords）三角化后独立测量：实体存在、Body/SweptSolid、空腔（内外环半径）、内外径、三维长度、轴线端点/方向、单位；体积一致性用实测环多边形鞋带面积 × 长度与网格体积比较（相对差 ≤1e-6），圆周多边形化的离散误差单独记录（内/外径 sagitta ≈1.28/1.54 mm，非工程容差）。解析失败/无法测量明确失败。
- **长度口径**：IFC 属性 `HorizontalLengthM` 是 IR 的水平投影长度（工程属性）；实体 `IfcExtrudedAreaSolid.Depth` 与几何读回的 `length_m` 均为真实三维轴线长度（斜管 L=√(Lh²+Δz²)）。两者不得互相替代或混报。
- **分层报告**：IFC 模式 / IDS 与语义/连接 / 实际几何 / 领域规则证据四层分开；IDS 仅覆盖已实现子集（entity/property/attribute/partOf + IfcRelConnectsPorts），不宣称完整 IDS 标准；规则报告绑定 IR/规则上下文，不冒充对 IFC 内容的独立工程合规检查。
- **规则身份**：新增 `RuleDeliveryEvidence`（版本 1.0，`model_rule_evidence_aggregate`）聚合确认后的 validation issues（或仅 IR 内嵌证据并显式标注 `evidence_source`），与单条 `RuleProjectionIdentity` 字段严格区分；聚合哈希可复算；review_required/unknown 不会被升级；无证据时失败关闭，禁止填充任意 64 位哈希。IFC 项目 Pset 携带 Delivery* 身份；`IfcIdsValidationReport` 与 schema 支持“单条评估身份或模型级聚合”二选一。
- **交付流程**：`export_revision` 在宿主成功后生成 IFC 并分项返回 generation/checks；IFC 失败不影响宿主回执/文件。新增 `POST /api/v1/municipal/{session}/ifc-retry`：仅重建 IFC，复用身份一致的宿主回执，不再次调用宿主、不覆盖宿主文件；校验当前版本/确认/IR；失败历史保留在 `ifc_history`；旧版本请求被拒绝。新增 IFC 文件下载端点 `GET /api/v1/municipal/{session}/delivery/ifc`。
- **前端**：区分宿主结果 / IFC 生成 / IFC 检查（模式、IDS/语义、几何读回、领域规则）；展示失败原因、涉及构件、下载链接与折叠哈希/诊断；宿主 partial 仅因 topology unverified 时不要求重新执行 CAD；“重试 IFC”仅在可复用宿主回执时可用。
- **真实验收产物**（gitignored）：`out/ifc-acceptance/20261018T034440Z/`（真实生成的 `.ifc`、`.ids`、IDS 报告、几何报告、规则证据、summary.json；IFC SHA-256=`6485dd0638c8fba57b8d1b6a2387137ca29ac08df600fe52501cb223b5bbc36b`；复现命令 `uv run python tools/ifc_local_acceptance.py --root ...`）。两管段（平面水平+斜向带坡）实测内/外径 300/360 mm、三维长度 10.0/10.007997 m、体积一致性 ≤1e-13；合成测试几何，非真实工程数据。
- **测试**：新增 `tests/test_ifc_geometry_delivery.py`（9 项：真实 IFC 读回、缺 Body、实心圆柱冒充、单位/长度/管径/放置错误、缺对象/重复 ID/断连接、规则身份缺失与不一致）；workflow 新增 IFC 重试复用回执/无宿主调用/旧版本拒绝；actions 更新为真实 IFC 生成与失败隔离。全仓 `uv run pytest tests/ -q` = **1641 passed / 6 skipped / 0 failed**；前端 28 passed、`npm run build` 通过、lint 0 errors（118 既有 warnings）。
- **边界不变**：未操作真实 Vectorworks、未调用 VLM、无三态经验/研究实验/论文笔记；未提交、未推送。
- **下一阶段**：IFC 与真实宿主/外部工具链的互操作验证（外部 IDS 引擎、IFC 查看器/质量规则）、完整 IDS 覆盖与 IFC 工程合规（超出本轮）；交付输出目录与权限策略的产品化。

### 2026-10-18 验收判定收紧与基准版本回归收尾（上一轮，优先于下方真机验收）

- **验收脚本成功条件收紧**：`tools/vw_real_acceptance.py` 不再把 completed/partial 直接当成功；新增可单测的纯函数分项判定：
  - run：执行状态、零错误、`applied_operations` 与 plan op 数一致、plan 身份、几何构件集合（无缺/无重复）、尺寸通过、语义 PASS 且对象数一致、每个预期管段有有效顶点哈希、输出文件存在且有 sha/字节；拓扑只允许分级 `unverified`。
  - reopen：同 IR/plan（plan_id/plan_sha256/source_ir_sha256 与构建回执一致）、op 数一致、语义 PASS、尺寸通过、无错误、预期管段全部有有效顶点哈希且逐一相等。空集合相等、缺对象、缺哈希、哈希不等、不同 plan 一律阻塞。
  - 方法诚实：`start-process-arg` 记为自动化磁盘重开；`--in-place` 需新增 `--operator-confirmed` 才声明磁盘重开，并记录确认来源；`--attach` 只证明活动文档读回、永不声明磁盘重开。退出码 0 仅当 readback 通过且磁盘重开被声明。
  - 证据保护：每次写入前将既有 receipt/snapshots/summary/compiled_ir 复制到 `history/<UTC>-run|reopen/`；成功后当前 `reopen_error` 清空。旧真机 summary 的陈旧错误字段已清空（原件：`out/vw-acceptance/20261017T155917Z/history/20261017T192632474696Z-stale-error-cleanup/summary.json`；失败原文仍保留在 IPC 归档）。
  - 测试：新增 `tests/test_vw_acceptance_verdict.py`（partial 误放行、缺对象/重复对象、缺哈希/哈希不等、空集合、不同 plan、attach/in-place 声明等 23 项）。
- **版本回归修复（benchmark / schema）**：不再使用无来源的冻结哈希。
  - 新增不可变历史快照 `schemas/frozen/compiled_utility_ir.v1.0.schema.json`（从 git HEAD 原样导出，字节级 SHA-256=`6230e208…`，与 HEAD 冻结常量一致；不是凭空重建）。
  - `m1_5_t7.py` 新增版本注册表 `IR_SCHEMA_SNAPSHOTS`：v1.0 → 归档快照，v1.1 → 当前 schema（SHA-256=`9bdeaebf…`）；`verify_ir_schema()` 覆盖版本选择与篡改拒绝；历史输入哈希 `_FROZEN_INPUT_SHA256_V1_0`（HEAD 值）只读保留，当前 v1.1 表继续驱动可运行 fixture。
  - `BENCHMARK_VERSION` 显式升到 1.1；artifact envelope `artifact_version` 允许 1.0/1.1；artifact `scenario_definition` 记录 `ir_schema_version` 与 `ir_schema_sha256`。迁移关系：B1-B7/B9/B10 合成输入显式携带内外径（期望域状态与 canonical IR 哈希不变）；B8 合成 fixture 固定 v1.0 身份字符串，其冻结输入/规则束哈希不动。
- **既有 WIP 夹具修复**：`test_nominal_pipe_dimension_and_host_axis_agree` 按各宿主真实契约断言（Vectorworks：DN/内径/外径/geometry_kind/circumferential_segments；Blender：物理外径 + `openbim_geometry_diameter_mm` / `openbim_geometry_outer_diameter_mm` 属性），保留“同一 IR 映射一致”的测试目的，未删除断言。
- **全仓测试**：`uv run pytest tests/ -q` = **1629 passed / 6 skipped / 0 failed**（约 540s；3 warnings 为既有 SyntaxWarning）。此前的 29 benchmark 失败 + 6 benchmark 错误与 1 个 WIP 夹具失败全部修复，未使用 skip/xfail/放宽检查。
- **边界不变**：本轮不操作真实 Vectorworks、不调用 VLM、不做三态经验/研究实验/论文数据；未提交、未推送。
- **下一阶段**：IFC 实体几何与独立规则身份交付（沿用本轮收紧后的验收判定；之后如需真机复验，默认 spawn 方式或 `--in-place --operator-confirmed` 才能声明磁盘重开）。

### 2026-10-18 Vectorworks 真机验收通过（上一轮，优先于下方各轮）

- **验收结论**：双直管实体网格（水平 + 平面斜向带竖坡）在真实 Vectorworks 2024 上完成 构建 → 逐操作验证式落盘 → 尺寸/语义读回 → 重开读回哈希比对 全链路。分级报告：`docs/architecture/VW_REAL_ACCEPTANCE_2026-10-18.md`。
- **构建**：22/22 typed operations 全部应用并落盘（每次保存 `SaveActiveDocument` 返回码 0 + mtime 前进校验）；sidecar 22/22、10 个对象确认；`receipt` status=partial（既定语义：闭合性不可验证 + 该入口无 IFC 规则身份）。
- **尺寸读回**：pipe-001 内/外径 300/360 mm、长 10.0 m；pipe-002 内/外径 300/360 mm、长 10.007997 m；`vertex_index_base=0`。语义比对 PASS（10 对象，0 差异）。
- **重开验证**：文件从磁盘重新打开后只读投影，双管顶点 SHA-256 与构建时逐一相同（`reopen_verified=true`、`vertex_sha256_stable=true`、method=`in-place-file-reopen`）。哈希见 summary.json。
- **真机发现并修复两个宿主缺陷**：① `zip(strict=True)` 在宿主 Python 3.9 报错 → runner 改 `_zip_strict` + AST 回归测试；② `vs.DoMenuTextByName("Save", 0)` 对已命名文档静默 no-op（探针实测 mtime 不动、不写盘、不报错）→ 每次保存改 `SaveActiveDocument` + 返回码 + mtime 前进校验，静默未写盘即失败关闭；新增源码回归测试与静默跳过写盘的失败关闭测试。缺陷现场副本在验收目录 `bug-evidence/`。
- **fail-closed 现场证据**：重开验证期间活动文档为空白 `Untitled 1/2/3` 时，runner 连续 3 次正确拒绝（recovery requires the controlled target document to be active），未写入任何内容。
- **测试**：`tests/test_vw_runner.py` 45 passed（含 2 个新回归）；+ `tests/test_municipal_delivery.py` 合计 79 passed；ruff 通过。全仓 `uv run pytest tests/ -q` = 1566 passed / 6 skipped / 1 既有 WIP 夹具失败（`test_utility_candidate.py::test_nominal_pipe_dimension_and_host_axis_agree`）+ 29 benchmark 失败 + 6 benchmark 错误；后者全部同一既有根因：`schemas/compiled_utility_ir.schema.json` 的 v1.1 WIP 修改（2026-10-17 23:48，当前 SHA `9bdeaebf…`）与 benchmark 冻结常量 `befe4f8f…` 不一致。本轮未动 schema/benchmark，也未重新冻结。
- **边界不变**：无 IFC 实体交付、无 VLM 调用、无弯头/三通；未提交/推送。
- **唯一下一动作**：处理 benchmark 冻结基线（重新冻结到当前 schema 或恢复 schema，使全仓测试回到单一既有失败）；本轮的 Vectorworks 真机验收本身已闭环。

### 2026-10-18 Vectorworks 几何验证边界强化（上一轮，优先于下方 2026-10-17 直管实体增量）

- **验证分层（本轮核心）**：`geometry_snapshot_version` 升到 "1.1"（delivery 只接受 1.1，并显式拒绝 1.0）。每个直管对象分开报告 `dimension_status`（12 项尺寸检查：端面内/外环质心与轴向、方向、长度、内外径等，全部由 `GetMeshVertex` 实际顶点计算）与 `topology_status`（5 项闭合性检查：面数与 `topology_face_count`、缺面、重复或多余面、朝向一致、边流形）。聚合对象状态为 verified / dimension_verified_topology_unverified / failed / unverified；旧粗粒度 `checks` 不再作为通过依据。
- **拓扑证据不可伪造**：VW 2024 官方 vs API 只有 `GetMeshVertsCnt` / `GetMeshVertex` / `SetMeshVertex` / `MeshToGroup`（破坏性），没有非破坏性面/边读回，因此宿主适配器恒报 topology unverified（method=`no_nondestructive_host_face_api`，`_read_host_mesh_faces` 恒返回 None）。delivery 要求声明 verified 时必须给出 5 项检查全 true 且 `topology_face_count == 4×24 = 96`，否则失败关闭；测试注入删面、反转面、重复面均按失败记录，不得用顶点数冒充闭合验证。面环比较按六位小数去重、旋转/取向后比较有向环。
- **读回容差与离散误差分离**：`readback_tolerance_m=1e-5`（0.01 mm，对应六位小数读回）附 `readback_tolerance_basis` 明文；新增 `discretization` 记录 N=24 的 mid-edge sagitta（h=R(1−cos(π/N))；内径 300 mm ≈ 1.283271 mm、外径 360 mm ≈ 1.539967 mm）并强制 `is_engineering_tolerance=False`。0.01 mm 是读回容差，不是工程验收阈值；报告不得把 24 段圆周离散描述成表面精度。
- **delivery 强校验**：非有限值全扫描、重复对象 ID、12 项必需尺寸检查缺失、容差范围 (0, 1e-3]、离散字段与公式不一致、claims 与实测推导状态不一致（快照/对象/拓扑声称对比实际测量）均为失败并失败关闭；completed + 未通过拒绝；partial + 通过（身份不一致）拒绝；`geometry_caveats` 只在非 verified 状态透传，verified 时不携带旧限制文本。
- **runner 可观测与可控停止**：心跳新增 `ipc_root` 与 `stopped_reason` 字段；`run_session` 支持有界会话（`OPENBIMAGENT_VW_RUNNER_MAX_SECONDS`）与 stop 文件（默认 `<ipc_root>/stop_runner`，可 `OPENBIMAGENT_VW_RUNNER_STOP_FILE` 覆写）。停止只在轮询轮次之间生效：进行中的宿主 API 调用不可取消；正常停止不写强杀、不关闭 VW。
- **客户端目录契约预检**：jobs/results 必须以 jobs/results 结尾、同父目录、与 `OPENBIMAGENT_VW_IPC_ROOT`（若设）一致；心跳存在则校验其 `ipc_root` 与新鲜度（>120s 快速失败），心跳缺失不误报（ping 仍是最终真值）。
- **前端**：新增状态标签「尺寸通过 · 闭合性未验证（宿主无面读回 API）」；对象详情分列尺寸/闭合性；receipt 的 `geometry_caveats`（几何验证限制）逐条可展开。
- **测试**：`tests/test_municipal_delivery.py` + `tests/test_vw_runner.py` 76 passed（新增拓扑负例、快照 1.1 严格校验、心跳/目录契约、有界会话与 stop 文件用例）；全仓 `uv run pytest tests/ -q` = 1598 passed / 6 skipped / **1 failed**；唯一失败是既有 WIP 夹具错误（`tests/test_utility_candidate.py::test_nominal_pipe_dimension_and_host_axis_agree` 期望 `VectorworksOperation.diameter_mm`，而模型只有 `nominal/inner/outer_diameter_mm`；与本轮改动无关，未修改）。前端 28 passed；build 通过（保留既有大包与 Vite 告警）。Ruff：本轮改动文件通过（`src/openbimagent/utility/__init__.py` 存在 15 条既有 WIP F401，未动）。
- **真机验收（已于当日执行并通过）**：`tools/vw_real_acceptance.py`（prepare/launch/run/reopen/status/stop；独立目录 `out/vw-acceptance/<UTC>/ipc/{jobs,results}` + `out/` 授权根；内置双管 IR，plan 22 个 typed operations）在真实 VW 2024 上完成构建与重开读回，结果见上方 2026-10-18 真机验收通过节与 `docs/architecture/VW_REAL_ACCEPTANCE_2026-10-18.md`。`GetMeshVertex` 索引起点已获真机证据：`measured.vertex_index_base=0`。
- **真机验证已完成（见上节）**：2026-10-18 当日在真实 VW 2024 上执行并通过（构建 22/22、尺寸读回、语义 PASS、重开哈希一致）；真机暴露的 `zip(strict)` 与菜单保存静默 no-op 缺陷均已修复并加回归测试。真机 API 与本地参考不一致处按实际失败记录，未改判为通过。
- 边界不变：本轮未追加 VLM 调用，未做 IFC 实体几何、弯头/三通、三态经验、研究实验或论文数据；未提交/推送。

```text
M1 G1–G5     = PASS
M1 G6        = PASS（Blender 5.2.0 LTS 真实 + Vectorworks 2024 GUI 验收通过）
M1 G7        = PASS（全仓质量门禁）
M1.5 T1–T7   = PASS（多节点、确定性路线、水力、规则证据、B1-B10 Benchmark）
M2 P0–P5     = PASS（FastAPI 只读/写控制、SSE 流、Web 数字化 3D 工作台）
微内核跃升   = PASS（registry.invoke 承重调度、UI-Slots 动态标签、HTTP 插件端点）
规则自愈强化 = PASS（规则集驱动净距核验 + RouteConstraintReport 覆土 + 自适应膨胀半径）
Benchmark 真实化 = PASS（M1.5 T7 真跑 + 直插基线 + SH 消融电池；LLM 行显式占位）
Profile 补丁层   = PASS（CapabilityOverride + 激活/停用还原 + profile.ablation.no_self_healing）
外部插件加载 = PASS（openbimagent-plugin.toml manifest 约定 + OPENBIMAGENT_PLUGINS_DIR 发现加载）
LLM-Direct 真实基线 = PASS（gpt-5.6-terra 实测 60% 合规；B10 超时 ×3 = 真实扩展性上限发现）
3D 视口真实化 = PASS（/api/v1/demo/municipal-pipeline 端点 + 自愈时间线接真实求解输出）
Codex 机制吸收 = PASS（规则自检样例 self_tests + invoke 三态策略门 + 健康探针/背压）
M3 Blender 通路 = PASS（cad_host:blender.execute 正式能力 + 默认 prompt 治理 + Web 导出按钮 + HTTP/真机 3 测全绿）
M3 VW 通路 = PASS（真机验收 2026-08-23:1 passed in 8.22s;m3_registry_e2e.vwx 44.6KB + completed 回执落盘;runner 日志 consumed_total=4 全链路证据）
前端全真实化 = PASS（中栏执行流/规则树/3D 视口/自愈时间线全部接真实数据;HITL 卡驱动真实策略门导出;唯 VLM 六维评分区仍为演示数值）
当前状态     = 全栈工程落地 + 双真机基线复绿，D:/G6_Test/m3_invoke_e2e.blend 为微内核全链路产物
Agent 工程吸收 = PASS（2026-09-19 批次，分支 feat/agent-engineering-absorption：schema 版本化/压缩 v2/OBSK/doom-loop/stop-gate、subagent output_schema 契约、投影 rewind/fork、SQLite 幂等、headless CLI、MCP serve、项目信任、组织 requirements 锁定、审批 fail-closed 加固；计划书 docs/research/2026-09-17_agent_engineering_implementation_plan.md，遗留登记 DEFERRED_ITEMS.md）
流式实时化   = PASS（2026-09-21：SSE 生产通路打通——EventSource ?token= 认证豁免 X-Request-ID、规范跟随端点 offset 增量读 + Last-Event-ID 恢复 + 流预算、前端活动状态行/Trace/3D 视口运行期联动刷新、/compact 假命令诚实化）
Provider 方言 = PASS（2026-09-21：anthropic Messages 与 openai-responses 方言落地，四方言齐备；models.toml 增直连 provider 示例）
Web 真链路   = PASS（2026-09-24 批次：Web「新建任务」接工作台真 LLM 规划链（缺 key 降级模板并如实发 llm_planner 事件，不假成功）+ 无 CAD 主机离线 IFC4X3/IDS 交付进归档与工件白名单 + HITL 审批卡渲染执行预览 + 生成精度四项（RAG few-shot 语料 / dispatch rework 透传 / spatial_constraints 回验 linter / benchmark 几何 diff）+ 审批票据进程级共享状态污染修复；逐项收益与证据边界见 docs/research/2026-09-24_web_run_llm_chain_and_accuracy_batch.md）
```

## 2. 恢复坐标

```text
分支：main
HEAD：以 `git rev-parse HEAD` 实测为准
全仓测试：见 §4（随每次实测更新）
代码规范：Ruff check src/ 100% checks passed
手动测试：参考根目录下 MANUAL_TESTING_GUIDE.md
```

## 3. 已完成核心能力矩阵

### M1 G1–G7：双宿主与核心确定性管道

- **双宿主 typed 执行**：Blender 5.2 + Vectorworks 2024，同一份 `CompiledUtilityIR v1` 确定性生成，双宿主语义一致性验证。
- **不可变工件交付**：`ArtifactManifest v1.1`、`IfcOpenShell IFC4X3`、`buildingSMART IDS 1.0`、`MunicipalRuleEvidenceBundle` 签名证据。

### M1.5 T1–T7：市政管网求解器矩阵

- **四大确定性 Solvers**：StraightGravitySolver / NetworkGravitySolver / GridRouteSolver (A*) / HydraulicSolver (Manning)。
- **B1–B10 Benchmark 体系**：覆盖串联、汇流、分流、高程冲突、断网、有向环、规则歧义与 102 节点复杂管网。

### M2：产品化服务与 Web 控制台

- **FastAPI / SSE 服务**：`/api/v1/sessions`、`/api/v1/tree`、`/api/v1/export` 等只读与控制端点。
- **现代化 3 栏数字化工作台 (`web_ui.py`)**：左栏领域包与会话树；中栏执行流卡片与 HITL 审批；右栏 WebGL 3D 视口 + 六标签工作台（标签栏由插件 `declared_slots` 动态组装）。

### 微内核与 DSH 对标机制（2026-08 本会话）

1. **承重能力调度**：全部 7 个内置插件在 setup() 绑定真实 Handler，经 `registry.invoke()` 调度；HTTP 端点 `/api/v1/plugins`、`/api/v1/ui/slots`、`/api/v1/plugins/invoke`。
2. **Profile 补丁层**（对标 DSH Cordis patch layer）：`CapabilityOverride` 声明能力重定向，`activate_profile` 失败关闭校验后应用补丁，`deactivate_profile` 自动还原；`plugin.ablation.direct_solver` + `profile.ablation.no_self_healing` 为内置消融示范。
3. **规则自愈强化**：净距核验由规则集 `select_clearance_rule` 驱动（MU-CLEAR-001 分级碰撞/缓冲判定），覆土核验消费求解器 `RouteConstraintReport`；膨胀半径按规则净距自适应（ceil(2.5m/分辨率)），起终点检查井网格受保护。
4. **Benchmark 数据诚信契约**：`run_academic_benchmark` 五行对比表——openBIMAgent (M1.5 T7 真跑)、自愈 ON/OFF（SH-1–SH-6 电池，OFF 行经 Profile 补丁）、Heuristic 直插基线（StraightGravitySolver 逐段真跑）、LLM-Direct（**UNMEASURED 占位**，Markdown/LaTeX 强制携带禁止引用警告）；每行携带 `measured` + `provenance`。
   - 当前实测基线：T7 判定正确 10/10；SH 电池 ON 收敛 5/6 (83.3%) vs OFF 2/6 (33.3%)；直插基线合规 0%。
5. **外部插件发现**：`openbimagent-plugin.toml` manifest 约定 + `OPENBIMAGENT_PLUGINS_DIR` 目录发现加载（失败关闭：manifest 与插件事实不符即拒绝注册）；生态索引约定 GitHub topic `openbimagent-plugin`。

### M3 真机 E2E 预演（2026-08-22，tools/m3_real_e2e_blender.py）

全链路每步经微内核调度、不绕过 registry：`invoke("solver:self_healing")` 真实收敛（2 轮/5 段/1 违规自愈）→ 策略门把 `cad_host:*` 设为 prompt（无 confirm 拒绝、confirm=True 放行，Codex execpolicy 语义在真机生效）→ `invoke("cad_host:blender")` 生成 typed plan → headless Blender 5.2 + fork addon `execute_plan` 受控执行：receipt=completed、semantic snapshot 与 IR 哈希绑定、22 对象、120KB .blend + sidecar 落盘授权根。前置修复：真机集成测试的临时相机/灯改走 `__OBMCP_` 作用域豁免（scope lock 语义正确，原测试自违其锁）；v1.2 前的过期受控产物已重生成。

### Codex 机制吸收（2026-08-22 会话，调研见 docs/research/02_opensource_landscape.md §6）

1. **规则自检样例（self_tests，对标 execpolicy "加载即单测"）**：MunicipalRuleSet 升 v1.2 / 编译器 v0.3.0；12 条净距规则全部携带 match/not_match 样例（合计 33 例，覆盖边界值/跨界命中兄弟规则/缺属性失败关闭），`compile_municipal_rule_set` 编译期对全规则集重放验证，任一失效即拒绝整个规则集；**production 规则两类样例缺一不可**（治理失败关闭）；`run_rule_self_tests` / `validate_rule_self_tests` 公共 API；schema 与 T7 冻结哈希同步重冻结。
2. **能力策略门（对标 execpolicy 三态决策）**：`CapabilityPolicyRule`（精确/`前缀:*` 通配 + 最长前缀获胜 + justification 必填进拒绝信息）；`registry.invoke` 执法——forbidden 直接拒绝、prompt 需显式 `confirm=True`（人工确认语义）、无策略默认放行（开放内核，治理显式开启）；策略表经 `set_capability_policies` 失败关闭装载并暴露于 inventory。
3. **服务加固（对标 app-server）**：`/healthz` `/readyz` 探针；`/api/v1/plugins/invoke` 有界并发背压（满载 503 + error code -32001 "Server overloaded; retry later."）+ 求解移入线程池不再阻塞事件循环；Web Console 增"确认执行"复选框走 prompt 全链路。

## 4. 最新有效质量证据

```text
全仓 pytest：1391 passed, 9 skipped, 2 warnings（2026-09-24 实测，8m59s；含双宿主真机 4 测）
前端 vitest：14 passed；pnpm build 与 tsc 通过；oxlint 0 errors
规则自检：真实知识源 33/33 样例重放通过（test_rule_self_tests）
Ruff 静态检查（src/ + 本轮改动文件）：All checks passed!
消融电池确定性：test_self_healing_ablation 跨运行逐字节一致
Web 真链路 e2e（2026-09-24，有 key 实测）：llm_planner mode=llm → 20 语义资产 / 56 空间约束 → domain_gate PASS → deliver 审批门人工放行 → ifc_delivery pass（checked_entity_count=6, findings=64）→ 12,818B IFC4X3_ADD2 经工件端点可读、4 产物入归档 index
```

## 5. 新会话与快速启动

- 启动 Web 工作台：`uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000`（模块级 `app` = 演示装配：空只读 service + 默认微内核；生产装配走 `build_m2_readonly_app(adapter)`）
- 运行全量测试：`uv run pytest tests/ -q`
- 生成论文对比表：`uv run python -c "from openbimagent.benchmark.academic_bench import run_academic_benchmark; print(run_academic_benchmark().to_markdown_table())"`
- 自愈消融电池：`uv run python -c "from openbimagent.benchmark.self_healing_ablation import run_self_healing_ablation; on, off = run_self_healing_ablation(); print(on); print(off)"`

## 6. 未完成债务与唯一下一动作

- **执行模式承诺与实现仍不符**：设置页三档模式无对应行为，服务端只有 `agent` / `yolo` 生效（`runs.py`）。2026-09-24 批次只完成选型建议（Ask/Plan/Build/Audit）并否掉增量编辑模式（宿主侧对象级回滚不可验证即不承诺），**代码一行未动**。
- **RAG few-shot 效果无量化**：机制（仅 PASS 入库 / 去重 / 检索 / 零重合不注入）有测试，"降低宿主 API 语法幻觉"这一主张缺 A/B；5 例种子为手写。
- **约束 linter 只证不误报**：真 LLM 运行 56 条约束 0 违规；对真违规的召回未验证（缺自然违规样本）。几何回验刻意不做（scene-IR ↔ compiled-IR 无 id 桥）。
- **前端审批卡与预览未浏览器实测**：仅 tsc/vitest 层通过；IDS 那 64 个 finding 的语义未逐条核。
- **子代理并发 + 多批次未过真模型**：只过了确定性模板。
- **`fastapi_app` import 期建 app**（生成 token、按默认路径读磁盘票据）是本轮测试污染的物理根因；本批只加守卫与测试清表，未改设计（惰性构建牵动 uvicorn 入口，需单独拍板）。
- **论文侧**：B10 LLM 超时 ×3 与 LLM 行多次运行方差待写入 limitations；execpolicy 吸收可作 rule-driven 可验证性论据；几何 diff 指标（invert/坡度/覆土误差）已可作为精度量化口径，但需补一轮完整 `llm_direct_baseline` 才有可引用数字。
- **VLM 六维评分区**仍为演示数值（前端其余区域已全真实化）。
- **双轨编排**：模板流水线轨不经过 `core/loop.py` 治理面，属事实缺口非完成态；收敛路径与触发条件已登记 `DEFERRED_ITEMS.md` §4.1。
- **Provider 方言**：anthropic / openai-responses 已落地但仅 mock 单测覆盖，未对真实端点冒烟；直连 profile 的 reasoning effort 映射待细化。
- **唯一下一动作**：① 论文正文写作（素材已备齐：docs/学术材料/实验数据与limitations草稿_2026-08-23.md，LLM 行已升级为 n=3 均值±标准差 60.0±0.0 / 7105±330ms / 10447±294 tok）；② 若要兑现三档模式承诺，先定 Ask/Plan/Build/Audit 边界再动 `runs.py`；③ 补一轮完整 `llm_direct_baseline` 使几何 diff 指标产出可引用数字。
