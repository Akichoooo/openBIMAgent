"""验收判定收紧的回归测试：任意 partial、缺对象/缺哈希/哈希不等都不得通过。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parents[1] / "tools" / "vw_real_acceptance.py"
_SPEC = importlib.util.spec_from_file_location("vw_real_acceptance_verdict", _HARNESS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)

HASH_A = "a" * 64
HASH_B = "b" * 64
PLAN_SHA = "c" * 64
IR_SHA = "d" * 64
SEMANTIC_IDS = (
    "sys-sewage",
    "mh-001",
    "mh-002",
    "mh-003",
    "mh-001-out",
    "mh-002-in",
    "mh-002-out",
    "mh-003-in",
    "pipe-001",
    "pipe-002",
)
EXPECTED = {
    "operation_count": 22,
    "plan_sha256": PLAN_SHA,
    "pipe_ids": ["pipe-001", "pipe-002"],
    "semantic_ids": sorted(SEMANTIC_IDS),
}


def _run_result(tmp_path: Path, **overrides: object) -> dict:
    output = tmp_path / "two-pipes.vwx"
    output.write_bytes(b"VWX")
    result: dict = {
        "status": "partial",
        "host_status": "partial",
        "applied_operations": 22,
        "plan_id": "plan-1",
        "plan_sha256": PLAN_SHA,
        "source_ir_sha256": IR_SHA,
        "errors": [],
        "dimension_verified": True,
        "geometry_failures": [],
        "topology_status": "unverified",
        "geometry_snapshot": {
            "topology_status": "unverified",
            "objects": [
                {
                    "stable_id": "pipe-001",
                    "object_type": "pipe_segment",
                    "status": "dimension_verified_topology_unverified",
                    "dimension_status": "verified",
                    "topology_status": "unverified",
                    "measured": {"vertex_sha256": HASH_A},
                    "failures": [],
                },
                {
                    "stable_id": "pipe-002",
                    "object_type": "pipe_segment",
                    "status": "dimension_verified_topology_unverified",
                    "dimension_status": "verified",
                    "topology_status": "unverified",
                    "measured": {"vertex_sha256": HASH_B},
                    "failures": [],
                },
            ],
        },
        "semantic_snapshot": {"objects": [{"stable_id": item} for item in SEMANTIC_IDS]},
        "semantic_verified": True,
        "semantic_comparison_report": {"status": "PASS", "compared_object_count": len(SEMANTIC_IDS)},
        "output_path": str(output),
        "output_sha256": "e" * 64,
        "output_bytes": 3,
    }
    result.update(overrides)
    return result


def _reopen_inputs(tmp_path: Path, **overrides: object) -> tuple[dict, dict, dict]:
    run = _run_result(tmp_path)
    run_receipt = {
        "plan_id": run["plan_id"],
        "plan_sha256": run["plan_sha256"],
        "source_ir_sha256": run["source_ir_sha256"],
        "applied_operations": run["applied_operations"],
        "semantic_snapshot": run["semantic_snapshot"],
    }
    run_classification = harness.classify(run)
    reopen = _run_result(tmp_path, **overrides)
    return run_receipt, run_classification, reopen


def test_run_verdict_passes_only_when_every_check_holds(tmp_path: Path) -> None:
    verdict = harness.build_run_verdict(_run_result(tmp_path), EXPECTED)
    assert verdict["passed"] is True
    assert verdict["topology_graded_unverified"] is True
    assert verdict["vertex_hashes"] == {"pipe-001": HASH_A, "pipe-002": HASH_B}


@pytest.mark.parametrize(
    ("override", "blocker"),
    [
        ({"status": "failed"}, "execution_status"),
        ({"errors": ["host write failed"]}, "execution_errors"),
        ({"applied_operations": 18}, "applied_operations"),
        ({"plan_sha256": "f" * 64}, "plan_sha256"),
        ({"dimension_verified": False}, "dimension verification"),
        ({"geometry_failures": ["bad dimension"]}, "dimension verification"),
        ({"semantic_verified": False}, "semantic verification"),
    ],
)
def test_run_verdict_blocks_partial_failures(
    tmp_path: Path, override: dict, blocker: str
) -> None:
    verdict = harness.build_run_verdict(_run_result(tmp_path, **override), EXPECTED)
    assert verdict["passed"] is False
    assert any(blocker in item for item in verdict["blocking_failures"])


def test_run_verdict_blocks_missing_hashes_and_incomplete_objects(tmp_path: Path) -> None:
    missing_objects = _run_result(tmp_path)
    missing_objects["geometry_snapshot"]["objects"] = missing_objects["geometry_snapshot"]["objects"][:1]
    verdict = harness.build_run_verdict(missing_objects, EXPECTED)
    assert verdict["passed"] is False
    assert any("geometry_objects" in item for item in verdict["blocking_failures"])

    duplicated = _run_result(tmp_path)
    objects = duplicated["geometry_snapshot"]["objects"]
    objects[1]["stable_id"] = "pipe-001"
    verdict = harness.build_run_verdict(duplicated, EXPECTED)
    assert verdict["passed"] is False
    assert any("duplicate" in item for item in verdict["blocking_failures"])

    unhashed = _run_result(tmp_path)
    unhashed["geometry_snapshot"]["objects"][0]["measured"] = {}
    verdict = harness.build_run_verdict(unhashed, EXPECTED)
    assert verdict["passed"] is False
    assert any("vertex_sha256 missing" in item for item in verdict["blocking_failures"])

    missing_semantic = _run_result(tmp_path)
    missing_semantic["semantic_snapshot"]["objects"] = (
        missing_semantic["semantic_snapshot"]["objects"][:-1]
    )
    verdict = harness.build_run_verdict(missing_semantic, EXPECTED)
    assert verdict["passed"] is False
    assert any("semantic_objects" in item for item in verdict["blocking_failures"])


def test_run_verdict_requires_output_document(tmp_path: Path) -> None:
    result = _run_result(tmp_path)
    Path(result["output_path"]).unlink()
    verdict = harness.build_run_verdict(result, EXPECTED)
    assert verdict["passed"] is False
    assert any("output" in item for item in verdict["blocking_failures"])


def test_reopen_verdict_passes_with_matching_hashes(tmp_path: Path) -> None:
    run_receipt, run_classification, reopen = _reopen_inputs(tmp_path)
    verdict = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=run_classification,
        reopen_result=reopen,
        method="start-process-arg",
        operator_confirmed=False,
    )
    assert verdict["readback_verified"] is True
    assert verdict["vertex_hashes_match"] is True
    assert verdict["disk_reopen_claimed"] is True


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("hash_mismatch", "vertex_sha256 mismatch"),
        ("missing_hash", "vertex_sha256 missing"),
        ("missing_object", "reopen geometry_objects"),
        ("duplicate_object", "duplicate"),
        ("dimension_failure", "dimension verification"),
        ("semantic_failure", "semantic verification"),
        ("execution_error", "execution_errors"),
        ("different_plan", "same IR/plan"),
    ],
)
def test_reopen_verdict_blocks_untrustworthy_readback(
    tmp_path: Path, mutation: str, blocker: str
) -> None:
    run_receipt, run_classification, reopen = _reopen_inputs(tmp_path)
    objects = reopen["geometry_snapshot"]["objects"]
    if mutation == "hash_mismatch":
        objects[1]["measured"]["vertex_sha256"] = "f" * 64
    elif mutation == "missing_hash":
        objects[0]["measured"] = {}
    elif mutation == "missing_object":
        reopen["geometry_snapshot"]["objects"] = objects[:1]
    elif mutation == "duplicate_object":
        objects[1]["stable_id"] = "pipe-001"
    elif mutation == "dimension_failure":
        reopen["dimension_verified"] = False
    elif mutation == "semantic_failure":
        reopen["semantic_verified"] = False
    elif mutation == "execution_error":
        reopen["errors"] = ["resume refused"]
    elif mutation == "different_plan":
        reopen["plan_sha256"] = "f" * 64
    verdict = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=run_classification,
        reopen_result=reopen,
        method="start-process-arg",
        operator_confirmed=False,
    )
    assert verdict["readback_verified"] is False
    assert any(blocker in item for item in verdict["blocking_failures"])


def test_reopen_verdict_rejects_empty_baseline_equality(tmp_path: Path) -> None:
    run_receipt, run_classification, reopen = _reopen_inputs(tmp_path)
    empty_classification = {"objects": []}
    reopen["geometry_snapshot"]["objects"] = []
    reopen["semantic_verified"] = False
    reopen["semantic_comparison_report"] = {"status": "FAIL", "compared_object_count": 0}
    verdict = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=empty_classification,
        reopen_result=reopen,
        method="start-process-arg",
        operator_confirmed=False,
    )
    assert verdict["readback_verified"] is False
    assert any("empty-set" in item for item in verdict["blocking_failures"])


def test_attach_mode_never_claims_a_disk_reopen(tmp_path: Path) -> None:
    run_receipt, run_classification, reopen = _reopen_inputs(tmp_path)
    verdict = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=run_classification,
        reopen_result=reopen,
        method="attach-live-session",
        operator_confirmed=True,
    )
    assert verdict["readback_verified"] is True
    assert verdict["disk_reopen_claimed"] is False
    assert "active-document readback" in verdict["confirmation_source"]


def test_in_place_mode_requires_operator_attestation(tmp_path: Path) -> None:
    run_receipt, run_classification, reopen = _reopen_inputs(tmp_path)
    unconfirmed = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=run_classification,
        reopen_result=reopen,
        method="in-place-file-reopen",
        operator_confirmed=False,
    )
    assert unconfirmed["readback_verified"] is True
    assert unconfirmed["disk_reopen_claimed"] is False
    assert "not attested" in unconfirmed["confirmation_source"]

    confirmed = harness.build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=run_classification,
        reopen_result=reopen,
        method="in-place-file-reopen",
        operator_confirmed=True,
    )
    assert confirmed["disk_reopen_claimed"] is True
    assert "--operator-confirmed" in confirmed["confirmation_source"]


def test_archive_artifacts_preserves_previous_evidence(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text('{"kind": "run"}', encoding="utf-8")
    (tmp_path / "receipt.json").write_text('{"status": "partial"}', encoding="utf-8")
    archived = harness._archive_artifacts(tmp_path, "reopen")
    assert archived is not None
    assert (archived / "summary.json").is_file()
    assert (archived / "receipt.json").is_file()
    assert harness._archive_artifacts(tmp_path / "empty", "reopen") is None
