# OPENBIMAGENT vectorworks-mcp runner (M1 phase 1)
# VW 宿主侧 Python runner:轮询 jobs/ 目录,执行命令,写 results/。
# 等价于 blender_mcp/addon.py,但走文件 IPC 替代 socket (VW 不支持常驻 socket)。
#
# 从 openBIMForge vendor/vs_interface.py 提取说明:
#   vendor/vs_interface.py 是 proxy/bridge 模块(非直接 vs.* 封装),硬依赖
#   vs 模块(VW 内置)与 forge_core 包,无法在测试环境独立 import。因此
#   本 runner 自行实现 execute_vs_code (exec with vs in globals),测试 mock vs。

from __future__ import annotations

import io
import json
import math
import sys
import time
import traceback
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

# OPENBIMAGENT (c): 已知 VW API 坑(节选自 README.md AGENTS.md 坑清单)
KNOWN_ISSUES = [
    "ArcByCenter 在 VW2024 中已损坏,用 Oval 替代",
    "Arc 第六参数为 Sweep 角度,非终点角度",
]


def get_vw_version() -> str:
    """获取 VectorWorks 版本。

    OPENBIMAGENT (c): 避免"模型不清楚 VW 版本工具出 bug"。在 VW 宿主内
    尝试 import vs 并调用版本 API;无 VW 环境时返回 "unknown"。

    Returns:
        VW 版本字符串 (如 "VectorWorks 2024") 或 "unknown"
    """
    try:
        import vs  # type: ignore[import-not-found]  # VectorWorks Python API
    except Exception:
        return "unknown"
    try:
        # vs.GetVersion() 返回 (major, minor, maintenance, build) 元组
        # 参考 openBIMForge 版本探测思路
        major = 2024  # 默认值,vendor 文件路径含 openBIMForge2024
        try:
            version_info = vs.GetVersion()
            if isinstance(version_info, (tuple, list)) and len(version_info) >= 1:
                major = int(version_info[0])
        except Exception:
            pass
        return f"VectorWorks {major}"
    except Exception:
        return "unknown"


def execute_vs_code(code: str) -> dict[str, Any]:
    """执行 VectorScript 代码 (vs.* API 调用)。

    从 openBIMForge vs_interface.py 提取思路:在 VW 内嵌 Python 中 exec
    代码,vs 模块注入 globals。捕获 stdout,异常转 error/traceback。

    Args:
        code: VectorScript 代码字符串

    Returns:
        {"ok": True, "stdout": ..., "stderr": ...} 成功
        {"ok": False, "error": ..., "traceback": ...} 失败
    """
    try:
        import vs  # type: ignore[import-not-found]  # VectorWorks Python API
    except Exception as e:
        return {
            "ok": False,
            "error": f"vs module not available: {e}",
            "traceback": traceback.format_exc(),
        }
    try:
        stdout_buf = io.StringIO()
        exec_globals: dict[str, Any] = {"vs": vs, "math": math, "__name__": "__vw_exec__"}
        with redirect_stdout(stdout_buf):
            exec(code, exec_globals)  # noqa: S102  (VW 代码执行器,沙箱由 VW 宿主保证)
        return {
            "ok": True,
            "stdout": stdout_buf.getvalue(),
            "stderr": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def execute_command(command: str, params: dict[str, Any]) -> dict[str, Any]:
    """命令分发:按 command 名路由到对应处理函数。

    Args:
        command: 命令名 (ping/describe_capabilities/execute_code)
        params: 命令参数

    Returns:
        执行结果字典

    Raises:
        ValueError: 未知命令
    """
    if command == "ping":
        return {"message": "pong"}
    elif command == "describe_capabilities":
        return {
            "vw_version": get_vw_version(),
            "python_version": sys.version,
            "known_issues": list(KNOWN_ISSUES),
        }
    elif command == "execute_code":
        code = params.get("code", "")
        return execute_vs_code(code)
    else:
        raise ValueError(f"Unknown command: {command}")


def poll_jobs_once(jobs_dir: Path, results_dir: Path) -> list[str]:
    """处理一轮 job(测试友好:不死循环,处理完当前所有 job 即返回)。

    OPENBIMAGENT (d): glob jobs/*.json,对每个 job:
      1. 写 results/<job_id>.running 标记(供客户端观测进行中状态)
      2. 读 job → execute_command()
      3. 成功写 results/<job_id>.json,失败写 results/<job_id>.failed
      4. 清理 jobs/<job_id>.json 与 .running 标记

    Args:
        jobs_dir: jobs 目录
        results_dir: results 目录

    Returns:
        本轮处理的 job_id 列表
    """
    processed: list[str] = []
    for job_path in sorted(jobs_dir.glob("*.json")):
        job_id = job_path.stem
        running_path = results_dir / f"{job_id}.running"
        result_path = results_dir / f"{job_id}.json"
        failed_path = results_dir / f"{job_id}.failed"

        # 标记为 running
        running_path.write_text(datetime.now().isoformat(), encoding="utf-8")

        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            result = execute_command(job["command"], job.get("params", {}))
            result_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            failed_path.write_text(str(e), encoding="utf-8")
        finally:
            job_path.unlink(missing_ok=True)
            running_path.unlink(missing_ok=True)
            processed.append(job_id)

    return processed


def main() -> None:
    """runner 主循环:死循环轮询 jobs/,100ms 间隔。

    在 VW 内嵌 Python 中运行;Ctrl-C 或 VW 退出时停止。
    """
    jobs_dir = Path("jobs")
    results_dir = Path("results")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    print("VW MCP runner started", flush=True)
    print(f"  jobs_dir:   {jobs_dir.resolve()}", flush=True)
    print(f"  results_dir:{results_dir.resolve()}", flush=True)
    try:
        while True:
            poll_jobs_once(jobs_dir, results_dir)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Runner stopped", flush=True)


if __name__ == "__main__":
    main()
