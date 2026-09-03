"""CAD 宿主 Supervisor（P0-3）：状态机探活 + 有界退避重启，对标 pi-mono 的进程监管语义。

设计（诚实优先）：
- Blender MCP 是本地 TCP 服务（缺省 127.0.0.1:9876，env 可覆盖）：可探测、可在配置
  ``OPENBIMAGENT_BLENDER_EXE`` 后以 headless 方式拉起；重启有界（默认 3 次）且线性退避，
  超限如实报 ``down``，绝不伪造 ``up``。
- Vectorworks 是外部 runner（File IPC 轮询，脚本线程模型）：supervisor **不探测、不重启**，
  状态恒为 ``external``——前端据此显示"外部 runner 未探测"，而不是假绿灯/假红灯。
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

STATE_UP = "up"
STATE_DOWN = "down"
STATE_RESTARTING = "restarting"
STATE_EXTERNAL = "external"  # 外部 runner：不探测不重启（诚实标记）


@dataclass
class HostState:
    """单个宿主的观测状态。"""

    id: str
    label: str
    state: str
    restartable: bool
    restart_count: int = 0
    last_probe_at: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HostSupervisor:
    """宿主状态监管：TCP 探活 + 有界退避重启（仅 Blender；VW 恒 external）。"""

    def __init__(
        self,
        *,
        blender_host: str | None = None,
        blender_port: int | None = None,
        blender_exe: str | None = None,
        blender_cmd: str | None = None,
        max_restarts: int = 3,
        probe_timeout: float = 0.4,
    ) -> None:
        self._addr = (
            blender_host or os.environ.get("OPENBIMAGENT_BLENDER_HOST", "127.0.0.1"),
            blender_port or int(os.environ.get("OPENBIMAGENT_BLENDER_PORT", "9876")),
        )
        self._addr = (
            blender_host or os.environ.get("OPENBIMAGENT_BLENDER_HOST", "127.0.0.1"),
            blender_port or int(os.environ.get("OPENBIMAGENT_BLENDER_PORT", "9876")),
        )
        self._exe = blender_exe if blender_exe is not None else os.environ.get("OPENBIMAGENT_BLENDER_EXE", "")
        # 完整启动命令覆盖（测试/自定义拉起脚本）；缺省 [exe, --background, --factory-startup]
        self._cmd = blender_cmd if blender_cmd is not None else os.environ.get("OPENBIMAGENT_BLENDER_CMD", "")
        self._max_restarts = max_restarts
        self._probe_timeout = probe_timeout
        self._lock = threading.Lock()
        self._blender = HostState(
            id="blender",
            label=f"Blender MCP · {self._addr[0]}:{self._addr[1]}",
            state=STATE_DOWN,
            restartable=bool(self._exe or self._cmd),
        )
        self._vw = HostState(
            id="vectorworks",
            label="Vectorworks IPC（外部 runner，supervisor 不探测不重启）",
            state=STATE_EXTERNAL,
            restartable=False,
        )

    # ---------- 探活 ----------
    def probe(self, host_id: str) -> HostState:
        """探测一次并更新状态；VW 恒返回 external（不做伪探测）。"""
        if host_id == "vectorworks":
            return self._vw
        if host_id != "blender":
            raise KeyError(f"未知宿主: {host_id}")
        ok = False
        try:
            with socket.create_connection(self._addr, timeout=self._probe_timeout):
                ok = True
        except OSError:
            ok = False
        with self._lock:
            self._blender.last_probe_at = time.time()
            if ok:
                self._blender.state = STATE_UP
                self._blender.detail = "TCP 探活通过"
                self._blender.restart_count = 0  # 恢复健康后重启计数归零
            elif self._blender.state != STATE_RESTARTING:
                self._blender.state = STATE_DOWN
                self._blender.detail = f"TCP {self._addr[0]}:{self._addr[1]} 不可达"
        return self._blender

    # ---------- 重启 ----------
    def restart(self, host_id: str) -> HostState:
        """有界退避重启；未配置 exe / 超限 / VW 均如实拒绝，不伪造成功。"""
        if host_id == "vectorworks":
            raise ValueError("Vectorworks 为外部 runner，请人工启动宿主后重连（supervisor 不代理其生命周期）")
        if host_id != "blender":
            raise KeyError(f"未知宿主: {host_id}")
        with self._lock:
            if not (self._exe or self._cmd):
                raise ValueError("未配置 OPENBIMAGENT_BLENDER_EXE/CMD，无法自动拉起 Blender（如实拒绝）")
            if self._blender.restart_count >= self._max_restarts:
                self._blender.state = STATE_DOWN
                self._blender.detail = f"重启次数超限（{self._max_restarts}），转人工处理"
                raise ValueError(self._blender.detail)
            self._blender.state = STATE_RESTARTING
            self._blender.restart_count += 1
            count = self._blender.restart_count
        # 线性退避：第 N 次重启前等待 N 秒（避免撞崩溃循环；不阻塞调用方——放后台线程）
        thread = threading.Thread(target=self._restart_worker, args=(count,), daemon=True)
        thread.start()
        return self._blender

    def _restart_worker(self, count: int) -> None:
        time.sleep(min(count, 5))  # 线性退避上限 5s
        if self._cmd:
            import shlex

            # Windows 路径含反斜杠（\t/\f 会被 shlex 转义吞掉）：原样交给 CreateProcess；POSIX 用 shlex
            argv: str | list[str] = self._cmd if os.name == "nt" else shlex.split(self._cmd)
        else:
            argv = [self._exe, "--background", "--factory-startup"]
        try:
            subprocess.Popen(  # noqa: S603 — 命令来自本地 env 配置，非用户输入
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            detail = f"已发起第 {count} 次拉起（headless）"
        except OSError as exc:
            with self._lock:
                self._blender.state = STATE_DOWN
                self._blender.detail = f"拉起失败: {exc}"
            return
        with self._lock:
            self._blender.detail = detail
        self.probe("blender")

    # ---------- 汇总 ----------
    def status(self) -> list[dict[str, Any]]:
        """全部宿主状态（Blender 先探活一次再汇总，保证新鲜）。"""
        self.probe("blender")
        return [self._blender.to_dict(), self._vw.to_dict()]


_SUPERVISOR: HostSupervisor | None = None
_SUPERVISOR_LOCK = threading.Lock()


def default_host_supervisor() -> HostSupervisor:
    """进程级默认 supervisor（测试请自建实例注入假端口）。"""
    global _SUPERVISOR
    with _SUPERVISOR_LOCK:
        if _SUPERVISOR is None:
            _SUPERVISOR = HostSupervisor()
        return _SUPERVISOR


def reset_host_supervisor() -> None:
    """测试隔离：重置进程级实例。"""
    global _SUPERVISOR
    with _SUPERVISOR_LOCK:
        _SUPERVISOR = None
