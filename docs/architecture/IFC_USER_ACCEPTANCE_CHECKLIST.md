# IFC 交付真实页面验收清单（准备稿，尚未执行）

> 前置条件：已确认**专用 Vectorworks 文档**与操作范围（空白未命名、米单位、仅本验收使用）。
> 未确认前不启动、不修改宿主，不触碰既有验收产物（`out/vw-acceptance/`、`out/ifc-acceptance/`）。
> 本清单只使用现有直管、CompiledUtilityIR 1.1、IR 派生 IFC 架构，不扩展功能。

## 0. 准备记录

- 会话 ID：`____________`
- 修订号 / candidate SHA-256：`____________ / ____________`
- 验收输出根：`____________`
- runner IPC 根与停止文件：`____________`
- 宿主调用次数基线：runner 心跳/日志中的 `consumed_total` = `______`
- 宿主输出文件路径与 SHA-256（导出前不存在）：`____________`

## 1. 首次导出

1. 在已确认的专用 VW 文档中完成“检查 → 确认版本”。
2. 选择宿主并执行导出。
3. 记录：
   - `execution_id`：`____________`
   - 宿主回执 `output_path` + 文件 SHA-256：`____________ / ____________`
   - 宿主调用次数（应为基线 + 1）：`______`
   - IFC `generation.status`、`attempt`、`ifc_path`、`ifc_sha256`、`schema/ids/geometry` 状态：`____________`
   - IFC stage 根部 `revision` / `candidate_sha256` 与当前版本一致：`是 / 否`
4. 预期：宿主结果分层与 IFC 分层分开显示；`topology=unverified` 保持未验证。

## 2. 下载与刷新恢复

1. 点击“下载 IFC”，记录下载文件 SHA-256，与 `generation.ifc_sha256` 比对。
2. 刷新页面，再次下载；确认：
   - 仍能下载（IFC 状态从 `delivery.receipt.ifc` 读取）；
   - `execution_id`、`revision`、`candidate_sha256` 不变；
   - 两次下载的文件 SHA-256 相同。
3. 若手动修改/删除 IFC 文件，下载应给出“内容不一致/文件缺失”的明确提示（验收后恢复或重试生成）。

## 3. 受控 IFC 失败（不重跑 CAD）

1. 在不影响宿主文件的前提下制造一次受控失败（例如临时占用 IFC attempt 目录/文件路径，随后恢复）。
2. 触发 IFC 生成/检查失败（若首次导出已成功，可在下一次同类导出前制造，或使用可重复的受控手段）。
3. 记录：
   - 失败阶段（生成/模式/IDS/几何）：`____________`
   - 错误信息与涉及构件 ID：`____________`
   - 宿主文件未变：`SHA-256 = ____________`（与第 1 步一致）
   - 宿主调用次数未增加：`______`（应等于第 1 步记录）

## 4. 仅重试 IFC

1. 点击“重试 IFC”（页面明确说明复用宿主回执、不重新执行 CAD）。
2. 记录：
   - 新 `attempt` 编号与独立目录：`____________`
   - 旧 IFC/报告/哈希仍存在：`是 / 否`
   - 新 IFC SHA-256、schema/IDS/geometry 状态：`____________`
   - `delivery_layers` 与 `receipt.ifc` 同步更新：`是 / 否`
   - 宿主 `execution_id` / 宿主文件 SHA-256 未变：`是 / 否`
   - 宿主调用次数未增加：`______`（应始终等于第 1 步）
   - `topology_status` 仍为 `unverified`：`是 / 否`
3. 旧版本保护（可选）：修改参数生成新版本后，尝试下载旧 IFC，应提示“IFC 属于旧版本”。

## 5. 再次下载

1. 下载重试后的 IFC，SHA-256 与 `generation.ifc_sha256` 一致。
2. 页面显示的分层结果与报告文件一致（schema/IDS/geometry/domain rules；domain rules 的 unknown/review_required 不得显示为通过）。

## 6. 通过/拒绝判据

- 通过至少需要：下载两次哈希一致；刷新后可下载；IFC 重试不增加宿主调用、不改宿主文件、旧产物保留；`delivery_layers` 与 `receipt.ifc` 无相反状态；schema/IDS/geometry 均通过（或明确不可用并拒绝标成功）。
- 任一宿主调用发生在“仅重试 IFC”期间、宿主文件哈希变化、顶层与回执 IFC 状态冲突、或 IFC 文件哈希不一致 → 验收失败并记录证据。
- IFC 通过不改变宿主 `topology=unverified` 的结论；模式校验通过不等于工程合规或完整 IDS 覆盖。
