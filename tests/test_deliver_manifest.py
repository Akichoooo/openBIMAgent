"""G2 unified Artifact Manifest and core deliver tests; all offline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openbimagent.core.loop import AgentLoop
from openbimagent.deliver.manifest import commit_delivery_manifest, verify_manifest_files
from openbimagent.orchestrator.artifacts import ImmutableArtifactError
from openbimagent.orchestrator.contracts import ArtifactManifest
from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.session.store import SessionStore


def _artifact(root, name: str, content: bytes = b"artifact") -> dict[str, object]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": name,
        "kind": "model",
        "media_type": "application/octet-stream",
        "sha256": hashlib.sha256(content).hexdigest(),
        "dependencies": [],
        "status": "completed",
    }


def test_commit_delivery_manifest_is_schema_valid_and_hash_verifiable(tmp_path) -> None:
    artifact = _artifact(tmp_path, "models/network.ifc", b"IFC4X3")
    result = commit_delivery_manifest(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:case-001",
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
        lineage_id="lineage-001",
        attempt_number=1,
    )

    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert validate_artifact("artifact_manifest", data) == []
    manifest = ArtifactManifest.model_validate(data)
    assert manifest.manifest_version == "1.1"
    assert manifest.domain_gate_status == "PASS"
    assert manifest.records[0].relative_path is not None
    assert manifest.records[0].relative_path.startswith("delivery-")
    assert manifest.records[0].media_type == "application/octet-stream"
    assert manifest.records[0].source_attempt_id == "attempt-001"
    assert manifest.records[0].generator is not None
    verify_manifest_files(manifest)


def test_repeat_deliver_same_key_and_semantics_reuses_manifest(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    kwargs = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:repeat",
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    first = commit_delivery_manifest(**kwargs)
    second = commit_delivery_manifest(**kwargs)
    assert first.reused is False
    assert second.reused is True
    assert second.manifest == first.manifest


def test_distinct_idempotency_keys_preserve_distinct_immutable_history(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    common = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    first = commit_delivery_manifest(**common, idempotency_key="delivery:first")
    second = commit_delivery_manifest(**common, idempotency_key="delivery:second")
    assert first.manifest_path != second.manifest_path
    assert first.manifest_path.is_file() and second.manifest_path.is_file()


def test_repeat_deliver_same_key_different_semantics_conflicts(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"v1")
    base = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:conflict",
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    commit_delivery_manifest(**base)
    (tmp_path / "report.json").write_bytes(b"v2")
    changed = _artifact(tmp_path, "report.json", b"v2")
    with pytest.raises(ImmutableArtifactError, match="不同工件语义"):
        commit_delivery_manifest(**{**base, "artifacts": [changed]})


def test_deliver_rejects_domain_gate_hash_path_and_partial_status(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    common = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:negative",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    with pytest.raises(ValueError, match="Domain Gate"):
        commit_delivery_manifest(**common, domain_gate_status="UNKNOWN")

    bad_hash = {**artifact, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="hash"):
        commit_delivery_manifest(**{**common, "artifacts": [bad_hash]}, domain_gate_status="PASS")

    outside = tmp_path.parent / "outside.ifc"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="相对路径"):
        commit_delivery_manifest(
            **{**common, "artifacts": [{**artifact, "path": str(outside)}]},
            domain_gate_status="PASS",
        )

    partial = {**artifact, "status": "partial"}
    with pytest.raises(ValueError, match="completed"):
        commit_delivery_manifest(
            **{**common, "artifacts": [partial]},
            domain_gate_status="PASS",
        )


def test_skipped_domain_gate_is_only_valid_when_no_gate_is_required(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    common = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        request_id="session-skipped",
        source_attempt_id="attempt-skipped",
    )
    result = commit_delivery_manifest(
        **common,
        idempotency_key="delivery:skipped-valid",
        domain_gate_status="SKIPPED",
        domain_gate_required=False,
    )
    assert result.manifest.domain_gate_status == "SKIPPED"
    assert validate_artifact(
        "artifact_manifest", result.manifest.model_dump(mode="json")
    ) == []

    with pytest.raises(ValueError, match="状态与交付上下文不一致"):
        commit_delivery_manifest(
            **common,
            idempotency_key="delivery:skipped-spoof",
            domain_gate_status="SKIPPED",
            domain_gate_required=True,
        )

    with pytest.raises(ValueError, match="状态与交付上下文不一致"):
        commit_delivery_manifest(
            **common,
            idempotency_key="delivery:pass-without-requirement",
            domain_gate_status="PASS",
            domain_gate_required=False,
        )


def test_delivery_recovers_published_artifact_before_manifest(tmp_path, monkeypatch) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    kwargs = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:recovery",
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    from openbimagent.orchestrator.artifacts import ArtifactStore

    original = ArtifactStore.write_manifest
    calls = 0

    def crash_once(self, **call_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected crash before manifest")
        return original(self, **call_kwargs)

    monkeypatch.setattr(ArtifactStore, "write_manifest", crash_once)
    with pytest.raises(RuntimeError, match="injected crash"):
        commit_delivery_manifest(**kwargs)
    recovered = commit_delivery_manifest(**kwargs)
    assert recovered.reused is False
    assert recovered.manifest_path.is_file()
    assert len(recovered.manifest.records) == 1


def test_manifest_tamper_is_detected_on_idempotent_retry(tmp_path) -> None:
    artifact = _artifact(tmp_path, "report.json", b"{}")
    kwargs = dict(
        workdir=tmp_path,
        artifacts=[artifact],
        idempotency_key="delivery:tamper",
        domain_gate_status="PASS",
        request_id="session-001",
        source_attempt_id="attempt-001",
    )
    result = commit_delivery_manifest(**kwargs)
    Path(result.manifest.records[0].path).write_bytes(b"tampered")
    with pytest.raises(ImmutableArtifactError, match="漂移"):
        commit_delivery_manifest(**kwargs)


def test_core_loop_deliver_returns_compact_manifest_view(tmp_path) -> None:
    artifact = _artifact(tmp_path, "model.ifc", b"IFC")
    session = SessionStore.create(tmp_path / "sessions")
    loop = AgentLoop(["deliver"], session, chat_fn=lambda **_: {}, workdir=tmp_path)
    result = loop._tool_deliver(
        {
            "artifacts": [artifact],
            "idempotency_key": "delivery:loop",
            "domain_gate_status": "PASS",
            "source_attempt_id": "attempt-loop",
        }
    )
    assert result["status"] == "ok"
    assert result["ui_view"]["record_count"] == 1
    assert result["ui_view"]["reused"] is False
    assert "manifest" in result["llm_view"]
