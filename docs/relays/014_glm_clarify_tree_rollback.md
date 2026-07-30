# Relay 014 · GLM 5.2 · M1 强化：Clarify 追问进 Session 树与 /tree 回退

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 013 完成；测试基线 250 passed；当前 commit `07eaf76`

---

## 0. 你的运行环境（与 013 一致）

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `07eaf76`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/` / `uv run python -m compileall -q src`
- 当前测试基线：250 passed + 1 skipped
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

M1 里程碑的 Clarify 强化（对应 ARCHITECTURE.md §2、COMPONENTS.md §2.2）：

**核心目标**：让 Clarify 追问的每一对问答都作为独立的 Session 事件记录到树中，支持 `/tree` 命令回退到任意追问前，修改答案后重新执行后续流程。

**当前状态**（M0 Relay 012 已实现）：
- ✅ Clarify 问答**已经**落成 `message` 事件（`pipeline.py:119` 的 `_record_clarify_messages`）
- ✅ 成对记录：assistant 问 + user 答
- ⚠️ **缺失**：无法通过 `/tree` 回退到某个追问前修改答案

**你的任务**：
1. 为 `SessionStore` 添加 `/tree` 回退能力（分支创建 + 事件复制）
2. 为 CLI 添加 `/tree` 命令（交互式选择父事件 + 创建分支会话）
3. 实现 Clarify 的"断点续跑"（从 Session 恢复已问过的槽位状态）
4. 编写完整的单元测试和集成测试

---

## 任务 A：SessionStore 添加 /tree 分支能力

### A1：实现 `fork` 方法（session/store.py）

当前 `SessionStore` 类只有 `append` 和 `load` 方法，需要添加分支创建能力。

**新增方法签名**：
```python
def fork(self, from_event_id: str, *, title: str | None = None) -> SessionStore:
    """创建分支会话：复制当前会话从根到 from_event_id 的所有事件到新 session 文件。
    
    Args:
        from_event_id: 分支点事件 id（复制到此事件为止，含此事件）
        title: 新会话标题（默认为 "原标题 · 分支自 <event_id前8位>"）
    
    Returns:
        新的 SessionStore 实例，指向新的 session 文件
    
    Raises:
        ValueError: from_event_id 不存在于当前会话
    """
```

**实现要点**：
1. 调用 `self.load()` 加载当前会话所有事件
2. 找到 `from_event_id` 在事件列表中的位置
3. 复制从索引 0 到该位置的所有事件（含 `from_event_id`）
4. 创建新的 `SessionStore`（自动分配新 session_id）
5. 将复制的事件逐条 `append` 到新 session
6. 在 `index.json` 中标记分支关系（新增 `forked_from` 字段）

**index.json 格式扩展**：
```json
{
  "sessions": {
    "<session_id>": {
      "title": "...",
      "created_at": "...",
      "last_active": "...",
      "playbook": "...",
      "forked_from": {
        "parent_session_id": "...",
        "parent_event_id": "..."
      }
    }
  }
}
```

### A2：实现 `find_event` 方法（session/store.py）

为了支持回退，需要能快速查找事件：

```python
def find_event(self, event_id: str) -> SessionEvent | None:
    """根据 event_id 查找事件。
    
    Args:
        event_id: 事件 id
    
    Returns:
        找到的事件，如果不存在返回 None
    """
```

**实现要点**：
1. 调用 `self.load()` 加载所有事件
2. 线性查找匹配 `event.id == event_id` 的事件
3. 找到返回事件对象，否则返回 `None`

### A3：实现 `get_event_chain` 方法（session/store.py）

为了 CLI 展示回退选项，需要获取事件链：

```python
def get_event_chain(self, until_event_id: str | None = None) -> list[SessionEvent]:
    """获取从根到指定事件的事件链（按时间顺序）。
    
    Args:
        until_event_id: 终止事件 id（默认为当前 head）
    
    Returns:
        事件链列表，如果 until_event_id 不存在则抛出 ValueError
    """
```

**实现要点**：
1. 加载所有事件并建立 `id → event` 映射
2. 从 `until_event_id`（或 `self.head`）开始，通过 `parentId` 反向遍历
3. 收集事件列表并反转（变为从根到目标的正向顺序）
4. 返回事件链

---

## 任务 B：CLI 添加 /tree 命令

### B1：实现 `/tree` 命令处理器（cli.py）

在 `cli.py` 中添加新的子命令 `tree`（与 `run`、`sessions` 并列）。

**命令签名**：
```bash
python -m openbimagent tree <session_id> [event_id] --sessions-dir <dir>
```

**参数说明**：
- `<session_id>`：要回退的会话 id
- `[event_id]`：可选，回退到的目标事件 id（默认交互式选择）
- `--sessions-dir`：会话目录（默认 `./sessions`）

**交互流程**：
1. 加载指定 session
2. 如果未指定 `event_id`，展示事件链供用户选择：
   ```
   可回退的事件:
   1. [message] assistant: 您想做什么资产？（event_id: abc12345）
   2. [message] user: 一台日式自动售货机（event_id: def67890）
   3. [message] assistant: 您想要什么风格？（event_id: ghi11223）
   ...
   
   请选择回退点（输入序号或事件 id 前 8 位）：
   ```
3. 用户选择后，调用 `store.fork(event_id)` 创建分支
4. 打印新会话信息：
   ```
   ✅ 已创建分支会话:
   Session ID: <new_session_id>
   Title: <title>
   已复制 5 个事件
   
   继续执行:
   python -m openbimagent run --playbook <playbook> --session <new_session_id> --sessions-dir <dir>
   ```

### B2：实现事件友好展示（cli.py 辅助函数）

```python
def _format_event_for_tree(event: SessionEvent, index: int) -> str:
    """将事件格式化为 /tree 命令的友好展示。
    
    Args:
        event: 事件对象
        index: 事件在链中的序号（从 1 开始）
    
    Returns:
        格式化字符串，如 "1. [message] assistant: 您想做什么资产？（abc12345）"
    """
```

**格式规则**：
- `message` 事件：显示 `role` + `content` 前 50 字符
- `tool_call` 事件：显示 `toolName` + `args_summary` 前 30 字符
- `custom` 事件：显示 `customType`
- 事件 id 显示前 8 位（便于用户输入）

---

## 任务 C：Clarify 断点续跑支持

### C1：实现 `resume_from_session`（clarify/slots.py）

```python
def resume_from_session(
    slots: list[Slot],
    session: SessionStore,
    from_event_id: str | None = None
) -> SlotState:
    """从 session 恢复 Clarify 状态（已问过的槽位 + 已填的答案）。
    
    Args:
        slots: 槽位定义列表
        session: 会话存储
        from_event_id: 恢复到的事件 id（默认为当前 head）
    
    Returns:
        恢复的 SlotState，包含已填的答案和 asked 集合
    """
```

**实现要点**：
1. 调用 `session.get_event_chain(from_event_id)` 获取事件链
2. 遍历事件链，提取 `type=message` 的问答对：
   - `role=assistant` 的 `content` 与槽位的 `question` 匹配
   - 紧接着的 `role=user` 的 `content` 作为答案
3. 回填匹配的槽位 `value`，并标记 `asked.add(slot.id)`
4. 返回 `SlotState(slots=slots, asked=asked)`

**匹配规则**：
- 完全匹配：`event.payload.content == slot.question`
- 或模糊匹配：`slot.question in event.payload.content`（容错追问变体）

### C2：修改 `run_clarify` 支持续跑（clarify/slots.py）

```python
def run_clarify(
    state: SlotState,
    input_func: Callable[[str], str] = input,
    *,
    resume: bool = False  # 新增参数
) -> None:
    """运行 Clarify 追问流程。
    
    Args:
        state: 槽位状态（如果 resume=True，应从 session 恢复）
        input_func: 输入函数（测试可注入）
        resume: 是否为续跑模式（跳过 state.asked 中已问过的槽位）
    """
```

**修改要点**：
1. 遍历 `state.slots` 时，如果 `resume=True` 且 `slot.id in state.asked`，跳过该槽位
2. 其余逻辑保持不变

### C3：修改 `pipeline.py` 支持 Clarify 续跑

在 `run_pipeline` 函数中，当检测到会话是分支会话时（`store` 的 index entry 含 `forked_from`），自动启用 Clarify 续跑：

```python
# pipeline.py:104 附近修改
entry = store._index_entry()  # 访问 index entry
is_resume = entry is not None and "forked_from" in entry

if is_resume:
    # 从 session 恢复 Clarify 状态
    state = clarify.resume_from_session(
        clarify.load_playbook_slots(Path(playbook_path)),
        store,
        from_event_id=entry["forked_from"]["parent_event_id"]
    )
    clarify.run_clarify(state, input_func=_clarify_input, resume=True)
else:
    # 正常流程
    state = clarify.SlotState(slots=clarify.load_playbook_slots(Path(playbook_path)))
    clarify.run_clarify(state, input_func=_clarify_input)
```

---

## 任务 D：单元测试

### D1：SessionStore 单元测试（tests/test_session.py，新建）

**7 个测试**：

1. `test_store_fork_creates_new_session`
   - 创建 session，追加 5 个事件
   - 调用 `fork(events[2].id)`
   - 断言：新 session 包含前 3 个事件（0, 1, 2）

2. `test_store_fork_updates_index_with_forked_from`
   - fork 后，检查 `index.json`
   - 断言：新 session entry 包含 `forked_from.parent_session_id` 和 `parent_event_id`

3. `test_store_fork_invalid_event_id_raises`
   - 调用 `fork("nonexistent")`
   - 断言：抛出 `ValueError`

4. `test_store_find_event_returns_event`
   - 追加事件，调用 `find_event(event.id)`
   - 断言：返回正确事件

5. `test_store_find_event_not_found_returns_none`
   - 调用 `find_event("nonexistent")`
   - 断言：返回 `None`

6. `test_store_get_event_chain`
   - 追加 5 个事件（每个 parentId 指向前一个）
   - 调用 `get_event_chain(events[3].id)`
   - 断言：返回事件链长度为 4（events[0..3]），顺序正确

7. `test_store_get_event_chain_defaults_to_head`
   - 调用 `get_event_chain()` 不传参数
   - 断言：返回完整事件链（到当前 head）

### D2：Clarify 续跑单元测试（tests/test_clarify.py，新增）

**5 个测试**：

1. `test_resume_from_session_restores_answered_slots`
   - 构造 session 包含 2 对 message 问答
   - 调用 `resume_from_session(slots, session)`
   - 断言：前 2 个槽位的 `value` 已填充，`asked` 包含它们的 id

2. `test_resume_from_session_partial_qa`
   - 构造 session 只包含 1 个 assistant 问（无 user 答）
   - 调用 `resume_from_session`
   - 断言：该槽位被标记为 `asked`，但 `value` 为 `None`

3. `test_resume_from_session_no_match_empty_state`
   - 构造 session 包含无关 message
   - 调用 `resume_from_session`
   - 断言：所有槽位 `value=None`，`asked` 为空

4. `test_run_clarify_resume_skips_asked_slots`
   - 构造 `SlotState` 其中 `asked={slot1.id, slot2.id}`
   - mock `input_func`（应只被调用 1 次，因为前 2 个跳过）
   - 调用 `run_clarify(state, input_func, resume=True)`
   - 断言：`input_func` 调用次数为 1

5. `test_run_clarify_resume_false_asks_all_slots`
   - 构造 `SlotState` 其中 `asked={slot1.id}`
   - mock `input_func`（应被调用 3 次，因为 resume=False）
   - 调用 `run_clarify(state, input_func, resume=False)`
   - 断言：`input_func` 调用次数为 3

### D3：集成测试（tests/test_assembly.py，新增）

**2 个测试**：

1. `test_pipeline_fork_and_resume_clarify`
   - 第一次 run：回答前 2 个槽位，手动中止（抛出异常模拟）
   - 调用 `store.fork(last_message_event.id)`
   - 第二次 run：使用分支 session，应只问剩余槽位
   - 断言：第二次 run 的 `input_func` 调用次数 = 总槽位数 - 2

2. `test_tree_command_integration`
   - 创建 session 包含 5 个 message 事件
   - 模拟 CLI `/tree` 命令（调用 `store.fork(events[2].id)`）
   - 断言：新 session 包含前 3 个事件
   - 断言：`index.json` 正确记录分支关系

---

## 任务 E：验收与提交

### E1：三条验收命令

```bash
# 1. 全量测试（应 ≥264 passed，新增 14 个测试）
uv run pytest -q

# 2. 代码检查
uv run ruff check src/ tests/

# 3. 编译检查
uv run python -m compileall -q src
```

### E2：提交策略（分两个 commit）

**Commit 1**（核心逻辑）：
```bash
git add src/openbimagent/session/store.py src/openbimagent/clarify/slots.py src/openbimagent/assembly/pipeline.py src/openbimagent/cli.py
git commit -m "M1 强化: Clarify 追问进 Session 树与 /tree 回退

- session/store: fork/find_event/get_event_chain 方法，支持分支创建
- clarify/slots: resume_from_session + run_clarify 续跑支持
- pipeline: 检测分支会话自动启用 Clarify 续跑
- cli: /tree 命令交互式回退到任意事件"
```

**Commit 2**（测试覆盖）：
```bash
git add tests/test_session.py tests/test_clarify.py tests/test_assembly.py
git commit -m "M1 测试: Session 分支与 Clarify 续跑单元测试全覆盖

- tests/test_session.py: fork/find_event/get_event_chain 7个测试
- tests/test_clarify.py: 续跑恢复与跳过逻辑 5个测试
- tests/test_assembly.py: 分支续跑与 tree 命令集成 2个测试"
```

---

## 任务 F：报告

写到 `relay_workspace/m1_clarify_tree/report.md`，包含：

### F1：实现总结
1. 每个子任务的实现要点（A1-A3、B1-B2、C1-C3）
2. 遇到的技术难点与解决方案
3. 代码质量自查（类型注解、docstring、错误处理）

### F2：测试证据
1. 三条验收命令的**原始输出**（完整 pytest 输出）
2. 新增测试的详细列表（测试名 + 覆盖的场景）
3. 测试覆盖率统计（可选）

### F3：Commit 证据
1. 两个 commit 的 hash（`git log --oneline -2`）
2. 每个 commit 的文件变更统计（`git show --stat <hash>`）

### F4：使用示例
演示 `/tree` 命令的完整流程：
```bash
# 1. 第一次运行，回答前 2 个槽位后中止
python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md --out test_run

# 2. 查看 session
python -m openbimagent sessions --sessions-dir test_run/sessions

# 3. 回退到第 2 个槽位
python -m openbimagent tree <session_id> --sessions-dir test_run/sessions

# 4. 选择回退点，创建分支

# 5. 续跑分支会话（只问剩余槽位）
python -m openbimagent run --playbook ... --session <new_session_id> --sessions-dir test_run/sessions
```

### F5：入库检查单
1. **改动文件清单**（只列 src/ 和 tests/）
2. **遗留问题**（如果有未完成或降级的实现）
3. **给主会话的建议**（优化方向、潜在风险）

---

## 回执格式

完成后只回：**「014 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 关键技术要点（避免踩坑）

### 1. SessionStore.fork 实现示例

```python
def fork(self, from_event_id: str, *, title: str | None = None) -> SessionStore:
    events = self.load()
    
    # 找到分支点
    fork_index = None
    for i, event in enumerate(events):
        if event.id == from_event_id:
            fork_index = i
            break
    
    if fork_index is None:
        raise ValueError(f"Event {from_event_id} not found in session {self.session_id}")
    
    # 创建新 session
    new_title = title or f"{self._title} · 分支自 {from_event_id[:8]}"
    new_session = SessionStore.create(
        self.path.parent,
        title=new_title,
        playbook=self._playbook
    )
    
    # 复制事件
    for event in events[:fork_index + 1]:
        new_session.append(event)
    
    # 更新 index 记录分支关系
    new_session._update_fork_metadata(self.session_id, from_event_id)
    
    return new_session
```

### 2. resume_from_session 匹配逻辑

```python
def resume_from_session(slots, session, from_event_id=None):
    chain = session.get_event_chain(from_event_id)
    state = SlotState(slots=[Slot(**s.__dict__) for s in slots])
    
    i = 0
    while i < len(chain):
        event = chain[i]
        if event.type != EventType.MESSAGE:
            i += 1
            continue
        
        # assistant 问
        if event.payload.role == "assistant":
            # 匹配槽位
            for slot in state.slots:
                if slot.question in event.payload.content:
                    state.asked.add(slot.id)
                    # 找紧接着的 user 答
                    if i + 1 < len(chain) and chain[i+1].payload.role == "user":
                        slot.value = chain[i+1].payload.content
                        i += 1  # 跳过 user 答
                    break
        i += 1
    
    return state
```

### 3. /tree CLI 交互示例

```python
def _cmd_tree(args):
    store = SessionStore(args.sessions_dir / f"{args.session_id}.jsonl")
    
    if args.event_id:
        target_event_id = args.event_id
    else:
        # 交互式选择
        chain = store.get_event_chain()
        print("可回退的事件:")
        for i, event in enumerate(chain):
            print(f"{i+1}. {_format_event_for_tree(event, i+1)}")
        
        choice = input("\n请选择回退点（输入序号或事件 id 前 8 位）：")
        if choice.isdigit():
            target_event_id = chain[int(choice) - 1].id
        else:
            # 按前缀匹配
            target_event_id = next(e.id for e in chain if e.id.startswith(choice))
    
    new_session = store.fork(target_event_id)
    print(f"\n✅ 已创建分支会话:")
    print(f"Session ID: {new_session.session_id}")
    print(f"Title: {new_session._title}")
```

---

## 最后检查清单

执行前确认：
- [ ] 已读完整个任务书
- [ ] 已理解 SessionStore 分支机制
- [ ] 已理解 Clarify 续跑逻辑
- [ ] 已理解 /tree 命令交互流程
- [ ] 已准备好写 14 个单元测试

执行中遵守：
- ✅ 诚实汇报：跑了什么、没跑成什么
- ✅ 代码质量：类型注解、docstring、错误处理
- ✅ 测试隔离：用 `tmp_path`，不污染项目目录
- ❌ 不编造证据：pytest 输出必须真实
- ❌ 不违反红线

祝顺利！🚀
