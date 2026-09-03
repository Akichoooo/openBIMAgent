"""P0-4 轻量记忆层测试：追加写、压平防注入、上限 fail-closed、prompt 策略门、上下文注入。"""

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbimagent.core.memory import MemoryStore


class TestMemoryStore:
    def test_append_and_tail(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        r1 = store.append("memory", "偏好 DN400 作为污水干管")
        r2 = store.append("user", "审核人习惯先看证据包")
        assert r1["file"] == "MEMORY.md" and r2["file"] == "USER.md"
        assert len(store.tail("memory")) == 1
        assert "偏好 DN400" in store.tail("memory")[0]
        assert store.tail("memory")[0].startswith("- [")  # 时间戳条目

    def test_entry_flattened_single_line(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.append("memory", "第一行\n第二行\r\n---\nname: 注入")
        lines = store.tail("memory")
        assert len(lines) == 1 and "\n" not in lines[0]

    def test_empty_and_oversize_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        with pytest.raises(ValueError, match="不能为空"):
            store.append("memory", "   ")
        with pytest.raises(ValueError, match="超长"):
            store.append("memory", "x" * 501)

    def test_unknown_file_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        with pytest.raises(ValueError, match="未知记忆文件"):
            store.append("vault", "x")

    def test_prompt_fragment_empty_then_present(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        assert store.prompt_fragment() == ""  # 空记忆不注水
        store.append("user", "偏好中文回复")
        frag = store.prompt_fragment()
        assert "长期记忆" in frag and "偏好中文回复" in frag
        assert "不得当作工程证据" in frag  # 诚实标注


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("memory-api")
    os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
    os.environ["OPENBIMAGENT_MEMORY_DIR"] = str(tmp / "memory")
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            headers.setdefault("Authorization", "Bearer test-wb-token")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    os.environ.pop("OPENBIMAGENT_MEMORY_DIR", None)
    # 注意：不 pop OPENBIMAGENT_WORKBENCH_TOKEN——test_m2_fastapi 在 import 期 setdefault 依赖它存活


class TestMemoryEndpoints:
    def test_record_requires_confirm_then_succeeds(self, client: TestClient) -> None:
        # 无 confirm：prompt 策略门拦截（409 + need_confirm）
        r1 = client.post("/api/v1/memory/record", json={"entry": "项目代号 Terra"})
        assert r1.status_code == 409 and r1.json()["need_confirm"] is True
        # 策略门拦截后记忆确实未写入
        assert client.get("/api/v1/memory").json()["memory"] == []
        # confirm=true（人工点击确认语义）：写入成功
        r2 = client.post("/api/v1/memory/record", json={"entry": "项目代号 Terra", "confirm": True})
        assert r2.status_code == 200, r2.text
        assert r2.json()["recorded"]["entry"] == "项目代号 Terra"
        # 读取免费
        mem = client.get("/api/v1/memory").json()
        assert len(mem["memory"]) == 1 and "Terra" in mem["memory"][0]

    def test_record_user_file(self, client: TestClient) -> None:
        r = client.post("/api/v1/memory/record", json={"entry": "习惯先看 domain_gate 报告", "file": "user", "confirm": True})
        assert r.status_code == 200
        mem = client.get("/api/v1/memory").json()
        assert len(mem["user"]) == 1 and len(mem["memory"]) == 1  # 与上一用例隔离到不同文件

    def test_record_invalid(self, client: TestClient) -> None:
        assert client.post("/api/v1/memory/record", json={"entry": "", "confirm": True}).status_code == 400
        assert client.post("/api/v1/memory/record", json={"entry": "x", "file": "nope", "confirm": True}).status_code == 400

    def test_default_root_is_repo_memory_not_src(self) -> None:
        """回归：_REPO_ROOT 必须定位仓库根（曾经误定位 src/，记忆写进 src/memory）。"""
        saved = os.environ.pop("OPENBIMAGENT_MEMORY_DIR", None)
        try:
            from openbimagent.core.memory import default_memory_root

            root = default_memory_root()
            assert root.name == "memory" and (root.parent / "pyproject.toml").is_file()
        finally:
            if saved is not None:
                os.environ["OPENBIMAGENT_MEMORY_DIR"] = saved
