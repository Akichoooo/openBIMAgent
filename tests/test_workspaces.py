"""workspaces 端点测试：工作区注册表 + 会话归属盖章。

隔离方式：OPENBIMAGENT_WORKSPACES_FILE / OPENBIMAGENT_SESSIONS_DIR 指向 tmp_path，
不碰真实 out/workspaces.json 与会话索引。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "llm_baseline.local.toml"))
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENBIMAGENT_WORKSPACES_FILE", str(tmp_path / "workspaces.json"))
    monkeypatch.setenv("OPENBIMAGENT_USAGE_LOG", str(tmp_path / "usage_log.jsonl"))
    from openbimagent.server.fastapi_app import build_demo_app

    c = TestClient(build_demo_app())
    c.headers["Authorization"] = "Bearer test-wb-token"
    return c


def _create(client: TestClient, path: Path, name: str = "") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name, "path": str(path)})
    assert resp.status_code == 200, resp.text
    return resp.json()["item"]


def test_list_empty(client: TestClient) -> None:
    body = client.get("/api/v1/workspaces").json()
    assert body["status"] == "success"
    assert body["current"] is None
    assert body["items"] == []


def test_create_registers_and_makes_dir(client: TestClient, tmp_path: Path) -> None:
    target = tmp_path / "my-project"
    item = _create(client, target)
    assert target.is_dir()  # 路径不存在时自动创建
    assert item["name"] == "my-project"  # name 缺省取目录名
    assert item["id"].startswith("ws-")
    body = client.get("/api/v1/workspaces").json()
    assert len(body["items"]) == 1


def test_create_same_path_reuses_id(client: TestClient, tmp_path: Path) -> None:
    first = _create(client, tmp_path / "proj")
    second = _create(client, tmp_path / "proj", name="改名项目")
    assert first["id"] == second["id"]
    body = client.get("/api/v1/workspaces").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "改名项目"


def test_create_requires_path(client: TestClient) -> None:
    resp = client.post("/api/v1/workspaces", json={"name": "x", "path": "  "})
    assert resp.status_code == 400


def test_set_and_clear_current(client: TestClient, tmp_path: Path) -> None:
    item = _create(client, tmp_path / "proj")
    resp = client.post("/api/v1/workspaces/current", json={"id": item["id"]})
    assert resp.status_code == 200
    assert client.get("/api/v1/workspaces").json()["current"] == item["id"]
    # 「不在项目中工作」= 置 null
    resp = client.post("/api/v1/workspaces/current", json={"id": None})
    assert resp.status_code == 200
    assert client.get("/api/v1/workspaces").json()["current"] is None


def test_set_current_unknown_id_404(client: TestClient) -> None:
    resp = client.post("/api/v1/workspaces/current", json={"id": "ws-nope"})
    assert resp.status_code == 404


def test_delete_removes_and_clears_current(client: TestClient, tmp_path: Path) -> None:
    item = _create(client, tmp_path / "proj")
    client.post("/api/v1/workspaces/current", json={"id": item["id"]})
    resp = client.delete(f"/api/v1/workspaces/{item['id']}")
    assert resp.status_code == 200
    body = client.get("/api/v1/workspaces").json()
    assert body["items"] == []
    assert body["current"] is None
    assert (tmp_path / "proj").is_dir()  # 不删磁盘目录


def test_stamp_session_workspace_flows_to_list(client: TestClient, tmp_path: Path) -> None:
    from openbimagent.server.workspaces import stamp_session_workspace
    from openbimagent.session.store import SessionStore

    item = _create(client, tmp_path / "proj")
    client.post("/api/v1/workspaces/current", json={"id": item["id"]})
    store = SessionStore(tmp_path / "sessions" / "sess-ws-1.jsonl", title="ws 测试", playbook="municipal_utility")
    stamp_session_workspace(store.session_id, item["id"])

    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200, resp.text
    sessions = resp.json()["data"]["items"]
    target = next(s for s in sessions if s["session_id"] == store.session_id)
    assert target["workspace"] == item["id"]
    # 会话计数随列表带出
    body = client.get("/api/v1/workspaces").json()
    assert body["items"][0]["session_count"] == 1


def test_registry_persists_on_disk(client: TestClient, tmp_path: Path) -> None:
    _create(client, tmp_path / "proj")
    data = json.loads((tmp_path / "workspaces.json").read_text(encoding="utf-8"))
    assert data["items"] and data["current"] is None


def test_update_workspace_security_preset(client: TestClient, tmp_path: Path) -> None:
    item = _create(client, tmp_path / "proj")
    assert item["execution_mode"] == "agent"

    # 更新执行模式为自主模式 yolo
    patch_resp = client.patch(
        f"/api/v1/workspaces/{item['id']}",
        json={
            "name": "Docker Project",
            "execution_mode": "yolo",
            "outside_file_access": "deny",
            "terminal_auto_exec": "proceed",
            "artifact_review_policy": "proceed",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()["item"]
    assert updated["name"] == "Docker Project"
    assert updated["execution_mode"] == "yolo"
    assert updated["outside_file_access"] == "deny"
    assert updated["terminal_auto_exec"] == "proceed"
    assert updated["artifact_review_policy"] == "proceed"

    # 404 test
    resp404 = client.patch("/api/v1/workspaces/ws-unknown", json={"name": "none"})
    assert resp404.status_code == 404

