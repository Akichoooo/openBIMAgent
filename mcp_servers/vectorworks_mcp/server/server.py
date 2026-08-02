# OPENBIMAGENT vectorworks-mcp server (M1 phase 1)
# UPSTREAM: openBIMForge vectorworks_plugin (自研单体,非第三方开源)
# Fork changes (marked "OPENBIMAGENT (<item>)"):
#   (a) telemetry 硬关 stub (参照 blender_mcp,见 ./telemetry.py)
#   (b) 文件 IPC 替代 socket (VW 不支持常驻 socket server)
#   (c) 版本探测: describe_capabilities 必带 VW 版本,避免版本工具 bug
#   (d) jobs/+results/ 轮询机制 (100ms 间隔, .running 标记)
# 参照规范: mcp_servers/blender_mcp/server/server.py (成熟的 FastMCP 封装,只读)

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

# OPENBIMAGENT (phase2 B/C): server.py 所在目录 (用于 vs_index/toolsets 默认路径
# 与 gate 模块的测试环境降级导入)
_SERVER_DIR = Path(__file__).resolve().parent

# OPENBIMAGENT: gate 模块导入。生产环境作为 server 包子模块加载 (from .gate);
# 测试环境用 importlib 按路径加载 server.py (无父包),降级为同目录绝对导入。
try:
    from .gate import check_gate  # type: ignore[no-redef]
except ImportError:  # pragma: no cover - 测试路径
    sys.path.insert(0, str(_SERVER_DIR))
    from gate import check_gate  # type: ignore[no-redef]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger("VectorworksMCPServer")

# OPENBIMAGENT: fork identity (phase2 保持 1.0.0-m1 不变,新增 vs_index/gate/toolset)
FORK_VERSION = "1.0.0-m1"
UPSTREAM_SOURCE = "openBIMForge vectorworks_plugin"

# OPENBIMAGENT (b/d): 文件 IPC 配置
DEFAULT_JOBS_DIR = os.getenv("VW_MCP_JOBS_DIR", "jobs")
DEFAULT_RESULTS_DIR = os.getenv("VW_MCP_RESULTS_DIR", "results")
DEFAULT_AUTHORIZED_ROOT = os.getenv("VW_MCP_AUTHORIZED_ROOT", "")
COMMAND_TIMEOUT = float(os.getenv("VW_MCP_TIMEOUT", "60"))
POLL_INTERVAL = 0.1  # 100ms 轮询间隔

# OPENBIMAGENT (phase2 B/C): vs_index/toolsets 默认路径 (相对 server.py 定位)
# server.py 在 mcp_servers/vectorworks_mcp/server/,vs_index.json 在上级目录
DEFAULT_VS_INDEX_PATH = _SERVER_DIR.parent / "vs_index.json"
DEFAULT_TOOLSETS_PATH = _SERVER_DIR.parent / "toolsets.json"
# 默认工具集 (可用 VW_TOOLSET 环境变量覆盖)
DEFAULT_TOOLSET = os.getenv("VW_TOOLSET", "minimal")

# 审批函数签名 (与 gate.ApprovalFn 一致,此处独立定义避免循环 import)
ApprovalFn = Callable[[str, str], bool]


def _archive_active_file(path: Path) -> Path | None:
    """将已消费文件原子移出活跃目录；避免删除失败影响 IPC，同时保留审计事实。"""
    if not path.exists():
        return None
    archive_dir = path.parent / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    if target.exists():
        target = archive_dir / f"{path.stem}.{uuid.uuid4().hex}{path.suffix}"
    os.replace(path, target)
    return target


class FileIPCClient:
    """文件 IPC 客户端:写 jobs/<job_id>.json,轮询 results/<job_id>.json/.failed。

    OPENBIMAGENT (b): 替代 blender_mcp 的 socket 传输层。VW 不支持常驻
    socket server,改用文件 IPC:jobs/ 放待执行 JSON,results/ 收执行结果。

    OPENBIMAGENT (phase2 B/D): send_command 写入 job 前做两重拦截:
      1. arity 校验:对 execute_code 命令,正则匹配 code 中 vs.FunctionName(...)
         调用,统计实际参数个数,与 vs_index 中 min_arity/max_arity 对比,
         不符抛 ValueError (防 VW 引擎崩溃)
      2. handoff/hash/approval 三重门禁:高风险操作 (ExportIFC/DeleteObj 等)
         未审批时抛 PermissionError (参照 openBIMForge Executor 层)

    协议:
      1. 生成唯一 job_id (uuid4 hex)
      2. 写入 jobs/<job_id>.json: {"command","params","timestamp"}
      3. 轮询 results/<job_id>.json (成功) 或 results/<job_id>.failed (失败)
         或 results/<job_id>.running (进行中,仅观测)
      4. 超时 (COMMAND_TIMEOUT) 抛 TimeoutError
    """

    def __init__(
        self,
        jobs_dir: str | Path = DEFAULT_JOBS_DIR,
        results_dir: str | Path = DEFAULT_RESULTS_DIR,
        timeout: float = COMMAND_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
        vs_index_path: str | Path | None = None,
        toolsets_path: str | Path | None = None,
        approval_fn: ApprovalFn | None = None,
    ) -> None:
        self.jobs_dir = Path(jobs_dir)
        self.results_dir = Path(results_dir)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.approval_fn = approval_fn

        # 加载 vs_index (失败降级为空 dict,不阻断启动但 arity 校验失效)
        self.vs_index: dict[str, dict[str, Any]] = {}
        idx_path = Path(vs_index_path) if vs_index_path else DEFAULT_VS_INDEX_PATH
        self.vs_index_path = idx_path
        self._load_vs_index(idx_path)

        # 加载 toolsets (失败降级为空 dict)
        self.toolsets: dict[str, Any] = {}
        ts_path = Path(toolsets_path) if toolsets_path else DEFAULT_TOOLSETS_PATH
        self.toolsets_path = ts_path
        self._load_toolsets(ts_path)

    def _load_vs_index(self, path: Path) -> None:
        """加载 vs_index.json,失败时降级为空 dict 并 log warning。

        OPENBIMAGENT (phase2 B): vs_index 缺失时不阻断 server 启动,但
        arity 校验失效 (所有 execute_code 直接送 runner,有崩溃风险)。
        """
        if not path.exists():
            logger.warning(
                f"vs_index.json not found at {path}; arity validation disabled "
                f"(VW engine crash risk for wrong-arity calls)"
            )
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # vs_index.json 的 functions 是 list,转 dict 便于按名查找
            funcs = data.get("functions", {})
            if isinstance(funcs, list):
                self.vs_index = {f["name"]: f for f in funcs if "name" in f}
            else:
                self.vs_index = dict(funcs)
            logger.info(
                f"Loaded vs_index: {len(self.vs_index)} functions from {path}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to load vs_index from {path}: {e}; "
                f"arity validation disabled"
            )

    def _load_toolsets(self, path: Path) -> None:
        """加载 toolsets.json,失败时降级为空 dict。"""
        if not path.exists():
            logger.warning(f"toolsets.json not found at {path}; toolset info unavailable")
            return
        try:
            self.toolsets = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"Loaded toolsets from {path}")
        except Exception as e:
            logger.warning(f"Failed to load toolsets from {path}: {e}")

    # ------------------------------------------------------------------
    # arity 校验 (phase2 B)
    # ------------------------------------------------------------------

    # 正则:匹配 vs.FunctionName( 调用开头 (FunctionName 首字母大写)
    _VS_CALL_RE = re.compile(r"vs\.([A-Za-z_]\w*)\s*\(")

    def _extract_vs_calls_ast(self, code: str) -> list[tuple[str, int]] | None:
        """用 ast.parse 提取 code 中所有 vs.FuncName(...) 调用。

        Args:
            code: 完整代码字符串

        Returns:
            [(func_name, actual_arity), ...] 列表;code 有语法错误时返回 None
            (调用方应降级到正则匹配)。actual_arity 含位置参数 + 关键字参数。
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        calls: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 仅识别 vs.FuncName 形式 (Attribute, value=Name(id='vs'))
            if not isinstance(func, ast.Attribute):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "vs"):
                continue
            actual = len(node.args) + len(node.keywords)
            calls.append((func.attr, actual))
        return calls

    def _find_call_arg_span(self, code: str, call_start: int) -> tuple[int, int]:
        """从 call_start (左括号位置) 找匹配右括号,返回 (end_pos, actual_arity)。

        降级路径:ast.parse 失败时用括号匹配 + 逗号计数 (不完美但能覆盖大多数)。
        end_pos = 右括号后位置;actual_arity = -1 表示无法解析。
        """
        depth = 0
        i = call_start
        end_pos = len(code)
        in_single = False  # 单引号字符串
        in_double = False  # 双引号字符串
        in_triple_single = False
        in_triple_double = False
        while i < len(code):
            ch = code[i]
            # 字符串状态机 (简化:不处理转义边界,降级模式不要求完美)
            if in_triple_single:
                if code[i:i + 3] == "'''":
                    in_triple_single = False
                    i += 3
                    continue
                i += 1
                continue
            if in_triple_double:
                if code[i:i + 3] == '"""':
                    in_triple_double = False
                    i += 3
                    continue
                i += 1
                continue
            if in_single:
                if ch == "\\":
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
                i += 1
                continue
            if in_double:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
                i += 1
                continue
            # 进入字符串
            if code[i:i + 3] == "'''":
                in_triple_single = True
                i += 3
                continue
            if code[i:i + 3] == '"""':
                in_triple_double = True
                i += 3
                continue
            if ch == "'":
                in_single = True
                i += 1
                continue
            if ch == '"':
                in_double = True
                i += 1
                continue
            if ch == "#":
                while i < len(code) and code[i] != "\n":
                    i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_pos = i + 1
                    break
            i += 1

        args_str = code[call_start + 1:end_pos - 1].strip()
        if not args_str:
            return (end_pos, 0)
        # 降级:粗略逗号计数 (不区分嵌套,ast 失败时才走此路径)
        rough = len([a for a in args_str.split(",") if a.strip()])
        return (end_pos, rough)

    def _validate_arity(self, command: str, params: dict[str, Any]) -> None:
        """arity 校验:execute_code 命令的 vs.* 调用参数个数不符时抛 ValueError。

        OPENBIMAGENT (phase2 B): 历史bug 显示参数个数不对会导致 VW 引擎崩溃,
        必须在发送前拦截。

        判定规则 (基于 vs_index 中的 min_arity/max_arity):
          - min_arity = 必填位置参数个数 (无默认值的位置参数)
          - max_arity = 含默认值的位置参数总数;*args 函数为 None (无上限)
          - 实际参数个数 < min_arity 或 (max_arity 非 None 且 > max_arity) 时报错
          - 未知函数 (不在 vs_index) 跳过 (可能是 LLM 写的辅助 Python 代码)
          - 关键字参数调用 (vs.Foo(a=1)) 不严格校验 (AST 路径会统计 keywords,
            但与 min_arity 比较时放宽:含 keyword 时跳过 min 检查,只查 max)

        Args:
            command: 命令名 (仅 execute_code 校验)
            params: 命令参数字典

        Raises:
            ValueError: 参数个数不符 (含函数名、期望/实际 arity)
        """
        if command != "execute_code":
            return
        if not self.vs_index:
            # vs_index 未加载,跳过校验 (启动时已 log warning)
            return

        code = params.get("code", "")
        if not code:
            return

        # 优先用 AST 提取 (最准确,正确处理嵌套/字符串/关键字参数)
        ast_calls = self._extract_vs_calls_ast(code)
        if ast_calls is not None:
            calls = ast_calls
        else:
            # 降级:正则匹配 + 括号计数
            calls = []
            pos = 0
            while pos < len(code):
                m = self._VS_CALL_RE.search(code, pos)
                if not m:
                    break
                func_short = m.group(1)
                call_start = m.end() - 1
                end_pos, actual = self._find_call_arg_span(code, call_start)
                calls.append((func_short, actual))
                pos = end_pos if end_pos > pos else m.end()

        for func_short, actual in calls:
            full_name = f"vs.{func_short}"
            if full_name not in self.vs_index:
                continue
            if actual < 0:
                continue

            spec = self.vs_index[full_name]
            min_a = spec.get("min_arity", spec.get("arity", 0))
            max_a = spec.get("max_arity", spec.get("arity", 0))

            if actual < min_a:
                raise ValueError(
                    f"arity 校验失败: {full_name} 至少需要 {min_a} 个参数,"
                    f"实际传入 {actual} 个 (防崩溃拦截)"
                )
            if max_a is not None and actual > max_a:
                raise ValueError(
                    f"arity 校验失败: {full_name} 最多接受 {max_a} 个参数,"
                    f"实际传入 {actual} 个 (防崩溃拦截)"
                )

    # ------------------------------------------------------------------
    # 发送命令 (含两重拦截:arity + gate)
    # ------------------------------------------------------------------

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """通过文件 IPC 发送命令并等待结果。

        OPENBIMAGENT (phase2 B/D): 写入 job 前做两重拦截:
          1. arity 校验 (失败抛 ValueError)
          2. handoff/hash/approval 门禁 (高风险未审批抛 PermissionError)

        Args:
            command: 命令名 (ping/describe_capabilities/execute_code)
            params: 命令参数字典

        Returns:
            执行结果字典

        Raises:
            ValueError: arity 校验失败 (参数个数不符)
            PermissionError: 门禁拦截 (高风险操作未审批)
            RuntimeError: runner 写入 .failed 标记 (命令执行异常)
            TimeoutError: 超过 timeout 秒未收到结果
        """
        params = params or {}

        # 1. arity 校验 (发送前拦截,防 VW 引擎崩溃)
        self._validate_arity(command, params)

        # 2. 三重门禁 (handoff + hash + approval)
        gate_result = check_gate(command, params, self.approval_fn)
        if not gate_result["ok"]:
            raise PermissionError(f"门禁拦截: {gate_result['reason']}")

        job_id = uuid.uuid4().hex
        job_path = self.jobs_dir / f"{job_id}.json"
        result_path = self.results_dir / f"{job_id}.json"
        failed_path = self.results_dir / f"{job_id}.failed"

        # 写入 job (含门禁审计字段,供 runner 侧审计)
        job_data = {
            "command": command,
            "params": params,
            "timestamp": datetime.now().isoformat(),
            "gate": {
                "handoff": gate_result["handoff"],
                "params_hash": gate_result["params_hash"],
                "requires_approval": gate_result["requires_approval"],
                "approved": gate_result["approved"],
            },
        }
        job_path.write_text(json.dumps(job_data, ensure_ascii=False), encoding="utf-8")
        logger.info(
            f"Sent command: {command} (job_id={job_id[:8]}, "
            f"handoff={gate_result['handoff'][:40]})"
        )

        # 轮询结果。结果文件可能刚变为可见但尚未完整写入；JSONDecodeError 时继续短轮询。
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(self.poll_interval)
                    continue
                _archive_active_file(result_path)
                _archive_active_file(job_path)
                return result

            if failed_path.exists():
                error = failed_path.read_text(encoding="utf-8")
                _archive_active_file(failed_path)
                _archive_active_file(job_path)
                raise RuntimeError(f"Command failed: {error}")

            time.sleep(self.poll_interval)

        # 超时:从活跃队列原子移入归档，避免删除策略干扰业务语义。
        _archive_active_file(job_path)
        raise TimeoutError(f"Command timed out after {self.timeout}s (command={command})")


mcp = FastMCP("vectorworks-mcp")

# Global IPC client (upstream pattern,参照 blender_mcp 的 _blender_connection)
_client: FileIPCClient | None = None


def get_client() -> FileIPCClient:
    """获取或创建全局 FileIPCClient (参照 blender_mcp get_blender_connection)。"""
    global _client
    if _client is None:
        _client = FileIPCClient()
    return _client


def set_client(client: FileIPCClient) -> None:
    """注入 IPC client (测试用,允许替换为 tmp_path 实例)。"""
    global _client
    _client = client


# ---------------------------------------------------------------------------
# tools (OPENBIMAGENT: 3 个基础工具,M1 第二阶段增强 arity/gate/toolset)
# ---------------------------------------------------------------------------

@mcp.tool()
def ping() -> str:
    """健康检查:验证 MCP server 与 VW runner 经文件 IPC 的连通性。

    OPENBIMAGENT (b): 经文件 IPC 发送 ping,runner 返回 {"message":"pong"}。
    """
    try:
        client = get_client()
        result = client.send_command("ping", {})
        return result.get("message", "pong")
    except Exception as e:
        logger.error(f"Ping failed: {str(e)}")
        return f"ping failed: {e}"


@mcp.tool()
def describe_capabilities() -> dict[str, Any]:
    """描述 VW MCP 能力:版本、工具集、限制、已知坑。

    OPENBIMAGENT (c): 关键 -- 返回 VW 宿主版本,避免"模型不清楚 VW 版本工具出 bug"。
    Agent 应在首次调用时先调此工具对齐版本与已知坑。返回的 known_issues 来自
    runner 侧 (VW 宿主实测),含 ArcByCenter 损坏等坑。

    OPENBIMAGENT (phase2 C): 返回当前工具集 (full/modeling/minimal,默认 minimal)
    + vs_index 加载状态 + 工具集函数数。Agent 可据此判断当前可用 API 范围。
    """
    try:
        client = get_client()
        result = client.send_command("describe_capabilities", {})
        current_toolset = DEFAULT_TOOLSET
        # getattr 容错:测试用 _FakeClient 可能无 toolsets/vs_index 属性
        toolsets = getattr(client, "toolsets", {}) or {}
        vs_index = getattr(client, "vs_index", {}) or {}
        toolset_info = toolsets.get(current_toolset, {})
        return {
            "server_version": FORK_VERSION,
            "vectorworks_version": result.get("vw_version", "unknown"),
            "python_version": result.get("python_version", "unknown"),
            "architecture": "file_ipc",
            "toolset": current_toolset,
            "toolset_info": {
                "description": toolset_info.get("description", ""),
                "count": toolset_info.get("count", 0),
            },
            "available_toolsets": list(toolsets.keys()),
            "vs_index_loaded": len(vs_index) > 0,
            "vs_index_count": len(vs_index),
            "file_ipc": True,
            "typed_execution": {
                "protocol_version": "1.0",
                "host_api_version": "2024",
                "units": ["m", "mm"],
                "operations": ["create_object", "set_record", "connect_topology"],
                "object_types": [
                    "utility_system",
                    "manhole",
                    "inlet",
                    "outlet",
                    "junction",
                    "valve",
                    "equipment",
                    "terminal",
                    "distribution_port",
                    "pipe_segment",
                ],
                "controlled_save": True,
                "idempotent_receipts": True,
            },
            "poll_interval_ms": int(POLL_INTERVAL * 1000),
            "command_timeout_s": int(COMMAND_TIMEOUT),
            "limitations": [
                "文件 IPC,不支持实时流式响应",
                "轮询间隔 100ms",
                f"单个命令超时 {int(COMMAND_TIMEOUT)}s",
                "不支持并发命令(串行处理)",
                "execute_code 发送前做 arity 校验 (防 VW 引擎崩溃)",
                "高风险操作 (ExportIFC/DeleteObj 等) 需审批,未审批被门禁拦截",
            ],
            "known_issues": result.get("known_issues", []),
        }
    except Exception as e:
        logger.error(f"describe_capabilities failed: {str(e)}")
        return {"error": str(e)}


@mcp.tool()
def execute_plan(
    plan: dict[str, Any],
    output_path: str,
    approved: bool = False,
) -> dict[str, Any]:
    """执行结构化 Vectorworks plan；typed 主链不经过自由脚本入口。"""
    try:
        client = get_client()
        params: dict[str, Any] = {
            "plan": plan,
            "output_path": output_path,
        }
        if DEFAULT_AUTHORIZED_ROOT:
            params["authorized_root"] = DEFAULT_AUTHORIZED_ROOT
        if approved:
            params["_approved"] = True
        return client.send_command("execute_plan", params)
    except PermissionError as exc:
        logger.warning(f"execute_plan blocked by gate: {exc}")
        return {"ok": False, "error": str(exc), "gate_blocked": True}
    except (TypeError, ValueError) as exc:
        logger.warning(f"execute_plan validation failed: {exc}")
        return {"ok": False, "error": str(exc), "validation_failed": True}
    except Exception as exc:
        logger.error(f"execute_plan failed: {exc}")
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def execute_vs_code(code: str, approved: bool = False) -> dict[str, Any]:
    """执行 VectorScript 代码 (vs.* API 调用)。

    OPENBIMAGENT (b): 经文件 IPC 发送 execute_code 命令,runner 在 VW 内嵌
    Python 中 exec 代码 (vs 模块注入 globals)。

    OPENBIMAGENT (phase2 B/D): 发送前做两重拦截:
      - arity 校验:vs.* 调用参数个数与 vs_index.json 不符时返回
        {"ok": False, "error": ..., "validation_failed": True}
      - 三重门禁:高风险操作 (ExportIFC/DeleteObj 等) 未审批时返回
        {"ok": False, "error": ..., "gate_blocked": True}

    Args:
        code: VectorScript 代码字符串
        approved: 是否已由上层人工审批；服务端只在此值为 True 时向内部门禁传递
            ``_approved=True``，避免将内部协议字段直接暴露为 MCP 参数。

    Returns:
        执行结果 (含 ok/stdout/stderr 或 error/traceback);校验/门禁失败时
        附 validation_failed / gate_blocked 标志位
    """
    try:
        client = get_client()
        params: dict[str, Any] = {"code": code}
        if approved:
            params["_approved"] = True
        result = client.send_command("execute_code", params)
        return result
    except ValueError as e:
        # arity 校验失败
        logger.warning(f"execute_vs_code arity validation failed: {str(e)}")
        return {"ok": False, "error": str(e), "validation_failed": True}
    except PermissionError as e:
        # 门禁拦截
        logger.warning(f"execute_vs_code blocked by gate: {str(e)}")
        return {"ok": False, "error": str(e), "gate_blocked": True}
    except Exception as e:
        logger.error(f"execute_vs_code failed: {str(e)}")
        return {"ok": False, "error": str(e)}


# Main execution

def main() -> None:
    """Run the MCP server (upstream pattern)."""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if interactive:
        logger.info(
            "vectorworks-mcp is an MCP server and is meant to be launched by "
            "your MCP client, not run by hand. It will now wait silently for a "
            "client on stdin -- that is normal, not a hang. Press Ctrl-C to exit."
        )
    mcp.run()


if __name__ == "__main__":
    main()
