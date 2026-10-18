from pathlib import Path

import httpx

from openbimagent.server import municipal_actions
from openbimagent.server.municipal_workflow import MunicipalWorkflowStore
from openbimagent.vision.municipal import MunicipalPreviewError, MunicipalVisualError
from test_utility_candidate import physical_candidate


class Preview:
    def __init__(self, revision: int):
        self.revision = revision

    def model_dump(self, **_):
        return {"revision": self.revision, "images": {"iso": "iso.png", "front": "front.png", "top": "top.png"}}


def ready_env(monkeypatch):
    monkeypatch.setenv("OPENBIMAGENT_PROFILE", "sensenova")
    monkeypatch.setenv("SENSENOVA_API_KEY", "test-only-key")


def failing_critic(exc, *, http_status=None, aborted=False):
    """Simulate a transport attempt that already produced call diagnostics."""
    def fail(*args, **kwargs):
        diagnostics = kwargs["diagnostics_out"]
        diagnostics.setdefault("attempts", []).append({
            "model": "sensenova-6.8-flash-lite",
            "provider": "sensenova",
            "endpoint": "https://token.sensenova.cn/v1/chat/completions",
            "request_sent": True,
            "http_status": http_status,
            "aborted": aborted,
            "first_event_at_ms": 100.0 if http_status == 200 else None,
        })
        diagnostics["business_outcome"] = "error"
        raise exc
    return fail


def test_visual_model_failure_keeps_real_preview_without_claiming_pass(tmp_path, monkeypatch):
    from openbimagent.vision import municipal

    ready_env(monkeypatch)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: Preview(state["revision"]))
    monkeypatch.setattr(
        municipal.MunicipalVLMCritic,
        "critique",
        failing_critic(RuntimeError("vision provider is not configured")),
    )
    report = municipal_actions.check_visual(state, tmp_path / "preview")
    assert report["status"] == "unavailable"
    assert report["vlm_called"] is True
    assert report["preview"]["images"]
    assert report["patches"] == []
    assert report["workflow_candidate_sha256"] == state["candidate_sha256"]
    assert report["diagnostics"]["attempts"][0]["request_sent"] is True


def test_visual_credentials_missing_keeps_views_and_does_not_call_model(tmp_path, monkeypatch):
    from openbimagent.vision import municipal

    monkeypatch.setenv("OPENBIMAGENT_PROFILE", "official")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: Preview(state["revision"]))
    monkeypatch.setattr(municipal.MunicipalVLMCritic, "critique", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    report = municipal_actions.check_visual(state, tmp_path / "preview")
    assert report["status"] == "unavailable"
    assert report["failure_code"] == "credentials_missing"
    assert report["vlm_called"] is False
    assert report["preview"]["images"]
    assert report["vision_config"]["credential_env"] == "GEMINI_API_KEY"
    assert report["vision_config"]["credential_present"] is False


def test_visual_timeout_invalid_json_and_rate_limit_are_distinct_bounded_failures(tmp_path, monkeypatch):
    from openbimagent.providers.registry import ProviderError
    from openbimagent.vision import municipal

    ready_env(monkeypatch)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: Preview(state["revision"]))

    monkeypatch.setattr(
        municipal.MunicipalVLMCritic, "critique",
        failing_critic(httpx.ReadTimeout("timed out"), http_status=200, aborted=True),
    )
    timed_out = municipal_actions.check_visual(state, tmp_path / "timeout")
    assert timed_out["failure_code"] == "timeout" and timed_out["vlm_called"] is True
    assert timed_out["preview"]["images"]
    assert timed_out["diagnostics"]["attempts"][0]["aborted"] is True

    monkeypatch.setattr(
        municipal.MunicipalVLMCritic, "critique",
        failing_critic(MunicipalVisualError("critic returned invalid structured observations twice: bad JSON"), http_status=200),
    )
    invalid = municipal_actions.check_visual(state, tmp_path / "invalid")
    assert invalid["failure_code"] == "response_invalid" and invalid["vlm_called"] is True
    assert invalid["preview"]["images"] and invalid["issues"] == []

    monkeypatch.setattr(
        municipal.MunicipalVLMCritic, "critique",
        failing_critic(ProviderError("upstream failure"), http_status=429),
    )
    limited = municipal_actions.check_visual(state, tmp_path / "limited")
    assert limited["failure_code"] == "rate_limited" and limited["vlm_called"] is True
    assert limited["preview"]["images"]


def test_cancelled_before_request_reports_not_called_and_no_usage(tmp_path, monkeypatch):
    from openbimagent.vision import municipal

    ready_env(monkeypatch)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: Preview(state["revision"]))
    # No request was sent: the simulated attempt must not fabricate request_sent=True.
    def pre_cancelled(*args, **kwargs):
        diagnostics = kwargs["diagnostics_out"]
        diagnostics["attempts"] = [{"model": "m", "provider": "p", "request_sent": False, "outcome": "cancelled_before_request"}]
        diagnostics["business_outcome"] = "cancelled_before_attempt"
        raise MunicipalVisualError("vision call cancelled before the next attempt")
    monkeypatch.setattr(municipal.MunicipalVLMCritic, "critique", pre_cancelled)
    report = municipal_actions.check_visual(state, tmp_path / "cancelled")
    assert report["failure_code"] == "timeout"
    assert report["vlm_called"] is False
    assert report["usage"] is None
    assert report["duration_ms"] is not None


def test_visual_success_metadata_matches_the_fallback_attempt(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openbimagent.vision import municipal

    ready_env(monkeypatch)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    config = {
        "status": "ready", "profile": "test", "configured_model": "primary", "effective_model": "primary",
        "provider": "p1", "endpoint": "https://p1/v1/chat/completions",
        "model_entries": [
            {"model": "primary", "provider": "p1", "endpoint": "https://p1/v1/chat/completions"},
            {"model": "fallback", "provider": "p2", "endpoint": "https://p2/v1/chat/completions"},
        ],
    }
    monkeypatch.setattr(municipal_actions, "_resolve_vision", lambda **_: (config, object()))
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: Preview(state["revision"]))

    def critique(self, preview, **kwargs):
        diagnostics = kwargs["diagnostics_out"]
        diagnostics.update(
            model_alias="fallback", response_model="service-fallback-id", business_outcome="report_validated",
            attempts=[{"model": "fallback", "provider": "p2", "request_sent": True, "http_status": 200}],
        )
        return SimpleNamespace(
            model="service-fallback-id", issues=(),
            model_dump=lambda **_: {
                "model": "service-fallback-id", "summary": "ok", "issues": [], "patches": [],
                "limitations": ["visual only"], "usage": {"total_tokens": 12},
            },
        )

    monkeypatch.setattr(municipal.MunicipalVLMCritic, "critique", critique)
    report = municipal_actions.check_visual(state, tmp_path / "preview")
    assert report["status"] == "observed"
    assert report["model"] == "service-fallback-id"
    assert report["model_alias"] == "fallback"
    assert report["provider"] == "p2"
    assert report["endpoint"] == "https://p2/v1/chat/completions"
    assert report["model_requested"] == "primary" and report["model_effective"] == "primary"
    assert report["usage"] == {"total_tokens": 12}


def test_diagnostic_identity_uses_the_fallback_that_was_attempted():
    config = {
        "configured_model": "primary",
        "effective_model": "primary",
        "profile": "test",
        "model_entries": [
            {"model": "primary", "provider": "p1", "endpoint": "https://p1/v1/chat/completions"},
            {"model": "fallback", "provider": "p2", "endpoint": "https://p2/v1/chat/completions"},
        ],
    }
    identity = municipal_actions._diagnostic_identity(
        config, {"model_alias": "fallback", "selected_model": "fallback", "response_model": "service-fallback-id"}
    )
    assert identity["model_alias"] == "fallback"
    assert identity["provider"] == "p2"
    assert identity["endpoint"] == "https://p2/v1/chat/completions"


def test_render_failure_keeps_no_fabricated_preview_or_pass(tmp_path, monkeypatch):
    from openbimagent.vision import municipal

    ready_env(monkeypatch)
    store = MunicipalWorkflowStore(tmp_path)
    state = store.save("s", {"candidate": physical_candidate().solver_input})
    monkeypatch.setattr(municipal, "render_municipal_preview", lambda *a, **k: (_ for _ in ()).throw(MunicipalPreviewError("OpenSCAD failed")))
    report = municipal_actions.check_visual(state, tmp_path / "preview")
    assert report["failure_code"] == "render_failed"
    assert report["vlm_called"] is False
    assert report["preview"] is None
    assert report["issues"] == [] and report["patches"] == []


def test_vision_configuration_reports_presence_not_secret_values(monkeypatch):
    ready_env(monkeypatch)
    monkeypatch.setenv("SENSENOVA_API_KEY", "test-only-secret-value")
    config = municipal_actions.vision_configuration()
    assert config["status"] == "ready"
    assert config["configured_model"] == "sensenova-6.8-flash-lite"
    assert config["provider"] == "sensenova"
    assert config["endpoint"] == "https://token.sensenova.cn/v1/chat/completions"
    assert config["credential_env"] == "SENSENOVA_API_KEY"
    assert config["credential_present"] is True
    assert config["configured_vision"] is True
    assert config["timeout_s"] == 90
    assert "test-only-secret-value" not in repr(config)


def test_vision_configuration_rejects_text_only_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBIMAGENT_PROFILE", "only")
    config_file = tmp_path / "models.toml"
    config_file.write_text(
        '[providers.test]\ntype = "openai-compatible"\nbase_url = "https://example.invalid/v1"\napi_key_env = "TEST_KEY"\n'
        '[models."text-only"]\nprovider = "test"\ncapabilities = ["tools"]\n'
        '[profiles.only]\ncritic_scad = "text-only"\n',
        encoding="utf-8",
    )
    config = municipal_actions.vision_configuration(config_path=config_file)
    assert config["status"] == "model_not_vision_capable"
    assert config["vision_chain"] == []


def test_vision_configuration_reports_missing_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBIMAGENT_PROFILE", "only")
    monkeypatch.delenv("MISSING_VISION_KEY", raising=False)
    config_file = tmp_path / "models.toml"
    config_file.write_text(
        '[providers.test]\ntype = "openai-compatible"\nbase_url = "https://example.invalid/v1"\napi_key_env = "MISSING_VISION_KEY"\n'
        '[models."vision-model"]\nprovider = "test"\ncapabilities = ["vision"]\n'
        '[profiles.only]\ncritic_scad = "vision-model"\n',
        encoding="utf-8",
    )
    config = municipal_actions.vision_configuration(config_path=config_file)
    assert config["status"] == "credentials_missing"
    assert config["credential_env"] == "MISSING_VISION_KEY"
    assert config["credential_present"] is False


def test_export_retains_host_success_and_generates_hollow_ifc(tmp_path, monkeypatch):
    from test_utility_candidate import physical_candidate
    from openbimagent.assembly import blender_host_executor
    from openbimagent.utility.candidate import validate_utility_candidate

    validation = validate_utility_candidate(physical_candidate())
    compiled = validation.compiled_ir.model_dump(mode="json")
    context = {
        "revision": 2,
        "candidate_sha256": validation.candidate_sha256,
        "validation_sha256": "b" * 64,
        "rule_context_sha256": validation.rule_context_sha256,
        "rule_context": validation.rule_context.model_dump(mode="json"),
        "issues": [issue.model_dump(mode="json") for issue in validation.issues],
    }
    called = []

    def execute(ir, **kwargs):
        called.append(kwargs)
        return {"status": "completed", "output_path": str(kwargs["output_path"]),
                "semantic_snapshot": {"host": "blender"}, "output_sha256": "a" * 64,
                "geometry_scope": "actual adapter limits"}

    monkeypatch.setenv("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", str(tmp_path))
    monkeypatch.setattr(blender_host_executor, "execute_blender_export", execute)
    result = municipal_actions.export_revision(compiled, "blender", "session-r2-unique", delivery_context=context)
    assert result["status"] == "partial" and result["host_status"] == "completed"
    assert result["geometry_scope"] == "actual adapter limits"
    assert Path(called[0]["output_path"]) == tmp_path / "session-r2-unique.blend"
    assert len(called) == 1
    ifc = result["ifc"]
    assert ifc["status"] == "verified", ifc
    assert ifc["attempt"] == 1
    assert ifc["revision"] == 2
    assert ifc["candidate_sha256"] == validation.candidate_sha256
    assert ifc["generation"]["status"] == "generated"
    assert len(ifc["generation"]["ifc_sha256"]) == 64
    assert Path(ifc["generation"]["ifc_path"]).is_file()
    assert Path(ifc["generation"]["schema_report_path"]).is_file()
    assert ifc["checks"]["ifc_schema"]["status"] == "pass"
    assert ifc["checks"]["ifc_schema"]["issue_count"] == 0
    assert ifc["checks"]["ids_semantic"]["status"] == "pass"
    assert ifc["checks"]["geometry"]["status"] == "pass"
    assert ifc["checks"]["domain_rules"]["status"] in {"pass", "fail", "unknown", "review_required"}
    assert result["delivery_layers"]["host"]["geometry_status"] == "unverified"
    assert ifc["rule_evidence"]["evidence_source"] == "confirmed_validation_result"


def test_export_ifc_failure_keeps_host_success_and_does_not_reinvoke_host(tmp_path, monkeypatch):
    from openbimagent.assembly import blender_host_executor

    # A legacy v1.0 IR is rejected for IFC body generation but the host receipt
    # (fake host) must still be preserved and the host called exactly once.
    from test_compiled_utility_ir import solved_payload

    compiled = solved_payload()
    assert compiled["protocol_version"] == "1.0"
    called = []

    def execute(ir, **kwargs):
        called.append(kwargs)
        return {"status": "completed", "output_path": str(kwargs["output_path"]),
                "semantic_snapshot": {"host": "blender"}, "output_sha256": "a" * 64}

    monkeypatch.setenv("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", str(tmp_path))
    monkeypatch.setattr(blender_host_executor, "execute_blender_export", execute)
    result = municipal_actions.export_revision(compiled, "blender", "session-old-ir")
    assert len(called) == 1
    assert result["status"] == "partial" and result["host_status"] == "completed"
    assert result["ifc"]["status"] == "failed"
    assert result["ifc"]["generation"]["status"] == "failed"
    assert "1.1" in result["ifc"]["error"]
    assert result["delivery_layers"]["host"]["status"] == "completed"
