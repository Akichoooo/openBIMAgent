"""Preview geometry provenance and structured visual observations with injected providers."""
from __future__ import annotations

import base64
import json
from copy import deepcopy

import pytest

from openbimagent.utility.candidate import UtilityCandidate, compiled_to_candidate, propose_candidate_repairs, validate_utility_candidate
from openbimagent.vision.municipal import MunicipalPreviewError, MunicipalVisualError, MunicipalVLMCritic, municipal_to_scad, render_municipal_preview
from test_utility_candidate import physical_candidate

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1cAAAAASUVORK5CYII=")


def injected_render(scad_path, out_dir):
    assert "rotate(a=" in scad_path.read_text(encoding="utf-8")
    paths = {}
    for view in ("iso", "front", "top"):
        paths[view] = out_dir / f"{view}.png"
        paths[view].write_bytes(PNG)
    return paths


def preview_case(tmp_path):
    candidate = physical_candidate()
    validation = validate_utility_candidate(candidate)
    preview = render_municipal_preview(validation.compiled_ir, tmp_path, revision=3, candidate_sha256=candidate.canonical_sha256(), collision_context=candidate.solver_input["collision_context"], render_fn=injected_render)
    return candidate, validation, preview


class Registry:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, role, messages, **kwargs):
        self.calls.append((role, messages, kwargs))
        return {"model": "injected-vision-model", "choices": [{"message": {"content": json.dumps(self.payload)}}]}


def report_payload(validation, preview):
    return {
        "revision": preview.revision,
        "candidate_sha256": preview.candidate_sha256,
        "compiled_ir_sha256": preview.compiled_ir_sha256,
        "rule_context_sha256": validation.rule_context_sha256,
        "summary": "Check the labelled branch connection in the top view.",
        "issues": [{"object_ids": ["pipe-002"], "constraint_ids": ["MU-DRAIN-004"], "view_id": "top", "evidence": "The labelled branch is visible in plan.", "description": "Slope cannot be measured from this projection.", "suggestion": "Use the supplied deterministic slope evidence.", "uncertainty": "Camera projection does not determine elevation.", "severity": "info"}],
        "patches": [],
        "limitations": ["Visual projection cannot replace numerical checks."],
    }


def test_preview_uses_true_pipe_geometry_and_obstacles(tmp_path):
    candidate = physical_candidate()
    candidate.solver_input["collision_context"]["obstacles"] = [{"obstacle_id": "building-1", "kind": "aabb", "category": "building", "min_corner": {"x_m": 30, "y_m": 5, "z_m": 8}, "max_corner": {"x_m": 35, "y_m": 10, "z_m": 15}}]
    validation = validate_utility_candidate(candidate)
    source, geometry = municipal_to_scad(validation.compiled_ir, collision_context=candidate.solver_input["collision_context"])
    assert "cylinder(h=" in source and "rotate(a=" in source
    assert "r=0.18" in source and 'text("pipe-001"' in source
    assert "building-1" in geometry["obstacles"]
    assert geometry["pipes"]["pipe-001"]["axis_m"][0][2] == pytest.approx(9.95)
    preview = render_municipal_preview(validation.compiled_ir, tmp_path, revision=1, candidate_sha256=candidate.canonical_sha256(), collision_context=candidate.solver_input["collision_context"], render_fn=injected_render)
    assert set(preview.images) == {"iso", "front", "top"}
    assert "building-1" in preview.object_ids
    assert (tmp_path / "municipal_preview.json").is_file()


def test_missing_real_renderer_is_explicit(tmp_path):
    candidate = physical_candidate()
    validation = validate_utility_candidate(candidate)
    with pytest.raises(MunicipalPreviewError, match="OpenSCAD executable is unavailable"):
        render_municipal_preview(validation.compiled_ir, tmp_path, revision=1, candidate_sha256=candidate.canonical_sha256(), openscad=tmp_path / "missing.exe")


def test_critic_sends_images_ids_limits_and_hashes(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    registry = Registry(report_payload(validation, preview))
    report = MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate, locked_paths=("/segments/0/design_slope",))
    assert report.model == "injected-vision-model"
    assert report.engineering_verdict_authority == "deterministic_validator"
    role, messages, kwargs = registry.calls[0]
    assert role == "critic_scad" and kwargs["required_capabilities"] == ["vision"]
    context = json.loads(messages[1]["content"][0]["text"])
    assert context["rule_context_sha256"] == validation.rule_context_sha256
    assert "MU-DRAIN-004" in context["constraint_ids"]
    assert context["geometry"]["pipes"]["pipe-001"]["outer_diameter_mm"] == 360
    assert context["views"]["top"]["projection"] == "plan/top"
    assert "metres" in context["coordinate_system"]["units"]
    assert context["locked_parameters"] == [{"path": "/segments/0/design_slope", "value": 0.003}]
    assert context["locked_paths"] == ["/segments/0/design_slope"]
    assert all(view in context["image_sha256"] for view in ("iso", "front", "top"))
    assert context["image_size_px"]["iso"] == [1, 1]
    assert len([part for part in messages[1]["content"] if part["type"] == "image_url"]) == 3
    assert "/segments/0/design_slope" not in context["allowed_patch_paths"]
    assert context["reviewable_parameter_paths"]["/segments/0/design_slope"] == 0.003


@pytest.mark.parametrize("change", ["unknown_object", "unknown_constraint", "unknown_view", "stale_revision", "stale_hash", "locked_patch", "unsupported_patch", "stale_old", "wrong_identity", "missing_parameter"])
def test_critic_rejects_unbound_or_unscoped_outputs(tmp_path, change):
    candidate, validation, preview = preview_case(tmp_path)
    payload = report_payload(validation, preview)
    if change == "unknown_object":
        payload["issues"][0]["object_ids"] = ["made-up"]
    elif change == "unknown_constraint":
        payload["issues"][0]["constraint_ids"] = ["made-up"]
    elif change == "unknown_view":
        payload["issues"][0]["view_id"] = "side"
    elif change == "stale_revision":
        payload["revision"] = 2
    elif change == "stale_hash":
        payload["candidate_sha256"] = "0" * 64
    else:
        patch = {"path": "/segments/0/design_slope", "old_value": 0.003, "value": 0.004, "reason": "A review proposal only.", "object_ids": ["pipe-001"], "constraint_ids": ["MU-DRAIN-004"]}
        if change == "unsupported_patch":
            patch["path"] = "/segments/0/outer_diameter_mm"
            patch["old_value"] = 360
        elif change == "stale_old":
            patch["old_value"] = 0.002
        elif change == "wrong_identity":
            patch["object_ids"] = ["pipe-002"]
        elif change == "missing_parameter":
            patch["path"] = "/nodes/1/invert_anchor_m"
            patch["old_value"] = None
            patch["value"] = 9.9
            patch["object_ids"] = ["junction"]
            patch["constraint_ids"] = ["MU-DRAIN-004"]
        payload["patches"] = [patch]
    registry = Registry(payload)
    with pytest.raises(MunicipalVisualError, match="twice"):
        MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate, locked_paths=("/segments/0",) if change == "locked_patch" else ())
    assert len(registry.calls) == 2


def test_clean_deterministic_network_can_review_visual_suspicion_and_parameter_check(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    assert validation.executable
    payload = report_payload(validation, preview)
    payload["issues"] = [
        {
            "observation_kind": "locatable_suspicion", "object_ids": ["pipe-001"], "constraint_ids": [],
            "view_id": "top", "evidence": "The branch appears to leave the manhole at a different angle.",
            "description": "Plan geometry needs human review.", "suggestion": "Compare the branch axis with the source drawing.",
            "uncertainty": "The label may hide the junction; this is not a measured angle.", "severity": "warning",
        },
        {
            "observation_kind": "parameter_check", "object_ids": ["pipe-001"], "constraint_ids": [],
            "parameter_paths": ["/segments/0/design_slope"], "view_id": "front",
            "evidence": "The projection cannot measure slope.", "description": "Check the recorded slope.",
            "suggestion": "Inspect the design slope against the deterministic evidence.", "uncertainty": "Elevation is not measurable from a single view.",
            "severity": "info",
        },
    ]
    registry = Registry(payload)
    report = MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate)
    assert [issue.observation_kind for issue in report.issues] == ["locatable_suspicion", "parameter_check"]
    assert report.issues[0].constraint_ids == ()
    context = json.loads(registry.calls[0][1][1]["content"][0]["text"])
    assert context["constraint_ids"]  # frozen rule identities are available even without failures
    assert context["reviewable_parameter_paths"]["/segments/0/design_slope"] == 0.003


def test_deterministic_reference_is_not_passed_off_as_visual_discovery(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    payload = report_payload(validation, preview)
    issue = payload["issues"][0]
    issue["evidence_source"] = "deterministic_reference"
    issue["constraint_ids"] = []
    with pytest.raises(MunicipalVisualError, match="twice"):
        MunicipalVLMCritic(Registry(payload)).critique(preview, validation=validation, candidate=candidate)

    issue["constraint_ids"] = ["MU-DRAIN-004"]
    report = MunicipalVLMCritic(Registry(payload)).critique(preview, validation=validation, candidate=candidate)
    assert report.issues[0].evidence_source == "deterministic_reference"


@pytest.mark.parametrize("change", ["no_parameter_path", "unsupported_parameter", "wrong_object"])
def test_parameter_check_requires_known_path_belonging_to_cited_object(tmp_path, change):
    candidate, validation, preview = preview_case(tmp_path)
    payload = report_payload(validation, preview)
    issue = {
        "observation_kind": "parameter_check", "object_ids": ["pipe-001"], "constraint_ids": [],
        "view_id": "top", "evidence": "Visibility is limited.", "description": "Check a parameter.",
        "suggestion": "Inspect the named value.", "uncertainty": "Projection only.", "severity": "info",
    }
    if change == "no_parameter_path":
        pass
    elif change == "unsupported_parameter":
        issue["parameter_paths"] = ["/segments/0/material"]
    else:
        issue["parameter_paths"] = ["/segments/0/design_slope"]
        issue["object_ids"] = ["pipe-002"]
    payload["issues"] = [issue]
    registry = Registry(payload)
    with pytest.raises(MunicipalVisualError, match="twice"):
        MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate)
    assert len(registry.calls) == 2


@pytest.mark.parametrize("change", ["no_issue", "nan_value"])
def test_patches_require_an_issue_and_finite_numeric_value(tmp_path, change):
    candidate, validation, preview = preview_case(tmp_path)
    payload = report_payload(validation, preview)
    patch = {"path": "/segments/0/design_slope", "old_value": 0.003, "value": 0.004,
             "reason": "Review proposal.", "object_ids": ["pipe-001"], "constraint_ids": ["MU-DRAIN-004"]}
    if change == "no_issue":
        payload["issues"] = []
    else:
        patch["value"] = float("nan")
    payload["patches"] = [patch]
    registry = Registry(payload)
    with pytest.raises(MunicipalVisualError, match="twice"):
        MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate)
    assert len(registry.calls) == 2


def test_aborted_bounded_call_does_not_spend_a_second_attempt(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)

    class AbortedRegistry(Registry):
        def chat(self, role, messages, **kwargs):
            self.calls.append((role, messages, kwargs))
            return {"aborted": True, "choices": [{"message": {"content": ""}}]}

    registry = AbortedRegistry({})
    diagnostics = {}
    with pytest.raises(MunicipalVisualError, match="aborted before a complete report"):
        MunicipalVLMCritic(registry).critique(
            preview, validation=validation, candidate=candidate, diagnostics_out=diagnostics
        )
    assert len(registry.calls) == 1
    assert diagnostics["business_outcome"] == "aborted_partial"
    assert diagnostics["request"]["image_count"] == 3
    assert all(item["encoded_bytes"] > 0 and item["width_px"] == 1 for item in diagnostics["request"]["images"])
    assert "data:image" not in json.dumps(diagnostics)


def test_deadline_before_structured_retry_does_not_start_another_call(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    import threading

    cancel = threading.Event()

    class CancelAfterFirst(Registry):
        def chat(self, role, messages, **kwargs):
            self.calls.append((role, messages, kwargs))
            if len(self.calls) == 1:
                cancel.set()
            return {"choices": [{"message": {"content": "not valid json"}}]}

    registry = CancelAfterFirst({})
    with pytest.raises(MunicipalVisualError, match="cancelled before the structured-output retry"):
        MunicipalVLMCritic(registry, cancel_event=cancel).critique(
            preview, validation=validation, candidate=candidate
        )
    assert len(registry.calls) == 1


def test_diagnostic_mode_can_disable_the_structured_retry(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    registry = Registry({})
    registry.payload = {"summary": "missing required fields"}
    with pytest.raises(MunicipalVisualError, match="after 1 attempts"):
        MunicipalVLMCritic(registry).critique(
            preview, validation=validation, candidate=candidate, max_validation_attempts=1
        )
    assert len(registry.calls) == 1


def test_changed_image_or_candidate_is_rejected_before_vlm(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    registry = Registry(report_payload(validation, preview))
    candidate.solver_input["segments"][0]["design_slope"] = 0.004
    with pytest.raises(MunicipalVisualError, match="hashes"):
        MunicipalVLMCritic(registry).critique(preview, validation=validation, candidate=candidate)
    assert registry.calls == []


def test_critic_model_and_usage_come_from_provider_metadata(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    payload = report_payload(validation, preview)
    payload["model"] = "untrusted-model-claim"
    payload["usage"] = {"total_tokens": 999999}

    class ProviderRegistry(Registry):
        def chat(self, role, messages, **kwargs):
            response = super().chat(role, messages, **kwargs)
            response.update(model_resolved="actual-configured-model", usage={"total_tokens": 123})
            return response

    report = MunicipalVLMCritic(ProviderRegistry(payload)).critique(preview, validation=validation, candidate=candidate)
    assert report.model == "actual-configured-model"
    assert report.usage == {"total_tokens": 123}

    class NoMetadataRegistry(Registry):
        def chat(self, role, messages, **kwargs):
            response = super().chat(role, messages, **kwargs)
            response.pop("model", None)
            return response

    report = MunicipalVLMCritic(NoMetadataRegistry(payload)).critique(preview, validation=validation, candidate=candidate)
    assert report.model == "unknown" and report.usage is None


def test_deterministic_repairs_fix_slope_and_endpoint_cover_without_mutation():
    candidate = physical_candidate()
    candidate.solver_input["segments"][0]["design_slope"] = -0.002
    candidate.solver_input["nodes"][0]["invert_anchor_m"] = 10.2
    before = deepcopy(candidate.solver_input)
    patches = propose_candidate_repairs(candidate)
    assert candidate.solver_input == before
    assert len(patches) == 2
    fixed = deepcopy(before)
    for patch in patches:
        group, index, field = patch["path"].strip("/").split("/")
        assert fixed[group][int(index)][field] == patch["old_value"]
        fixed[group][int(index)][field] = patch["value"]
    assert validate_utility_candidate(UtilityCandidate(solver_input=fixed)).executable


def test_repairs_respect_ancestor_locks_and_do_not_invent_dimensions():
    candidate = physical_candidate()
    candidate.solver_input["segments"][0]["design_slope"] = -0.002
    assert propose_candidate_repairs(candidate, locked_paths=("/segments",)) == []
    candidate = physical_candidate()
    candidate.solver_input["nodes"][0]["invert_anchor_m"] = 10.2
    assert propose_candidate_repairs(candidate, locked_paths=("/nodes/0/invert_anchor_m",)) == []
    for segment in candidate.solver_input["segments"]:
        segment.pop("outer_diameter_mm")
    assert propose_candidate_repairs(candidate) == []


def test_compiled_import_preserves_ids_and_leaves_missing_context_unknown():
    validation = validate_utility_candidate(physical_candidate())
    imported = compiled_to_candidate(validation.compiled_ir)
    assert {node["node_id"] for node in imported.solver_input["nodes"]} == {node.node_id for node in validation.compiled_ir.nodes}
    assert {segment["segment_id"] for segment in imported.solver_input["segments"]} == {segment.segment_id for segment in validation.compiled_ir.segments}
    assert imported.solver_input["source_ir_sha256"] == validation.compiled_ir.source_ir_sha256
    assert imported.solver_input["collision_context"] is None
    assert all(segment["surface_context"] is None for segment in imported.solver_input["segments"])


def test_critique_carries_requirement_brief_and_iteration_context(tmp_path):
    candidate, validation, preview = preview_case(tmp_path)
    registry = Registry(report_payload(validation, preview))
    report = MunicipalVLMCritic(registry).critique(
        preview,
        validation=validation,
        candidate=candidate,
        requirement_brief="keep the branch clear of the building",
        iteration_round=2,
        prior_iteration_patches=[{
            "path": "/segments/0/design_slope", "old_value": 0.003, "value": 0.004,
            "reason": "tighten slope after first review", "round": 1, "source": "automatic",
        }],
    )
    assert report.summary
    context = json.loads(registry.calls[0][1][1]["content"][0]["text"])
    assert context["requirement_brief"] == "keep the branch clear of the building"
    assert context["iteration_round"] == 2
    assert context["prior_iteration_patches"][0]["path"] == "/segments/0/design_slope"
    assert context["prior_iteration_patches"][0]["source"] == "automatic"
    system_text = registry.calls[0][1][0]["content"]
    assert "Anti-Oscillation" in system_text
    assert "patches as an empty array" in system_text
