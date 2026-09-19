"""收尾批 B 测试:B8 子树溯源、F8 组织锁定、C7 path-scoped 规则、C8 /context。"""

from __future__ import annotations

import os
from pathlib import Path

from openbimagent.core.loop import AgentLoop
from openbimagent.core.path_scoped_rules import rule_for
from openbimagent.core.requirements import is_org_denied, refresh_requirements
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


# ---------- B8 ----------

def test_find_children_returns_registered_child_sessions(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    parent = SessionStore.create(sessions, title="parent")
    child = SessionStore.create(sessions, title="child")
    child.mark_child_of(
        parent_session_id=parent.session_id,
        parent_event_id=None,
        request_id="req-1",
        agent_id="agent-1",
        role="modeler",
    )
    found = SessionStore.find_children(sessions, parent.session_id)
    assert [c["id"] for c in found] == [child.session_id]
    assert found[0]["child_of"]["role"] == "modeler"
    assert SessionStore.find_children(sessions, "no-such-parent") == []


# ---------- F8 ----------

def test_org_requirements_force_deny_overrides_role(tmp_path: Path, monkeypatch) -> None:
    req = tmp_path / "requirements.toml"
    req.write_text('[deny]\ntools = ["bash:rm -rf *"]\n', encoding="utf-8")
    monkeypatch.setenv("OPENBIMAGENT_REQUIREMENTS_FILE", str(req))
    refresh_requirements()
    assert is_org_denied("bash:rm -rf /tmp") is True
    assert is_org_denied("bash:echo hi") is False
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    loop = AgentLoop(
        ["bash"], session, chat_fn=lambda *a, **k: {"content": "x"}, workdir=tmp_path,
        permission_rules={"bash": "allow"}, approval_callback=lambda name, args: True,
    )
    result = loop._dispatch("bash", {"command": "rm -rf /tmp/x"})
    assert result["status"] == "denied"
    assert result["ui_view"]["org_locked"] is True
    refresh_requirements()  # 清缓存,避免污染后续测试


def test_org_requirements_absent_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("OPENBIMAGENT_REQUIREMENTS_FILE", raising=False)
    refresh_requirements()
    assert is_org_denied("bash:anything") is False


# ---------- C7 ----------

def test_path_scoped_rules_match_extensions() -> None:
    assert rule_for("model.vwx").startswith("Vectorworks")
    assert rule_for("def.gh").startswith("Grasshopper")
    assert rule_for("building.ifc").startswith("IFC")
    assert rule_for("notes.txt") is None


def test_read_injects_path_scoped_rule(tmp_path: Path) -> None:
    session = SessionStore(tmp_path / "s.jsonl", title="t")
    loop = AgentLoop(["read"], session, chat_fn=lambda *a, **k: {"content": "x"}, workdir=tmp_path)
    target = tmp_path / "model.ifc"
    target.write_text("#IFC content", encoding="utf-8")
    result = loop._dispatch("read", {"path": str(target)})
    assert result["status"] == "ok"
    assert "[path-scoped 规则]" in result["llm_view"]
    assert "IFC4X3" in result["llm_view"]
