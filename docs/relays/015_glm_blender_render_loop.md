# Relay 015 · GLM 5.2 · M1 强化：Blender 精检环全维评分 + HITL /tree 统一

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 014 完成；测试基线 264 passed；当前 commit `262a12b`

---

## 0. 你的运行环境（与 014 一致）

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `262a12b`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/` / `uv run python -m compileall -q src`
- 当前测试基线：264 passed + 1 skipped
- Blender：`D:\devloop\blender\blender.exe`（5.2.0，headless 可行）

## 红线（违反即任务失败）

1. **禁止改动** `mcp_servers/`、`config/`、`domain_packs/`、`docs/`（`relay_workspace/` 内你的报告除外）、`.env`
2. 禁止新增第三方依赖；禁止删改任何现有测试（可新增测试）
3. commit 前必须三条验收命令全绿
4. commit 只提交 `src/`、`tests/` 相关改动
5. 任何 401/额度异常：**立即停止，保留日志，报告部分结果**
6. 全程诚实汇报：跑了什么、什么没跑成，不许编造证据（主会话会逐项自验）

---

## 任务概述

M1 里程碑的 Blender 精检环强化 + HITL 体验统一（对应 ARCHITECTURE.md §3、COMPONENTS.md §2.5）：

**核心目标**：
1. 完善 `render_loop.py` 的六维评分与防放水五件套
2. 统一 HITL `/tree` 斜杠命令到 `fork` 机制
3. 实现 A/B swap 对比、强制 CoT、锚点对齐

**当前状态**（M0 已实现部分）：
- ✅ `render_loop.py` 基础循环结构已存在
- ✅ `VLMCritic` 已实现（critic.py）
- ✅ 六维 rubric 常量已定义（rubric.py）
- ⚠️ **缺失**：A/B swap 对比、防放水五件套验证、HITL `/tree` 统一

**你的任务**：
1. 强化 `render_loop.py` 的六维评分逻辑（A/B swap + 防放水验证）
2. 完善 `check_score_payload` 防放水校验
3. 统一 HITL `/tree` 命令到 `fork` 机制
4. 编写完整的单元测试和集成测试

---

## 任务 A：Blender 精检环六维评分强化

### A1：实现 A/B swap 对比机制（render_loop.py）

**背景**：防放水第 1 条要求"A/B swap 两两比较：与上一版快照对比，交换顺序防位置偏置"

**当前代码位置**：`render_loop.py:74-233`（`run_render_loop` 函数）

**需要修改的地方**：

1. **构建 A/B swap 上下文**（iteration >= 2 时）：
```python
# render_loop.py:180 附近（critic 调用前）
context = {
    "iteration": iteration,
    "ir": batch_ctx.get("ir"),
}

if iteration >= 2 and prev_images:
    # A/B swap: 与上一版快照对比，交换顺序防位置偏置
    context["previous_image_paths"] = prev_images
    context["ab_swap_ref"] = f"iter{iteration-1} vs iter{iteration}"
```

2. **传递上下文给 critic**：
```python
# render_loop.py:185
critique = critic.critique(
    image_paths=verify_paths,
    dimensions=BLENDER_DIMENSIONS,
    context=context  # 传递 A/B swap 上下文
)
```

3. **保存当前迭代截图供下轮对比**：
```python
# render_loop.py:195 附近（critic 调用后）
prev_images = verify_paths  # 保存当前截图供下轮 A/B swap
```

### A2：强化 check_score_payload 防放水校验（rubric.py）

**当前代码位置**：`rubric.py:200-260`（`check_score_payload` 函数）

**需要新增的校验**：

1. **强制 reasoning**（防放水第 3 条）：
```python
# rubric.py:210 附近
if not payload.get("reasoning") or len(payload["reasoning"]) < 50:
    raise ValueError("reasoning 缺失或过短（< 50 字符），违反防放水第 3 条（强制 CoT）")
```

2. **强制 anchor_ref**（防放水第 3 条）：
```python
# rubric.py:215 附近
if not payload.get("anchor_ref"):
    raise ValueError("anchor_ref 缺失，违反防放水第 3 条（锚点对齐）")
```

3. **低分强制 actionable_feedback**（防放水第 2 条）：
```python
# rubric.py:225 附近
overall_score = payload.get("overall_score", 0)
if overall_score < REWORK_COMMAND_REQUIRED_BELOW:  # 8.0
    feedback = payload.get("actionable_feedback", "")
    if not feedback or len(feedback) < 30:
        raise ValueError(f"overall_score={overall_score} < 8.0，actionable_feedback 缺失或过短，违反防放水第 2 条")
    
    # 简化判定：须含量化参数（数字）
    if not _QUANTIFIED_COMMAND.search(feedback):
        raise ValueError(f"actionable_feedback 缺少量化参数（须含数字），违反防放水第 2 条")
```

4. **维度评分范围校验**：
```python
# rubric.py:240 附近
for dim in dimensions:
    score = payload.get(dim.value)
    if score is None:
        raise ValueError(f"维度 {dim.value} 评分缺失")
    if not (0 <= score <= 10):
        raise ValueError(f"维度 {dim.value} 评分 {score} 超出范围 [0, 10]")
```

### A3：实现收敛判定完整逻辑（render_loop.py）

**当前代码位置**：`render_loop.py:190-230`（收敛判定逻辑）

**需要完善的四选一判定**（与 scad_loop.py 保持一致）：

1. **perfect_score**：`score >= min_score` → 立即收敛
2. **convergence_delta**：连续 2 轮 `delta < CONVERGENCE_DELTA` 且非下降
3. **divergence_fallback**：连续 `FALLBACK_CONSECUTIVE_DROPS` 轮降分 → 回退 best_so_far
4. **hard_limit**：达到 `max_iters`

**实现要点**：
```python
# render_loop.py:200-230 完善收敛判定
if score >= min_score:
    terminate_reason = "perfect_score"
    converged = True
    break

# 检测 divergence_fallback（先于 delta，防误判）
if prev_score is not None and score < prev_score:
    consecutive_drops += 1
    if consecutive_drops >= FALLBACK_CONSECUTIVE_DROPS and best_snapshot:
        # 回退到 best_so_far
        await client.restore_snapshot(str(best_snapshot))
        terminate_reason = "divergence_fallback"
        break
else:
    consecutive_drops = 0

# 检测 convergence_delta
if len(scores) >= 2:
    delta1 = abs(scores[-1] - scores[-2])
    if len(scores) >= 3:
        delta2 = abs(scores[-2] - scores[-3])
        if delta1 < CONVERGENCE_DELTA and delta2 < CONVERGENCE_DELTA:
            terminate_reason = "convergence_delta"
            break

# hard_limit 在循环外处理
if iteration >= max_iters:
    terminate_reason = "hard_limit"
```

---

## 任务 B：HITL /tree 命令统一到 fork

### B1：修改 HITL `/tree` 处理器（cli.py）

**当前代码位置**：`cli.py` 中的 HITL 斜杠命令处理（需要定位到具体位置）

**背景**：Relay 014 实现了 CLI `tree` 子命令（用 `fork`），但 HITL 的 `/tree` 斜杠命令仍用旧的 `branch`

**修改步骤**：

1. **定位 HITL 命令处理器**：
   - 搜索 `cli.py` 中处理 `/tree` 的代码
   - 通常在 `_handle_slash_command` 或类似函数中

2. **替换 `branch` 调用为 `fork`**：
```python
# 旧代码（假设）
if command == "/tree":
    new_session = current_session.branch(event_id)

# 新代码
if command == "/tree":
    new_session = current_session.fork(event_id)
```

3. **确保返回值一致**：
   - `fork` 返回 `SessionStore` 实例
   - 与 `branch` 返回值类型相同，无需额外修改

### B2：添加测试验证统一性（tests/test_cli.py）

**新增测试**：
```python
def test_hitl_tree_uses_fork(tmp_path):
    """验证 HITL /tree 命令使用 fork 而非 branch。"""
    # 创建 session，追加事件
    session = SessionStore.create(tmp_path / "sessions", title="test")
    event1 = session.append_new(EventType.MESSAGE, MessagePayload(role="user", content="test"))
    
    # 模拟 HITL /tree 调用（通过 CLI）
    # 实际实现取决于 HITL 架构
    
    # 断言：新 session 的 index entry 包含 forked_from
    # （这证明使用了 fork 而非 branch）
```

---

## 任务 C：单元测试

### C1：render_loop 单元测试（tests/test_vision.py，新建或扩展）

**8 个测试**：

1. `test_render_loop_perfect_score_converges`
   - mock critic 返回 overall=9.5（>= min_score=8.0）
   - 断言：第 1 轮后收敛，`terminate_reason == "perfect_score"`

2. `test_render_loop_convergence_delta`
   - mock critic 连续返回 overall=[7.0, 7.3, 7.4]
   - 断言：第 3 轮后停止，`terminate_reason == "convergence_delta"`

3. `test_render_loop_divergence_fallback`
   - mock critic 连续返回 overall=[8.0, 7.0, 6.0]
   - 断言：触发 fallback，调用 `client.restore_snapshot`

4. `test_render_loop_hard_limit`
   - mock critic 始终返回 overall=6.0，max_iters=3
   - 断言：第 3 轮后停止，`terminate_reason == "hard_limit"`

5. `test_render_loop_ab_swap_context`
   - 运行 2 轮，检查第 2 轮 critic 调用的 context
   - 断言：context 包含 `previous_image_paths` 和 `ab_swap_ref`

6. `test_render_loop_saves_best_snapshot`
   - mock critic 返回递增评分 [6.0, 8.0, 7.0]
   - 断言：best_snapshot 指向第 2 轮的快照（8.0 最高分）

7. `test_render_loop_scope_lock_enabled`
   - 运行 1 轮，检查 client.set_editable_scope 调用
   - 断言：被调用，参数包含 batch 对象列表

8. `test_render_loop_html_report_generated`
   - 运行完整循环，检查 HTML 报告路径
   - 断言：result.html_report 存在且文件实际生成

### C2：check_score_payload 单元测试（tests/test_rubric.py，新增）

**5 个测试**：

1. `test_check_score_payload_missing_reasoning`
   - payload 缺少 `reasoning` 字段
   - 断言：抛出 `ValueError`，消息包含"reasoning 缺失"

2. `test_check_score_payload_short_reasoning`
   - payload 的 `reasoning` 只有 20 字符
   - 断言：抛出 `ValueError`，消息包含"reasoning 过短"

3. `test_check_score_payload_missing_anchor_ref`
   - payload 缺少 `anchor_ref` 字段
   - 断言：抛出 `ValueError`，消息包含"anchor_ref 缺失"

4. `test_check_score_payload_low_score_no_feedback`
   - overall_score=6.0（< 8.0），但 `actionable_feedback` 为空
   - 断言：抛出 `ValueError`，消息包含"actionable_feedback 缺失"

5. `test_check_score_payload_feedback_no_quantified`
   - overall_score=7.0，actionable_feedback="改进材质"（无数字）
   - 断言：抛出 `ValueError`，消息包含"缺少量化参数"

### C3：集成测试（tests/test_assembly.py，新增）

**2 个测试**：

1. `test_pipeline_blender_loop_six_dimensions`
   - 用 SINGLE playbook 跑 pipeline
   - mock render_critic 返回完整六维评分
   - 断言：session 包含 score 事件，payload 包含全部六维

2. `test_pipeline_ab_swap_second_iteration`
   - 用 SINGLE playbook，max_iters=2
   - mock render_critic，检查第 2 次调用的 context
   - 断言：第 2 次调用包含 `previous_image_paths` 和 `ab_swap_ref`

---

## 任务 D：验收与提交

### D1：三条验收命令

```bash
# 1. 全量测试（应 ≥279 passed，新增 15 个测试）
uv run pytest -q

# 2. 代码检查
uv run ruff check src/ tests/

# 3. 编译检查
uv run python -m compileall -q src
```

### D2：提交策略（分两个 commit）

**Commit 1**（核心逻辑）：
```bash
git add src/openbimagent/vision/render_loop.py src/openbimagent/vision/rubric.py src/openbimagent/cli.py
git commit -m "M1 强化: Blender 精检环全维评分 + HITL /tree 统一

- vision/render_loop: A/B swap 对比、收敛判定四选一完整实现
- vision/rubric: check_score_payload 防放水校验强化（reasoning/anchor_ref/actionable_feedback）
- cli: HITL /tree 命令统一到 fork 机制"
```

**Commit 2**（测试覆盖）：
```bash
git add tests/test_vision.py tests/test_rubric.py tests/test_assembly.py tests/test_cli.py
git commit -m "M1 测试: Blender 精检环与防放水校验单元测试全覆盖

- tests/test_vision.py: render_loop 收敛/AB swap/best snapshot 8个测试
- tests/test_rubric.py: check_score_payload 防放水校验 5个测试
- tests/test_assembly.py: 六维评分与 AB swap 集成 2个测试
- tests/test_cli.py: HITL /tree 统一验证 1个测试（可选）"
```

---

## 任务 E：报告

写到 `relay_workspace/m1_blender_render_loop/report.md`，包含：

### E1：实现总结
1. 每个子任务的实现要点（A1-A3、B1-B2、C1-C3）
2. 遇到的技术难点与解决方案
3. 防放水五件套的实现验证

### E2：测试证据
1. 三条验收命令的**原始输出**（完整 pytest 输出）
2. 新增测试的详细列表（测试名 + 覆盖的场景）
3. 防放水校验的具体案例

### E3：Commit 证据
1. 两个 commit 的 hash（`git log --oneline -2`）
2. 每个 commit 的文件变更统计（`git show --stat <hash>`）

### E4：防放水五件套验证清单
对照 ARCHITECTURE.md §3，逐条验证：
1. ✅ A/B swap：实现位置 + 测试覆盖
2. ✅ 强制 rework：check_score_payload 校验逻辑
3. ✅ 锚点对齐：reasoning + anchor_ref 强制
4. ⏭️ 关键维门禁：留待 domain_gate 实现（M1.5）
5. ✅ judge 分家：VLMCritic 已实现（critic.py）

### E5：入库检查单
1. **改动文件清单**（只列 src/ 和 tests/）
2. **遗留问题**（如果有未完成或降级的实现）
3. **给主会话的建议**（优化方向、潜在风险）

---

## 回执格式

完成后只回：**「015 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 关键技术要点（避免踩坑）

### 1. A/B swap 上下文构建

```python
# render_loop.py:180 附近
context = {
    "iteration": iteration,
    "ir": batch_ctx.get("ir"),
}

if iteration >= 2 and prev_images:
    context["previous_image_paths"] = prev_images
    context["ab_swap_ref"] = f"iter{iteration-1} vs iter{iteration}"

critique = critic.critique(
    image_paths=verify_paths,
    dimensions=BLENDER_DIMENSIONS,
    context=context
)

# 保存当前截图供下轮对比
prev_images = list(verify_paths)
```

### 2. check_score_payload 防放水校验

```python
# rubric.py:200-260 强化校验
def check_score_payload(payload: dict[str, Any], dimensions: tuple[Dimension, ...], phase: str) -> None:
    """防放水校验：强制 reasoning/anchor_ref，低分强制 actionable_feedback。"""
    
    # 1. 强制 reasoning（>= 50 字符）
    reasoning = payload.get("reasoning", "")
    if not reasoning or len(reasoning) < 50:
        raise ValueError("reasoning 缺失或过短（< 50 字符），违反防放水第 3 条（强制 CoT）")
    
    # 2. 强制 anchor_ref
    if not payload.get("anchor_ref"):
        raise ValueError("anchor_ref 缺失，违反防放水第 3 条（锚点对齐）")
    
    # 3. 低分强制 actionable_feedback（< 8.0）
    overall = payload.get("overall_score", 0)
    if overall < 8.0:
        feedback = payload.get("actionable_feedback", "")
        if not feedback or len(feedback) < 30:
            raise ValueError(f"overall={overall} < 8.0，actionable_feedback 缺失或过短，违反防放水第 2 条")
        if not re.search(r"\d", feedback):
            raise ValueError("actionable_feedback 缺少量化参数（须含数字），违反防放水第 2 条")
    
    # 4. 维度评分范围校验
    for dim in dimensions:
        score = payload.get(dim.value)
        if score is None:
            raise ValueError(f"维度 {dim.value} 评分缺失")
        if not (0 <= score <= 10):
            raise ValueError(f"维度 {dim.value} 评分 {score} 超出范围 [0, 10]")
```

### 3. 收敛判定四选一（与 scad_loop 保持一致）

```python
# render_loop.py:200-230
# 1. perfect_score
if score >= min_score:
    terminate_reason = "perfect_score"
    converged = True
    break

# 2. divergence_fallback（先于 delta）
if prev_score is not None and score < prev_score:
    consecutive_drops += 1
    if consecutive_drops >= FALLBACK_CONSECUTIVE_DROPS and best_snapshot:
        await client.restore_snapshot(str(best_snapshot))
        terminate_reason = "divergence_fallback"
        break
else:
    consecutive_drops = 0

# 3. convergence_delta（连续 2 轮 < 0.5）
if len(scores) >= 2:
    delta1 = abs(scores[-1] - scores[-2])
    if len(scores) >= 3:
        delta2 = abs(scores[-2] - scores[-3])
        if delta1 < CONVERGENCE_DELTA and delta2 < CONVERGENCE_DELTA:
            terminate_reason = "convergence_delta"
            break

# 4. hard_limit（循环外）
if iteration >= max_iters:
    terminate_reason = "hard_limit"
```

### 4. HITL /tree 统一示例

```python
# cli.py 中的 HITL 命令处理
def _handle_slash_command(command: str, args: str, current_session: SessionStore):
    if command == "/tree":
        # 解析 event_id
        event_id = args.strip() or current_session.head
        
        # 使用 fork 替代 branch
        new_session = current_session.fork(event_id)
        
        print(f"✅ 已创建分支会话: {new_session.session_id}")
        return new_session
```

---

## 最后检查清单

执行前确认：
- [ ] 已读完整个任务书
- [ ] 已理解 A/B swap 对比机制
- [ ] 已理解防放水五件套验证逻辑
- [ ] 已理解收敛判定四选一
- [ ] 已准备好写 16 个单元测试

执行中遵守：
- ✅ 诚实汇报：跑了什么、没跑成什么
- ✅ 代码质量：类型注解、docstring、错误处理
- ✅ 测试隔离：用 `tmp_path`，不污染项目目录
- ❌ 不编造证据：pytest 输出必须真实
- ❌ 不违反红线

祝顺利！🚀
