from __future__ import annotations

import json

import pytest

from openbimagent.server.municipal_workflow import MunicipalWorkflowStore
from openbimagent.server.runs import (
    _archive_run_artifacts,
    _retrieve_exemplars,
    record_exemplar_feedback,
)
from openbimagent.utility.state_diff import (
    compute_candidate_state_diff,
    derive_revision_three_states,
)


def _candidate() -> dict:
    return {
        "protocol_version": "0.1",
        "request_id": "req-1",
        "system_id": "wastewater-1",
        "system_name": "gravity wastewater",
        "nodes": [
            {"node_id": "MH-1", "node_type": "manhole", "x_m": 0.0, "y_m": 0.0,
             "ground_elevation_m": 10.0, "invert_anchor_m": 8.5, "connection_radius_m": 1.5},
            {"node_id": "MH-2", "node_type": "manhole", "x_m": 30.0, "y_m": 0.0,
             "ground_elevation_m": 10.0, "invert_anchor_m": 8.4, "connection_radius_m": 1.5},
        ],
        "segments": [
            {"segment_id": "P-1", "start_node_id": "MH-1", "end_node_id": "MH-2",
             "diameter_mm": 300, "material": "concrete", "design_slope": 0.002,
             "surface_context": "roadway", "inner_diameter_mm": 300, "outer_diameter_mm": 360},
        ],
        "collision_context": None,
    }


def _validator(candidate: dict, context: dict | None) -> dict:
    issues = []
    slope = candidate["segments"][0].get("design_slope")
    slope_ok = isinstance(slope, (int, float)) and not isinstance(slope, bool) and slope >= 0.003
    issues.append({
        "rule_id": "R-SLOPE", "check_name": "min_slope",
        "status": "pass" if slope_ok else "fail",
        "severity": "info" if slope_ok else "error",
        "object_ids": ["P-1"], "field_path": "design_slope",
        "message": "minimum slope satisfied" if slope_ok else "minimum slope violated",
    })
    for node in candidate["nodes"]:
        cover = node["ground_elevation_m"] - node["invert_anchor_m"]
        if cover < 0.7:
            issues.append({
                "rule_id": "R-COVER", "check_name": "min_cover", "status": "fail",
                "severity": "error", "object_ids": [node["node_id"]],
                "field_path": "invert_anchor_m", "message": "cover too low",
            })
    return {
        "executable": not any(item["status"] == "fail" for item in issues),
        "issues": issues,
        "compiled_ir": {"protocol_version": "0.1"},
        "rule_context": {},
        "rule_context_sha256": "frozen-rule-context",
        "candidate_sha256": "validator-computed",
    }


def _proposer(state: dict) -> list[dict]:
    slope = state["candidate"]["segments"][0]["design_slope"]
    if slope < 0.003:
        return [{
            "path": "/segments/0/design_slope", "old_value": slope, "value": 0.003,
            "reason": "raise to verified minimum slope", "constraint_ids": ["R-SLOPE"],
        }]
    return []


def _visual_success(state: dict, output_dir, **kwargs) -> dict:
    if kwargs["iteration_round"] == 1:
        slope = state["candidate"]["segments"][0]["design_slope"]
        return {
            "status": "review_required", "failure_code": None,
            "patches": [{
                "path": "/segments/0/design_slope", "old_value": slope, "value": 0.004,
                "reason": "visual slope tightening", "constraint_ids": ["R-SLOPE"],
            }],
            "issues": [], "preview": {},
        }
    return {"status": "observed", "failure_code": None, "patches": [], "issues": [], "preview": {}}


def _export(ir, host, execution_id, delivery_context=None):
    return {"status": "completed", "host_status": "completed"}


def _write_run_artifacts(tmp_path, session_id: str, brief: str) -> dict:
    pack = tmp_path / "pack"
    pack.mkdir(exist_ok=True)
    playbook = pack / "playbook.md"
    playbook.write_text("# test pack\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "compiled_utility_ir.json").write_text(
        json.dumps({"evidence": [{"rule_id": "R-SLOPE", "status": "pass"}]}), encoding="utf-8"
    )
    entry = _archive_run_artifacts(playbook, session_id, brief, out_dir)
    assert entry is not None
    return entry


def test_iterative_refinement_state_diff_and_experience_feedback(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    store = MunicipalWorkflowStore(sessions / "municipal", validator=_validator)
    session_id = "sess-loop-success"
    store.save(session_id, {
        "base_revision": 0, "candidate": _candidate(), "source": "correction",
        "requirement_brief": "repair the low slope and keep cover",
    })

    calls: list[dict] = []

    def visual(state, output_dir, **kwargs):
        calls.append({
            "round": kwargs["iteration_round"],
            "brief": kwargs["requirement_brief"],
            "prior": kwargs["prior_iteration_patches"],
        })
        return _visual_success(state, output_dir, **kwargs)

    result = store.iterative_refine(session_id, {}, _proposer, visual, max_rounds=3)
    assert result["converged"] is True
    assert result["rounds_completed"] == 2
    assert result["auto_retry_rounds"] == 2
    assert calls[0]["brief"] == "repair the low slope and keep cover"
    assert calls[0]["prior"][0]["source"] == "automatic"
    assert any(item["source"] == "visual_proposal" for item in calls[1]["prior"])

    state = store.get(session_id)
    assert state["candidate"]["segments"][0]["design_slope"] == pytest.approx(0.004)

    state = store.confirm(session_id, {
        "base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
        "reviewed": True, "review_token": state["review_token"],
    })
    state = store.export(session_id, {
        "base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
        "confirm": True, "host": "vectorworks",
    }, _export)
    assert state["delivery"]["status"] == "completed"

    three = derive_revision_three_states(state["history"])
    assert three["sha256"]["S0"] and three["sha256"]["S1"] and three["sha256"]["S2"]
    assert three["sha256"]["S1"] == three["sha256"]["S2"]
    diff_0_1 = compute_candidate_state_diff(three["S0"], three["S1"])
    assert any(
        item["field"] == "design_slope" and item["delta"] == pytest.approx(0.002)
        for item in diff_0_1
    )
    assert compute_candidate_state_diff(three["S1"], three["S2"]) == []

    entry = _write_run_artifacts(tmp_path, session_id, "repair the low slope and keep cover")
    record = entry["experience_record"]
    assert record["admission_verified"] is True
    assert "R-SLOPE" in record["failure_modes"]
    assert record["evidence_sources"] == ["deterministic_repair", "visual_proposal", "human_correction"]
    assert record["utility_stats"] == {"used_count": 0, "pass_count": 0, "fail_streak": 0, "active": True}
    assert any(
        item["field"] == "design_slope" and item["direction"] == "increase" and item["delta"] > 0
        for item in record["repair_strategy"]
    )
    assert all(
        "delta" not in item for item in record["repair_strategy"] if item["field"] in {"x_m", "y_m"}
    )

    pack = tmp_path / "pack"
    hits = _retrieve_exemplars("repair the low slope and keep cover", pack)
    assert [hit["session_id"] for hit in hits] == [session_id]

    first = record_exemplar_feedback([session_id], ["R-SLOPE"], pack=pack)
    assert first["deactivated"] == []
    second = record_exemplar_feedback([session_id], ["R-SLOPE"], pack=pack)
    assert second["deactivated"] == [session_id]
    assert _retrieve_exemplars("repair the low slope and keep cover", pack) == []


def test_iterative_refinement_rolls_back_degrading_visual_patches(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    store = MunicipalWorkflowStore(tmp_path / "sessions" / "municipal", validator=_validator)
    session_id = "sess-loop-rollback"
    store.save(session_id, {"base_revision": 0, "candidate": _candidate(), "source": "correction"})

    def visual(state, output_dir, **kwargs):
        node = state["candidate"]["nodes"][0]
        return {
            "status": "review_required", "failure_code": None,
            "patches": [{
                "path": "/nodes/0/invert_anchor_m", "old_value": node["invert_anchor_m"],
                "value": 9.99, "reason": "visual lowering", "constraint_ids": ["R-COVER"],
            }],
            "issues": [], "preview": {},
        }

    result = store.iterative_refine(session_id, {}, _proposer, visual, max_rounds=3)
    assert result["rolled_back"] is True
    assert result["degradation_rollback"] is True
    assert "从 0 项增至 1 项" in result["rollback_reason"]

    state = store.get(session_id)
    assert state["candidate"]["nodes"][0]["invert_anchor_m"] == pytest.approx(8.5)
    assert state["candidate"]["segments"][0]["design_slope"] == pytest.approx(0.003)
    assert state["degradation_rollback"] is True
    assert state["history"][-1]["action"] == "auto_loop_complete"


def test_experience_record_requires_confirmation_and_delivery(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    store = MunicipalWorkflowStore(sessions / "municipal", validator=_validator)
    session_id = "sess-loop-unconfirmed"
    store.save(session_id, {
        "base_revision": 0, "candidate": _candidate(), "source": "correction",
        "requirement_brief": "repair slope",
    })
    store.iterative_refine(session_id, {}, _proposer, _visual_success, max_rounds=2)

    entry = _write_run_artifacts(tmp_path, session_id, "repair slope")
    assert "experience_record" not in entry
    assert entry["three_state_summary"]["sha256"]["S0"]
    assert entry["three_state_summary"]["sha256"]["S1"]


def test_preference_only_history_does_not_create_experience_record(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    store = MunicipalWorkflowStore(sessions / "municipal", validator=_validator)
    session_id = "sess-preference-only"
    draft = _candidate()
    draft["segments"][0]["design_slope"] = 0.003
    state = store.save(session_id, {"base_revision": 0, "candidate": draft, "source": "preference"})
    state = store.confirm(session_id, {
        "base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
        "reviewed": True, "review_token": state["review_token"],
    })
    store.export(session_id, {
        "base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
        "confirm": True, "host": "vectorworks",
    }, _export)

    entry = _write_run_artifacts(tmp_path, session_id, "prefer a slightly steeper slope")
    assert "experience_record" not in entry
