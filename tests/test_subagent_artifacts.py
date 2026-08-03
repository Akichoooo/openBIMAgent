"""Subagent Runtime v1 不可变工件与 manifest 测试。"""

import hashlib
import json

import pytest

from openbimagent.orchestrator.artifacts import ArtifactStore, ImmutableArtifactError
from openbimagent.schema_gate.gate import validate_artifact


def test_commit_text_records_hash_size_and_manifest(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.commit_text("agent-1", name="summary.md", kind="summary", content="完成")
    path = tmp_path / "artifacts" / "agent-1" / "summary.md"
    assert path.read_text(encoding="utf-8") == "完成"
    assert record.sha256 == hashlib.sha256("完成".encode()).hexdigest()
    assert record.size_bytes == len("完成".encode())

    manifest, manifest_path = store.write_manifest(request_id="req-1", agent_id="agent-1", records=(record,))
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert validate_artifact("artifact_manifest", data) == []
    assert manifest.records[0] == record


def test_immutable_artifact_rejects_overwrite(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.commit_text("agent-1", name="summary.md", kind="summary", content="v1")
    with pytest.raises(ImmutableArtifactError, match="拒绝覆盖"):
        store.commit_text("agent-1", name="summary.md", kind="summary", content="v2")
    assert (tmp_path / "artifacts" / "agent-1" / "summary.md").read_text(encoding="utf-8") == "v1"


def test_artifact_name_cannot_escape_run_directory(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.commit_text("agent-1", name="../../outside.txt", kind="output", content="safe")
    assert record.path == str((tmp_path / "artifacts" / "agent-1" / "outside.txt").resolve())
    assert not (tmp_path / "outside.txt").exists()


def test_atomic_create_does_not_repeat_long_destination_name_in_temp_path(tmp_path) -> None:
    artifact_root = tmp_path / ("nested-" + "x" * 10)
    store = ArtifactStore(artifact_root)
    record = store.commit_text(
        "agent-" + "a" * 36,
        name="checkpoint-" + "b" * 70 + ".bin",
        kind="side-effect-checkpoint",
        content="stable",
    )
    assert record.size_bytes == len("stable")
    assert record.sha256 == hashlib.sha256(b"stable").hexdigest()
