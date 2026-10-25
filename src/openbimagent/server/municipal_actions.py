"""Concrete adapters for the revision-bound municipal workbench."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from openbimagent.server.municipal_workflow import WorkflowError
from openbimagent.utility.candidate import UtilityCandidate, propose_candidate_repairs

VISION_ROLE = "critic_scad"
"""Role resolved through the existing models.toml profile binding."""

VISION_TIMEOUT_S = 90
"""Per-action cap measured from the real diagnostics.

A 60 s run completed at 59.4 s while another spent 60.4 s in reasoning without a
single content chunk. 90 s is still below the global 120 s provider timeout and
remains a single bounded deadline, not a retry loop.
"""

VISION_RETRY_MAX = 1
"""One transport attempt per model. The critic itself allows one JSON repair retry."""


def _endpoint(provider: Any) -> str | None:
    base_url = getattr(provider, "base_url", None)
    if not base_url:
        return None
    if getattr(provider, "type", "") in {"openai-compatible", "openai-completions"}:
        return f"{str(base_url).rstrip('/')}/chat/completions"
    return str(base_url)


def _model_entry(registry: Any, model_name: str) -> dict[str, Any]:
    model = registry.models[model_name]
    provider = registry.providers[model.provider]
    env_name = provider.api_key_env
    return {
        "model": model_name,
        "provider": model.provider,
        "provider_type": provider.type,
        "endpoint": _endpoint(provider),
        "credential_env": env_name,
        "credential_present": bool(os.environ.get(env_name)),
        "configured_vision": "vision" in model.capabilities,
        "capabilities": list(model.capabilities),
    }


def _secret_values(registry: Any | None) -> list[str]:
    values: list[str] = []
    if registry is not None:
        for provider in registry.providers.values():
            value = os.environ.get(provider.api_key_env)
            if value:
                values.append(value)
    for name, value in os.environ.items():
        if name.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET")) and value and len(value) >= 8:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _safe_error(exc: BaseException, registry: Any | None = None) -> str:
    """Provider errors may quote upstream response text; never echo a key."""
    text = str(exc)
    for secret in _secret_values(registry):
        text = text.replace(secret, "***")
    return text[:2000]


def _resolve_vision(
    *, role: str = VISION_ROLE, config_path: Path | str | None = None
) -> tuple[dict[str, Any], Any | None]:
    """Resolve the configured role without calling a model.

    Returns a configuration view that is safe to expose (no key values) and, when
    the effective model is callable, a bounded registry clone. The original
    global registry/profile is never mutated.
    """
    from openbimagent.providers.registry import DEFAULT_CONFIG_PATH, ModelRegistry
    from openbimagent.vision.scad_loop import DEFAULT_OPENSCAD

    configured_openscad = os.environ.get("OPENSCAD_PATH") or shutil.which("openscad.com") or shutil.which("openscad") or str(DEFAULT_OPENSCAD)
    config: dict[str, Any] = {
        "status": "configuration_error",
        "role": role,
        "profile": None,
        "configured_model": None,
        "effective_model": None,
        "provider": None,
        "provider_type": None,
        "endpoint": None,
        "credential_env": None,
        "credential_present": False,
        "configured_vision": False,
        "requested_chain": [],
        "vision_chain": [],
        "usable_chain": [],
        "model_entries": [],
        "skipped": [],
        "retry_max": VISION_RETRY_MAX,
        "timeout_s": VISION_TIMEOUT_S,
        "critic_validation_attempts": 2,
        "openscad": {"path": configured_openscad, "available": Path(configured_openscad).is_file()},
        "message": "",
    }
    try:
        registry = ModelRegistry.load(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    except Exception as exc:  # noqa: BLE001 — profile/TOML errors are configuration diagnostics
        config["message"] = f"模型配置无法加载：{_safe_error(exc)}。请检查 config/models.toml 与 OPENBIMAGENT_PROFILE。"
        return config, None

    try:
        configured_model = registry.model_name_for_role(role)
    except ValueError as exc:
        config["message"] = f"{_safe_error(exc)}。请检查当前 profile 的角色绑定。"
        return config, None

    requested_chain = registry.fallback_chain(configured_model)
    entries = [_model_entry(registry, name) for name in requested_chain]
    vision_chain = [entry for entry in entries if entry["configured_vision"]]
    config.update(
        profile=registry.active_profile,
        configured_model=configured_model,
        requested_chain=requested_chain,
        vision_chain=[entry["model"] for entry in vision_chain],
        model_entries=entries,
        skipped=[
            {**entry, "reason": "no_vision_capability"}
            for entry in entries
            if not entry["configured_vision"]
        ],
    )
    if not vision_chain:
        config["status"] = "model_not_vision_capable"
        config["message"] = (
            f"当前 profile {registry.active_profile!r} 的 {role} 模型及其降级链均未声明视觉能力。"
            "请在 config/models.toml 中把 critic_scad 或其降级链绑定到具备图像输入的模型。"
        )
        return config, None

    # Prefer the role-bound model when it can see images. If that binding is
    # text-only, use the first declared vision-capable fallback in config order.
    effective_index = next(
        (index for index, entry in enumerate(vision_chain) if entry["model"] == configured_model),
        0,
    )
    effective = vision_chain[effective_index]
    config.update(
        effective_model=effective["model"],
        provider=effective["provider"],
        provider_type=effective["provider_type"],
        endpoint=effective["endpoint"],
        credential_env=effective["credential_env"],
        credential_present=effective["credential_present"],
        configured_vision=effective["configured_vision"],
    )
    if not effective["credential_present"]:
        config["status"] = "credentials_missing"
        config["message"] = (
            f"视觉模型 {effective['model']}（provider {effective['provider']}）缺少凭据："
            f"环境变量 {effective['credential_env']} 未设置或为空。"
            "请在现有「设置 → 模型与供应商」中补充该 key（会写入 .env），或检查 .env 的 "
            "OPENBIMAGENT_PROFILE。三视图仍会生成并保留。"
        )
        return config, None

    fallback = next(
        (
            entry
            for entry in vision_chain[effective_index + 1 :]
            if entry["credential_present"]
        ),
        None,
    )
    config["usable_chain"] = [effective["model"], *([fallback["model"]] if fallback else [])]
    config["skipped"].extend(
        {**entry, "reason": "missing_credential"}
        for entry in vision_chain[effective_index + 1 :]
        if not entry["credential_present"]
    )
    config.update(
        status="ready",
        fallback_model=fallback["model"] if fallback else None,
        message=(
            f"配置可用：{effective['model']}（{effective['provider']}）；"
            "该能力位来自配置声明，真实图像输入是否有效以本次调用结果为准。"
        ),
    )

    settings = registry.model_dump()
    settings["profiles"][registry.active_profile][role] = effective["model"]
    settings["fallbacks"][effective["model"]] = [fallback["model"]] if fallback else []
    settings["resilience"]["retry"]["max"] = VISION_RETRY_MAX
    settings["resilience"]["timeout_s"] = min(int(settings["resilience"]["timeout_s"]), VISION_TIMEOUT_S)
    bounded = ModelRegistry.model_validate(settings)
    return config, bounded


def vision_configuration(*, role: str = VISION_ROLE, config_path: Path | str | None = None) -> dict[str, Any]:
    """Expose the current visual-call configuration without invoking a model."""
    config, _ = _resolve_vision(role=role, config_path=config_path)
    return config


def _diagnostic_identity(
    config: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    *,
    reported_model: str | None = None,
) -> dict[str, Any]:
    """Map the attempted model alias to its own provider/endpoint.

    The service may return a different model identifier. The alias is what the
    gateway actually attempted; the response model is separate evidence.
    """
    config = config or {}
    diagnostics = diagnostics or {}
    attempts = diagnostics.get("attempts") or []
    alias = diagnostics.get("model_alias") or diagnostics.get("selected_model")
    if not alias and attempts:
        alias = attempts[-1].get("model")
    entries = config.get("model_entries") or []
    if not alias and reported_model and any(entry.get("model") == reported_model for entry in entries):
        alias = reported_model
    entry = next((item for item in entries if item.get("model") == alias), None)
    return {
        "model_alias": alias,
        "provider": (entry or {}).get("provider") or config.get("provider"),
        "endpoint": (entry or {}).get("endpoint") or config.get("endpoint"),
        "model_entry": entry,
    }


def _failure(
    state: dict,
    *,
    code: str,
    stage: str,
    message: str,
    error: str = "",
    preview: Any | None = None,
    config: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    vlm_called: bool = False,
    duration_ms: int | None = None,
    render_duration_ms: int | None = None,
    vlm_duration_ms: int | None = None,
) -> dict:
    """A failed check is still evidence: it never reports a pass or score."""
    identity = _diagnostic_identity(config, diagnostics)
    response_model = (diagnostics or {}).get("response_model")
    return {
        "protocol_version": "1.0",
        "status": "unavailable",
        "failure_code": code,
        "failure_stage": stage,
        "summary": message,
        "error": error,
        "revision": state.get("revision"),
        "candidate_sha256": state.get("candidate_sha256"),
        "workflow_candidate_sha256": state.get("candidate_sha256"),
        "issues": [],
        "patches": [],
        "limitations": [
            "视觉检查未完成或未通过结构化校验，不能作为工程通过依据。",
            "确定性检查仍是唯一工程判定来源；未执行视觉检查不等于检查通过。",
        ],
        "model": response_model,
        "model_alias": identity["model_alias"],
        "model_requested": (config or {}).get("configured_model"),
        "model_effective": (config or {}).get("effective_model"),
        "model_attempted": identity["model_alias"],
        "provider": identity["provider"],
        "endpoint": identity["endpoint"],
        "profile": (config or {}).get("profile"),
        "usage": None,
        "vlm_called": vlm_called,
        "duration_ms": duration_ms,
        "render_duration_ms": render_duration_ms,
        "vlm_duration_ms": vlm_duration_ms,
        "diagnostics": diagnostics or {},
        "engineering_verdict_authority": "deterministic_validator",
        "preview": preview.model_dump(mode="json") if preview is not None else None,
        "vision_config": config or {},
    }


def _attempt_was_sent(diagnostics: dict[str, Any] | None) -> bool:
    """A model call counts as attempted only if a transport request started."""
    attempts = (diagnostics or {}).get("attempts") or []
    return any(
        bool(attempt.get("request_sent"))
        or attempt.get("http_status") is not None
        or attempt.get("first_event_at_ms") is not None
        for attempt in attempts
    )


def _classify_vlm_exception(
    exc: BaseException, diagnostics: dict[str, Any] | None = None
) -> tuple[str, str, bool]:
    """Return (failure_code, failure_stage, call_was_attempted).

    Prefer structured call events over exception text. Exception text is only a
    fallback for provider-specific details that the transport layer cannot see.
    """
    from openbimagent.providers.registry import ProviderKeyError

    called = _attempt_was_sent(diagnostics)
    if isinstance(exc, ProviderKeyError):
        return "credentials_missing", "vision_configuration", False
    attempts = (diagnostics or {}).get("attempts") or []
    statuses = [attempt.get("http_status") for attempt in attempts if attempt.get("http_status") is not None]
    if 429 in statuses:
        return "rate_limited", "vision_model", called
    if any(attempt.get("aborted") or attempt.get("cancel_reason") for attempt in attempts):
        return "timeout", "vision_model", called
    text = _safe_error(exc).lower()
    if isinstance(exc, httpx.TimeoutException) or "timeout" in text or "timed out" in text or "aborted" in text or "cancelled before" in text:
        return "timeout", "vision_model", called
    if "429" in text or "too many requests" in text:
        return "rate_limited", "vision_model", called
    if "无满足能力" in text or ("vision" in text and "capabilit" in text):
        return "model_not_vision_capable", "vision_configuration", called
    if "preview, candidate and validation hashes" in text or "changed after rendering" in text or "stale revision" in text:
        return "stale_evidence", "vision_evidence", called
    if "invalid structured observations" in text or "无法解析为 json" in text or "输出缺" in text:
        return "response_invalid", "vision_model", called
    return "provider_error", "vision_model", called


def propose_repairs(state: dict) -> list[dict]:
    ontology = state.get("ontology") if isinstance(state.get("ontology"), dict) else {}
    return propose_candidate_repairs(
        UtilityCandidate(solver_input=state["candidate"]),
        rule_context=state["rule_context"],
        locked_paths=[item["path"] for item in state["locks"]],
        operator_priority=ontology.get("operator_priority"),
    )


def check_visual(
    state: dict,
    output_dir: Path,
    *,
    requirement_brief: str | None = None,
    iteration_round: int = 1,
    prior_iteration_patches: list[dict] | None = None,
) -> dict:
    from openbimagent.vision.municipal import MunicipalVLMCritic, render_municipal_preview

    started = time.perf_counter()
    if not state["validation"].get("compiled_ir"):
        return _failure(
            state,
            code="missing_compiled_ir",
            stage="prerequisites",
            message="当前版本还缺少生成三视图所需的工程参数；视觉模型未调用，参数草稿保留。",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    config, registry = _resolve_vision()
    candidate = UtilityCandidate(solver_input=state["candidate"])
    render_started = time.perf_counter()
    try:
        preview = render_municipal_preview(
            state["validation"]["compiled_ir"],
            output_dir,
            candidate_sha256=candidate.canonical_sha256(),
            revision=state["revision"],
            collision_context=state["candidate"].get("collision_context"),
        )
    except Exception as exc:  # noqa: BLE001 — render failure is an explicit user-visible outcome
        return _failure(
            state,
            code="render_failed",
            stage="render",
            message="三视图生成失败，视觉模型未调用；参数草稿保留。请检查 OpenSCAD 路径和模型几何。",
            error=_safe_error(exc, registry),
            config=config,
            duration_ms=int((time.perf_counter() - started) * 1000),
            render_duration_ms=int((time.perf_counter() - render_started) * 1000),
        )
    render_ms = int((time.perf_counter() - render_started) * 1000)

    # 具身主动相机：默认挂接 1 张 NBV 冲突正交特写 + 1 张工程纵断面剖切图。
    # 3D 特写依赖 OpenSCAD，不可用时如实记录 render_unavailable，绝不伪造。
    extra_views: list[dict[str, Any]] = []
    try:
        from openbimagent.vision.municipal import render_custom_camera_views

        extra_views = render_custom_camera_views(
            state["validation"]["compiled_ir"],
            state["candidate"],
            [
                {"mode": "nbv_clash_normal", "view_id": "nbv_clash_normal"},
                {"mode": "longitudinal_profile", "view_id": "longitudinal_profile"},
            ],
            output_dir,
        )
    except Exception:  # noqa: BLE001 — 主动相机失败不阻断基础三视图检查
        extra_views = []

    if registry is None or config.get("status") != "ready":
        code = str(config.get("status") or "configuration_error")
        return _failure(
            state,
            code=code,
            stage="vision_configuration",
            message=str(config.get("message") or "视觉模型配置不可用；三视图已生成并保留。"),
            error=str(config.get("message") or ""),
            preview=preview,
            config=config,
            duration_ms=int((time.perf_counter() - started) * 1000),
            render_duration_ms=render_ms,
        )

    vlm_started = time.perf_counter()
    cancel_event = threading.Event()
    timer = threading.Timer(VISION_TIMEOUT_S, cancel_event.set)
    diagnostics: dict[str, Any] = {"deadline_s": VISION_TIMEOUT_S}
    timer.start()
    try:
        report = MunicipalVLMCritic(
            registry,
            cancel_event=cancel_event,
            cancel_reason="deadline",
            reasoning_level="low",
        ).critique(
            preview,
            validation=state["validation"],
            candidate=candidate,
            locked_paths=[item["path"] for item in state["locks"]],
            diagnostics_out=diagnostics,
            requirement_brief=requirement_brief if requirement_brief is not None else state.get("requirement_brief"),
            iteration_round=iteration_round,
            prior_iteration_patches=prior_iteration_patches,
            extra_views=extra_views,
            active_camera_trajectory=extra_views,
        )
    except Exception as exc:  # noqa: BLE001 — classify provider/render/schema failures explicitly
        code, stage, called = _classify_vlm_exception(exc, diagnostics)
        messages = {
            "credentials_missing": "三视图已生成，但视觉模型未调用：凭据缺失。参数草稿和三视图已保留。",
            "model_not_vision_capable": "三视图已生成，但当前配置没有可用的视觉模型；视觉模型未调用。",
            "timeout": "三视图已生成，视觉模型调用超过有界时限，未得到可审阅结果；不会自动重试。",
            "rate_limited": "三视图已生成，但视觉服务限流（429），本次未得到可审阅结果；不会自动重试。",
            "response_invalid": "三视图已生成，视觉模型返回了无法校验的结构化结果，未应用任何建议。",
            "stale_evidence": "视觉结果与当前版本证据不一致，已拒绝；请对当前版本重新检查。",
            "provider_error": "三视图已生成，视觉模型调用失败，未得到可审阅结果。",
        }
        return _failure(
            state,
            code=code,
            stage=stage,
            message=messages.get(code, messages["provider_error"]),
            error=_safe_error(exc, registry),
            preview=preview,
            config=config,
            diagnostics=diagnostics,
            vlm_called=called,
            duration_ms=int((time.perf_counter() - started) * 1000),
            render_duration_ms=render_ms,
            vlm_duration_ms=int((time.perf_counter() - vlm_started) * 1000),
        )
    finally:
        timer.cancel()
    vlm_ms = int((time.perf_counter() - vlm_started) * 1000)

    identity = _diagnostic_identity(config, diagnostics, reported_model=report.model)
    diagnostics["business_outcome"] = diagnostics.get("business_outcome", "report_validated")
    diagnostics["selected_provider"] = identity["provider"]
    diagnostics["selected_endpoint"] = identity["endpoint"]
    diagnostics["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
    diagnostics["vlm_duration_ms"] = vlm_ms
    payload = report.model_dump(mode="json")
    payload.update(
        {
            "status": "review_required" if report.issues else "observed",
            "failure_code": None,
            "failure_stage": None,
            "vlm_called": True,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "render_duration_ms": render_ms,
            "vlm_duration_ms": vlm_ms,
            "model": report.model,
            "model_alias": identity["model_alias"],
            "model_attempted": identity["model_alias"],
            "model_requested": config.get("configured_model"),
            "model_effective": config.get("effective_model"),
            "provider": identity["provider"],
            "endpoint": identity["endpoint"],
            "profile": config.get("profile"),
            "diagnostics": diagnostics,
            "preview": preview.model_dump(mode="json"),
            "workflow_candidate_sha256": state["candidate_sha256"],
            "vision_config": config,
            "active_camera_trajectory": [
                {key: value for key, value in view.items() if key in {"view_id", "mode", "status", "path", "camera", "image_sha256"}}
                for view in extra_views
                if isinstance(view, dict)
            ],
            "next_camera_requests": [dict(item) for item in (getattr(report, "next_camera_requests", None) or []) if isinstance(item, dict)],
        }
    )
    return payload


def run_iterative_refinement_loop(
    session_id: str,
    max_rounds: int = 3,
    *,
    store: Any | None = None,
) -> dict:
    """Workflow entry point for bounded deterministic + visual self-refinement."""
    from openbimagent.server.municipal_workflow import (
        MunicipalWorkflowStore,
        get_default_workflow_store,
    )

    if store is None:
        store = get_default_workflow_store()
    if store is None:
        from openbimagent.server.runs import _sessions_dir

        store = MunicipalWorkflowStore(_sessions_dir() / "municipal")
    return store.iterative_refine(
        session_id,
        {"max_rounds": max_rounds},
        propose_repairs,
        check_visual,
    )


DELIVERY_CHECKER_VERSION = "openbimagent.municipal.delivery-1.0"
IDS_SUPPORTED_SCOPE = (
    "IDS 1.0 subset actually implemented here: entity/property/attribute/partOf"
    "(IFCRELASSIGNSTOGROUP) facets plus IfcRelConnectsPorts identity checks. This is not full "
    "IDS-standard coverage."
)
DOMAIN_RULE_SCOPE = (
    "Deterministic checks bound to the confirmed CompiledUtilityIR and the frozen rule context; "
    "this is not an independent engineering compliance check of the IFC file content."
)


def _not_attempted_ifc(ir: dict, reason: str) -> dict:
    return {
        "status": "not_attempted",
        "generation": {"status": "not_attempted", "error": reason},
        "generation_source": "compiled_utility_ir_derived",
        "representation": "IfcCircleHollowProfileDef + IfcExtrudedAreaSolid",
        "native_host_export": False,
        "affected_ids": [str(item.get("segment_id")) for item in (ir.get("segments") or [])],
        "checks": {
            "ifc_schema": {"status": "not_run"},
            "ids_semantic": {"status": "not_run"},
            "geometry": {"status": "not_run"},
            "domain_rules": {"status": "not_run", "scope": DOMAIN_RULE_SCOPE},
            "status": "not_run",
        },
        "error": reason,
    }


def _frozen_rule_set(delivery_context: dict | None) -> Any:
    """Return the task-frozen MunicipalRuleSet, never the current rule sources."""
    raw = (delivery_context or {}).get("rule_context")
    if not raw:
        return None
    from openbimagent.utility.candidate import FrozenRuleContext

    try:
        return FrozenRuleContext.model_validate(raw).municipal_rule_set
    except Exception:  # noqa: BLE001 - an invalid frozen context cannot grant eligibility
        return None


def _delivery_evidence(compiled: Any, delivery_context: dict | None) -> Any:
    from openbimagent.assembly.rule_projection import RuleDeliveryEvidence

    context = delivery_context or {}
    rules = _frozen_rule_set(context)
    issues = context.get("issues")
    if (
        issues is not None
        and context.get("candidate_sha256")
        and context.get("validation_sha256")
        and context.get("rule_context_sha256")
    ):
        return RuleDeliveryEvidence.from_validation(
            evidence_source="confirmed_validation_result",
            rule_context_sha256=context["rule_context_sha256"],
            source_ir_sha256=compiled.canonical_sha256(),
            issues=issues,
            checker_version=DELIVERY_CHECKER_VERSION,
            candidate_sha256=context["candidate_sha256"],
            validation_sha256=context["validation_sha256"],
            rules=rules,
        )
    ir_issues = [item.model_dump(mode="json") for item in compiled.evidence]
    if rules is None:
        raise ValueError("缺少任务冻结规则上下文，不能形成交付规则证据")
    return RuleDeliveryEvidence.from_validation(
        evidence_source="compiled_ir_evidence_only",
        rule_context_sha256=context.get("rule_context_sha256") or rules.canonical_sha256,
        source_ir_sha256=compiled.canonical_sha256(),
        issues=ir_issues,
        checker_version=DELIVERY_CHECKER_VERSION,
        candidate_sha256=None,
        validation_sha256=None,
        rules=rules,
    )


def build_ifc_delivery_stage(
    ir: dict,
    execution_id: str,
    delivery_context: dict | None,
    output_root: Path,
    attempt: int = 1,
) -> dict:
    from openbimagent.assembly.semantic_snapshot import SnapshotHost, expected_semantic_snapshot
    from openbimagent.deliver.ifc_ids import ValidationStatus, build_ifc_ids_package
    from openbimagent.utility.contracts import CompiledUtilityIR

    context = delivery_context or {}
    stage = _not_attempted_ifc(ir, "IFC 尚未生成。")
    stage.update(
        {
            "status": "failed",
            "attempt": attempt,
            "revision": context.get("revision"),
            "candidate_sha256": context.get("candidate_sha256"),
        }
    )
    try:
        compiled = CompiledUtilityIR.model_validate(ir)
        if compiled.protocol_version == "1.0":
            raise ValueError(
                "IFC 实体生成要求 CompiledUtilityIR 1.1（含显式内外径与轴线语义）"
            )
        evidence = _delivery_evidence(compiled, delivery_context)
        snapshot = expected_semantic_snapshot(compiled, SnapshotHost.IFC)
        package = build_ifc_ids_package(
            snapshot,
            output_dir=Path(output_root) / "ifc" / execution_id / f"attempt-{attempt:02d}",
            ir=compiled,
            delivery_evidence=evidence,
        )
        schema = package.schema_report
        geometry = package.geometry_report
        ifc_failures = [
            finding
            for finding in package.report.findings
            if finding.status is ValidationStatus.FAIL
        ]
        geometry_failures = [
            finding
            for finding in (geometry.findings if geometry is not None else ())
            if finding.status is ValidationStatus.FAIL
        ]
        checks_ok = (
            schema is not None
            and schema.ok
            and package.report.ok
            and geometry is not None
            and geometry.ok
        )
        stage["source_ir_sha256"] = evidence.source_ir_sha256
        stage["generation"] = {
            "status": "generated",
            "attempt": attempt,
            "ifc_path": str(package.ifc_path),
            "ifc_sha256": hashlib.sha256(package.ifc_path.read_bytes()).hexdigest(),
            "ifc_bytes": package.ifc_path.stat().st_size,
            "ids_path": str(package.ids_path),
            "report_path": str(package.report_path),
            "schema_report_path": (
                str(package.schema_report_path)
                if package.schema_report_path is not None
                else None
            ),
            "geometry_report_path": (
                str(package.geometry_report_path)
                if package.geometry_report_path is not None
                else None
            ),
            "rule_evidence_path": str(package.evidence_path),
        }
        stage["checks"] = {
            "ifc_schema": {
                "status": schema.status.value.lower() if schema is not None else "not_run",
                "schema": schema.ifc_schema if schema is not None else None,
                "validator": schema.validator if schema is not None else None,
                "scope": schema.scope if schema is not None else None,
                "issue_count": schema.issue_count if schema is not None else 0,
                "duplicate_global_ids": (
                    list(schema.duplicate_global_ids) if schema is not None else []
                ),
                "invalid_global_ids": (
                    list(schema.invalid_global_ids) if schema is not None else []
                ),
                "issues": (
                    [item.model_dump(mode="json") for item in schema.issues[:20]]
                    if schema is not None
                    else []
                ),
            },
            "ids_semantic": {
                "status": package.report.status.value.lower(),
                "findings": len(package.report.findings),
                "failures": [
                    f"{item.stable_id} {item.field_path}: {item.detail}"
                    for item in ifc_failures[:20]
                ],
                "scope": IDS_SUPPORTED_SCOPE,
            },
            "geometry": {
                "status": geometry.status.value.lower() if geometry is not None else "not_run",
                "checked_segments": geometry.checked_segment_count if geometry is not None else 0,
                "segments": (
                    [item.model_dump(mode="json") for item in geometry.segments]
                    if geometry is not None
                    else []
                ),
                "failures": [
                    f"{item.stable_id} {item.check}: {item.detail}"
                    for item in geometry_failures[:20]
                ],
            },
            "domain_rules": {
                "status": evidence.decision_status,
                "production_verification": evidence.production_verification,
                "checked_rule_count": evidence.checked_rule_count,
                "scope": DOMAIN_RULE_SCOPE,
            },
            "status": "pass" if checks_ok else "fail",
        }
        stage["status"] = "verified" if checks_ok else "failed"
        stage["rule_evidence"] = evidence.model_dump(mode="json")
        if not checks_ok:
            schema_failures = []
            if schema is not None and not schema.ok:
                schema_failures = [
                    f"schema:{item.message}" for item in schema.issues[:10]
                ] or [
                    f"schema:duplicate_global_ids={list(schema.duplicate_global_ids)}"
                ]
            stage["affected_ids"] = sorted(
                {
                    item.stable_id
                    for item in [*ifc_failures, *geometry_failures]
                }
            )
            stage["error"] = "; ".join(
                [
                    *schema_failures,
                    *[
                        f"{item.stable_id}:{item.detail}"
                        for item in [*ifc_failures, *geometry_failures][:10]
                    ],
                ]
            ) or "IFC 检查未通过"
    except Exception as exc:  # noqa: BLE001 - IFC failure must never hide the host receipt
        stage["status"] = "failed"
        stage["generation"] = {"status": "failed", "error": str(exc)}
        stage["error"] = str(exc)
    return stage


def build_delivery_layers(receipt: dict, ifc_stage: dict) -> dict:
    geometry_snapshot = receipt.get("geometry_snapshot") or {}
    geometry_status = (
        receipt.get("geometry_status") or geometry_snapshot.get("status") or "unverified"
    )
    semantic_verified = bool(
        receipt.get("semantic_verified")
        or (receipt.get("semantic_comparison_report") or {}).get("status") == "PASS"
    )
    checks = ifc_stage.get("checks") or {}
    return {
        "host": {
            "status": receipt.get("status"),
            "host_status": receipt.get("host_status") or receipt.get("status"),
            "semantic_verified": semantic_verified,
            "geometry_status": geometry_status,
            "dimension_verified": receipt.get("dimension_verified"),
            "topology_status": receipt.get("topology_status") or "unverified",
        },
        "ifc_generation": {"status": (ifc_stage.get("generation") or {}).get("status")},
        "ifc_checks": {
            "status": checks.get("status"),
            "ifc_schema": (checks.get("ifc_schema") or {}).get("status"),
            "ids_semantic": (checks.get("ids_semantic") or {}).get("status"),
            "geometry": (checks.get("geometry") or {}).get("status"),
        },
        "domain_rules": {
            "status": (checks.get("domain_rules") or {}).get("status"),
            "production_verification": (checks.get("domain_rules") or {}).get(
                "production_verification"
            ),
        },
    }


def export_revision(
    ir: dict,
    host: str,
    execution_id: str,
    *,
    delivery_context: dict | None = None,
) -> dict:
    # An output UUID is allocated by the store. Never overwrite a demo or user file.
    if host == "vectorworks":
        from openbimagent.assembly.vectorworks_host_executor import execute_vectorworks_export

        root = os.environ.get("OPENBIMAGENT_VW_AUTHORIZED_ROOT", "").strip()
        if not root:
            raise WorkflowError("尚未配置 Vectorworks 授权输出目录和 runner。请先完成宿主连接配置。", 503)
        output_root = Path(root)
        receipt = execute_vectorworks_export(ir, output_path=output_root / f"{execution_id}.vwx")
    elif host == "blender":
        from openbimagent.assembly.blender_host_executor import (
            DEFAULT_AUTHORIZED_ROOT,
            execute_blender_export,
        )

        output_root = Path(
            os.environ.get("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT") or DEFAULT_AUTHORIZED_ROOT
        )
        receipt = execute_blender_export(
            ir,
            output_path=output_root / f"{execution_id}.blend",
            authorized_root=output_root,
        )
    else:
        raise WorkflowError("不支持的执行宿主。", 422)
    # This is the real host receipt; an expected/fake snapshot is not delivery evidence.
    if receipt.get("status") == "failed":
        return {
            **receipt,
            "status": "failed",
            "errors": [*receipt.get("errors", []), "宿主执行未完成。"],
            "ifc": _not_attempted_ifc(ir, "宿主执行失败，未生成 IFC。"),
        }
    if not receipt.get("semantic_snapshot") and not receipt.get("geometry_snapshot"):
        return {
            **receipt,
            "status": "failed",
            "errors": [*receipt.get("errors", []), "宿主未返回语义或几何读回证据。"],
            "ifc": _not_attempted_ifc(ir, "宿主未返回读回证据，未生成 IFC。"),
        }
    geometry_snapshot = receipt.get("geometry_snapshot") or {}
    geometry_status = (
        receipt.get("geometry_status") or geometry_snapshot.get("status") or "unverified"
    )
    semantic_verified = bool(
        receipt.get("semantic_verified")
        or (receipt.get("semantic_comparison_report") or {}).get("status") == "PASS"
    )
    ifc_stage = build_ifc_delivery_stage(ir, execution_id, delivery_context, output_root, attempt=1)
    return {
        **receipt,
        "status": "partial",
        "host_status": receipt.get("host_status") or receipt.get("status"),
        "semantic_verified": semantic_verified,
        "geometry_status": geometry_status,
        "geometry_verified": geometry_status in {"verified", "not_applicable"},
        "ifc": ifc_stage,
        "delivery_layers": build_delivery_layers(receipt, ifc_stage),
        "delivery_scope": (
            "宿主实体与几何读回以适配器实际返回的网格顶点为限；节点/检查井为示意对象。"
            "IFC 为 IR 派生的直管空腔拉伸实体；IFC 通过不会把宿主 topology=unverified 改成通过。"
        ),
    }


def retry_ifc_delivery(
    ir: dict,
    receipt: dict,
    delivery_context: dict | None,
    execution_id: str,
    attempt: int = 2,
) -> dict:
    """仅重建 IFC：不调用宿主，也不改动宿主输出文件。

    每次重试使用独立 attempt 目录，旧 IFC/报告/哈希不会被覆盖。
    """
    output_path = str(receipt.get("output_path") or "")
    if not output_path:
        raise WorkflowError("宿主回执缺少输出路径，无法定位 IFC 输出目录。")
    return build_ifc_delivery_stage(
        ir, execution_id, delivery_context, Path(output_path).parent, attempt=attempt
    )
