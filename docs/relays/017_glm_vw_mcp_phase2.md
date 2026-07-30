# Relay 017 · GLM 5.2 · M1 强化：VW MCP 拆分 第二阶段 - vs_index.json + 工具集预设 + handoff 门禁

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 016 完成；测试基线 296 passed；当前 commit `6ef43de`
源文件:D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/forge_core/design_agent/vs_interface.py（Opus 5 裁定）

---

## 0. 你的运行环境

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `6ef43de`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/ mcp_servers/vectorworks_mcp/` / `uv run python -m compileall -q src mcp_servers/`
- 当前测试基线：296 passed + 1 skipped
- **源文件**：`D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/forge_core/design_agent/vs.py`（1.4MB vs.* 绑定）

## 红线（违反即任务失败）

1. **禁止改动** `config/`、`domain_packs/`、`docs/`（`relay_workspace/` 内你的报告除外）、`.env`
2. **禁止改动** `mcp_servers/blender_mcp/`（blender_mcp 的 ruff 错误推迟到 M1 收官后处理）
3. **可以改动** `mcp_servers/vectorworks_mcp/`（新增文件）
4. 禁止新增第三方依赖；禁止删改任何现有测试（可新增测试）
5. commit 前必须三条验收命令全绿
6. commit 只提交 `mcp_servers/vectorworks_mcp/` 和 `tests/` 相关改动
7. 任何 401/额度异常：**立即停止，保留日志，报告部分结果**
8. 全程诚实汇报：跑了什么、什么没跑成，不许编造证据（主会话会逐项自验）

---

## 任务概述

M1 里程碑的 vectorworks-mcp 拆分第二阶段（对应 ARCHITECTURE.md §5、COMPONENTS.md §5）：

**核心目标**：
1. 从 vs.py（1.4MB 绑定）生成 `vs_index.json`（args/arity/ret/doc）
2. 实现 arity 校验防崩溃（发送前检查参数个数）
3. 实现工具集预设（full/modeling/minimal：248→40~100）
4. 植入 handoff/hash/approval 三重门禁（Executor 层）

**为什么需要这些**：
- **vs_index.json**：避免 LLM 编造 vs 函数，提供准确的函数签名、参数、返回值文档
- **arity 校验**：参数个数不对会导致 VW 引擎崩溃（历史 bug），必须拦截
- **工具集预设**：248 个工具太多（超过 MCP 上下文预算），按场景裁剪到 40-100
- **handoff 门禁**：副作用操作（创建墙体、导出 IFC）需要三重验证（摘要+hash+审批）

**当前状态**（Relay 016 已完成）：
- ✅ MCP server 基础实现（FastMCP + 文件 IPC）
- ✅ runner.py 轮询机制
- ✅ 基础工具（ping/describe_capabilities/execute_vs_code）
- ⚠️ **缺失**：vs_index.json、arity 校验、工具集预设、handoff 门禁

**你的任务**：
1. 从 vs.py 生成 vs_index.json（离线提取函数签名）
2. 实现 arity 校验（发送前拦截）
3. 实现工具集预设（三档切换）
4. 植入 handoff/hash/approval 门禁
5. 编写完整的单元测试

---

## 任务 A：生成 vs_index.json（从 vs.py 离线提取）

### A1：编写 generate_vs_index.py（离线脚本）

创建 `mcp_servers/vectorworks_mcp/tools/generate_vs_index.py`：

**功能**：
- 解析 `D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/forge_core/design_agent/vs.py`
- 提取所有 `vs.*` 函数的签名（args/arity/ret/doc）
- 输出 `vs_index.json`

**关键技术**：
```python
import ast
import json
from pathlib import Path

def extract_vs_functions(vs_py_path: str) -> dict:
    """从 vs.py 提取函数签名。
    
    Returns:
        {
            "functions": {
                "vs.Rectangle": {
                    "args": ["x", "y", "width", "height"],
                    "arity": 4,
                    "return_type": "HANDLE",
                    "doc": "创建矩形对象"
                },
                ...
            },
            "total_count": 248
        }
    """
    with open(vs_py_path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    
    functions = {}
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = f"vs.{node.name}"
            
            # 提取参数列表
            args = [arg.arg for arg in node.args.args if arg.arg != 'self']
            
            # 提取 docstring
            doc = ast.get_docstring(node) or ""
            
            # 提取返回类型（从注解或 docstring 推断）
            return_type = "unknown"
            if node.returns:
                return_type = ast.unparse(node.returns)
            
            functions[func_name] = {
                "args": args,
                "arity": len(args),
                "return_type": return_type,
                "doc": doc[:200]  # 限制 200 字符
            }
    
    return {
        "functions": functions,
        "total_count": len(functions),
        "generated_at": datetime.now().isoformat()
    }

def main():
    vs_py_path = "D:/devloop/workSpace/app_codex/GenerativeBIM/openBIMForge/forge_core/design_agent/vs.py"
    output_path = "mcp_servers/vectorworks_mcp/vs_index.json"
    
    print(f"Extracting from {vs_py_path}...")
    index = extract_vs_functions(vs_py_path)
    print(f"Extracted {index['total_count']} functions")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    
    print(f"Generated {output_path}")

if __name__ == "__main__":
    main()
```

**执行**：
```bash
uv run python mcp_servers/vectorworks_mcp/tools/generate_vs_index.py
```

### A2：验证 vs_index.json 格式

生成后验证：
```bash
# 检查文件大小（应在 100KB-500KB 范围）
ls -lh mcp_servers/vectorworks_mcp/vs_index.json

# 检查 JSON 格式
python -c "import json; print(json.load(open('mcp_servers/vectorworks_mcp/vs_index.json'))['total_count'])"
```

---

## 任务 B：实现 arity 校验（发送前拦截）

### B1：修改 server/server.py（添加 arity 校验）

在 `FileIPCClient.send_command` 中添加 arity 校验：

```python
class FileIPCClient:
    def __init__(self, jobs_dir="jobs", results_dir="results", vs_index_path=None):
        self.jobs_dir = Path(jobs_dir)
        self.results_dir = Path(results_dir)
        
        # 加载 vs_index.json
        self.vs_index = {}
        if vs_index_path:
            with open(vs_index_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.vs_index = data.get("functions", {})
    
    def _validate_arity(self, command: str, params: dict):
        """arity 校验：参数个数不对拒绝执行（防崩溃）。"""
        if command != "execute_code":
            return  # 只校验 execute_code 命令
        
        code = params.get("code", "")
        
        # 简化实现：正则匹配 vs.FunctionName(arg1, arg2, ...)
        import re
        pattern = r'vs\.(\w+)\s*\('
        matches = re.findall(pattern, code)
        
        for func_name in matches:
            full_name = f"vs.{func_name}"
            if full_name in self.vs_index:
                expected_arity = self.vs_index[full_name]["arity"]
                
                # 提取实际参数个数（简化：统计逗号 + 1）
                # TODO: 更精确的解析（ast.parse）
                func_call_match = re.search(rf'vs\.{func_name}\s*\((.*?)\)', code)
                if func_call_match:
                    args_str = func_call_match.group(1).strip()
                    actual_arity = 0 if not args_str else len(args_str.split(','))
                    
                    if actual_arity != expected_arity:
                        raise ValueError(
                            f"arity 校验失败: {full_name} 需要 {expected_arity} 个参数，"
                            f"实际传入 {actual_arity} 个（防崩溃拦截）"
                        )
    
    def send_command(self, command: str, params: dict) -> dict:
        # arity 校验
        self._validate_arity(command, params)
        
        # 原有的文件 IPC 逻辑
        job_id = str(uuid.uuid4())
        # ...
```

### B2：更新 execute_vs_code 工具（添加校验提示）

```python
@mcp.tool()
def execute_vs_code(code: str) -> dict:
    """执行 VectorScript 代码（vs.* API 调用）。
    
    Args:
        code: VectorScript 代码字符串
    
    Returns:
        执行结果（含 stdout/stderr/return_value）
    
    注意：
    - 发送前会进行 arity 校验（参数个数必须匹配 vs_index.json）
    - 参数个数不对会被拦截，避免 VW 引擎崩溃
    """
    try:
        result = client.send_command("execute_code", {"code": code})
        return result
    except ValueError as e:
        # arity 校验失败
        return {"ok": False, "error": str(e), "validation_failed": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

---

## 任务 C：实现工具集预设（三档切换）

### C1：生成工具集预设配置

创建 `mcp_servers/vectorworks_mcp/toolsets.json`：

```json
{
  "full": {
    "description": "完整工具集（248 个函数）",
    "functions": ["*"],
    "count": 248
  },
  "modeling": {
    "description": "建模工具集（约 80 个核心建模函数）",
    "functions": [
      "vs.Rectangle",
      "vs.Oval",
      "vs.Polygon",
      "vs.Line",
      "vs.Arc",
      "vs.CreateWall",
      "vs.CreateSlab",
      "vs.CreateColumn",
      "vs.CreateBeam",
      "vs.CreateDoor",
      "vs.CreateWindow",
      "vs.Extrude",
      "vs.Loft",
      "vs.Sweep",
      "vs.Move3DObj",
      "vs.Rotate3D",
      "vs.Scale3D",
      "vs.SetFillFore",
      "vs.SetFillBack",
      "vs.SetLW",
      "vs.SetClass"
    ],
    "count": 80
  },
  "minimal": {
    "description": "最小工具集（约 40 个基础函数）",
    "functions": [
      "vs.Rectangle",
      "vs.Oval",
      "vs.Line",
      "vs.CreateWall",
      "vs.CreateSlab",
      "vs.Move3DObj",
      "vs.Rotate3D",
      "vs.SetFillFore",
      "vs.SetClass",
      "vs.Message"
    ],
    "count": 40
  }
}
```

**注意**：实际函数列表需要从 vs_index.json 中挑选，上面只是示例。

### C2：修改 describe_capabilities（返回当前工具集）

```python
@mcp.tool()
def describe_capabilities() -> dict:
    """描述 VW MCP 能力。"""
    result = client.send_command("describe_capabilities", {})
    
    # 读取当前工具集配置
    toolsets_path = Path("mcp_servers/vectorworks_mcp/toolsets.json")
    toolsets = json.load(toolsets_path.open())
    
    current_toolset = os.environ.get("VW_TOOLSET", "minimal")
    
    return {
        "server_version": "1.0.0-m1-phase2",
        "vectorworks_version": result.get("vw_version", "unknown"),
        "toolset": current_toolset,
        "toolset_info": toolsets.get(current_toolset, {}),
        "vs_index_loaded": len(client.vs_index) > 0,
        "vs_index_count": len(client.vs_index),
        "known_issues": result.get("known_issues", [])
    }
```

---

## 任务 D：植入 handoff/hash/approval 三重门禁

### D1：理解门禁机制（参照 openBIMForge）

**三重门禁**（参照 ARCHITECTURE.md §5）：
1. **handoff 摘要**：副作用操作前，生成操作摘要（"创建墙体 @ (10, 0) 长度 5m"）
2. **参数 hash**：对参数计算 hash，防止参数被篡改
3. **权限审批**：高风险操作（导出 IFC、删除对象）需要用户确认

### D2：实现门禁检查（server/gate.py，新建）

```python
"""handoff/hash/approval 三重门禁（参照 openBIMForge）。"""

import hashlib
import json
from typing import Any

def generate_handoff_summary(command: str, params: dict) -> str:
    """生成操作摘要（handoff 第 1 重）。"""
    if command == "execute_code":
        code = params.get("code", "")
        
        # 提取关键操作
        if "CreateWall" in code:
            return f"创建墙体: {code[:50]}..."
        elif "ExportIFC" in code:
            return f"导出 IFC: {code[:50]}..."
        else:
            return f"执行代码: {code[:50]}..."
    
    return f"执行命令: {command}"

def compute_params_hash(params: dict) -> str:
    """计算参数 hash（handoff 第 2 重）。"""
    params_str = json.dumps(params, sort_keys=True)
    return hashlib.sha256(params_str.encode()).hexdigest()[:16]

def requires_approval(command: str, params: dict) -> bool:
    """判断是否需要审批（handoff 第 3 重）。"""
    if command != "execute_code":
        return False
    
    code = params.get("code", "")
    
    # 高风险操作列表
    high_risk_ops = ["ExportIFC", "DeleteObj", "DeleteAll", "CloseDocument"]
    
    return any(op in code for op in high_risk_ops)

def check_gate(command: str, params: dict, approval_fn=None) -> dict:
    """三重门禁检查。
    
    Returns:
        {"ok": True} 或 {"ok": False, "reason": "..."}
    """
    # 第 1 重：生成摘要
    summary = generate_handoff_summary(command, params)
    
    # 第 2 重：计算 hash
    params_hash = compute_params_hash(params)
    
    # 第 3 重：审批
    if requires_approval(command, params):
        if approval_fn is None:
            return {"ok": False, "reason": "需要审批但未提供 approval_fn"}
        
        approved = approval_fn(summary, params_hash)
        if not approved:
            return {"ok": False, "reason": "用户拒绝审批"}
    
    return {"ok": True, "summary": summary, "hash": params_hash}
```

### D3：修改 FileIPCClient（植入门禁）

```python
from .gate import check_gate

class FileIPCClient:
    def __init__(self, ..., approval_fn=None):
        # ...
        self.approval_fn = approval_fn
    
    def send_command(self, command: str, params: dict) -> dict:
        # 1. arity 校验
        self._validate_arity(command, params)
        
        # 2. 三重门禁检查
        gate_result = check_gate(command, params, self.approval_fn)
        if not gate_result["ok"]:
            raise RuntimeError(f"门禁拦截: {gate_result['reason']}")
        
        # 3. 原有的文件 IPC 逻辑
        job_id = str(uuid.uuid4())
        # ...
```

---

## 任务 E：单元测试

### E1：vs_index 生成测试（tests/test_vs_index.py，新建）

**4 个测试**：

1. `test_generate_vs_index_success`
   - 运行 generate_vs_index.py
   - 断言：vs_index.json 生成成功

2. `test_vs_index_format`
   - 加载 vs_index.json
   - 断言：包含 "functions"、"total_count" 字段

3. `test_vs_index_function_schema`
   - 检查第一个函数的 schema
   - 断言：包含 args/arity/return_type/doc

4. `test_vs_index_count_reasonable`
   - 检查 total_count
   - 断言：在 100-500 范围内（vs.py 应有数百个函数）

### E2：arity 校验测试（tests/test_arity_validation.py，新建）

**5 个测试**：

1. `test_arity_validation_pass`
   - 构造正确的 vs 调用（vs.Rectangle(0, 0, 10, 10)，4 个参数）
   - 断言：校验通过

2. `test_arity_validation_fail_too_few`
   - 构造错误的调用（vs.Rectangle(0, 0)，只有 2 个参数）
   - 断言：抛出 ValueError，消息包含 "arity 校验失败"

3. `test_arity_validation_fail_too_many`
   - 构造错误的调用（vs.Rectangle(0, 0, 10, 10, 20)，5 个参数）
   - 断言：抛出 ValueError

4. `test_arity_validation_skip_non_vs_functions`
   - 调用非 vs 函数（print("test")）
   - 断言：不触发 arity 校验

5. `test_arity_validation_multiple_functions`
   - 代码包含多个 vs 调用
   - 断言：所有调用都被校验

### E3：工具集预设测试（tests/test_toolsets.py，新建）

**3 个测试**：

1. `test_toolsets_json_format`
   - 加载 toolsets.json
   - 断言：包含 full/modeling/minimal 三档

2. `test_describe_capabilities_returns_toolset`
   - 设置 VW_TOOLSET=minimal
   - 调用 describe_capabilities()
   - 断言：返回 toolset="minimal"

3. `test_toolset_function_count`
   - 检查三档工具集的 count
   - 断言：full > modeling > minimal

### E4：门禁测试（tests/test_gate.py，新建）

**6 个测试**：

1. `test_generate_handoff_summary`
   - 调用 generate_handoff_summary("execute_code", {"code": "vs.CreateWall(...)"})
   - 断言：返回包含 "创建墙体"

2. `test_compute_params_hash`
   - 计算参数 hash
   - 断言：返回 16 字符 hash

3. `test_requires_approval_high_risk`
   - 检查 ExportIFC 操作
   - 断言：requires_approval() 返回 True

4. `test_requires_approval_low_risk`
   - 检查普通操作（CreateWall）
   - 断言：requires_approval() 返回 False

5. `test_check_gate_approved`
   - mock approval_fn 返回 True
   - 断言：check_gate() 返回 {"ok": True}

6. `test_check_gate_rejected`
   - mock approval_fn 返回 False
   - 断言：check_gate() 返回 {"ok": False}

---

## 任务 F：验收与提交

### F1：三条验收命令

```bash
# 1. 全量测试（应 ≥314 passed，新增 18 个测试）
uv run pytest -q

# 2. 代码检查
uv run ruff check src/ tests/ mcp_servers/vectorworks_mcp/

# 3. 编译检查
uv run python -m compileall -q src mcp_servers/
```

### F2：提交策略（分两个 commit）

**Commit 1**（核心实现）：
```bash
git add mcp_servers/vectorworks_mcp/
git commit -m "M1: VW MCP 拆分第二阶段 - vs_index + 工具集预设 + 门禁

- vs_index.json: 从 vs.py 离线提取 248 个函数签名（args/arity/ret/doc）
- arity 校验: 发送前检查参数个数，不符拦截（防 VW 引擎崩溃）
- toolsets.json: 三档工具集预设（full/modeling/minimal: 248→80→40）
- gate.py: handoff/hash/approval 三重门禁（参照 openBIMForge）"
```

**Commit 2**（测试覆盖）：
```bash
git add tests/
git commit -m "M1 测试: VW MCP vs_index + arity + 工具集 + 门禁单元测试全覆盖

- tests/test_vs_index.py: vs_index 生成与格式 4个测试
- tests/test_arity_validation.py: arity 校验拦截 5个测试
- tests/test_toolsets.py: 工具集预设 3个测试
- tests/test_gate.py: 三重门禁 6个测试"
```

---

## 任务 G：报告

写到 `relay_workspace/m1_vw_mcp_phase2/report.md`，包含：

### G1：实现总结
1. vs_index.json 生成统计（提取了多少函数、文件大小）
2. arity 校验的拦截案例
3. 工具集预设的三档对比
4. 门禁机制的实现细节

### G2：测试证据
1. 三条验收命令的**原始输出**
2. 新增测试列表（18 个测试）

### G3：Commit 证据
1. 两个 commit 的 hash
2. 文件变更统计

### G4：生产就绪清单
1. vs_index.json 覆盖率（248 个函数是否全部提取）
2. arity 校验覆盖的函数比例
3. 工具集预设是否满足 40-100 目标
4. 门禁机制是否能拦截高风险操作

### G5：入库检查单
1. 改动文件清单
2. 遗留问题
3. 给主会话的建议（Relay 018 预置库的工作范围）

---

## 回执格式

完成后只回：**「017 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 关键技术要点（避免踩坑）

### 1. 从 vs.py 提取函数签名（完整示例）

任务书 A1 有完整的 `extract_vs_functions` 实现，直接复制使用。

### 2. arity 校验实现（简化版）

任务书 B1 有完整的 `_validate_arity` 实现，支持正则匹配 vs 调用。

### 3. 三重门禁实现（完整示例）

任务书 D2 有完整的 `check_gate` 实现，包含摘要生成、hash 计算、审批判定。

### 4. 工具集预设配置

任务书 C1 有 toolsets.json 示例，但实际函数列表需要从 vs_index.json 中挑选。

---

## 最后检查清单

执行前确认：
- [ ] 已读完整个任务书
- [ ] 已确认 vs.py 路径存在
- [ ] 已理解 arity 校验的重要性
- [ ] 已理解三重门禁机制
- [ ] 已准备好写 18 个单元测试

执行中遵守：
- ✅ 诚实汇报：跑了什么、没跑成什么
- ✅ 代码质量：类型注解、docstring、错误处理
- ✅ 测试隔离：用 `tmp_path`，不污染项目目录
- ❌ 不编造证据：pytest 输出必须真实
- ❌ 不违反红线

祝顺利！🚀
