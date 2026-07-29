# Relay 013 · GLM 5.2 · M1 核心：Orchestrator 并发调度 + SCAD 环完整化

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 012 完成；M0 收尾补丁已提交(`dffef44`)；测试基线 233 passed

---

## 0. 你的运行环境（与 012 一致）

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `dffef44`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/` / `uv run python -m compileall -q src`
- 当前测试基线：233 passed + 1 skipped
- Blender：`D:\devloop\blender\blender.exe`（5.2.0，headless 可行）
- `.env` 已有 AGENTROUTER_API_KEY（最新 key）
- profile=test 角色绑定：modeler=claude-opus-4-8、critic=gpt-5.5、其余=glm-5.2-ar

## 红线（违反即任务失败）

1. **禁止改动** `mcp_servers/`、`config/`、`domain_packs/`、`docs/`（`relay_workspace/` 内你的报告除外）、`.env`
2. 禁止新增第三方依赖；禁止删改任何现有测试（可新增测试）
3. commit 前必须三条验收命令全绿
4. commit 只提交 `src/`、`tests/` 相关改动
5. 任何 401/额度异常：**立即停止，保留日志，报告部分结果**
6. 全程诚实汇报：跑了什么、什么没跑成，不许编造证据（主会话会逐项自验）

---

## 任务概述

M1 里程碑包含三大核心（对应 ARCHITECTURE.md §3/COMPONENTS.md §2.4-2.5）：

1. **Orchestrator 并发调度器强化**（dispatch.py）：实现并发 ≤4、doom_loop 检测、SubagentResult 契约完整化
2. **SCAD 环完整化**（scad_loop.py）：补充 IR→OpenSCAD 转换的健壮性、patch 应用的严格校验、收敛判定的完整实现
3. **单元测试全覆盖**：为上述两个模块补充完整的单元测试，覆盖边界情况

**你的任务**：按照下面的规格，完成代码实现 + 单元测试编写 + 验收 + commit。

---

## 任务 A：Orchestrator 并发调度器强化（dispatch.py）

### A1：实现并发控制（asyncio.Semaphore）

当前 `run_plan` 是顺序执行（逐批串行）。M1 需要支持并发 ≤4：

1. 在 `run_plan` 函数中引入 `asyncio.Semaphore(MAX_CONCURRENCY)` 控制并发数
2. 将批次执行改为 `asyncio.gather` 并发调度（保持批次间顺序，批次内资产可并发）
3. **注意**：M0 的 `agent_fn` 是同步函数，需要用 `asyncio.to_thread` 包装
4. 保持现有的 `max_retries` 和 `doom_max_fix` 逻辑不变

### A2：doom_loop 检测增强

当前 `_check_doom_loop` 只是占位实现。补充完整逻辑：

1. 维护一个 `fix_history: dict[str, list[float]]`（资产 id → 历史评分列表）
2. 每次 FIX 后记录当前评分（从 `BatchReport.hint` 或其他评分来源提取）
3. 如果同一资产连续 `doom_max_fix` 次 FIX 且评分无进展（变化 < 0.3），返回 `True`
4. **评分提取**：如果 `BatchReport.hint` 包含 `"overall="` 或 `"score="` 字样，用正则提取；否则记录为 `-1.0`（表示无评分）

### A3：SubagentResult 契约完整化

当前 `SubagentResult` 定义了字段但未在 `run_plan` 中实际使用。补充：

1. 在 `run_plan` 返回的 `PlanRunResult` 中新增字段：`subagent_results: list[SubagentResult]`（默认空列表）
2. 每次调用 `agent_fn` 后，将结果包装为 `SubagentResult`（`summary` 从 `BatchReport.hint` 提取前 200 字，`artifact_paths` 暂为空列表）
3. 将所有 `SubagentResult` 累积到 `PlanRunResult.subagent_results` 中

### A4：单元测试（tests/test_orchestrator.py）

新建测试文件 `tests/test_orchestrator.py`，包含：

1. **test_run_plan_concurrent_batches**：
   - 构造 3 个批次的 plan，每批 2 个资产
   - `agent_fn` 用 mock，每次调用记录时间戳，sleep 0.1s 模拟耗时
   - 断言：总耗时 < 1.5s（证明并发生效，而非顺序执行的 0.6s）
   - 断言：`result.ok == True`，所有批次 PASS

2. **test_doom_loop_detection**：
   - 构造单批次 plan，`agent_fn` 返回连续 4 次 `Verdict.FIX`，每次 `hint` 包含 `"overall=5.0"`（评分无进展）
   - 断言：第 4 次调用后，`result.verdict == Verdict.ESCALATE`
   - 断言：`result.error` 包含 `"doom_loop"` 字样

3. **test_subagent_result_accumulation**：
   - 构造 2 批次 plan，`agent_fn` 返回 `BatchReport(verdict=PASS, hint="批次X完成")`
   - 断言：`result.subagent_results` 长度为 2
   - 断言：每个 `SubagentResult.summary` 包含对应批次的 hint

---

## 任务 B：SCAD 环完整化（scad_loop.py）

### B1：IR→OpenSCAD 转换健壮性增强

当前 `_asset_scad` 只支持 4 种图元（cube/cylinder/sphere/cone）。补充：

1. **图元缺失检查**：如果 `primitive` 不在 `PRIMITIVES` 中，抛出 `ValueError("不支持的图元: {primitive}")`
2. **size 格式校验**：为每种图元的 `size` 字段添加详细校验（长度、类型、非负），不符合抛出 `ValueError`
3. **position 边界检查**：如果 `position` 任一维度绝对值 > 1000，抛出 `ValueError("position 超出合理范围")`
4. **color 可选支持**：如果资产有 `color` 字段（RGB 三元组，值 0-1），在 OpenSCAD 代码中添加 `color([r,g,b])`

### B2：JSON Patch 严格校验

当前 `apply_patch` 只是占位实现。补充完整逻辑（参考 RFC 6902）：

1. 支持 3 种操作：`replace`（必须）、`add`（可选）、`remove`（可选）
2. **`replace` 操作**：
   - 检查 `path`（JSON Pointer 格式，如 `/assets/0/position/1`）是否存在
   - 检查 `old_value` 是否与当前值严格相等（`==`，浮点数容差 1e-6）
   - 不符合则抛出 `PatchValidationError("old_value 不匹配")`
   - 符合则应用 `value` 替换
3. **`add` 操作**：在指定路径插入新值（如果路径已存在则覆盖）
4. **`remove` 操作**：删除指定路径的值
5. **原子性**：所有 patch 操作要么全部成功，要么全部回滚（用 `deepcopy` 保护）

### B3：收敛判定完整实现

当前 `run_scad_loop` 的收敛判定只有 `hard_limit`。补充完整四选一（ADR-0004）：

1. **perfect_score**：overall >= 9.5，立即返回
2. **convergence_delta**：连续 2 轮评分变化 < `CONVERGENCE_DELTA`（0.5），判定收敛
3. **divergence_fallback**：连续 `FALLBACK_CONSECUTIVE_DROPS` 轮评分下降，回退到 `best_so_far`（历史最高分的 IR）
4. **hard_limit**：达到 `max_iterations` 上限

在 `LoopResult` 中新增字段：`terminate_reason: str`（四选一），记录收敛原因。

### B4：单元测试（tests/test_scad_loop.py）

在现有 `tests/test_scad_loop.py` 中新增：

1. **test_ir_to_scad_unsupported_primitive**：
   - 构造 IR 包含 `primitive="pyramid"`（不支持）
   - 断言：`ir_to_scad` 抛出 `ValueError`，消息包含 `"不支持的图元"`

2. **test_ir_to_scad_invalid_position**：
   - 构造 IR 包含 `position=[2000, 0, 0]`（超出范围）
   - 断言：`ir_to_scad` 抛出 `ValueError`，消息包含 `"超出合理范围"`

3. **test_ir_to_scad_with_color**：
   - 构造 IR 包含 `color=[1.0, 0.0, 0.0]`（红色）
   - 断言：生成的 OpenSCAD 代码包含 `"color([1.0000, 0.0000, 0.0000])"`

4. **test_apply_patch_replace_old_value_mismatch**：
   - 构造 IR，应用 `{"op": "replace", "path": "/assets/0/position/0", "old_value": 99.0, "value": 10.0}`
   - 断言：抛出 `PatchValidationError`，消息包含 `"old_value 不匹配"`

5. **test_apply_patch_replace_success**：
   - 构造 IR，应用 `{"op": "replace", "path": "/assets/0/position/0", "old_value": 0.0, "value": 5.0}`
   - 断言：IR 的 `assets[0].position[0]` 被修改为 5.0

6. **test_convergence_perfect_score**：
   - mock critic 返回 overall=9.8（超过 9.5 阈值）
   - 断言：循环在第 1 轮后终止，`terminate_reason == "perfect_score"`

7. **test_convergence_delta**：
   - mock critic 连续返回 overall=[7.0, 7.3, 7.4]（第 2-3 轮变化 < 0.5）
   - 断言：循环在第 3 轮后终止，`terminate_reason == "convergence_delta"`

8. **test_divergence_fallback**：
   - mock critic 连续返回 overall=[8.0, 7.0, 6.0]（连续下降）
   - 断言：循环触发 `divergence_fallback`，返回 IR 为历史最高分（第 1 轮的 IR）

---

## 任务 C：集成测试与冒烟验证

### C1：补充集成测试（tests/test_assembly.py）

在现有 `tests/test_assembly.py` 中新增：

1. **test_pipeline_with_scad_loop_convergence**：
   - 用 `SINGLE` playbook 跑 `run_pipeline`
   - mock `scad_critic` 返回递增评分 [6.0, 7.5, 8.5]（模拟收敛）
   - 断言：`result.ok == True`，session 包含 ≥3 个 `score` 事件

2. **test_pipeline_with_orchestrator_concurrent**：
   - 构造一个包含 3 批次的 playbook（临时创建 `tmp_path/test_playbook.md`）
   - mock `agent_fn` 每批耗时 0.1s
   - 断言：总耗时 < 1.5s（证明并发生效）

### C2：冒烟验证（可选，视额度而定）

如果额度允许（≤ 30k tokens），可选择性跑一次端到端冒烟：

```bash
uv run python -m openbimagent run \
  --playbook domain_packs/single_asset_hero/playbook.md \
  --out relay_workspace/m1_smoke --yes --no-hitl --profile test --image-size 512
```

- stdin 喂：`一个立方体\n简约现代\n3\n`
- 核对：session 包含 SCAD 环的 `score`/`patch` 事件，usage_summary.json 存在
- **注意**：如遇 401 立即停止，报告已完成的单元测试部分

---

## 任务 D：验收与 commit

### D1：验收清单

1. 三条验收命令全绿（pytest 应 ≥ 240 passed，新增约 7-10 个测试）
2. `uv run pytest tests/test_orchestrator.py -v`：新增的 3 个测试全 PASS
3. `uv run pytest tests/test_scad_loop.py -v`：新增的 8 个测试全 PASS
4. `uv run pytest tests/test_assembly.py::test_pipeline_with_scad_loop_convergence -v`：集成测试 PASS

### D2：commit 策略

分两个 commit 提交（职责分离）：

**Commit 1**（核心逻辑）：
```
M1 核心: Orchestrator 并发调度 + SCAD 环完整化

- orchestrator: 实现并发≤4控制、doom_loop检测、SubagentResult契约
- scad_loop: IR转换健壮性、JSON Patch严格校验、收敛判定四选一
```

**Commit 2**（测试覆盖）：
```
M1 测试: Orchestrator + SCAD 环单元测试全覆盖

- tests/test_orchestrator.py: 并发、doom_loop、SubagentResult 3个测试
- tests/test_scad_loop.py: IR转换、Patch校验、收敛判定 8个测试
- tests/test_assembly.py: 集成测试 2个
```

---

## 任务 E：报告

写到 `relay_workspace/m1_orchestrator_scad/report.md`，包含：

### E1：实现总结
1. 每个子任务的实现要点（A1-A4、B1-B4、C1-C2）
2. 遇到的技术难点与解决方案
3. 代码质量自查（是否符合现有代码风格、类型注解是否完整）

### E2：测试证据
1. 三条验收命令的**原始输出**（完整 pytest 输出）
2. 新增测试的详细列表（测试名 + 覆盖的场景）
3. 测试覆盖率统计（可选，用 `uv run pytest --cov=src/openbimagent/orchestrator --cov=src/openbimagent/vision`）

### E3：Commit 证据
1. 两个 commit 的 hash（`git log --oneline -2`）
2. 每个 commit 的文件变更统计（`git show --stat <hash>`）

### E4：冒烟结果（如果跑了）
1. returncode、session 事件统计、usage_summary.json 摘要
2. 如遇 401 或其他错误，报告详细日志与停止时刻

### E5：入库检查单
1. **改动文件清单**（只列 src/ 和 tests/）
2. **遗留问题**（如果有未完成或降级的实现）
3. **给主会话的建议**（优化方向、潜在风险）

---

## 回执格式

完成后只回：**「013 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 附录：技术参考

### 参考文件（按需阅读）
- `src/openbimagent/orchestrator/dispatch.py`（当前实现）
- `src/openbimagent/vision/scad_loop.py`（当前实现）
- `docs/architecture/ARCHITECTURE.md` §3（双环设计）
- `docs/architecture/COMPONENTS.md` §2.4-2.5（Orchestrator/Vision 规格）
- `docs/architecture/DECISIONS_DRAFT.md`（ADR-0004：收敛四选一）

### JSON Pointer 示例（RFC 6901）
- `/assets/0/position/1`：访问 `ir["assets"][0]["position"][1]`
- `/assets/0/size`：访问 `ir["assets"][0]["size"]`
- 实现时可用 `jsonpointer` 库（如果已有依赖），或手写解析（split "/"）

### asyncio 并发控制示例
```python
import asyncio

sem = asyncio.Semaphore(4)

async def worker(i):
    async with sem:
        await asyncio.sleep(0.1)
        return i

results = await asyncio.gather(*[worker(i) for i in range(10)])
```

### doom_loop 评分提取示例
```python
import re

hint = "批次1完成，overall=7.5，需改进材质"
match = re.search(r"overall=([0-9.]+)", hint)
if match:
    score = float(match.group(1))
```

---

## 最后提醒

1. **代码风格**：严格遵循现有代码的 docstring 风格（三引号文档头 + 中文注释）
2. **类型注解**：所有新增函数必须有完整的类型注解（参数 + 返回值）
3. **错误处理**：边界情况必须抛出明确的异常（ValueError/RuntimeError），不允许静默失败
4. **测试隔离**：每个测试用 `tmp_path` fixture，不污染项目目录
5. **诚实汇报**：如果某个子任务因技术难度或额度限制未完成，在报告中明确说明，不要跳过

祝顺利！🚀
