"""P1-2 Hooks 生命周期事件总线测试。

验证：pre_tool 可否决（返回 False 或 handler 崩溃均 fail-closed 阻断调用）、post_tool
在成功与异常路径都触发、观测型 handler 异常被隔离、触发次序正确、ring buffer 有界、
run_end 在真实运行 finally 触发。
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from openbimagent.core.hooks import HookBus, HookVeto, default_hook_bus, reset_hook_bus
from openbimagent.core.plugin import PluginRegistry


@pytest.fixture(autouse=True)
def _clean_bus():
    reset_hook_bus()
    yield
    reset_hook_bus()


class TestHookBusSemantics:
    def test_unknown_event_rejected(self) -> None:
        bus = HookBus()
        with pytest.raises(ValueError, match="未知 hook"):
            bus.register("nope", lambda **_: None)
        with pytest.raises(ValueError, match="不可否决"):
            bus.check("post_tool")
        with pytest.raises(ValueError, match="可否决语义"):
            bus.emit("pre_tool")

    def test_pre_tool_veto_blocks_and_records(self) -> None:
        bus = HookBus()
        bus.register("pre_tool", lambda **_: False)
        with pytest.raises(HookVeto, match="否决"):
            bus.check("pre_tool", capability="solver:self_healing")
        assert bus.fired[-1]["outcome"].startswith("veto")

    def test_pre_tool_handler_crash_is_fail_closed(self) -> None:
        bus = HookBus()

        def _crash(**_):  # handler 崩溃 = 否决（fail-closed）
            raise RuntimeError("boom")

        bus.register("pre_tool", _crash)
        with pytest.raises(HookVeto, match="fail-closed"):
            bus.check("pre_tool", capability="x")

    def test_observational_handler_error_isolated(self) -> None:
        bus = HookBus()
        seen: list[str] = []

        def _bad(**_):
            raise RuntimeError("观测handler炸了")

        bus.register("post_tool", _bad)
        bus.register("post_tool", lambda **_: seen.append("second"))
        bus.emit("post_tool", capability="x")  # 不抛异常
        assert seen == ["second"]  # 后续 handler 仍执行
        assert "handler_errors" in bus.fired[-1]["outcome"]

    def test_ring_buffer_bounded(self) -> None:
        bus = HookBus()
        for i in range(250):
            bus.emit("run_end", seq=i)
        assert len(bus.fired) == 200


class TestRegistryInvokeWiring:
    """真实注册表接线：pre/post_tool 随 invoke 触发，否决真实阻断。"""

    @pytest.fixture()
    def registry(self) -> PluginRegistry:
        from openbimagent.core.plugin import BIMPlugin

        class _Echo(BIMPlugin):
            plugin_id = "plugin.test.echo"
            name = "回声"
            version = "1.0.0"
            description = "测试"
            provides_capabilities = ("test:echo",)

            def setup(self, ctx) -> None:
                super().setup(ctx)
                self.register_handler("test:echo", lambda text="": {"echo": text})

        reg = PluginRegistry()
        reg.register(_Echo())
        return reg

    def test_invoke_fires_pre_then_post(self, registry: PluginRegistry) -> None:
        bus = default_hook_bus()
        order: list[str] = []
        bus.register("pre_tool", lambda **_: order.append("pre"))
        bus.register("post_tool", lambda **_: order.append("post"))
        result = registry.invoke("test:echo", text="hi")
        assert result == {"echo": "hi"}
        assert order == ["pre", "post"]

    def test_veto_blocks_real_invoke(self, registry: PluginRegistry) -> None:
        bus = default_hook_bus()
        bus.register("pre_tool", lambda capability=(), **_: False)
        with pytest.raises(HookVeto):
            registry.invoke("test:echo", text="hi")
        # 被否决：只有 pre_tool 记录，无 post_tool
        events = [f["event"] for f in bus.fired]
        assert "post_tool" not in events

    def test_post_tool_fires_on_failure(self, registry: PluginRegistry) -> None:
        bus = default_hook_bus()
        errors: list[str] = []
        bus.register("post_tool", lambda **kw: errors.append(kw.get("error", "")))
        with pytest.raises(TypeError):
            registry.invoke("test:echo", wrong_kwarg=1)
        assert errors and "wrong_kwarg" in errors[0]


class TestRunEndHook:
    @pytest.fixture(scope="class")
    def client(self, tmp_path_factory: pytest.TempPathFactory) -> TestClient:
        tmp = tmp_path_factory.mktemp("hooks-run")
        os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
        os.environ["OPENBIMAGENT_SESSIONS_DIR"] = str(tmp / "sessions")
        os.environ["OPENBIMAGENT_PENDING_APPROVALS"] = str(tmp / "pending.json")
        os.environ["OPENBIMAGENT_ARCHIVE_DIR"] = str(tmp / "archive")
        os.environ["OPENBIMAGENT_SKILLS_ROOT"] = str(tmp / "skills")
        from openbimagent.server.fastapi_app import build_demo_app

        class _RidClient(TestClient):
            def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
                headers = dict(kwargs.pop("headers", {}) or {})
                headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
                headers.setdefault("Authorization", "Bearer test-wb-token")
                return super().request(method, url, headers=headers, **kwargs)

        yield _RidClient(build_demo_app())
        os.environ.pop("OPENBIMAGENT_SESSIONS_DIR", None)
        os.environ.pop("OPENBIMAGENT_PENDING_APPROVALS", None)
        os.environ.pop("OPENBIMAGENT_ARCHIVE_DIR", None)
        os.environ.pop("OPENBIMAGENT_SKILLS_ROOT", None)

    def test_run_end_fires_after_run(self, client: TestClient) -> None:
        bus = default_hook_bus()
        seen: list[str] = []
        bus.register("run_end", lambda **kw: seen.append(kw.get("session_id", "")))
        resp = client.post("/api/v1/runs", json={"brief": "hooks run_end 测试", "playbook": "municipal_utility"})
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        deadline = time.time() + 180
        while time.time() < deadline:
            for item in client.get("/api/v1/approvals").json()["items"]:
                if not item.get("expired"):
                    client.post(f"/api/v1/approvals/{item['id']}/decide", json={"decision": "approved"})
            run = client.get("/api/v1/runs/active").json()["run"]
            if run and not run["active"]:
                break
            time.sleep(1.5)
        deadline = time.time() + 10  # run_end 在 finally 触发，略等落定
        while time.time() < deadline and session_id not in seen:
            time.sleep(0.3)
        assert session_id in seen
