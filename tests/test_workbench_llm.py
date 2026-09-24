"""Web run LLM registry 适配器 + 离线 IFC 交付层测试。

隔离：OPENBIMAGENT_LLM_BASELINE / OPENBIMAGENT_CUSTOM_PROVIDERS 指向 tmp_path，
保证 _resolve_lll 不触达本机真实配置（chat 调用本身不触网——只测不可解析路径与
registry 存在性）；IFC 交付用 solved_payload 全离线构造（与 test_ifc_ids_delivery 同源）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_compiled_utility_ir import solved_payload


@pytest.fixture(autouse=True)
def _isolated_llm_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "baseline.none.toml"))
    monkeypatch.setenv("OPENBIMAGENT_CUSTOM_PROVIDERS", str(tmp_path / "providers.none.json"))


def test_workbench_registry_none_without_usable_config() -> None:
    from openbimagent.server.workbench_llm import workbench_registry

    assert workbench_registry() is None


def test_workbench_registry_resolved_with_baseline(tmp_path: Path) -> None:
    (tmp_path / "b.toml").write_text(
        'model = "m1"\nbase_url = "https://x.invalid/v1"\napi_key = "k"\n', encoding="utf-8"
    )
    import os

    os.environ["OPENBIMAGENT_LLM_BASELINE"] = str(tmp_path / "b.toml")
    from openbimagent.server.workbench_llm import workbench_registry

    assert workbench_registry() is not None


def test_registry_chat_fails_closed_when_unresolvable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = tmp_path / "b.toml"
    baseline.write_text('model = "m1"\nbase_url = "https://x.invalid/v1"\napi_key = "k"\n', encoding="utf-8")
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(baseline))
    from openbimagent.server.workbench_llm import WorkbenchChatRegistry, workbench_registry

    assert workbench_registry() is not None
    # chat 每次调用重读配置：配置中途失效必须清晰报错（由 planner 侧降级模板），不静默
    monkeypatch.setenv("OPENBIMAGENT_LLM_BASELINE", str(tmp_path / "gone.toml"))
    with pytest.raises(RuntimeError, match="未配置可用模型"):
        WorkbenchChatRegistry().chat("planner", [{"role": "user", "content": "x"}])


def test_run_llm_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbimagent.server.runs import _run_llm_enabled

    assert _run_llm_enabled() is True  # 缺省开
    monkeypatch.setenv("OPENBIMAGENT_RUN_LLM", "0")
    assert _run_llm_enabled() is False
    monkeypatch.setenv("OPENBIMAGENT_RUN_LLM", "false")
    assert _run_llm_enabled() is False


def _write_gate(out: Path, payload: dict) -> None:
    (out / "domain_gate_report.json").write_text(json.dumps(payload), encoding="utf-8")


def test_deliver_offline_ifc_pass(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "compiled_utility_ir.json").write_text(json.dumps(solved_payload()), encoding="utf-8")
    (out / "municipal_rule_set.json").write_text('{"rules": []}', encoding="utf-8")
    _write_gate(out, {"status": "pass", "ok": True})

    from openbimagent.server.runs import _deliver_offline_ifc
    from openbimagent.session.store import SessionStore

    store = SessionStore(tmp_path / "s.jsonl", title="t")
    info = _deliver_offline_ifc(out, store)

    assert info is not None and info["status"] == "pass"
    assert info["checked_entity_count"] > 0
    for name in ("municipal_utility.ifc", "municipal_utility.ids", "ifc_ids_validation_report.json"):
        assert (out / name).is_file(), f"缺交付产物 {name}"
    customs = [e.payload for e in store.load() if e.type.value == "custom"]
    ifc_events = [p for p in customs if str(getattr(p, "customType", "")) == "ifc_delivery"]
    assert len(ifc_events) == 1 and ifc_events[0].status == "pass"


def test_deliver_offline_ifc_skips_when_gate_fails(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "compiled_utility_ir.json").write_text(json.dumps(solved_payload()), encoding="utf-8")
    (out / "municipal_rule_set.json").write_text('{"rules": []}', encoding="utf-8")
    _write_gate(out, {"status": "fail", "ok": False})

    from openbimagent.server.runs import _deliver_offline_ifc
    from openbimagent.session.store import SessionStore

    store = SessionStore(tmp_path / "s.jsonl", title="t")
    assert _deliver_offline_ifc(out, store) is None
    assert not (out / "municipal_utility.ifc").exists()


def test_deliver_offline_ifc_missing_artifacts_skips(tmp_path: Path) -> None:
    from openbimagent.server.runs import _deliver_offline_ifc
    from openbimagent.session.store import SessionStore

    out = tmp_path / "empty"
    out.mkdir()
    store = SessionStore(tmp_path / "s.jsonl", title="t")
    assert _deliver_offline_ifc(out, store) is None
