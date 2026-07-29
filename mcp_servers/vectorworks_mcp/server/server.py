# OPENBIMAGENT vectorworks-mcp server (M1 phase 1)
# UPSTREAM: openBIMForge vectorworks_plugin (自研单体,非第三方开源)
# Fork changes (marked "OPENBIMAGENT (<item>)"):
#   (a) telemetry 硬关 stub (参照 blender_mcp,见 ./telemetry.py)
#   (b) 文件 IPC 替代 socket (VW 不支持常驻 socket server)
#   (c) 版本探测: describe_capabilities 必带 VW 版本,避免版本工具 bug
#   (d) jobs/+results/ 轮询机制 (100ms 间隔, .running 标记)
# 参照规范: mcp_servers/blender_mcp/server/server.py (成熟的 FastMCP 封装,只读)

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger("VectorworksMCPServer")

# OPENBIMAGENT: fork identity
FORK_VERSION = "1.0.0-m1"
UPSTREAM_SOURCE = "openBIMForge vectorworks_plugin"

# OPENBIMAGENT (b/d): 文件 IPC 配置
DEFAULT_JOBS_DIR = os.getenv("VW_MCP_JOBS_DIR", "jobs")
DEFAULT_RESULTS_DIR = os.getenv("VW_MCP_RESULTS_DIR", "results")
COMMAND_TIMEOUT = float(os.getenv("VW_MCP_TIMEOUT", "60"))
POLL_INTERVAL = 0.1  # 100ms 轮询间隔


class FileIPCClient:
    """文件 IPC 客户端:写 jobs/<job_id>.json,轮询 results/<job_id>.json/.failed。

    OPENBIMAGENT (b): 替代 blender_mcp 的 socket 传输层。VW 不支持常驻
    socket server,改用文件 IPC:jobs/ 放待执行 JSON,results/ 收执行结果。

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
    ) -> None:
        self.jobs_dir = Path(jobs_dir)
        self.results_dir = Path(results_dir)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """通过文件 IPC 发送命令并等待结果。

        Args:
            command: 命令名 (ping/describe_capabilities/execute_code)
            params: 命令参数字典

        Returns:
            执行结果字典

        Raises:
            RuntimeError: runner 写入 .failed 标记 (命令执行异常)
            TimeoutError: 超过 timeout 秒未收到结果
        """
        job_id = uuid.uuid4().hex
        job_path = self.jobs_dir / f"{job_id}.json"
        result_path = self.results_dir / f"{job_id}.json"
        failed_path = self.results_dir / f"{job_id}.failed"

        # 写入 job
        job_data = {
            "command": command,
            "params": params or {},
            "timestamp": datetime.now().isoformat(),
        }
        job_path.write_text(json.dumps(job_data, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Sent command: {command} (job_id={job_id[:8]})")

        # 轮询结果
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink(missing_ok=True)
                job_path.unlink(missing_ok=True)
                return result

            if failed_path.exists():
                error = failed_path.read_text(encoding="utf-8")
                failed_path.unlink(missing_ok=True)
                job_path.unlink(missing_ok=True)
                raise RuntimeError(f"Command failed: {error}")

            time.sleep(self.poll_interval)

        # 超时:清理 job 文件
        job_path.unlink(missing_ok=True)
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
# tools (OPENBIMAGENT: 3 个基础工具,M1 第一阶段)
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
    """
    try:
        client = get_client()
        result = client.send_command("describe_capabilities", {})
        return {
            "server_version": FORK_VERSION,
            "vectorworks_version": result.get("vw_version", "unknown"),
            "python_version": result.get("python_version", "unknown"),
            "architecture": "file_ipc",
            "toolset": "minimal",
            "file_ipc": True,
            "poll_interval_ms": int(POLL_INTERVAL * 1000),
            "command_timeout_s": int(COMMAND_TIMEOUT),
            "limitations": [
                "文件 IPC,不支持实时流式响应",
                "轮询间隔 100ms",
                f"单个命令超时 {int(COMMAND_TIMEOUT)}s",
                "不支持并发命令(串行处理)",
            ],
            "known_issues": result.get("known_issues", []),
        }
    except Exception as e:
        logger.error(f"describe_capabilities failed: {str(e)}")
        return {"error": str(e)}


@mcp.tool()
def execute_vs_code(code: str) -> dict[str, Any]:
    """执行 VectorScript 代码 (vs.* API 调用)。

    OPENBIMAGENT (b): 经文件 IPC 发送 execute_code 命令,runner 在 VW 内嵌
    Python 中 exec 代码 (vs 模块注入 globals)。

    Args:
        code: VectorScript 代码字符串

    Returns:
        执行结果 (含 ok/stdout/stderr 或 error/traceback)
    """
    try:
        client = get_client()
        result = client.send_command("execute_code", {"code": code})
        return result
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
