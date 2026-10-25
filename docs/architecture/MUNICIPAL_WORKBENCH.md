# 市政管网检查与修复工作台

实现说明，2026-10-25（上一版 2026-10-17）。当前验收与待验证项以 [交接状态](PROJECT_HANDOFF_STATUS.md) 顶部本轮记录为准。管网能力面已从 DN300 单一管径升级为 [150, 2000] mm 动态查表分档（DN300/DN400/DN500/DN600 全覆盖）；2026-10-25 起还包含 CEGIS 拓扑修复沙箱、工程纵断面与 NBV 主动相机、BIM-Wiki-Ontology 经验演化（S0→S1→S2 沉淀）与确定性消融基准，详见文末「2026-10-25 四大架构升级」一节。

## 从用户操作出发

1. 新建或选择工程对话，在「管网检查与修复」填写节点坐标、地面/起点管底标高、管段连接、坡度、实际内外径和地表条件。可导入当前对话的已编译模型，但不猜测缺失工程参数。
2. 明确障碍物检查范围。空障碍物清单只有经用户明确声明完整覆盖才视为该范围内无障碍物；未提供资料是未知。
3. 「保存并检查」保存草稿，显示对象、规则、实测值和证据；有错误也能保存。问题可定位到参数。当前检查范围通过不等于工程全面合规。
4. 「生成修复建议」先展示修改前后值，再由用户采纳。2026-10-25 起为 CEGIS 多分支拓扑沙箱：并行推演顺坡传导调坡、局部跌水井、平面正交折线绕行、超长管段自动插井、满包混凝土加固受控特批 5 类算子（含组合分支），按违规数/碰撞数/造价 J(S) 字典序选出胜出方案并返回帕累托比选账本；锁定节点绝不整体罢工，只改未锁定部分或启用其他拓扑算子。仍不凭空改管径。
5. 可锁定具体参数；删除、替换或重排锁定对象会被后端拒绝。人工修改区分纠错、偏好和需求变更，需求变更新增需求版本。
6. 「视觉检查」生成轴测/正视/俯视图，再调用配置的 `critic_scad` 角色。页面显示视觉角色解析到的 profile、模型、provider、端点、凭据变量是否存在（不显示值），以及本轮是否真正调用、服务返回模型、耗时和用量。耗时始终显示，不依赖 usage 是否返回；provider、端点和传输事件放在可展开详情。缺少凭据、模型未声明视觉能力、429 限流、超时、结构化结果非法会分别提示；三视图和参数草稿保留，不伪造评分或通过。视觉建议仍需审阅、确定性复核和再次视觉检查。
7. 视觉模型输入与修复建议都绑定同一版本：三视图、构件 ID、坐标/单位、视角说明、锁定参数、冻结规则、确定性检查结果、候选/IR/规则哈希和允许修改路径一并发送。视觉输出区分可定位疑点、证据不足/遮挡、建议检查参数和有限补丁，并用 `evidence_source` 区分“图像观察”“引用确定性检查”“来源不确定”；仅复述已知确定性冲突不算独立视觉发现。补丁必须引用已知规则、精确旧值、未锁定且允许的路径，并在应用后重新通过完整确定性校验。没有再次视觉检查，不能声称视觉疑点已修复。
8. 人工确认当前版本后选择宿主执行。新修改使旧确认失效。顶部 CAD 导出入口打开当前任务工作台，不导出演示模型。导出前说明宿主准备条件和支持范围；导出后分别显示实体是否生成、语义读回是否通过、几何读回哪些通过/未验证，以及输出文件和失败原因。可按构件 ID 查看几何读回详情。Vectorworks 实体成功不代表 IFC 或工程合规完成。

## 代码与数据流

| 职责 | 真实文件 / 符号 |
|---|---|
| 页面和会话隔离 | `frontend/src/App.tsx`、`MunicipalWorkbench.tsx::MunicipalWorkbench` |
| HTTP 客户端 | `frontend/src/services/api.ts` 的 municipal 方法 |
| 版本、锁、修复与确认 | `src/openbimagent/server/municipal_workflow.py::MunicipalWorkflowStore` |
| 实际动作适配 | `server/municipal_actions.py::propose_repairs/check_visual/export_revision` |
| 草稿与冻结规则 | `utility/candidate.py::UtilityCandidate/FrozenRuleContext/validate_utility_candidate` |
| 有界修复（CEGIS 沙箱） | `utility/candidate.py::propose_candidate_repairs`（兼容入口）、`evaluate_repair_sandbox`、`propose_candidate_repairs_with_ledger`、`compute_engineering_cost`、`check_candidate_state` |
| 经验本体与素材复用 | `utility/wiki_ontology.py::compile_and_archive_wiki_case/retrieve_ontology_recipes/update_recipe_probe_feedback` |
| 公共几何 | `utility/geometry.py::axis_centerline/execution_geometry/closest_segment_points/closest_segment_box_points` |
| 管网与拓扑求解 | `utility/network_solver.py::solve_network_gravity_utility`、`utility/spatial_graph.py::SpatialBIMGraph` |
| OpenSCAD 与视觉 | `vision/municipal.py::render_municipal_preview/MunicipalVLMCritic` |
| NBV 视点 / 纵断面 / 自由相机 | `vision/municipal.py::compute_clash_normal_nbv/render_longitudinal_profile_view/render_custom_camera_views` |
| 视觉配置解析与错误分类 | `server/municipal_actions.py::vision_configuration/check_visual` |
| Vectorworks 直管实体计划 | `assembly/vectorworks_plan.py::VectorworksBuilder`（plan 1.1） |
| Vectorworks 网格实体与几何读回 | `mcp_servers/vectorworks_mcp/runner.py::_create_pipe_mesh/_project_geometry_snapshot` |
| 语义/几何分层交付校验 | `deliver/host_delivery.py::verify_host_delivery` |
| 仓库 `.env` 加载 | `env.py::load_dotenv`（CLI 与 FastAPI lifespan 共用） |
| 页面几何映射 | `frontend/src/components/viewport/sceneGeometry.ts::parseSceneGeometry`、`Scene3D.tsx`、`CanvasViewport.tsx` |
| 宿主与实际读回 | `assembly/blender_host_executor.py`、`assembly/vectorworks_host_executor.py`、`deliver/host_delivery.py::verify_host_delivery` |

前端组件文件位于 `frontend/src/components/viewport/`；表中 Python 短路径均相对 `src/openbimagent/`。

API 为 `GET/PUT /api/v1/municipal/{session_id}`、`GET /api/v1/municipal/vision-config`（只返回角色/provider/模型/端点/凭据变量与存在性，不含密钥），以及 POST `import-ir`、`lock`、`propose`、`apply`、`visual`、`auto-refine`（有界自动迭代：默认 4 轮 + 新违规码/碰撞引入即回滚的严格门禁）、`confirm`、`export`、`ifc-retry`。操作绑定 `base_revision` 与 `candidate_sha256`，应用修复还需匹配 `proposal_id`，人工确认需匹配当前检查证据的 `review_token`。预览接口要求当前图像版本并限制到该会话目录。只接受已存在的工程会话。

保存流程为：候选条件 → 固定规则验证 → `CompiledUtilityIR 1.1` → 页面/OpenSCAD/宿主适配器。该 IR 显式区分管底线与轴线、DN 与真实内外径；轴线取管底加内半径。旧 1.0 IR 保留读取与原序列化行为，执行前必须显式提供物理尺寸并升级，不能把 DN 当外径。

## 校验与修复边界

- 领域规则沿用仓库规则编译器与设计限值证据，首次保存冻结到任务；不临时编造规范阈值。冻结规则便于复核，但不等于已证明规范适用于所有实际项目。
- 三维几何使用有限轴线段之间、轴线段到盒体的距离，再结合明确外径。规范水平净距与物理外包络分开记录；不把 AABB 重叠体积当管道碰撞体积。
- 直管胶囊包络端部与盒角存在近似边界；不能作确定结论的情况报告 UNKNOWN。合法连接仅在明确的共用节点局部连接区域内豁免，不能使整段管线免检。
- 当前不校核水力、结构承载、精确开口端部实体、节点间未提供的地形。检查井是示意对象。2026-10-25 起提供真实里程—高程工程纵断面渲染（`render_longitudinal_profile_view`：地面线、0.7m 覆土控制线与 0.45m 特批线、管顶/管底线、检查井竖井剖面、障碍物投影）；3D 预览内部的高程近似仍只是坐标轴—高程投影，不替代纵断面图。
- 确定性修复应用前必须减少阻断问题且不新增其他阻断问题。视觉修改必须重新通过确定性校验；这不能证明视觉缺陷已消除。
- VLM 接收真实图片、当前约束/问题、构件标识、锁和视角/单位说明，输出 `observation_kind` 为 `locatable_suspicion`、`insufficient_evidence` 或 `parameter_check` 的观察，以及有限数值补丁。补丁必须引用已知规则身份、精确旧值、匹配对象、未锁定且允许的路径；非法字段、未知对象/规则、旧版本、旧值不符、非有限数值、空操作补丁均被拒绝。没有关联规则的视觉疑点允许 `constraint_ids` 为空，但仍是视觉疑点而非工程判定。
- 视觉补丁应用后旧视觉结果和人工确认失效，必须重新运行完整确定性校验并再次视觉检查；确定性失败不能被视觉“通过”覆盖。单次动作最多 1 次传输尝试/模型，结构化输出最多 1 次修正重试，视觉调用有 90 秒单一有界截止时间；每次模型/fallback/结构化重试启动前都检查取消，超时后不再发新请求。超时/429/凭据缺失/模型无视觉能力/解析失败分别返回 `failure_code`；aborted/部分流只记录诊断，不解析为有效报告。
- 传输诊断记录请求模型与实际尝试模型、provider/端点、图片数量/尺寸/编码与解码字节数、文本长度、响应头/首个流事件/首正文/首 reasoning/结束时间、HTTP 状态、finish_reason、正文与 reasoning 字符数、usage 是否存在、aborted/取消原因、关闭请求和后台线程是否退出；不记录密钥、完整 base64 或完整 reasoning 正文。诊断仅证明调用事件，不等于有效视觉报告。openai-compatible 方言只能证明“已请求关闭连接”，不能宣称所有 provider 都能被硬中断；Google GenAI 方言只能记录响应后取消。90 秒截止是当前端点实测后的有界余量，不是成功保证。

## 视觉调用链与配置

`critic_scad` 走现有 `config/models.toml` 的 profile 角色绑定，不新增平行配置系统。解析顺序为：`.env` 的 `OPENBIMAGENT_PROFILE` → `models.toml` profile → `critic_scad` 模型 → provider → `api_key_env` 环境变量 → 端点。CLI 与 FastAPI lifespan 共用 `env.py::load_dotenv`，因此直接 `uvicorn openbimagent.server.fastapi_app:app` 也会加载仓库 `.env`，且不覆盖进程已注入的变量。诊断入口为 `GET /api/v1/municipal/vision-config`；只报告凭据变量名和是否存在，不返回密钥值，也不把“配置声明 vision”当作“本轮已真实调用成功”。

当前机器上的真实解析结果（2026-10-17，仅记录非敏感事实）：profile `sensenova`，`critic_scad` = `sensenova-6.8-flash-lite`，provider `sensenova`，端点 `https://token.sensenova.cn/v1/chat/completions`，凭据变量 `SENSENOVA_API_KEY` 存在；降级为 `mimo-v2.5`（`FREETOKENFAUCET_API_KEY` 存在，但真实调用返回 402）。实际调用结果与耗时见交接状态，不能仅凭此配置声明推断视觉检查已通过。

**接口文档核对**：2026-10-17 未能获取 SenseNova 官方 API 参考页；公开教程确认该端点走 OpenAI 兼容 `POST /v1/chat/completions`、模型名 `sensenova-6.8-flash-lite`、图像使用 `image_url` 内容块，但未确认 `reasoning_effort`、`max_tokens`、流式细节或 base64 data-URI。实际请求证据：base64 data-URI 工程图可被接受；`reasoning_effort: low` 被发送且未导致 400，但真实运行中 reasoning 字符数仍可从约 0.7k 到 10.7k，不能宣称“已关闭思考”或已限制推理长度。`max_tokens` 未设置；不根据猜测新增参数。诊断阶段禁用 fallback 和结构化重试，避免把 429/超时原因混在一起。

## 宿主与持久化边界

状态位于配置的 sessions 目录下 `municipal/<session_id>/state.json`，采用临时文件替换。锁仅限单进程，部署必须单 worker；没有跨进程或跨 CAD 宿主事务承诺。

执行前重新校验持久化条件和冻结规则，不接受前端自行提供的执行 IR。运行中禁止修改；服务中断后显示结果未知，不自动重试。相同版本和宿主的成功请求返回既有回执，避免重复生成；失败/中断后必须核对宿主并通过最新 `execution_id` 显式声明重试，新执行使用独立输出名。这是单 worker 范围的重复请求保护，不是跨 CAD 宿主事务或任意故障下恰好一次执行保证。

Blender 适配器使用真实轴线与外径并读取实际对象。Vectorworks 适配器在 plan 1.1 下为每个直管段生成闭合网格实体：外壁、内壁和两端环面；轴线、DN、真实内径和外径分别传递，不再用单个 `diameter_mm` 同时表示 DN 和 OD。网格使用官方本地 Script Reference 中的 `BeginMesh` / `BeginPoly3D` / `Add3DPt` / `EndPoly3D` / `EndMesh` / `LNewObj`；读回使用 `GetMeshVertsCnt` / `GetMeshVertex`，并用 `GetTypeN` 要求 Mesh 类型 40。节点/检查井仍是示意对象；不支持弯头、三通实体、检查井精细构造或原生专业排水插件。执行前要求空白未命名文档、米单位和授权输出根，防止改动既有图纸。

Vectorworks 逐管段的语义字段（稳定 ID、系统、端口拓扑、材料、DN、坡度、起终管底）来自宿主记录；轴向端点、长度、实测内外径、环顶点数、顶点哈希和误差来自实际网格顶点。`geometry_snapshot` 与 `semantic_snapshot` 分开保存；`host_delivery` 独立比较 IR 与实测几何，读回容差 `1e-5 m`（0.01 mm，基于六位小数读回，附基据明文）。尺寸验证与闭合性验证分开：`dimension_status` 由实测顶点核对 12 项尺寸检查；`topology_status` 需要宿主非破坏性面/边读回，而 VW 2024 无此 API（`MeshToGroup` 为破坏性转换），故恒为 `unverified`——任何无面证据的通过声称都会被 delivery 失败关闭。24 段圆周是 `discretization` 记录的离散误差（内径 sagitta ≈1.283 mm，`is_engineering_tolerance=false`），不是工程容差，也不能当作表面精度。倾斜管不使用世界坐标 AABB 当管径；包围盒未作为直径证据。缺少内/外径、非直管、退化长度、内径不小于外径或宿主缺少 mesh API 时，在修改宿主前拒绝。

当前工作台宿主成功回执通常标为 `partial`：宿主文件、实体、语义和几何读回可以完成，但现有 IFC 构建器缺实体几何，且该入口未传递 IFC 所需的独立规则评估身份。回执分开报告 `geometry_status`（verified / dimension_verified_topology_unverified / failed / unverified / not_applicable）和语义比较结果；尺寸通过但闭合性未验证时界面显示「尺寸通过 · 闭合性未验证」，不等于完整几何通过；几何失败或未验证时不会显示通过，`geometry_caveats` 逐条列出读回范围与限制。不能宣称 IFC 工程交付完成，也不会因此自动重复宿主执行。

## 2026-10-25 四大架构升级（CEGIS 沙箱 · 主动相机 · 本体演化 · 消融基准）

- **动态查表管径**：`network_solver.py` 移除 DN300 硬编码，能力面放宽为 [150, 2000] mm（区间外报 `unsupported_diameter`）；新增 `lookup_pipe_normative_limits(diameter_mm, surface_type)` 按分档返回最小坡度（DN300 0.003 / DN400 0.002 / DN500 0.0015 / DN600+ 0.0010）、检查井间距分档（DN300-600 设计保守 40m / >600 50m，规范判定仍按 MU-WELL-001 编译限值）、最小覆土（车行道 0.7 / 人行道绿地 0.6）与最小障碍物净距 0.15m；逐段证据链按管径分档取限值。`schemas/network_utility_solver_input.schema.json` 仅追加可选字段（`manhole_type`/`drop_height_m`/`protection_measure`/`exception_approval_id`/`constraints`），向后兼容。
- **三级刚柔约束与造价 J(S)**：Tier 1 冻结国标红线（确定性校验器独占判定，不可放宽）；Tier 2 国标受控特批——满包混凝土加固（`protection_measure=concrete_encasement`）或显式 `constraints.allow_exception_approval` 时，覆土红线从 0.7m 受控放宽至 0.45m，检查报告附 `exception_applied` 与 `exception_approval_id`（默认 `EXC-MU-ELEV-001-ENCASEMENT`）；Tier 3 以 `compute_engineering_cost` 的土方开挖指数 `L·(D+0.6)·h̄²` 与管井/加固材料加权造价做比选，同时返回最小覆土/净距裕度。
- **CEGIS 五算子沙箱**：`evaluate_repair_sandbox` 在内存深拷贝中并行推演 5 类拓扑修复算子——顺坡传导调坡（含向下游未锁定节点传导）、局部跌水井、平面正交折线绕行（沿障碍物包络盒法向插入绕行井对并重算长度/坡度）、超长管段自动插井（W_mid 线性插值拆分）、满包加固受控特批——及其组合分支，逐分支运行 `check_candidate_state` + `compute_engineering_cost`，按 `(violation_count, hard_clash_count, total_cost_score)` 字典序选出胜出方案。`propose_candidate_repairs` 返回胜出补丁（签名与 `list[dict]` 返回类型不变），`propose_candidate_repairs_with_ledger` 返回完整帕累托账本。锁定节点绝不整体罢工：只修改未锁定节点或启用其他拓扑算子。
- **严格回滚门禁与默认轮次**：`iterative_refine` 默认 4 轮（可用 `max_rounds` 覆盖，仍夹在 [1, 10]）；回滚条件升级为「出现修改前不存在的新违规码 ∥ 硬违规总数增加 ∥ 引入 obstacle_clearance 碰撞」任一即回滚本轮补丁；每轮把 `pareto_proposals_ledger`、`engineering_cost`（before/after）与 `active_camera_trajectory` 写入 history 与 `state_diff_audit.json`（只增不改）。
- **具身主动相机**：`compute_clash_normal_nbv` 以管轴线 × 障碍物主轴/轴线的叉乘公垂线求正交于冲突平面的最佳视线（方位/仰角/对焦中心，支持模型自由偏移角）；`render_longitudinal_profile_view` 以 Matplotlib 渲染里程-高程纵断面 PNG；3D 视图自动叠加 Set-of-Mark 方位罗盘（`+X = E`、`+Y = N`、`+Z = Up`）、水流方向箭头（`W1 → W2`）与米制高程标尺；`render_custom_camera_views` 支持 `free_orbit` / `nbv_clash_normal` / `longitudinal_profile` 三模式（OpenSCAD 缺失或失败时如实报 `render_unavailable`，不伪造图片）。`check_visual` 默认附挂 1 张 NBV 特写与 1 张纵断面图送入 VLM；`MunicipalVisualReport` 新增 `next_camera_requests`（模型复拍请求）与 `active_camera_trajectory`。
- **BIM-Wiki-Ontology**：`utility/wiki_ontology.py` 维护属性图 `ontology_graph.json` 与 Obsidian 双链词条（`rules/ patterns/ assets/ cases/`，五类实体节点）；`compile_and_archive_wiki_case` 在确认 + 零硬冲突 + 真实宿主回执时沉淀 S0→S1→S2 案例词条（含最佳相机配方、胜出算子、造价指标）并同步旧 `exemplars/auto_archive/index.json`；`retrieve_ontology_recipes` 两跳检索（违规 → 冲突模式 → 案例/算子优先级/推荐相机/复用构件）驱动沙箱算子优先级与相机请求，`demoted` 经验自动过滤；`update_recipe_probe_feedback` 实现 Harness² 探针门控（`fail_streak >= 2` 自动熔断并写入 `> [!WARNING]` 标注）。检索严格只读，绝不写盘。
- **消融基准**：`scripts/run_municipal_ablation_benchmark.py` 确定性运行 12 个标准场景（DN300/400/500/600 × 5 类空间病害：坡度不足、下游浅埋、跨中障碍物碰撞、超长管段缺井、起点锁定受限浅埋），输出 `output/benchmark/ablation_report.md` 与 `ablation_metrics.json`；实测首次合规率 16.7% → 最终合规率 100%（≤4 轮），本体自进化前后算子命中率 0% → 66.7%，NBV 与纵断面触发 12/12。

## 验证与后续接入

单元/集成测试覆盖草稿校验、几何距离、规则来源、参数锁、过期修复/视觉结果拒绝、确认失效、进程中断、真实读回证据要求、视觉配置解析、错误分类、上下文内容、未知对象/规则/字段拒绝、非有限数值与空操作补丁拒绝、视觉补丁重新校验、证据来源、预取消零请求、部分流诊断保留、超时后不 fallback/不重试、fallback 元数据一致、直管实体网格、尺寸/拓扑分层读回、拓扑负例（删面/反转/重复面）、快照 1.1 严格校验、心跳与 IPC 目录契约、有界会话与 stop 文件、逐操作验证式保存与静默 no-op 拒绝、验收判定分项（partial 误放行/缺哈希/空集合/不同 plan）、benchmark schema 版本选择与篡改拒绝、IFC 空腔实体生成与文件重开测量（缺 Body/实心冒充/单位/长度/管径/放置错误/缺对象/重复 ID/断连接/规则身份缺失）、IFC 重试复用宿主回执、页面几何。2026-10-18 相关后端 79 项通过（`test_vw_runner` 45 + `test_municipal_delivery` 34）；验收判定 23 项；IFC 几何/交付 9 项；benchmark 版本/篡改新增 4 项。2026-10-25 升级轮：全仓 `python -m pytest tests/ -q` = **1633 passed / 6 skipped / 1 failed**（唯一失败 `test_ids_xsd_compiles_in_fresh_process_with_broken_network` 经 git stash 基线复核为改动前既有的环境性失败，与本轮无关）；新增 `test_ultimate_municipal_combo.py` 28 项。当前验收数字与真实外部运行证据见交接状态，合成测试数据不作为研究实验结果。

### Vectorworks 直管实体验证边界

- 生成物是**闭合网格实体**（外壁 + 内壁 + 两端环面），不是轴线、不是单纯外包络，也不是原生参数化管道/布尔实体。网格可以被宿主选中、命名、设类、挂记录和建立端口拓扑。
- 水平直管、平面斜向 + 竖向坡度直管、多管段与示意节点均由同一份 `CompiledUtilityIR 1.1` 映射；plan 1.1 显式携带 `nominal_diameter_mm`、`inner_diameter_mm`、`outer_diameter_mm`、`geometry_kind=hollow_tube_mesh`、`circumferential_segments`。
- 离线假 `vs` 测试已验证：96 个去重网格顶点（4 环 × 24 段）、实测内径 300 mm、外径 360 mm、轴向/长度/环顶点数核对；移动网格、缩放网格、缺失实体但记录残留、宿主缺少 mesh API、内径不小于外径、非直管、非空文档均在宿主写入前或读回阶段失败关闭；重复请求复用回执。
- **真机已验证（2026-10-18）**：在真实 Vectorworks 2024 上完成构建 → 逐操作验证式落盘 → 重开读回。22/22 操作、sidecar 22/22、10 对象确认；双管内/外径 300/360 mm、长度 10.0 / 10.007997 m、`vertex_index_base=0`；语义比对 PASS；`receipt` 为 partial（闭合性不可验证的既定语义）。关闭重开后从磁盘只读投影，双管顶点 SHA-256 与构建时逐一相同（`vertex_sha256_stable=true`，method=`in-place-file-reopen`）。真机发现并修复两个宿主缺陷：`zip(strict=True)` 在宿主 Python 3.9 不可用（改 `_zip_strict` + AST 回归测试）；`vs.DoMenuTextByName("Save", 0)` 对已命名文档静默 no-op（改 `SaveActiveDocument` + 返回码 + mtime 前进校验 + 回归测试）。分级报告：`docs/architecture/VW_REAL_ACCEPTANCE_2026-10-18.md`；证据（gitignored）在 `out/vw-acceptance/20261017T155917Z/`（含 `bug-evidence/` 缺陷现场副本）。
- **验收判定收紧（2026-10-18）**：`tools/vw_real_acceptance.py` 对 run/reopen 分项判定（执行、尺寸、语义、构件集合、顶点哈希、拓扑分级），任意 partial 不再视为成功；reopen 要求同 IR/plan、无缺对象/重复对象、全部预期管段哈希有效且逐一相等，空集合/缺哈希/哈希不等均阻塞；`--attach` 仅证明活动文档读回，`--in-place` 需 `--operator-confirmed` 才声明磁盘重开；新运行写入前自动把既有产物归档到 `history/`，成功后清空当前错误字段。

### 直管 IFC 实体交付（2026-10-18）

- **表示**：每个直管段输出真实空腔实体 `IfcCircleHollowProfileDef` + `IfcExtrudedAreaSolid`（Depth=真实三维轴线长度）；项目 SI 单位、Model/Body 几何上下文、`IfcAxis2Placement3D` 轴向放置；`Body/SweptSolid` 表示。节点/检查井保持示意（`GeometryRepresentation=schematic_none`）。来源标注为 `compiled_utility_ir`，不是 Vectorworks 原生导出。
- **输入与字段**：仅接受服务端保存/检查/确认的 CompiledUtilityIR 1.1；DN、内径、外径、壁厚分字段；复用共享的 `resolve_dimensions`/`execution_geometry`；支持平面水平管与斜向带坡管、多管段；不使用水平长度代替三维长度。
- **读回**：保存后的 IFC 重新打开，经 IfcOpenShell 几何引擎三角化（world coords）独立测量实体、空腔内外环、内外径、三维长度、轴线端点/方向、单位；体积一致性用实测环多边形面积 × 长度与网格体积比较（≤1e-6 相对差）；圆周离散误差（sagitta）单独记录，不是工程容差。解析失败/不可测量明确失败。
- **长度口径**：IFC 属性 `HorizontalLengthM` 是 IR 的水平投影长度（工程属性）；实体 `IfcExtrudedAreaSolid.Depth` 与几何读回的 `length_m` 都是真实三维轴线长度（斜管 L=√(Lh²+Δz²)）。两者不得互相替代或混报。
- **分层**：IFC 模式、IDS/语义/连接、几何读回、领域规则证据分开报告；IDS 仅实现子集，不宣称完整标准覆盖；规则身份使用版本化模型级聚合（`RuleDeliveryEvidence`），不挑选单条评估代表模型，不升级 unknown/review_required，无证据时失败关闭。
- **IFC 失败隔离与重试**：IFC 失败不改写宿主回执/文件；`ifc-retry` 仅重建 IFC，复用身份一致的宿主回执，不再次调用宿主；旧版本/未确认/IR 不一致请求被拒绝；失败历史保留。前端分三层显示并提供下载与重试入口，宿主 partial 仅因 topology unverified 时不要求重跑 CAD。
- **真实验收**：`out/ifc-acceptance/attempt-20261018T052030Z-01/`（gitignored）保存真实 IFC、schema/IDS/几何/规则报告与 summary；IFC SHA-256=`5d8c4bf5…`；schema/IDS/geometry=PASS，domain_rules=unknown（未升级），`confirmation_claim=none`；复现命令 `uv run python tools/ifc_local_acceptance.py`。输入为合成测试几何，非真实工程数据。
- **XSD 依赖（2026-10-18）**：IDS 1.0 XSD 的 W3C 导入依赖已本地化到 `schemas/w3c/`（来源 URL 与 SHA-256 固定），通过显式 lxml Resolver 仅从本地加载并在编译前校验摘要；不再依赖网络 schema 解析（`no_network=True` 本身不足以阻断 import 抓取）。
- **收尾（2026-10-18）**：IFC 下载统一从 `delivery.ifc`/`delivery.receipt.ifc` 读取并校验文件哈希与当前版本；引入 IfcOpenShell 模式/EXPRESS 校验（含重复/非法 GlobalId），报告随交付保存并纳入判定；重试要求宿主回执带可验证 IR 身份，使用独立 `attempt-NN` 目录并同步 `delivery_layers`；规则资格只取任务冻结上下文，unknown/review_required 不升级；验收脚本按分项判定退出码且不覆盖旧证据。
- **离线证据**：`out/municipal-vw-solid-validation/20261017T144248Z/summary.json`（`evidence_kind=offline_fake_vs`、`not_real_vectorworks=true`）保存了 plan 1.1、13 个操作、Mesh 类型 40、96 顶点/4×24 环、实测内外径、轴向和几何 checks。它只证明代码与读回协议，不是真机测量。2026-10-18 新增拓扑负例测试（假宿主注入删面/反转/重复面必须失败）扩展了该证据边界：离线只验证失败路径与协议，不证明真机闭合性——VW 2024 无面读回 API，真机恒为 topology unverified。

### 上一轮视觉诊断证据（本轮未追加 VLM 调用）

真实联调证据位于 `out/municipal-visual-validation/`（gitignored）。上一轮使用合成工程的三视图，禁用 fallback 和结构化重试，按 A 单张轴测图 → B 三视图短问答 → C 完整结构化协议逐级诊断（真实请求 6 次，达到预算上限）：
- A 单张工程轴测图：HTTP 200，32.35s，1,333 tokens，返回 281 字符观察，说明单图输入可用。
- B 三视图短问答：HTTP 200，15.76s，3,528 tokens，返回 523 字符观察，说明多图输入可用。
- C 完整结构化（旧上下文，60s）：HTTP 200，60.17s 被截止，正文 4,528 字符、reasoning 5,088 字符、无 usage；未生成有效报告。
- C 完整结构化（缩减确定性详情 + 输出上限，60s）：HTTP 200，59.43s 完成，12,520 tokens，2 个疑点、0 个补丁；模型明确没有把已知 P-002/SYN-BLDG-1 冲突作为独立视觉发现重述。
- 产品路径 `check_visual`（60s）：HTTP 200，60.43s 被截止，正文 0、reasoning 10,746 字符；说明 reasoning 长度波动是主要风险。
- 产品路径 `check_visual`（90s）：HTTP 200，27.14s 完成，12,108 tokens，2 个疑点、0 个补丁；服务返回模型 `sensenova-6.8-flash-lite`，provider/端点与诊断一致。

上一轮视觉结论：单图、三图和结构化检查已达到；没有得到可应用的补丁，因此没有执行“真实修复后复查”。90 秒是实测后的有界余量，不是成功保证；`reasoning_effort: low` 的官方语义未知，真实运行中 reasoning 长度仍可能很长。模型在 90 秒结果中把 P-002/SYN-BLDG-1 标为 `visual_observation`，但该冲突已由确定性输入提供，不能据此宣称独立视觉发现。本轮不追加 VLM 调用。

本轮 Vectorworks 结论：代码已支持直管内外表面网格实体、实际顶点尺寸读回，并明确区分尺寸验证与闭合性验证（VW 2024 无面读回 API，闭合性恒为 unverified 且不可声称通过）；离线假宿主测试通过；**真机验收已通过**（2026-10-18：22/22 落盘、尺寸读回全对、语义 PASS、重开哈希一致），并修复了两个真机才暴露的宿主兼容缺陷。带实体几何和规则身份的 IFC 交付已在同日接通最小闭环：真实空腔拉伸实体、保存后重开测量、四层报告、模型级规则证据聚合、IFC 单独重试与前端分层展示；benchmark/schema 冻结基线已修复（全仓 1641 passed / 0 failed）。下一阶段为 IFC 与外部工具链互操作、完整 IDS 覆盖和工程合规验证。2026-10-25 起工作台已实现 S0→S1 自动迭代记录与 BIM-Wiki-Ontology 经验演化（确认交付后沉淀案例词条、两跳检索驱动修复与相机），工程判定仍由确定性校验独占。
