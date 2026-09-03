"""P0-1 Skill 系统测试：SKILL.md 加载校验 / 多源发现 / 渐进披露 / 自蒸馏候选·人工批准流转。"""

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbimagent.skills.registry import (
    SkillRegistry,
    builtin_skills_root,
    candidates_dir,
    distill_candidate,
    load_skill,
    reload_skills,
)


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "正文") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return path


class TestLoader:
    """失败关闭：坏文件拒载且不拖垮全局。"""

    def test_missing_required_key_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "no-name", "description: 缺 name")
        reg = SkillRegistry(extra_dirs=[tmp_path])
        assert all(s["name"] != "no-name" for s in reg.catalog())
        assert any("name" in r["error"] for r in reg.rejected)

    def test_illegal_name_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "BadName", "name: BadName!\ndescription: 非法 slug")
        reg = SkillRegistry(extra_dirs=[tmp_path])
        assert all("BadName" not in s["name"] for s in reg.catalog())
        assert reg.rejected

    def test_empty_body_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "empty-body", "name: empty-body\ndescription: 正文为空", body="")
        reg = SkillRegistry(extra_dirs=[tmp_path])
        assert all(s["name"] != "empty-body" for s in reg.catalog())

    def test_no_frontmatter_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "plain" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("# 没有 frontmatter\n", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            load_skill(path, source="external")


class TestDiscovery:
    def test_discovers_builtin_and_extra(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "extra-skill", "name: extra-skill\ndescription: 外部技能")
        reg = SkillRegistry(extra_dirs=[tmp_path])
        names = [s["name"] for s in reg.catalog()]
        assert "extra-skill" in names
        # 仓库内置两个真实技能
        assert "municipal-gravity-brief" in names
        assert "ir-inspection" in names

    def test_candidates_never_auto_active(self, tmp_path: Path) -> None:
        cand = distill_candidate(tmp_path, session_id="abcdef1234567890", brief="DN400 测试", playbook="municipal_utility", files=["a.json"], archived_at="now")
        assert cand is not None and cand.parent.name == "_candidates"
        reg = SkillRegistry(extra_dirs=[tmp_path])
        assert all(s["source"] != "distilled" for s in reg.catalog())

    def test_catalog_fragment_progressive_disclosure(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "frag-skill", "name: frag-skill\ndescription: 目录可见", body="正文只在调用时披露 XYZSECRET")
        reg = SkillRegistry(extra_dirs=[tmp_path])
        fragment = reg.catalog_fragment()
        assert "frag-skill" in fragment and "目录可见" in fragment
        assert "XYZSECRET" not in fragment  # 目录态不含正文（上下文成本按使用付费）


class TestDistill:
    def test_distill_output_passes_loader(self, tmp_path: Path) -> None:
        cand = distill_candidate(tmp_path, session_id="s1abcdefgh", brief="DN400 污水管", playbook="municipal_utility", files=["compiled_utility_ir.json"], archived_at="2026-09-04T00:00:00Z")
        assert cand is not None
        skill = load_skill(cand, source="distilled")
        assert skill.name and skill.description and skill.body
        assert "禁止照抄" in skill.body  # C2 铁律提醒写入模板

    def test_distill_empty_brief_noop(self, tmp_path: Path) -> None:
        assert distill_candidate(tmp_path, session_id="s1", brief="  ", playbook="p", files=[], archived_at="t") is None


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("skills-api")
    os.environ["OPENBIMAGENT_WORKBENCH_TOKEN"] = "test-wb-token"
    os.environ["OPENBIMAGENT_SKILLS_ROOT"] = str(tmp / "skills")
    reload_skills()  # 全局注册表可能已被先前模块按旧 root 实例化，强制按新 env 重建
    from openbimagent.server.fastapi_app import build_demo_app

    class _RidClient(TestClient):
        def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-Request-ID", f"test-{uuid.uuid4().hex[:16]}")
            headers.setdefault("Authorization", "Bearer test-wb-token")
            return super().request(method, url, headers=headers, **kwargs)

    yield _RidClient(build_demo_app())
    os.environ.pop("OPENBIMAGENT_SKILLS_ROOT", None)
    reload_skills()


class TestSkillEndpoints:
    def test_list_skills_catalog(self, client: TestClient) -> None:
        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        names = [s["name"] for s in data["skills"]]
        # 沙箱 root 为空目录：目录为空但端点正常；candidates/rejected 字段齐备
        assert isinstance(names, list) and "candidates" in data and "rejected" in data

    def test_invoke_returns_body_and_404(self, client: TestClient) -> None:
        root = builtin_skills_root()
        _write_skill(root, "api-skill", "name: api-skill\ndescription: 端点调用", body="完整正文 BODY-OK")
        reload_skills()
        resp = client.post("/api/v1/skills/invoke", json={"name": "api-skill"})
        assert resp.status_code == 200
        assert "BODY-OK" in resp.json()["skill"]["body"]  # 调用才披露正文
        missing = client.post("/api/v1/skills/invoke", json={"name": "no-such-skill"})
        assert missing.status_code == 404

    def test_candidate_approve_flow(self, client: TestClient) -> None:
        root = builtin_skills_root()
        cand = distill_candidate(root, session_id="deadbeefcafe1234", brief="DN300 雨水管", playbook="municipal_utility", files=["PLAN.md"], archived_at="t")
        assert cand is not None
        filename = cand.name
        # 候选出现在列表但未生效
        listing = client.get("/api/v1/skills").json()
        assert filename in listing["candidates"]
        skill_name = load_skill(cand, source="distilled").name
        assert all(s["name"] != skill_name for s in listing["skills"])
        # 批准：转正 + 文件移动 + reload 生效
        ok = client.post("/api/v1/skills/candidates/approve", json={"file": filename})
        assert ok.status_code == 200, ok.text
        assert ok.json()["approved"] == skill_name
        assert not cand.is_file()
        assert (root / skill_name / "SKILL.md").is_file()
        after = client.get("/api/v1/skills").json()
        assert any(s["name"] == skill_name for s in after["skills"])
        assert filename not in after["candidates"]
        # 重复批准 → 404（候选已消费）
        again = client.post("/api/v1/skills/candidates/approve", json={"file": filename})
        assert again.status_code == 404

    def test_approve_rejects_path_traversal(self, client: TestClient) -> None:
        resp = client.post("/api/v1/skills/candidates/approve", json={"file": "../evil.md"})
        assert resp.status_code == 400

    def test_candidates_dir_helper(self) -> None:
        assert candidates_dir() == builtin_skills_root() / "_candidates"
