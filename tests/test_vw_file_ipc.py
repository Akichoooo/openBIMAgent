"""VW MCP FileIPCClient 单测:文件 IPC 协议(jobs/+results/)全覆盖。

测试位置裁定:pyproject.toml 的 testpaths=["tests"],故测试放根 tests/
而非 mcp_servers/vectorworks_mcp/tests/(后者不被 uv run pytest 收集)。
用 importlib 按路径加载 server.py(mcp_servers 不在 pythonpath)。

覆盖(8 个测试):
- send_command 成功:写 job → 模拟 runner 写 result → 返回正确结果
- send_command 超时:写 job,不写 result → 抛 TimeoutError
- send_command 失败:写 job,runner 写 .failed → 抛 RuntimeError
- job 文件清理:发送后 job_path 不存在
- 并发 job:3 个 job 各自得到正确结果
- poll_jobs_once 处理全部:写 3 个 job → 调用一次 → 3 个 result 生成
- poll_jobs_once 异常处理:会抛异常的 job → 生成 .failed
- .running 标记清理:执行后 .running 不存在
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

# ---------- 按路径加载 server.py(mcp_servers 不在 pythonpath) ----------

_SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "server" / "server.py"
)


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 按路径加载 runner.py ----------

_RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "runner.py"
)


def _load_runner_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_runner", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 辅助:模拟 runner 写 result ----------


def _write_result_after_delay(
    results_dir: Path, job_id: str, result: dict[str, Any], delay: float = 0.05
) -> None:
    """延迟写 result 文件,模拟 runner 处理后回写。"""

    def _write() -> None:
        time.sleep(delay)
        (results_dir / f"{job_id}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )

    t = threading.Thread(target=_write, daemon=True)
    t.start()


def _find_job_id(jobs_dir: Path) -> str:
    """从 jobs/ 目录中读取唯一的 job_id。"""
    jobs = list(jobs_dir.glob("*.json"))
    assert len(jobs) == 1, f"expected 1 job, got {len(jobs)}"
    return jobs[0].stem


# ---------- 8 个测试 ----------


def test_send_command_success(tmp_path: Path) -> None:
    """send_command 成功:写 job → runner 写 result → 返回正确结果。"""
    server = _load_server_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    client = server.FileIPCClient(jobs_dir, results_dir, timeout=2.0, poll_interval=0.02)

    # 模拟 runner 延迟写 result
    def _fake_runner() -> None:
        time.sleep(0.05)
        job_id = _find_job_id(jobs_dir)
        (results_dir / f"{job_id}.json").write_text(
            json.dumps({"message": "pong"}), encoding="utf-8"
        )

    t = threading.Thread(target=_fake_runner, daemon=True)
    t.start()

    result = client.send_command("ping", {})
    assert result == {"message": "pong"}
    t.join(timeout=1.0)


def test_send_command_timeout(tmp_path: Path) -> None:
    """send_command 超时:写 job,不写 result → 抛 TimeoutError。"""
    server = _load_server_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    client = server.FileIPCClient(jobs_dir, results_dir, timeout=0.3, poll_interval=0.02)

    with pytest.raises(TimeoutError, match="timed out"):
        client.send_command("ping", {})

    # 超时后 job 文件应被清理
    assert not list(jobs_dir.glob("*.json"))


def test_send_command_failed(tmp_path: Path) -> None:
    """send_command 失败:写 job,runner 写 .failed → 抛 RuntimeError。"""
    server = _load_server_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    client = server.FileIPCClient(jobs_dir, results_dir, timeout=2.0, poll_interval=0.02)

    def _fake_runner() -> None:
        time.sleep(0.05)
        job_id = _find_job_id(jobs_dir)
        (results_dir / f"{job_id}.failed").write_text("boom", encoding="utf-8")

    t = threading.Thread(target=_fake_runner, daemon=True)
    t.start()

    with pytest.raises(RuntimeError, match="Command failed: boom"):
        client.send_command("ping", {})
    t.join(timeout=1.0)


def test_job_file_cleanup(tmp_path: Path) -> None:
    """job 文件清理:发送成功后 job_path 不存在。"""
    server = _load_server_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    client = server.FileIPCClient(jobs_dir, results_dir, timeout=2.0, poll_interval=0.02)

    def _fake_runner() -> None:
        time.sleep(0.05)
        job_id = _find_job_id(jobs_dir)
        (results_dir / f"{job_id}.json").write_text(
            json.dumps({"ok": True}), encoding="utf-8"
        )

    t = threading.Thread(target=_fake_runner, daemon=True)
    t.start()

    client.send_command("execute_code", {"code": "1+1"})
    t.join(timeout=1.0)

    # job 文件应已清理
    assert not list(jobs_dir.glob("*.json"))
    # result 文件也应已清理(客户端读后删除)
    assert not list(results_dir.glob("*.json"))


def test_concurrent_jobs(tmp_path: Path) -> None:
    """并发 job:3 个 job 各自得到正确结果(文件 IPC 天然支持并发写入不同 job_id)。"""
    server = _load_server_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    client = server.FileIPCClient(jobs_dir, results_dir, timeout=3.0, poll_interval=0.02)

    def _fake_runner() -> None:
        """模拟 runner 处理所有 pending job。"""
        time.sleep(0.05)
        for jp in list(jobs_dir.glob("*.json")):
            jid = jp.stem
            job = json.loads(jp.read_text(encoding="utf-8"))
            (results_dir / f"{jid}.json").write_text(
                json.dumps({"echo": job["params"]}), encoding="utf-8"
            )

    t = threading.Thread(target=_fake_runner, daemon=True)
    t.start()

    results: list[dict[str, Any]] = []
    threads = [
        threading.Thread(
            target=lambda i=i: results.append(
                client.send_command("execute_code", {"code": str(i)})
            ),
            daemon=True,
        )
        for i in range(3)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=3.0)
    t.join(timeout=1.0)

    assert len(results) == 3
    echoed_codes = {r["echo"]["code"] for r in results}
    assert echoed_codes == {"0", "1", "2"}


def test_poll_jobs_processes_all(tmp_path: Path) -> None:
    """poll_jobs_once 处理全部:写 3 个 job → 调用一次 → 3 个 result 生成。"""
    runner = _load_runner_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    jobs_dir.mkdir()
    results_dir.mkdir()

    for i in range(3):
        (jobs_dir / f"job{i}.json").write_text(
            json.dumps({"command": "ping", "params": {}}), encoding="utf-8"
        )

    processed = runner.poll_jobs_once(jobs_dir, results_dir)

    assert len(processed) == 3
    assert len(list(results_dir.glob("*.json"))) == 3
    # job 文件全部清理
    assert not list(jobs_dir.glob("*.json"))
    # 每个 result 内容正确
    for rp in results_dir.glob("*.json"):
        data = json.loads(rp.read_text(encoding="utf-8"))
        assert data == {"message": "pong"}


def test_poll_jobs_handles_exception(tmp_path: Path) -> None:
    """poll_jobs_once 异常处理:会抛异常的 job → 生成 .failed。"""
    runner = _load_runner_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    jobs_dir.mkdir()
    results_dir.mkdir()

    # unknown 命令会抛 ValueError
    (jobs_dir / "bad.json").write_text(
        json.dumps({"command": "unknown_cmd", "params": {}}), encoding="utf-8"
    )

    runner.poll_jobs_once(jobs_dir, results_dir)

    failed = list(results_dir.glob("*.failed"))
    assert len(failed) == 1
    err_text = failed[0].read_text(encoding="utf-8")
    assert "Unknown command" in err_text
    # 不应有 .json 成功结果
    assert not list(results_dir.glob("*.json"))
    # job 文件清理
    assert not list(jobs_dir.glob("*.json"))


def test_running_marker_cleanup(tmp_path: Path) -> None:
    """running 标记清理:执行后 .running 不存在(poll_jobs_once finally 清理)。"""
    runner = _load_runner_module()
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    jobs_dir.mkdir()
    results_dir.mkdir()

    (jobs_dir / "marker.json").write_text(
        json.dumps({"command": "ping", "params": {}}), encoding="utf-8"
    )

    runner.poll_jobs_once(jobs_dir, results_dir)

    # .running 标记应被清理
    assert not list(results_dir.glob("*.running"))
    # 成功结果存在
    assert len(list(results_dir.glob("*.json"))) == 1
