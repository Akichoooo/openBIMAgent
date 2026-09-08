"""F2：回退源冻结校验——frontend/dist 存在时 server 必须优先伺服 React SPA，
dist 缺失才回退内嵌单文件 Franken UI（防止"权威源倒退"，见方案 F2）。"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBIMAGENT_WORKBENCH_TOKEN", "test-wb-token")

from fastapi.testclient import TestClient  # noqa: E402

import openbimagent.server.web_ui as web_ui  # noqa: E402
from openbimagent.server.fastapi_app import build_demo_app  # noqa: E402


def _make_spa_dist(tmp_path):
    """构造一个最小 React SPA dist（含 #root 挂载点 + token 注入占位）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>openBIMAgent</title></head><body>'
        '<div id="root"></div>'
        '<script>window.__WB_TOKEN = window.__WB_TOKEN || "";</script>'
        '<script type="module" src="/assets/index-abc.js"></script></body></html>',
        encoding="utf-8",
    )
    return dist


def test_serves_spa_when_dist_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(web_ui, "_FRONTEND_DIST", _make_spa_dist(tmp_path))
    client = TestClient(build_demo_app())
    resp = client.get("/")
    assert resp.status_code == 200
    # 权威源：伺服 React SPA（含 #root 挂载点），而非内嵌 Franken UI
    assert 'id="root"' in resp.text
    # token 注入到 SPA
    assert "test-wb-token" in resp.text


def test_falls_back_to_embedded_when_dist_absent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(web_ui, "_FRONTEND_DIST", tmp_path / "no-such-dist")
    client = TestClient(build_demo_app())
    resp = client.get("/")
    assert resp.status_code == 200
    # dist 缺失 → 回退内嵌单文件工作台（非 React SPA，无 #root）
    assert 'id="root"' not in resp.text
    assert len(resp.text) > 1000  # 内嵌 PAGE 是大体量单文件
