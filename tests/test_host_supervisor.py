"""P0-3 宿主 Supervisor + 工具集预设测试。

Supervisor 用真实 TCP 监听模拟 Blender MCP（不 mock socket 库本身）；重启集成测试
用 Python 子进程扮演假 Blender（真实拉起、真实探活）。工具集预设验证双层过滤
（清单可见面 + invoke 调用门 403 fail-closed）。
"""

import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbimagent.core.toolset import current_toolset, filter_capabilities, is_allowed, reset_toolset, set_toolset
from openbimagent.mcp_clients.supervisor import STATE_DOWN, STATE_EXTERNAL, STATE_RESTARTING, STATE_UP, HostSupervisor


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TcpServer:
    """最小 TCP 监听（扮演 Blender MCP 端口）。"""

    def __init__(self, port: int) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(4)
        self._srv.settimeout(0.3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
                conn.close()
            except socket.timeout:
                pass
            except OSError:
                break

    def close(self) -> None:
        self._stop.set()
        self._srv.close()
        self._thread.join(timeout=2)


class TestProbe:
    def test_probe_up_then_down(self) -> None:
        port = _free_port()
        sup = HostSupervisor(blender_port=port)
        assert sup.probe("blender").state == STATE_DOWN  # 未起服务：如实 down
        server = _TcpServer(port)
        try:
            st = sup.probe("blender")
            assert st.state == STATE_UP and st.last_probe_at is not None
        finally:
            server.close()
        time.sleep(0.2)
        assert sup.probe("blender").state == STATE_DOWN  # 服务消失：如实回落 down

    def test_vw_always_external_and_unrestartable(self) -> None:
        sup = HostSupervisor(blender_port=_free_port())
        vw = sup.probe("vectorworks")
        assert vw.state == STATE_EXTERNAL and vw.restartable is False
        with pytest.raises(ValueError, match="外部 runner"):
            sup.restart("vectorworks")

    def test_unknown_host(self) -> None:
        sup = HostSupervisor(blender_port=_free_port())
        with pytest.raises(KeyError):
            sup.probe("autocad")


class TestRestart:
    def test_restart_refused_without_exe_or_cmd(self) -> None:
        sup = HostSupervisor(blender_port=_free_port(), blender_exe="", blender_cmd="")
        with pytest.raises(ValueError, match="OPENBIMAGENT_BLENDER_EXE"):
            sup.restart("blender")

    def test_restart_max_attempts_then_refuse(self, tmp_path: Path) -> None:
        sup = HostSupervisor(
            blender_port=_free_port(),
            blender_cmd=f"{sys.executable} -c \"import time; time.sleep(0.1)\"",  # 拉起即退（探活必失败）
            max_restarts=2,
        )
        sup.restart("blender")
        assert sup.status()[0]["state"] in (STATE_RESTARTING, STATE_DOWN)
        time.sleep(2.5)  # 等第一次退避+拉起失败落定
        sup.restart("blender")  # 第二次
        time.sleep(3.5)
        with pytest.raises(ValueError, match="重启次数超限"):
            sup.restart("blender")  # 第三次：超限如实拒绝
        assert sup.status()[0]["state"] == STATE_DOWN

    def test_restart_with_fake_blender_becomes_up(self, tmp_path: Path) -> None:
        port = _free_port()
        fake = tmp_path / "fake_blender.py"
        fake.write_text(
            "import socket, sys, time\n"
            "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
            "srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "srv.bind(('127.0.0.1', port)); srv.listen(4); srv.settimeout(0.3)\n"
            "deadline = time.time() + 30\n"
            "while time.time() < deadline:\n"
            "    try:\n"
            "        conn, _ = srv.accept(); conn.close()\n"
            "    except socket.timeout:\n"
            "        pass\n",
            encoding="utf-8",
        )
        sup = HostSupervisor(blender_port=port, blender_cmd=f"{sys.executable} {fake} --port {port}")
        assert sup.probe("blender").state == STATE_DOWN
        sup.restart("blender")
        deadline = time.time() + 10
        state = sup.probe("blender").state
        while time.time() < deadline and state != STATE_UP:
            time.sleep(0.5)
            state = sup.probe("blender").state
        assert state == STATE_UP  # 假 Blender 真被拉起且探活通过
        assert sup.status()[0]["restart_count"] == 0  # 恢复健康后计数归零


class TestToolsetUnit:
    def setup_method(self) -> None:
        reset_toolset()

    def teardown_method(self) -> None:
        reset_toolset()

    def test_default_full_allows_everything(self) -> None:
        assert current_toolset() == "full"
        assert is_allowed("cad_host:blender.execute") and is_allowed("solver:self_healing")

    def test_minimal_blocks_host_capabilities(self) -> None:
        set_toolset("minimal")
        assert is_allowed("solver:grid_route")
        assert not is_allowed("cad_host:blender.execute")
        cmap = filter_capabilities({"solver:self_healing": "p1", "cad_host:blender": "p2"})
        assert list(cmap) == ["solver:self_healing"]

    def test_modeling_allows_hosts(self) -> None:
        set_toolset("modeling")
        assert is_allowed("cad_host:vectorworks.execute")

    def test_unknown_preset_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知工具集预设"):
            set_toolset("everything")


@pytest.fixture(scope="module")
def client() -> TestClient:
    os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            headers.setdefault("Authorization", "Bearer test-wb-token")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    reset_toolset()
    # 注意：不 pop OPENBIMAGENT_WORKBENCH_TOKEN——test_m2_fastapi 在 import 期 setdefault 依赖它存活


class TestToolsetEndpoints:
    def test_get_and_put_toolset(self, client: TestClient) -> None:
        resp = client.get("/api/v1/toolset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == "full" and set(data["presets"]) == {"minimal", "modeling", "full"}
        ok = client.put("/api/v1/toolset", json={"name": "minimal"})
        assert ok.status_code == 200 and ok.json()["current"] == "minimal"
        bad = client.put("/api/v1/toolset", json={"name": "nope"})
        assert bad.status_code == 400
        client.put("/api/v1/toolset", json={"name": "full"})

    def test_invoke_gate_and_inventory_filter(self, client: TestClient) -> None:
        client.put("/api/v1/toolset", json={"name": "minimal"})
        try:
            # 调用门：cad_host 被滤 → 403
            blocked = client.post("/api/v1/plugins/invoke", json={"capability": "cad_host:blender.execute", "payload": {}})
            assert blocked.status_code == 403
            assert "minimal" in blocked.json()["error"]
            # solver 放行（能力不存在也过了预设门，由注册表报未知能力）
            allowed = client.post("/api/v1/plugins/invoke", json={"capability": "solver:no_such", "payload": {}})
            assert allowed.status_code == 200
            # 清单可见面同步过滤
            inv = client.get("/api/v1/plugins").json()
            assert inv["toolset"] == "minimal"
            assert all(k.startswith("solver:") for k in inv["capabilities_map"])
        finally:
            client.put("/api/v1/toolset", json={"name": "full"})

    def test_hosts_endpoint_supervisor_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/hosts")
        assert resp.status_code == 200
        hosts = {h["id"]: h for h in resp.json()["hosts"]}
        assert hosts["blender"]["state"] in ("up", "down", "restarting")
        assert hosts["blender"]["connected"] in (True, False)
        assert hosts["vectorworks"]["state"] == "external"
        assert hosts["vectorworks"]["connected"] is None  # 诚实标记：不探测
        bad = client.post("/api/v1/hosts/autocad/restart")
        assert bad.status_code == 404
        vw = client.post("/api/v1/hosts/vectorworks/restart")
        assert vw.status_code == 400 and "外部 runner" in vw.json()["error"]
