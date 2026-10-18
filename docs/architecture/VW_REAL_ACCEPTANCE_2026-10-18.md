# Vectorworks 真机验收报告（2026-10-18）

- 验收对象：市政直管实体网格（水平直管 + 平面斜向带竖坡直管）+ 尺寸读回 + 语义读回 + 落盘/重开完整性
- 验收根目录（gitignored）：`out/vw-acceptance/20261017T155917Z/`
- 宿主：Windows 上 `Vectorworks Design Suite 2024`（报告 `VectorWorks 29`，嵌入 Python 3.9），可执行文件 `D:\devloop\VectorWorks 24\Vectorworks2024.exe`
- 工具：`tools/vw_real_acceptance.py`（prepare/launch/run/reopen/status/stop）+ `mcp_servers/vectorworks_mcp/runner.py`（经 `Tools > Plug-ins > Run Script` 加载）
- 结论：**通过（按分级口径）**——构建、逐操作落盘、尺寸读回、语义比对、重开读回哈希一致均通过；闭合性（拓扑）按宿主 API 限制恒为 `unverified`，全程未声称通过、未伪造证据。

## 1. 分级验收矩阵

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 首次执行边界：空白未命名文档、米单位、授权层 | 通过 | runner 预检；`M1-Municipal-Utility` 层；对象全部落层 |
| 2 | typed plan 执行（22 个操作，双管 + 3 检查井 + 系统/端口） | 通过 | `receipt.json`：applied_operations=22 |
| 3 | 逐操作持久化（每次保存返回码 + mtime 前进校验） | 通过 | 22 次 `SaveActiveDocument` 全部 code=0 且 mtime 前进；失败即 fail-closed |
| 4 | 执行状态 sidecar | 通过 | applied 22/22；confirmed_objects=10 |
| 5 | 尺寸读回（实测网格顶点） | 通过 | 见 §2；`geometry_snapshot.json` |
| 6 | 语义快照与比对 | 通过 | semantic_verified=true；对比 status=PASS，10 对象，0 差异 |
| 7 | 闭合实体拓扑（面数/重复面/共享边/朝向） | **不可验证**（按 API 限制，记录 unverified） | VW 2024 无非破坏性面/边读回；`topology_status=unverified`，caveat 明文 |
| 8 | 重开读回（文件从磁盘重新打开后测） | 通过 | 双管顶点 SHA-256 与构建时逐一相同；`reopen_verified=true`，`vertex_sha256_stable=true` |
| 9 | 生命周期（stop 文件、心跳、正常停止） | 通过 | `runner_heartbeat.json` / `runner_log.txt`；未强杀、未关闭宿主进程 |

回执总状态 `status=partial` 属既定语义：闭合性不可验证，且该交付入口未携带 IFC 交付所需的独立规则评估身份。**partial 不等于失败，也不等于 completed**。

## 2. 实测几何（构建与重开一致）

| 对象 | 内径 mm | 外径 mm | 长度 m | index_base | 顶点 SHA-256 |
|---|---|---|---|---|---|
| pipe-001（水平） | 300.0 | 360.0 | 10.0 | 0 | `494bc28e84c05451c8b3979f3f9dad0e3805e23087fc22d47115dd905305eb1f` |
| pipe-002（斜向 + 0.4 m 落坡） | 300.0 | 360.0 | 10.007997 | 0 | `3f4453ffb1392c7a3ace06aa81985f6854017198e9048e3654e30fd0d4dcc99d` |

- 读回容差 `1e-5 m`（0.01 mm）是六位小数读回的数值分辨率，不是表面精度或工程验收容差。
- 24 段圆周离散的 mid-edge sagitta（内径 ≈1.283 mm / 外径 ≈1.540 mm）记录为 `discretization`，`is_engineering_tolerance=false`。
- 构建耗时 `elapsed_ms=4304`；输出文件 56,490 字节，`output_sha256=bf8345bab3229c213cf63af1b7722403c9656e4ce8ce2bf4645ea7e95a2f`。

## 3. 本轮真机发现并修复的宿主缺陷

### 3.1 宿主 Python 3.9 不兼容 `zip(strict=True)`
- 现象：首次真机 run 在 `create:pipe-001` 处失败，回执错误 `zip() takes no keyword arguments`；此前 16 个操作已执行。
- 根因：VW 2024 嵌入 Python 为 3.9，`zip(strict=...)` 是 3.10+。
- 修复：runner 全部 12 处改为 `_zip_strict`（保留长度一致性校验）；新增 AST 回归测试 `test_runner_source_runs_on_vectorworks_2024_host_python`（禁 `zip(strict=)`、`itertools.pairwise`、`match`）。

### 3.2 `vs.DoMenuTextByName("Save", 0)` 对已命名文档是静默 no-op
- 现象：修复 3.1 后重建完成（活文档读回全过），但关闭重开后文件缺 `pipe-002`、`pipe-001` 的记录字段为空。
- 真机探针（验收目录内 `probe_save.py`）实测：
  - `SaveActiveDocument(path)` → 返回码 0，每次都真实写盘（mtime 前进，含无修改调用）；
  - `DoMenuTextByName("Save", 0)` → 无异常、无对话框、**mtime 不变**（不写盘）。
- 根因：runner 对已命名文档走菜单保存，落盘不可观测，部分操作从未持久化。
- 修复：每次保存统一 `SaveActiveDocument` + 返回码校验 + mtime 前进校验（不改写即抛错失败关闭）；新增源码回归测试 `test_runner_source_never_persists_through_menu_save` 与静默跳过写盘的失败关闭测试。
- 旧缺陷文件副本：`out/vw-acceptance/20261017T155917Z/bug-evidence/`（缺 `pipe-002` 的 `.vwx`、sidecar 与第一轮构建产物）。

### 3.3 fail-closed 现场证据
重开验证期间，当活动文档为空白 `Untitled 1/2/3` 时，runner 连续 3 次以 `Vectorworks recovery requires the controlled target document to be active: expected=…, actual=Untitled N` 拒绝执行，未写入任何内容——活动文档守卫按设计工作。

## 4. 重开验证方法与诚实声明

- 方法：构建完成后，`two-pipes.vwx` 由脚本经 Windows 文件关联在**同一 VW 进程内从磁盘打开**（此前也已验证 `Vectorworks2024.exe <path>` 参数启动可用），重新加载 runner 后以 recovery 路径只读投影：`reopen_method=in-place-file-reopen`。
- 哈希比对：构建与重开两轮的双管顶点 SHA-256 完全一致（§2 表），证明落盘内容完整、加载无损。
- 局限：未做"全新进程 + 关联打开"的额外一轮；VW 没有文档打开 API，重开只能依赖 GUI/文件关联。闭合性无法验证的原因与限制见 §1 row 7。

## 5. 产物清单（验收目录内）

- `receipt.json`、`geometry_snapshot.json`、`semantic_snapshot.json`（构建）
- `receipt_reopen.json`、`geometry_snapshot_reopen.json`（重开读回）
- `summary.json`（kind=reopen；`reopen_verified=true`、`vertex_sha256_stable=true`、双轮哈希表）
- `compiled_ir.json`、`out/two-pipes.vwx`（56,490 B）、`out/two-pipes.vwx.openbimagent.json`（sidecar 22/22）
- `bug-evidence/`（3.2 缺陷现场副本）
- `ipc/runner_log.txt`、`ipc/runner_heartbeat.json`（会话/停止记录）
- 探针脚本：`probe_enum.py`、`probe_save.py`

## 6. 残余风险与限制

- VW 的定时备份对话框可能在长时间会话或关闭时弹出，会打断人工操作；本轮以 "No" 处理，未影响已落盘内容。
- runner 轮询期间 VW 界面显示"未响应"是设计使然（脚本占用主线程），所有 GUI 操作必须在会话停止后进行。
- 工作区既有 WIP：`schemas/compiled_utility_ir.schema.json`（v1.1 编辑）与 benchmark 冻结常量 `befe4f8f…` 不一致（当前 SHA `9bdeaebf…`），导致 29 个 benchmark 失败 + 6 个错误；与本轮无关，未重新冻结（冻结是带证据含义的独立操作，需单独决策）。
- 超出本轮范围（未做）：IFC 实体几何与独立规则身份、弯头/三通、原生排水插件、VLM 调用；未提交/推送。

## 7. 本轮结论

在真实 Vectorworks 2024 上，双直管实体网格的创建、逐操作验证式保存、尺寸读回、语义比对与重开落盘完整性均已通过；闭合性因宿主 API 限制保持 `unverified`（不冒充通过）。本轮同时修复了两个只有在真机上才会暴露的宿主兼容缺陷。验收链闭环，带 IFC 实体几何的交付仍为后续工作。
