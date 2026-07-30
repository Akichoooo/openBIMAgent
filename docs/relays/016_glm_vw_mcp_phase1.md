# Relay 016 · GLM 5.2 · M1 强化：VW MCP 拆分 第一阶段 - MCP 标准封装 + Python Runner

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 015 完成；测试基线 281 passed；当前 commit `f901115`
参照规范:mcp_servers/blender_mcp/（成熟的 MCP 标准实现）

---

## 0. 你的运行环境

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `f901115`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/` / `uv run python -m compileall -q src`
- 当前测试基线：281 passed + 1 skipped
- **参照项目**：`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMForge`（原单体，包含 VW 模块）

## 红线（违反即任务失败）

1. **禁止改动** `config/`、`domain_packs/`、`docs/`（`relay_workspace/` 内你的报告除外）、`.env`
2. **可以改动** `mcp_servers/vectorworks_mcp/`（新建目录）
3. 禁止新增第三方依赖（除非 pyproject.toml 已有）；禁止删改任何现有测试（可新增测试）
4. commit 前必须三条验收命令全绿
5. commit 只提交 `mcp_servers/` 和 `tests/` 相关改动
6. 任何 401/额度异常：**立即停止，保留日志，报告部分结果**
7. 全程诚实汇报：跑了什么、什么没跑成，不许编造证据（主会话会逐项自验）

---

## 任务概述

M1 里程碑的 vectorworks-mcp 拆分第一阶段（对应 ARCHITECTURE.md §5、COMPONENTS.md §5）：

**核心目标**：
1. 从 openBIMForge 单体中提取 VW 模块，创建独立的 MCP server
2. 严格参照 blender-mcp 的成熟规范（FastMCP + 文件 IPC）
3. 实现 Python Runner（VW 宿主侧轮询器）
4. 实现版本探测与兼容性声明（避免"模型不清楚 VW 版本工具出 bug"）

**为什么要参照 blender-mcp**：
- blender-mcp 是 openBIMAgent 已经 fork 并改造的成熟项目
- 包含完整的 MCP 标准封装（FastMCP stdio + socket 通信）
- 已有健康检查、超时重试、错误处理等生产级特性
- 已验证的两段式架构（MCP server + 宿主 addon）

**当前状态**（M0 已有）：
- ✅ `mcp_servers/vectorworks_mcp/README.md` 存在（规格文档）
- ✅ `src/openbimagent/mcp_clients/vectorworks.py` 存在（占位客户端）
- ✅ openBIMForge 中有完整的 VW 实现可提取
- ⚠️ **缺失**：MCP server 实现、Python runner、文件 IPC

**你的任务**：
1. 创建 vectorworks-mcp 目录结构（参照 blender-mcp）
2. 实现 MCP server（FastMCP + 文件 IPC）
3. 实现 VW Python runner（宿主侧轮询器）
4. 实现版本探测与兼容性声明
5. 编写完整的单元测试

---

## 任务 A：创建目录结构（参照 blender-mcp）

### A1：创建 vectorworks-mcp 目录结构

参照 `mcp_servers/blender_mcp/` 的结构，创建：

```
mcp_servers/vectorworks_mcp/
├── FORK_NOTES.md           # 改造说明（从 openBIMForge 提取的说明）
├── runner.py               # VW 宿主侧 Python runner（等价于 blender addon.py）
├── server/                 # MCP stdio server
│   ├── __init__.py
│   ├── server.py          # FastMCP server 主逻辑
│   └── telemetry.py       # telemetry stub（参照 blender-mcp，硬关闭）
├── tests/                  # 单元测试
│   ├── test_file_ipc.py
│   ├── test_runner.py
│   └── test_server.py
└── vendor/                 # 原始 vs_interface.py 基线（存档）
    ├── vs_interface.py    # 从 openBIMForge 复制
    └── UPSTREAM.txt       # 记录提取来源
```

### A2：编写 FORK_NOTES.md

参照 `mcp_servers/blender_mcp/FORK_NOTES.md` 的格式，记录：

1. **提取来源**：openBIMForge 项目路径、提取时间、commit hash
2. **架构说明**：MCP server + runner 两段式、文件 IPC 协议
3. **改造项**：
   - (a) telemetry 硬关闭
   - (b) 文件 IPC 替代 socket（VW 不支持常驻 socket server）
   - (c) 版本探测与兼容性声明
   - (d) jobs/ + results/ 轮询机制
4. **状态清单**：
   - [x] 目录结构创建
   - [x] MCP server 基础实现
   - [ ] vs_index.json 生成（Relay 017）
   - [ ] 工具集预设（Relay 017）

---

## 任务 B：实现 MCP server（参照 blender-mcp）

### B1：实现 server/server.py（FastMCP + 文件 IPC）

参照 `mcp_servers/blender_mcp/server/server.py`，但使用**文件 IPC** 替代 socket：

**关键修改点**：

1. **不使用 socket**：
```python
# blender-mcp 用 socket:
self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
self.sock.connect((self.host, self.port))

# vectorworks-mcp 用文件 IPC:
self.jobs_dir = Path("jobs")
self.results_dir = Path("results")
self.jobs_dir.mkdir(parents=True, exist_ok=True)
self.results_dir.mkdir(parents=True, exist_ok=True)
```

2. **文件 IPC 协议**（jobs/ + results/）：
```python
def _send_command(self, command: str, params: dict) -> dict:
    """通过文件 IPC 发送命令。
    
    协议:
    1. 生成唯一 job_id（uuid4）
    2. 写入 jobs/<job_id>.json: {"command": "...", "params": {...}}
    3. 轮询 results/<job_id>.json 或 results/<job_id>.failed
    4. 超时或成功后返回结果
    """
    job_id = str(uuid.uuid4())
    job_path = self.jobs_dir / f"{job_id}.json"
    result_path = self.results_dir / f"{job_id}.json"
    failed_path = self.results_dir / f"{job_id}.failed"
    running_path = self.results_dir / f"{job_id}.running"
    
    # 写入 job
    job_path.write_text(json.dumps({
        "command": command,
        "params": params,
        "timestamp": datetime.now().isoformat()
    }), encoding="utf-8")
    
    # 轮询结果（最长 COMMAND_TIMEOUT 秒）
    start_time = time.time()
    while time.time() - start_time < COMMAND_TIMEOUT:
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result_path.unlink()  # 清理
            job_path.unlink(missing_ok=True)
            return result
        
        if failed_path.exists():
            error = failed_path.read_text(encoding="utf-8")
            failed_path.unlink()
            job_path.unlink(missing_ok=True)
            raise RuntimeError(f"Command failed: {error}")
        
        time.sleep(0.1)  # 100ms 轮询间隔
    
    # 超时
    job_path.unlink(missing_ok=True)
    raise TimeoutError(f"Command timed out after {COMMAND_TIMEOUT}s")
```

3. **基础工具实现**（参照 blender-mcp）：

```python
mcp = FastMCP("vectorworks-mcp")

@mcp.tool()
def ping() -> str:
    """健康检查：验证 MCP server 与 VW runner 连通性。"""
    try:
        result = client._send_command("ping", {})
        return result.get("message", "pong")
    except Exception as e:
        return f"ping failed: {e}"

@mcp.tool()
def describe_capabilities() -> dict:
    """描述 VW MCP 能力：版本、工具集、限制、已知坑。
    
    关键：返回 VW 宿主版本，避免"模型不清楚 VW 版本工具出 bug"。
    """
    try:
        result = client._send_command("describe_capabilities", {})
        return {
            "server_version": "1.0.0-m1",
            "vectorworks_version": result.get("vw_version", "unknown"),
            "python_version": result.get("python_version", "unknown"),
            "toolset": "minimal",  # full/modeling/minimal
            "file_ipc": True,
            "limitations": [
                "文件 IPC，不支持实时流式响应",
                "轮询间隔 100ms",
                "单个命令超时 60s"
            ],
            "known_issues": result.get("known_issues", [])
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def execute_vs_code(code: str) -> dict:
    """执行 VectorScript 代码（vs.* API 调用）。
    
    Args:
        code: VectorScript 代码字符串
    
    Returns:
        执行结果（含 stdout/stderr/return_value）
    """
    try:
        result = client._send_command("execute_code", {"code": code})
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

### B2：实现 telemetry.py stub

直接复制 `mcp_servers/blender_mcp/server/telemetry.py`（telemetry 硬关闭）：

```python
"""Telemetry stub (硬关闭,参照 blender-mcp)。"""

class Telemetry:
    def __init__(self, *args, **kwargs):
        pass
    
    def track_event(self, *args, **kwargs):
        pass
```

---

## 任务 C：实现 VW Python runner（宿主侧）

### C1：实现 runner.py（VW 宿主侧轮询器）

参照 `mcp_servers/blender_mcp/addon.py` 的架构，但简化为**纯 Python 脚本**（不是 Blender addon）：

**关键设计**：

1. **轮询 jobs/ 目录**：
```python
def poll_jobs():
    """轮询 jobs/ 目录，处理新 job。"""
    jobs_dir = Path("jobs")
    results_dir = Path("results")
    
    while True:
        # 扫描 jobs/*.json
        for job_path in jobs_dir.glob("*.json"):
            job_id = job_path.stem
            running_path = results_dir / f"{job_id}.running"
            result_path = results_dir / f"{job_id}.json"
            failed_path = results_dir / f"{job_id}.failed"
            
            # 标记为 running
            running_path.write_text(datetime.now().isoformat(), encoding="utf-8")
            
            try:
                # 读取 job
                job = json.loads(job_path.read_text(encoding="utf-8"))
                command = job["command"]
                params = job["params"]
                
                # 执行命令
                result = execute_command(command, params)
                
                # 写入结果
                result_path.write_text(json.dumps(result), encoding="utf-8")
                
            except Exception as e:
                # 写入失败标记
                failed_path.write_text(str(e), encoding="utf-8")
            
            finally:
                # 清理
                job_path.unlink(missing_ok=True)
                running_path.unlink(missing_ok=True)
        
        time.sleep(0.1)  # 100ms 轮询间隔
```

2. **命令分发**：
```python
def execute_command(command: str, params: dict) -> dict:
    """执行命令并返回结果。"""
    if command == "ping":
        return {"message": "pong"}
    
    elif command == "describe_capabilities":
        return {
            "vw_version": get_vw_version(),
            "python_version": sys.version,
            "known_issues": [
                "ArcByCenter 已损坏，用 Oval 替代",
                "Arc 第六参数为 Sweep 角度"
            ]
        }
    
    elif command == "execute_code":
        code = params["code"]
        return execute_vs_code(code)
    
    else:
        raise ValueError(f"Unknown command: {command}")
```

3. **从 openBIMForge 提取 vs.* 接口**：

从 `D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/vectorworks_plugin/openBIMForge2024/tool_agent/vs_interface.py` 提取核心函数：

```python
def execute_vs_code(code: str) -> dict:
    """执行 VectorScript 代码。
    
    从 openBIMForge vs_interface.py 提取。
    """
    try:
        import vs  # VectorWorks Python API
        
        # 执行代码
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec_globals = {"vs": vs, "math": math}
            exec(code, exec_globals)
        
        return {
            "ok": True,
            "stdout": stdout.getvalue(),
            "stderr": ""
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

def get_vw_version() -> str:
    """获取 VectorWorks 版本。"""
    try:
        import vs
        # 从 vs.GetVersion() 或类似 API 获取版本
        return "2024"  # TODO: 实际实现
    except Exception:
        return "unknown"
```

### C2：vendor/ 基线存档

将 openBIMForge 的 vs_interface.py 复制到 `vendor/` 作为基线：

```bash
cp D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/vectorworks_plugin/openBIMForge2024/tool_agent/vs_interface.py mcp_servers/vectorworks_mcp/vendor/
```

创建 `vendor/UPSTREAM.txt`：
```
UPSTREAM: openBIMForge vectorworks_plugin
PATH: D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/vectorworks_plugin/openBIMForge2024/tool_agent/vs_interface.py
EXTRACTED: 2026-07-29
COMMIT: (记录 openBIMForge 的 git commit hash)
```

---

## 任务 D：单元测试

### D1：文件 IPC 协议测试（tests/test_file_ipc.py，新建）

**8 个测试**：

1. `test_send_command_success`
   - 写入 job，模拟 runner 写入 result
   - 断言：返回正确结果

2. `test_send_command_timeout`
   - 写入 job，不写入 result
   - 断言：抛出 TimeoutError

3. `test_send_command_failed`
   - 写入 job，runner 写入 .failed
   - 断言：抛出 RuntimeError

4. `test_job_file_cleanup`
   - 发送命令后，验证 job 文件被清理
   - 断言：job_path 不存在

5. `test_concurrent_jobs`
   - 并发发送 3 个 job
   - 断言：所有 job 都得到正确结果

6. `test_poll_jobs_processes_all`
   - 写入 3 个 job 文件
   - 调用 poll_jobs 一次
   - 断言：3 个 result 文件都生成

7. `test_poll_jobs_handles_exception`
   - 写入 1 个会抛异常的 job
   - 调用 poll_jobs
   - 断言：生成 .failed 文件

8. `test_running_marker_cleanup`
   - 发送 job，验证 .running 标记生成和清理
   - 断言：执行后 .running 不存在

### D2：runner 单元测试（tests/test_runner.py，新建）

**4 个测试**：

1. `test_execute_command_ping`
   - 调用 execute_command("ping", {})
   - 断言：返回 {"message": "pong"}

2. `test_execute_command_describe_capabilities`
   - 调用 execute_command("describe_capabilities", {})
   - 断言：返回包含 vw_version/python_version

3. `test_execute_command_unknown`
   - 调用 execute_command("unknown", {})
   - 断言：抛出 ValueError

4. `test_execute_vs_code_success`
   - mock vs 模块，执行简单代码
   - 断言：返回 {"ok": True, "stdout": "..."}

### D3：server 单元测试（tests/test_server.py，新建）

**3 个测试**：

1. `test_ping_tool`
   - 调用 ping() 工具
   - mock _send_command 返回 {"message": "pong"}
   - 断言：返回 "pong"

2. `test_describe_capabilities_tool`
   - 调用 describe_capabilities() 工具
   - 断言：返回包含 server_version/vectorworks_version

3. `test_execute_vs_code_tool`
   - 调用 execute_vs_code("vs.Message('test')")
   - mock _send_command
   - 断言：返回 {"ok": True}

---

## 任务 E：验收与提交

### E1：三条验收命令

```bash
# 1. 全量测试（应 ≥296 passed，新增 15 个测试）
uv run pytest -q

# 2. 代码检查
uv run ruff check src/ tests/ mcp_servers/

# 3. 编译检查
uv run python -m compileall -q src mcp_servers/
```

### E2：提交策略（分两个 commit）

**Commit 1**（MCP server 实现）：
```bash
git add mcp_servers/vectorworks_mcp/
git commit -m "M1: VW MCP 拆分第一阶段 - MCP 标准封装 + Python Runner

- server/server.py: FastMCP + 文件 IPC 协议（jobs/ + results/ 轮询）
- runner.py: VW 宿主侧 Python runner（从 openBIMForge 提取）
- 版本探测: describe_capabilities 返回 VW 版本，避免版本工具 bug
- 参照规范: blender-mcp 成熟 MCP 实现"
```

**Commit 2**（测试覆盖）：
```bash
git add tests/mcp_servers/
git commit -m "M1 测试: VW MCP 文件 IPC 与 runner 单元测试全覆盖

- tests/test_file_ipc.py: jobs/results 协议、超时、并发 8个测试
- tests/test_runner.py: 命令分发、vs 代码执行 4个测试
- tests/test_server.py: MCP 工具调用 3个测试"
```

---

## 任务 F：报告

写到 `relay_workspace/m1_vw_mcp_phase1/report.md`，包含：

### F1：实现总结
1. 每个子任务的实现要点（A1-A2、B1-B2、C1-C2、D1-D3）
2. 从 openBIMForge 提取的核心代码清单
3. 与 blender-mcp 的对比（socket vs 文件 IPC）

### F2：测试证据
1. 三条验收命令的**原始输出**（完整 pytest 输出）
2. 新增测试的详细列表（测试名 + 覆盖的场景）

### F3：Commit 证据
1. 两个 commit 的 hash（`git log --oneline -2`）
2. 每个 commit 的文件变更统计（`git show --stat <hash>`）

### F4：版本兼容性说明
1. describe_capabilities 返回的 VW 版本格式
2. 已知坑清单（从 openBIMForge AGENTS.md 提取）
3. 如何避免"模型不清楚 VW 版本工具出 bug"

### F5：入库检查单
1. **改动文件清单**（只列 mcp_servers/ 和 tests/）
2. **遗留问题**（如果有未完成或降级的实现）
3. **给主会话的建议**（Relay 017 的工作范围）

---

## 回执格式

完成后只回：**「016 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 关键技术要点（避免踩坑）

### 1. 文件 IPC 协议示例（完整实现）

```python
import json
import time
import uuid
from pathlib import Path
from datetime import datetime

class FileIPCClient:
    def __init__(self, jobs_dir="jobs", results_dir="results", timeout=60):
        self.jobs_dir = Path(jobs_dir)
        self.results_dir = Path(results_dir)
        self.timeout = timeout
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
    
    def send_command(self, command: str, params: dict) -> dict:
        job_id = str(uuid.uuid4())
        job_path = self.jobs_dir / f"{job_id}.json"
        result_path = self.results_dir / f"{job_id}.json"
        failed_path = self.results_dir / f"{job_id}.failed"
        
        # 写入 job
        job_data = {
            "command": command,
            "params": params,
            "timestamp": datetime.now().isoformat()
        }
        job_path.write_text(json.dumps(job_data, ensure_ascii=False), encoding="utf-8")
        
        # 轮询结果
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink()
                job_path.unlink(missing_ok=True)
                return result
            
            if failed_path.exists():
                error = failed_path.read_text(encoding="utf-8")
                failed_path.unlink()
                job_path.unlink(missing_ok=True)
                raise RuntimeError(f"Command failed: {error}")
            
            time.sleep(0.1)
        
        job_path.unlink(missing_ok=True)
        raise TimeoutError(f"Command timed out after {self.timeout}s")
```

### 2. runner.py 轮询循环示例

```python
def poll_jobs_once(jobs_dir: Path, results_dir: Path):
    """处理一轮 job（测试友好：不死循环）。"""
    for job_path in sorted(jobs_dir.glob("*.json")):
        job_id = job_path.stem
        running_path = results_dir / f"{job_id}.running"
        result_path = results_dir / f"{job_id}.json"
        failed_path = results_dir / f"{job_id}.failed"
        
        running_path.write_text(datetime.now().isoformat(), encoding="utf-8")
        
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            result = execute_command(job["command"], job["params"])
            result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            failed_path.write_text(str(e), encoding="utf-8")
        finally:
            job_path.unlink(missing_ok=True)
            running_path.unlink(missing_ok=True)

def main():
    """runner 主循环。"""
    jobs_dir = Path("jobs")
    results_dir = Path("results")
    
    print("VW MCP runner started")
    while True:
        try:
            poll_jobs_once(jobs_dir, results_dir)
            time.sleep(0.1)
        except KeyboardInterrupt:
            print("Runner stopped")
            break
```

### 3. describe_capabilities 实现示例

```python
def get_vw_version() -> str:
    """获取 VectorWorks 版本。"""
    try:
        import vs
        # VW 2024 示例
        version = vs.GetVersion()
        return f"VectorWorks {version}"
    except Exception:
        return "unknown"

def describe_capabilities() -> dict:
    """返回 VW 能力描述（关键：包含版本）。"""
    return {
        "server_version": "1.0.0-m1",
        "vectorworks_version": get_vw_version(),
        "python_version": sys.version,
        "architecture": "file_ipc",
        "poll_interval_ms": 100,
        "command_timeout_s": 60,
        "toolset": "minimal",
        "known_issues": [
            "ArcByCenter 在 VW2024 中已损坏，用 Oval 替代",
            "Arc 第六参数为 Sweep 角度，非终点角度"
        ],
        "limitations": [
            "文件 IPC，不支持实时流式响应",
            "单个命令超时 60s",
            "不支持并发命令（串行处理）"
        ]
    }
```

---

## 最后检查清单

执行前确认：
- [ ] 已读完整个任务书
- [ ] 已理解文件 IPC 协议（jobs/ + results/）
- [ ] 已理解 runner 轮询机制
- [ ] 已理解版本探测的重要性
- [ ] 已准备好从 openBIMForge 提取代码
- [ ] 已准备好写 15 个单元测试

执行中遵守：
- ✅ 诚实汇报：跑了什么、没跑成什么
- ✅ 代码质量：类型注解、docstring、错误处理
- ✅ 测试隔离：用 `tmp_path`，不污染项目目录
- ❌ 不编造证据：pytest 输出必须真实
- ❌ 不违反红线

祝顺利！🚀
