"""停止运行 + 会话导出端点测试。

停止语义：不杀线程——拒绝该会话全部待决审批票据，pipeline 在审批门处按拒绝路径退出
（确定性求解中段不打断）。导出：jsonl 原始事件流 / md 可读纪要。
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("stop-export")
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
    for key in ("OPENBIMAGENT_SESSIONS_DIR", "OPENBIMAGENT_PENDING_APPROVALS", "OPENBIMAGENT_ARCHIVE_DIR", "OPENBIMAGENT_SKILLS_ROOT"):
        os.environ.pop(key, None)


@pytest.fixture(scope="module")
def finished_session(client: TestClient) -> str:
    """完成一次运行（批准所有门），返回 session_id。"""
    resp = client.post("/api/v1/runs", json={"brief": "导出测试任务", "playbook": "single_asset_hero"})
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        for item in client.get("/api/v1/approvals").json()["items"]:
            if not item.get("expired"):
                client.post(f"/api/v1/approvals/{item['id']}/decide", json={"decision": "approved"})
        run = client.get("/api/v1/runs/active").json()["run"]
        if run and run["session_id"] == sid and not run["active"]:
            return sid
        time.sleep(1.5)
    raise AssertionError("运行 180s 未收敛")


class TestStop:
    def test_stop_unknown_404(self, client: TestClient) -> None:
        assert client.post("/api/v1/runs/no-such-run/stop").status_code == 404

    def test_stop_finished_conflict_409(self, client: TestClient, finished_session: str) -> None:
        resp = client.post(f"/api/v1/runs/{finished_session}/stop")
        assert resp.status_code == 409

    def test_stop_active_run_rejects_pending_gate(self, client: TestClient) -> None:
        resp = client.post("/api/v1/runs", json={"brief": "停止语义测试任务", "playbook": "single_asset_hero"})
        assert resp.status_code == 200
        sid = resp.json()["session_id"]
        # 等运行抵达审批门
        deadline = time.time() + 90
        ticket = None
        while time.time() < deadline:
            items = [i for i in client.get("/api/v1/approvals").json()["items"] if i["session_id"] == sid and not i.get("expired")]
            if items:
                ticket = items[0]
                break
            time.sleep(1.0)
        assert ticket is not None, "90s 内运行未抵达审批门"
        # 停止：唤醒票据按拒绝退出，运行收敛为 inactive
        stop = client.post(f"/api/v1/runs/{sid}/stop")
        assert stop.status_code == 200
        assert stop.json()["woken_approvals"] >= 1
        deadline = time.time() + 60
        while time.time() < deadline:
            run = client.get("/api/v1/runs/active").json()["run"]
            mine = next((r for r in client.get("/api/v1/runs/active").json()["runs"] if r["session_id"] == sid), None)
            if mine and not mine["active"]:
                break
            if run and not run["active"]:
                break
            time.sleep(1.0)
        runs = client.get("/api/v1/runs/active").json()["runs"]
        mine = next((r for r in runs if r["session_id"] == sid), None)
        assert mine is not None and mine["active"] is False and mine.get("stop_requested") is True
        # 审批决策事件落盘（拒绝路径可溯源）
        events = client.get(f"/api/v1/sessions/{sid}/events").json()["events"]
        decided = [e for e in events if (e.get("payload") or {}).get("customType") == "approval_decided"]
        assert decided and decided[-1]["payload"]["decision"] == "rejected"


class TestExport:
    def test_export_jsonl(self, client: TestClient, finished_session: str) -> None:
        resp = client.get(f"/api/v1/sessions/{finished_session}/export?fmt=jsonl")
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]
        assert b'"id"' in resp.content  # 原始事件流逐行 JSON

    def test_export_md_readable(self, client: TestClient, finished_session: str) -> None:
        resp = client.get(f"/api/v1/sessions/{finished_session}/export?fmt=md")
        assert resp.status_code == 200
        text = resp.content.decode("utf-8")
        assert text.startswith(f"# 会话导出 {finished_session}")
        assert "用户" in text or "Agent" in text

    def test_export_bad_fmt_and_unknown(self, client: TestClient, finished_session: str) -> None:
        assert client.get(f"/api/v1/sessions/{finished_session}/export?fmt=pdf").status_code == 400
        assert client.get("/api/v1/sessions/no-such-session/export").status_code == 404
