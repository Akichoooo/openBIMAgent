from copy import deepcopy
import hashlib
from pathlib import Path
import threading

import pytest

from openbimagent.server.municipal_workflow import MunicipalWorkflowStore, WorkflowError


def test_real_validator_repair_lock_restart_and_confirm(tmp_path):
    from test_utility_candidate import physical_candidate
    from openbimagent.server.municipal_actions import propose_repairs

    store = MunicipalWorkflowStore(tmp_path)
    draft = physical_candidate().solver_input
    draft["segments"][0]["design_slope"] = 0.001
    state = store.save("real", {"candidate": draft})
    pinned = state["rule_context"]
    assert not state["validation"]["executable"]
    state = store.propose("real", bound(state), propose_repairs)
    assert state["proposal"]["patches"][0]["path"] == "/segments/0/design_slope"
    state = store.apply("real", bound(state))
    assert state["validation"]["executable"]
    assert state["rule_context"] == pinned
    assert state["validation"]["compiled_ir"]["segments"][0]["outer_diameter_mm"] == 360
    state = store.lock("real", {**bound(state), "path": "/segments/0/design_slope", "locked": True})
    restarted = MunicipalWorkflowStore(tmp_path)
    state = restarted.confirm("real", {**bound(state), "reviewed": True})
    assert state["confirmation"]["visual_review"] == "not_run_acknowledged"
    state = restarted.export("real", {**bound(state), "host": "blender", "confirm": True}, lambda ir, *_, **__: {"status": "completed", "actual_ir": ir})
    assert state["delivery"]["receipt"]["actual_ir"] == state["validation"]["compiled_ir"]


def test_visual_patch_requires_current_observation_and_full_revalidation(tmp_path):
    from test_utility_candidate import physical_candidate

    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("visual", {"candidate": physical_candidate().solver_input})
    with pytest.raises(WorkflowError, match="视觉检查"):
        store.propose("visual", {**bound(state), "source": "visual"}, lambda _: [])
    def checker(s, _):
        return {"revision": s["revision"], "workflow_candidate_sha256": s["candidate_sha256"],
                "patches": [{"path": "/nodes/0/x_m", "old_value": 0, "value": 1, "reason": "test observation"}]}
    state = store.visual("visual", bound(state), checker)
    state = store.propose("visual", {**bound(state), "source": "visual"}, lambda _: [])
    state = store.apply("visual", bound(state))
    assert state["validation"]["executable"]
    assert state["visual"] is None and state["confirmation"] is None
    assert state["history"][-1]["source"] == "visual_proposal"


def test_interrupted_delivery_is_not_silently_retried_or_confirmed(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("restart", {"candidate": candidate()})
    state["delivery"] = {"status": "running", "owner": "previous-process"}
    store._write("restart", state)
    recovered = MunicipalWorkflowStore(tmp_path, validator=validate).get("restart")
    assert recovered["delivery"]["status"] == "interrupted"
    assert recovered["confirmation"] is None


def test_real_http_save_repair_missing_session_and_safe_import(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openbimagent.server.municipal_workflow import add_municipal_workflow
    from test_utility_candidate import physical_candidate

    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path))
    (tmp_path / "api-session.jsonl").write_text("", encoding="utf-8")
    app = FastAPI()
    add_municipal_workflow(app)
    client = TestClient(app)
    assert client.get("/api/v1/municipal/missing").status_code == 404
    url = "/api/v1/municipal/api-session"
    state = client.put(url, json={"candidate": physical_candidate().solver_input}).json()["state"]
    assert state["validation"]["executable"]
    assert client.post(url + "/import-ir", json={**bound(state), "compiled_ir": state["validation"]["compiled_ir"]}).status_code == 200
    imported = client.get(url).json()["state"]
    # Missing obstacle survey is never reconstructed as an empty/complete survey.
    assert imported["candidate"]["collision_context"] is None
    assert not imported["validation"]["executable"]
    assert client.post(url + "/confirm", json={**bound(state), "reviewed": True}).status_code == 409


def test_vision_config_endpoint_is_static_and_never_exposes_the_key(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openbimagent.server.municipal_workflow import add_municipal_workflow

    monkeypatch.setenv("OPENBIMAGENT_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("OPENBIMAGENT_PROFILE", "sensenova")
    monkeypatch.setenv("SENSENOVA_API_KEY", "test-only-secret-value")
    app = FastAPI()
    add_municipal_workflow(app)
    response = TestClient(app).get("/api/v1/municipal/vision-config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "critic_scad"
    assert payload["status"] == "ready"
    assert payload["credential_env"] == "SENSENOVA_API_KEY"
    assert payload["credential_present"] is True
    assert "test-only-secret-value" not in response.text


def validate(candidate, context):
    slope = candidate["segments"][0]["design_slope"]
    bad = slope < 0.003
    return {"executable": not bad, "compiled_ir": {"source": deepcopy(candidate)},
            "rule_context": context or {"version": "pinned"},
            "issues": [{"rule_id": "slope", "check_name": "slope", "object_ids": ["p"], "field_path": "/segments/0/design_slope", "status": "fail", "severity": "error"}] if bad else []}


def candidate(slope=0.003):
    return {"segments": [{"segment_id": "p", "design_slope": slope}], "nodes": [{"node_id": "n", "x_m": 10}]}


def bound(state):
    return {"base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
            "proposal_id": (state.get("proposal") or {}).get("proposal_id"), "review_token": state.get("review_token")}


def test_invalid_draft_is_retained_and_cannot_be_confirmed(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate(-0.1)})
    assert store.get("s")["candidate"]["segments"][0]["design_slope"] == -0.1
    with pytest.raises(WorkflowError, match="硬约束"):
        store.confirm("s", {**bound(state), "reviewed": True})


def test_lock_survives_restart_and_rejects_whole_draft_or_identity_replacement(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    state = store.lock("s", {**bound(state), "path": "/segments/0/design_slope", "locked": True})
    restarted = MunicipalWorkflowStore(tmp_path, validator=validate)
    changed = deepcopy(state["candidate"])
    changed["segments"][0]["design_slope"] = 0.1
    with pytest.raises(WorkflowError, match="锁定"):
        restarted.save("s", {**bound(state), "candidate": changed})
    changed = deepcopy(state["candidate"])
    changed["segments"][0]["segment_id"] = "different-pipe"
    with pytest.raises(WorkflowError, match="替换"):
        restarted.save("s", {**bound(state), "candidate": changed})


def test_old_revision_and_confirmation_never_export_revised_model(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    state = store.confirm("s", {**bound(state), "reviewed": True})
    changed = store.save("s", {**bound(state), "candidate": candidate(0.004), "source": "requirement_change"})
    assert changed["requirement_revision"] == 2
    assert changed["confirmation"] is None
    with pytest.raises(WorkflowError, match="任务已更新"):
        store.export("s", {**bound(state), "host": "vectorworks", "confirm": True}, lambda *_: pytest.fail("host must not run"))
    with pytest.raises(WorkflowError, match="先确认"):
        store.export("s", {**bound(changed), "host": "vectorworks", "confirm": True}, lambda *_: pytest.fail("host must not run"))


def test_bounded_repair_keeps_original_when_new_hard_failure_appears(tmp_path):
    def validator(c, context):
        result = validate(c, context)
        if c["segments"][0]["design_slope"] > 0.01:
            result["issues"] = [{"rule_id": "new_cover_violation", "status": "fail", "severity": "error"}]
        return result
    store = MunicipalWorkflowStore(tmp_path, validator=validator)
    state = store.save("s", {"candidate": candidate(0.001)})
    state = store.propose("s", bound(state), lambda _: [{"path": "/segments/0/design_slope", "old_value": 0.001, "value": 0.02}])
    with pytest.raises(WorkflowError, match="新的硬约束"):
        store.apply("s", bound(state))
    assert store.get("s")["candidate"]["segments"][0]["design_slope"] == 0.001
    state = store.propose("s", bound(state), lambda _: [{"path": "/segments/0/design_slope", "old_value": 0.001, "value": 0.003}])
    repaired = store.apply("s", bound(state))
    assert repaired["validation"]["executable"]
    assert repaired["history"][-1]["source"] == "automatic"


def test_export_uses_saved_candidate_and_blocks_concurrent_edit(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate(0.004)})
    state = store.confirm("s", {**bound(state), "reviewed": True})
    entered, release = threading.Event(), threading.Event()
    received = []
    def exporter(ir, host, name, **_kwargs):
        received.append((ir, host, name))
        entered.set()
        assert release.wait(3)
        return {"status": "completed"}
    worker = threading.Thread(target=lambda: store.export("s", {**bound(state), "host": "vectorworks", "confirm": True}, exporter))
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(WorkflowError, match="正在交给宿主"):
            store.save("s", {**bound(state), "candidate": candidate(0.1)})
    finally:
        release.set()
        worker.join(3)
    assert received[0][0]["source"]["segments"][0]["design_slope"] == 0.004
    assert received[0][1] == "vectorworks"
    assert store.get("s")["delivery"]["status"] == "completed"


def test_stale_visual_result_cannot_replace_current_model_evidence(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    def checker(snapshot, output):
        store.save("s", {**bound(state), "candidate": candidate(0.004)})
        return {"issues": [], "status": "reviewed"}
    with pytest.raises(WorkflowError, match="任务已更新"):
        store.visual("s", bound(state), checker)
    assert store.get("s")["visual"] is None


def test_old_tab_cannot_apply_a_different_proposal(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    initial = store.save("s", {"candidate": candidate(0.001)})
    first = store.propose("s", bound(initial), lambda _: [{"path": "/segments/0/design_slope", "old_value": 0.001, "value": 0.003}])
    latest = store.propose("s", bound(initial), lambda _: [{"path": "/segments/0/design_slope", "old_value": 0.001, "value": 0.004}])
    with pytest.raises(WorkflowError, match="建议已更新"):
        store.apply("s", bound(first))
    assert store.get("s")["candidate"]["segments"][0]["design_slope"] == 0.001
    assert store.apply("s", bound(latest))["candidate"]["segments"][0]["design_slope"] == 0.004


def test_confirmation_is_bound_to_reviewed_visual_evidence(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    old = store.save("s", {"candidate": candidate()})
    latest = store.visual("s", bound(old), lambda *_: {"status": "review_required", "issues": ["new observation"]})
    assert old["revision"] == latest["revision"]
    with pytest.raises(WorkflowError, match="检查结果已更新"):
        store.confirm("s", {**bound(old), "reviewed": True})
    assert store.confirm("s", {**bound(latest), "reviewed": True})["confirmation"]


@pytest.mark.parametrize("receipt", [{"status": "completed"}, {"status": "partial", "host_status": "completed"}])
def test_repeat_export_reuses_success_even_after_restart_or_other_host(tmp_path, receipt):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    state = store.confirm("s", {**bound(state), "reviewed": True})
    calls = []
    def exporter(ir, host, name, **_kwargs):
        calls.append(host)
        return {**receipt, "output_path": name}
    request = {**bound(state), "host": "blender", "confirm": True}
    first = store.export("s", request, exporter)
    restarted = MunicipalWorkflowStore(tmp_path, validator=validate)
    assert restarted.export("s", request, exporter)["delivery"] == first["delivery"]
    restarted.export("s", {**request, "host": "vectorworks"}, exporter)
    assert restarted.export("s", request, exporter)["delivery"] == first["delivery"]
    assert calls == ["blender", "vectorworks"]


def test_failed_export_needs_fresh_explicit_retry_intent(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    state = store.confirm("s", {**bound(state), "reviewed": True})
    calls = []
    def exporter(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("unknown host result")
    request = {**bound(state), "host": "blender", "confirm": True}
    failed = store.export("s", request, exporter)
    with pytest.raises(WorkflowError, match="明确确认重新执行"):
        store.export("s", request, exporter)
    retry = {**request, "retry_of": failed["delivery"]["execution_id"]}
    store.export("s", retry, exporter)
    with pytest.raises(WorkflowError, match="明确确认重新执行"):
        store.export("s", retry, exporter)
    assert len(calls) == 2


def test_interrupted_attempt_survives_delivery_to_another_host(tmp_path):
    store = MunicipalWorkflowStore(tmp_path, validator=validate)
    state = store.save("s", {"candidate": candidate()})
    state["delivery"] = {"status": "running", "host": "vectorworks", "revision": state["revision"],
                         "execution_id": "interrupted-vw", "owner": "previous-process"}
    store._write("s", state)
    recovered = store.get("s")
    state = store.confirm("s", {**bound(recovered), "reviewed": True})
    calls = []
    def exporter(ir, host, execution_id, **_kwargs):
        calls.append(host)
        return {"status": "completed"}
    store.export("s", {**bound(state), "host": "blender", "confirm": True}, exporter)
    request = {**bound(state), "host": "vectorworks", "confirm": True}
    with pytest.raises(WorkflowError, match="明确确认重新执行"):
        store.export("s", request, exporter)
    store.export("s", {**request, "retry_of": "interrupted-vw"}, exporter)
    assert calls == ["blender", "vectorworks"]


@pytest.mark.parametrize("sid", ["../other", "..", "a/b", "a\\b", "", "x" * 129])
def test_session_path_is_not_a_filesystem_path(tmp_path, sid):
    with pytest.raises(WorkflowError):
        MunicipalWorkflowStore(tmp_path, validator=validate).get(sid)


def test_ifc_retry_reuses_host_receipt_without_extra_host_call(tmp_path):
    from test_utility_candidate import physical_candidate
    from openbimagent.utility.contracts import CompiledUtilityIR

    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("ifc", {"candidate": physical_candidate().solver_input})
    state = store.confirm("ifc", {**bound(state), "reviewed": True})
    compiled_sha256 = CompiledUtilityIR.model_validate(
        state["validation"]["compiled_ir"]
    ).canonical_sha256()
    host_calls = []

    def exporter(ir, host, execution_id, *, delivery_context=None):
        host_calls.append(execution_id)
        assert delivery_context and delivery_context["issues"]
        return {
            "status": "completed",
            "host_status": "completed",
            "output_path": str(tmp_path / f"{execution_id}.blend"),
            "source_ir_sha256": compiled_sha256,
            "semantic_snapshot": {"host": "blender", "objects": []},
            "semantic_verified": True,
            "geometry_snapshot": {"status": "dimension_verified_topology_unverified"},
            "geometry_status": "dimension_verified_topology_unverified",
            "ifc": {
                "status": "failed",
                "generation": {"status": "failed", "error": "injected IFC failure"},
                "error": "injected IFC failure",
                "affected_ids": ["pipe-001"],
            },
        }

    state = store.export("ifc", {**bound(state), "host": "blender", "confirm": True}, exporter)
    assert len(host_calls) == 1
    before = deepcopy(state["delivery"])
    assert before["receipt"]["ifc"]["status"] == "failed"

    retry_calls = []

    def retryer(ir, receipt, context, execution_id, attempt):
        retry_calls.append((execution_id, attempt))
        assert receipt["output_path"] == before["receipt"]["output_path"]
        assert context["candidate_sha256"] == state["candidate_sha256"]
        return {
            "status": "verified",
            "attempt": attempt,
            "revision": context["revision"],
            "candidate_sha256": context["candidate_sha256"],
            "generation": {"status": "generated", "ifc_path": str(tmp_path / "ifc.ifc")},
            "checks": {
                "ifc_schema": {"status": "pass"},
                "ids_semantic": {"status": "pass"},
                "geometry": {"status": "pass"},
                "domain_rules": {"status": "unknown", "production_verification": "review_required"},
                "status": "pass",
            },
        }

    state = store.retry_ifc("ifc", {**bound(state), "confirm": True}, retryer)
    assert len(host_calls) == 1
    assert retry_calls == [(before["execution_id"], 2)]
    assert state["delivery"]["receipt"]["ifc"]["status"] == "verified"
    assert "ifc" not in state["delivery"]
    assert state["delivery"]["receipt"]["output_path"] == before["receipt"]["output_path"]
    assert state["delivery"]["ifc_history"][-1]["previous_status"] == "failed"
    assert state["history"][-1]["action"] == "ifc_retry"
    assert state["delivery"]["delivery_layers"] == state["delivery"]["receipt"]["delivery_layers"]
    assert state["delivery"]["delivery_layers"]["ifc_generation"]["status"] == "generated"
    assert state["delivery"]["delivery_layers"]["ifc_checks"]["status"] == "pass"
    assert state["delivery"]["delivery_layers"]["host"]["topology_status"] == "unverified"
    assert state["delivery"]["ifc_history"][-1]["attempt"] == 2


def test_ifc_retry_requires_reusable_receipt_and_current_confirmation(tmp_path):
    from test_utility_candidate import physical_candidate

    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("guard", {"candidate": physical_candidate().solver_input})
    state = store.confirm("guard", {**bound(state), "reviewed": True})
    with pytest.raises(WorkflowError, match="宿主回执"):
        store.retry_ifc(
            "guard",
            {**bound(state), "confirm": True},
            lambda *_: pytest.fail("retry must not run without a host receipt"),
        )

    def exporter(ir, host, execution_id, *, delivery_context=None):
        return {
            "status": "completed",
            "host_status": "completed",
            "output_path": str(tmp_path / f"{execution_id}.blend"),
            "source_ir_sha256": "f" * 64,
            "semantic_snapshot": {"host": "blender", "objects": []},
            "geometry_snapshot": {"status": "unverified"},
            "ifc": {"status": "failed"},
        }

    store.export("guard", {**bound(state), "host": "blender", "confirm": True}, exporter)
    with pytest.raises(WorkflowError, match="IR 身份不一致"):
        store.retry_ifc(
            "guard",
            {**bound(state), "confirm": True},
            lambda *_: pytest.fail("identity mismatch must not rebuild IFC"),
        )

    store.save(
        "guard", {**bound(store.get("guard")), "candidate": physical_candidate().solver_input}
    )
    with pytest.raises(WorkflowError):
        store.retry_ifc(
            "guard",
            {**bound(state), "confirm": True},
            lambda *_: pytest.fail("stale revision must not rebuild IFC"),
        )


def test_ifc_retry_rejects_receipt_without_verifiable_ir_identity(tmp_path):
    from test_utility_candidate import physical_candidate

    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("no-id", {"candidate": physical_candidate().solver_input})
    state = store.confirm("no-id", {**bound(state), "reviewed": True})

    def exporter(ir, host, execution_id, *, delivery_context=None):
        return {
            "status": "completed",
            "host_status": "completed",
            "output_path": str(tmp_path / f"{execution_id}.blend"),
            "semantic_snapshot": {"host": "blender", "objects": []},
            "geometry_snapshot": {"status": "unverified"},
            "ifc": {"status": "failed"},
        }

    store.export("no-id", {**bound(state), "host": "blender", "confirm": True}, exporter)
    with pytest.raises(WorkflowError, match="IR 身份"):
        store.retry_ifc(
            "no-id",
            {**bound(state), "confirm": True},
            lambda *_: pytest.fail("missing identity must not rebuild IFC"),
        )


def test_ifc_retry_uses_independent_attempt_directories(tmp_path):
    from test_utility_candidate import physical_candidate
    from openbimagent.server.municipal_actions import retry_ifc_delivery
    from openbimagent.utility.candidate import validate_utility_candidate

    validation = validate_utility_candidate(physical_candidate())
    compiled = validation.compiled_ir
    context = {
        "revision": 1,
        "candidate_sha256": validation.candidate_sha256,
        "validation_sha256": "a" * 64,
        "rule_context_sha256": validation.rule_context_sha256,
        "rule_context": validation.rule_context.model_dump(mode="json"),
        "issues": [issue.model_dump(mode="json") for issue in validation.issues],
    }
    receipt = {"output_path": str(tmp_path / "host.blend")}
    (tmp_path / "host.blend").write_bytes(b"host")
    first = retry_ifc_delivery(
        compiled.model_dump(mode="json"), receipt, context, "exec", attempt=1
    )
    second = retry_ifc_delivery(
        compiled.model_dump(mode="json"), receipt, context, "exec", attempt=2
    )
    first_path = Path(first["generation"]["ifc_path"])
    second_path = Path(second["generation"]["ifc_path"])
    assert first["status"] == "verified", first.get("error")
    assert second["status"] == "verified", second.get("error")
    assert first["attempt"] == 1 and second["attempt"] == 2
    assert first_path != second_path
    assert first_path.parent.name == "attempt-01"
    assert second_path.parent.name == "attempt-02"
    assert first_path.is_file() and second_path.is_file()
    assert Path(first["generation"]["schema_report_path"]).is_file()
    assert Path(second["generation"]["schema_report_path"]).is_file()
    assert (tmp_path / "host.blend").read_bytes() == b"host"


def test_ifc_download_uses_first_export_receipt_and_validates_file(tmp_path):
    from test_utility_candidate import physical_candidate

    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("dl", {"candidate": physical_candidate().solver_input})
    state = store.confirm("dl", {**bound(state), "reviewed": True})
    ifc_file = tmp_path / "case.ifc"
    ifc_file.write_bytes(b"IFC-CONTENT")
    digest = hashlib.sha256(ifc_file.read_bytes()).hexdigest()

    def exporter(ir, host, execution_id, *, delivery_context=None):
        return {
            "status": "completed",
            "host_status": "completed",
            "output_path": str(tmp_path / f"{execution_id}.blend"),
            # first-export shape: IFC stage only inside the receipt; identity at
            # the stage root (the production layout), never inside generation.
            "ifc": {
                "status": "failed",
                "revision": state["revision"],
                "candidate_sha256": state["candidate_sha256"],
                "generation": {
                    "status": "generated",
                    "ifc_path": str(ifc_file),
                    "ifc_sha256": digest,
                    "attempt": 1,
                },
            },
        }

    store.export("dl", {**bound(state), "host": "blender", "confirm": True}, exporter)
    assert store.ifc_download("dl") == ifc_file

    ifc_file.write_bytes(b"TAMPERED")
    with pytest.raises(WorkflowError, match="不一致"):
        store.ifc_download("dl")

    ifc_file.unlink()
    with pytest.raises(WorkflowError, match="缺失"):
        store.ifc_download("dl")


def _production_ifc_stage(tmp_path, compiled, validation, revision, candidate_sha256):
    from openbimagent.server.municipal_actions import build_ifc_delivery_stage

    context = {
        "revision": revision,
        "candidate_sha256": candidate_sha256,
        "validation_sha256": "a" * 64,
        "rule_context_sha256": validation.rule_context_sha256,
        "rule_context": validation.rule_context.model_dump(mode="json"),
        "issues": [issue.model_dump(mode="json") for issue in validation.issues],
    }
    return build_ifc_delivery_stage(
        compiled.model_dump(mode="json"),
        "dl-exec",
        context,
        tmp_path / "delivery-root",
        attempt=1,
    )


def test_ifc_download_rejects_old_invalid_or_missing_stage_identity(tmp_path):
    from test_utility_candidate import physical_candidate
    from openbimagent.utility.candidate import validate_utility_candidate

    validation = validate_utility_candidate(physical_candidate())
    compiled = validation.compiled_ir
    store = MunicipalWorkflowStore(tmp_path / "current")
    state = store.save("dl", {"candidate": physical_candidate().solver_input})
    state = store.confirm("dl", {**bound(state), "reviewed": True})
    stage = _production_ifc_stage(
        tmp_path, compiled, validation, state["revision"], state["candidate_sha256"]
    )
    assert stage["status"] == "verified", stage.get("error")
    # The production stage keeps identity at the root, not in generation.
    assert stage["revision"] == state["revision"]
    assert stage["candidate_sha256"] == state["candidate_sha256"]
    assert "revision" not in stage["generation"]
    assert "candidate_sha256" not in stage["generation"]

    def exporter_for(stage_value):
        def exporter(ir, host, execution_id, *, delivery_context=None):
            return {
                "status": "completed",
                "host_status": "completed",
                "output_path": str(tmp_path / f"{execution_id}.blend"),
                "ifc": stage_value,
            }

        return exporter

    store.export("dl", {**bound(state), "host": "blender", "confirm": True}, exporter_for(deepcopy(stage)))
    assert store.ifc_download("dl") == Path(stage["generation"]["ifc_path"])

    cases = [
        (lambda item: item.update({"revision": state["revision"] + 1}), "旧版本"),
        (lambda item: item.update({"candidate_sha256": "f" * 64}), "不一致"),
        (lambda item: item.pop("revision", None), "版本身份"),
        (lambda item: item.pop("candidate_sha256", None), "候选版本身份"),
        (lambda item: item.update({"candidate_sha256": "not-a-hash"}), "候选版本身份"),
    ]
    for index, (mutate, message) in enumerate(cases):
        case_root = tmp_path / f"case-{index}"
        case_store = MunicipalWorkflowStore(case_root)
        case_state = case_store.save("dl", {"candidate": physical_candidate().solver_input})
        case_state = case_store.confirm("dl", {**bound(case_state), "reviewed": True})
        mutated = deepcopy(stage)
        mutate(mutated)
        case_store.export(
            "dl",
            {**bound(case_state), "host": "blender", "confirm": True},
            exporter_for(mutated),
        )
        with pytest.raises(WorkflowError, match=message):
            case_store.ifc_download("dl")
