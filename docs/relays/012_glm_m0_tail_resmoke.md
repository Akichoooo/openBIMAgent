# Relay 012 · GLM 5.2 · M0 收尾验证 + 补跑冒烟(中断实演 + 六维收敛)

版本:v1 · 2026-07-27 · 发出:主会话(Kimi)· 执行:GLM 5.2
前置:008-011 已完成;M0 冒烟报告 `relay_workspace/m0_smoke/report.md`;首个 git 存档点 `53154b4`

---

## 0. 你的运行环境(已实测,直接用)

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`,git 分支 `main`,已有一个 commit(`53154b4`)
- Python 走 uv:所有命令前缀 `uv run`(或直接调 `.venv/Scripts/python.exe`,见任务 D)
- 三条验收命令:`uv run pytest -q` / `uv run ruff check src/ tests/` / `uv run python -m compileall -q src`
- 当前测试基线:229 passed + 1 skipped
- Blender:`D:\devloop\blender\blender.exe`(5.2.0,headless 可行)
- `.env` 已有**新** AGENTROUTER_API_KEY(旧 key 额度耗尽已更换,不要再报旧 key 的 401)
- profile=test 角色绑定:modeler=claude-opus-4-8、critic=gpt-5.5、其余=glm-5.2-ar

## 红线(违反即任务失败)

1. **禁止改动** `mcp_servers/`(fork 已冻结)、`config/`、`domain_packs/`、`docs/`(`relay_workspace/` 内你的报告除外)、`.env`
2. 禁止新增第三方依赖;禁止删改任何现有测试
3. commit 前必须三条验收命令全绿;commit 只提交 `src/`、`tests/` 相关改动
4. 任何 401/额度异常:**立即停止该 run,保留日志,报告部分结果**,不要反复重试烧额度
5. 全程诚实汇报:跑了什么、什么没跑成,不许编造证据(主会话会逐项自验)

---

## 任务 A:阅读主会话的 4 项未提交修复

主会话刚完成 4 项 src/ 修复(未测试、未提交)。先用 `git diff` 通读全部改动,理解后再动手:

| # | 修复 | 位置 | 意图 |
|---|---|---|---|
| 1 | clarify 问答落 message 事件 | `src/openbimagent/assembly/pipeline.py`(clarify 段 + `_record_clarify_messages`) | 问答对按 assistant 问/user 答成对写 session 事件树,补验收 e 缺的 message 类 |
| 2 | usage 异常退出也落盘 | `src/openbimagent/cli.py`(`atexit` + `_dump_usage_on_exit` + `_dump_usage` 一次性封装) | M0 冒烟教训:崩溃时 usage_summary.json 丢失;显式落盘置 done,atexit 只兜底 |
| 3 | modeler prompt 加风格锚点 | `src/openbimagent/assembly/builder.py`(`_build_modeler_messages` 的 blocks) | 冒烟 finding #4:modeler 退化灰盒;加结构拆分/PBR/磨损/霓虹/三点布光五条硬要求 |
| 4 | critic fallback 同族降级留痕 | `config/models.toml` `[fallbacks]` 注释(只读,别改) | 复核结论:gpt-5.5→opus-4-6 是按设计触发,critic_model 字段留痕可识别 |

如发现修复本身有 bug:允许最小修补 `src/`,但必须在报告里单列「主会话修复的补丁」一节说明。

## 任务 B:补 3 个回归测试(规格已钉死,照写)

### B1 `tests/test_assembly.py::test_pipeline_clarify_qa_recorded_as_message_events`

- 用 `SINGLE` playbook 跑 `run_pipeline`(注入式:`blender_client=None`、`yes=True`、`sessions_dir=tmp_path/"sessions"`)
- `input_func` 用闭包依次返回 3 个明确答案(如 `"复古售货机"` / `"江户x赛博"` / `"7"`)
- 断言:`result.session.load()` 里 `EventType.MESSAGE` 事件 **≥6 条**;按顺序成对出现——奇数位 role=assistant 且 content 含槽位问题文本,偶数位 role=user 且 content 等于注入答案;**user 事件 content 等于注入答案原文**(不是默认值)
- 参照文件末尾 `test_pipeline_session_recorded_with_playbook` 的写法

### B2 `tests/test_assembly.py::test_modeler_messages_include_style_anchors`

- 从 `openbimagent.assembly.builder` 导入 `_build_modeler_messages`(白盒,允许)
- 构造最小 batch_ctx:`{"batch": [" vending "], "ir": {"assets": [{"id": "vending", "category": "prop"}]}}`,brief 任意字符串,prev_critique=None
- 断言返回的 messages 里 user 内容含:`"风格锚点"`、`"Emission"`、`"三点"`、`"metallic"`(四条全中)

### B3 `tests/test_cli.py::test_dump_usage_on_exit_writes_when_not_done`

- 从 `openbimagent.cli` 导入 `_UsageTrackingRegistry` 和 `_dump_usage_on_exit`
- fake inner registry:`.chat(role, messages, **kw)` 返回 `{"model_resolved": "fake-1", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}`,其余属性随意
- 包一层 `_UsageTrackingRegistry`,调 1 次 chat;`_dump_usage_on_exit(reg, tmp_path, {"done": False})` → 断言 `tmp_path/usage_summary.json` 存在且 `total.total_tokens == 15`
- 再追加一次 chat 后以 `{"done": True}` 调 `_dump_usage_on_exit` → 断言文件内容未变(读文本对比)

## 任务 C:验收 + commit

1. 三条验收命令全绿(pytest 应 ≥ 232 passed)
2. commit(只含 `src/`、`tests/`):

```
M0 收尾: clarify message 事件 / usage atexit 落盘 / modeler 风格锚点 + 回归测试 3 个
```

3. 报告 commit hash(`git log --oneline -1`)

## 任务 D:补跑冒烟(新 key,真 Blender + 真 LLM,两个 run)

> 额度纪律:全程最多 3 个 pipeline run;`--image-size 512`;任一 run 报 401 立即停,保留日志报部分结果。

### D1 Run A:中断-续跑实演(验收 c)

写驱动脚本 `relay_workspace/m0_resmoke/interrupt_driver.py`:

```python
import os, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(r"D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent")
OUT = ROOT / "relay_workspace" / "m0_resmoke" / "run_interrupt"
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "run.log", "w", encoding="utf-8")
# 直接调 venv python(绕开 uv 包装进程,确保 CTRL_C_EVENT 打到 python 本体)
cmd = [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "openbimagent", "run",
       "--playbook", "domain_packs/single_asset_hero/playbook.md",
       "--out", str(OUT), "--yes", "--no-hitl", "--profile", "test", "--image-size", "512"]
proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, text=True)
proc.stdin.write("一台日式自动售货机\n江户x赛博x拟洋风\n6\n")
proc.stdin.close()
# 等建模开始的标志:batches/*/iter1_viewport.png 出现(轮询,超时 300s 则到点直接断)
marker = None
deadline = time.time() + 300
while time.time() < deadline and proc.poll() is None:
    hits = list(OUT.glob("batches/*/iter1_viewport.png"))
    if hits:
        marker = hits[0]
        break
    time.sleep(3)
time.sleep(5)  # 让建模再跑一会,确保断在建模中段
if proc.poll() is None:
    os.kill(proc.pid, signal.CTRL_C_EVENT)  # Windows:python 收到后抛 KeyboardInterrupt
rc = proc.wait(timeout=120)
print(f"returncode={rc} marker={marker}")
```

- 期望:`returncode=130`;`OUT/sessions/*.jsonl` 含 `[checkpoint]` message 事件;`OUT/usage_summary.json` 存在且 `total.calls ≥ 1`(**这就是修复 2 的活体验证**)
- 续跑演示:`python -m openbimagent sessions --sessions-dir <OUT>/sessions` 列出会话;取 checkpoint 事件 id,`python -m openbimagent tree <session_id> <event_id> --sessions-dir <OUT>/sessions` 建分支,报告分支会话 id
- 若 CTRL_C_EVENT 在 Windows 不生效(proc 无反应/直接被杀):换 `signal.SIGINT` 再试一次;仍不行就报告「信号投递失败」+ 日志,不要硬耗

### D2 Run B:六维收敛(验收 b,带修复 1/3 的活体验证)

```
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md \
  --out relay_workspace/m0_resmoke/run_converge --yes --no-hitl --profile test --image-size 512
```

- stdin 喂:`一台日式自动售货机\n江户x赛博x拟洋风\n6\n`
- 让它按 playbook 上限自然迭代收敛,不要中途干预;预计 10-30 分钟
- 跑完核对:
  1. session JSONL 里 message 事件 ≥6(clarify 3 对,**修复 1 活体验证**)
  2. score 事件曲线:记录每轮六维 + overall;任一 iter overall ≥ 8 → 验收 b 全 PASS;未达 → 记录曲线与返工轨迹(M0_PLAN 条款,仍是 PARTIAL PASS)
  3. `scene.blend` 用 Blender headless 打开数对象:`blender.exe -b --factory-startup <blend> --python-expr "import bpy; print('OBJS:', len(bpy.data.objects))"`(期望 ≥10)
  4. HTML 验收页存在且含六维评分
  5. `usage_summary.json` 落盘,`by_model` 有 claude-opus-4-8/gpt-5.5/glm-5.2-ar 真实 token 数(**验收 f 精确计量,这次不是估算**)

## 任务 E:报告

写到 `relay_workspace/m0_resmoke/report.md`,含:

1. 任务 A 的阅读结论(4 项修复是否各如其述,有无打补丁)
2. 任务 B/C 证据:三条验收命令**原始输出**、commit hash
3. 任务 D 证据:Run A 的 returncode/checkpoint 事件/tree 分支;Run B 的六维曲线表、对象数、usage_summary.json 全文
4. 六道验收最终结论对照表(a-f,对照 `docs/architecture/M0_PLAN.md`)
5. 入库检查单回执:①报告路径与结论 ②改动文件清单 ③遇到的问题 ④未完成项与原因 ⑤token 精确消耗

## 回执格式

完成后只回:**「012 完成」+ 报告路径 + 六道验收一行结论 + commit hash**。细节都在报告里,主会话自己去验。
