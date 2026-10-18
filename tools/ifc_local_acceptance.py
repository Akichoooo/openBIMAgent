"""Local IFC delivery acceptance: generate, save, reopen, and classify.

NOT part of the product path and not part of the test suite. Each run writes a
new ``attempt-*`` directory under the acceptance root (old evidence is never
overwritten), containing a real IFC file plus schema/IDS/geometry/rule-evidence
reports and a summary.

The input is synthetic test geometry (horizontal pipe + plan-oblique pipe with
slope). No human confirmation exists for this synthetic input and the summary
says so explicitly; it is not a real project model and not research data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def acceptance_payload() -> dict:
    return {
        "protocol_version": "0.1",
        "request_id": "ifc-local-acceptance",
        "source_ir_sha256": "b" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:IFC-ACCEPTANCE",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "system_id": "sys-sewage",
        "system_name": "IFC acceptance sewage",
        "nodes": [
            {"node_id": "mh-001", "node_type": "manhole", "x_m": 0.0, "y_m": 0.0,
             "ground_elevation_m": 11.0, "invert_anchor_m": 9.97, "connection_radius_m": 0.6},
            {"node_id": "mh-002", "node_type": "manhole", "x_m": 10.0, "y_m": 0.0,
             "ground_elevation_m": 11.0, "invert_anchor_m": None, "connection_radius_m": 0.6},
            {"node_id": "mh-003", "node_type": "outlet", "x_m": 18.0, "y_m": 6.0,
             "ground_elevation_m": 11.0, "invert_anchor_m": None, "connection_radius_m": 0.6},
        ],
        "segments": [
            {"segment_id": "pipe-001", "start_node_id": "mh-001", "end_node_id": "mh-002",
             "diameter_mm": 300.0, "inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0,
             "material": "concrete", "design_slope": 0.003, "surface_context": "driveway"},
            {"segment_id": "pipe-002", "start_node_id": "mh-002", "end_node_id": "mh-003",
             "diameter_mm": 300.0, "inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0,
             "material": "concrete", "design_slope": 0.04, "surface_context": "driveway"},
        ],
        "collision_context": {"coverage": "complete", "obstacles": []},
    }


def acceptance_verdict(summary: dict) -> tuple[bool, list[str]]:
    """Explicit per-layer success criteria for the exit code."""
    blockers: list[str] = []
    layers = summary.get("layers") or {}
    if layers.get("ifc_schema") != "PASS":
        blockers.append(f"ifc_schema: {layers.get('ifc_schema')!r}")
    if layers.get("ids_semantic") != "PASS":
        blockers.append(f"ids_semantic: {layers.get('ids_semantic')!r}")
    if layers.get("geometry_readback") != "PASS":
        blockers.append(f"geometry_readback: {layers.get('geometry_readback')!r}")
    generation = summary.get("generation") or {}
    if (generation.get("status") or "") != "generated":
        blockers.append(f"ifc_generation: {generation.get('status')!r}")
    ifc_path = Path(str(generation.get("ifc_path") or ""))
    if not ifc_path.is_file():
        blockers.append("ifc file missing")
    else:
        digest = hashlib.sha256(ifc_path.read_bytes()).hexdigest()
        if digest != generation.get("ifc_sha256"):
            blockers.append("ifc file sha256 does not match the recorded hash")
    evidence = summary.get("rule_evidence") or {}
    if evidence.get("identity_kind") != "model_rule_evidence_aggregate":
        blockers.append("rule evidence identity_kind is missing/invalid")
    if not evidence.get("evidence_aggregate_sha256"):
        blockers.append("rule evidence aggregate hash is missing")
    return (not blockers, blockers)


def _next_attempt_dir(root: Path, stamp: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    counter = 1
    while True:
        candidate = root / f"attempt-{stamp}-{counter:02d}"
        if not candidate.exists():
            return candidate
        counter += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="", help="output root (default: out/ifc-acceptance)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else REPO_ROOT / "out" / "ifc-acceptance"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    attempt_dir = _next_attempt_dir(root, stamp)
    attempt_dir.mkdir(parents=True)

    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from openbimagent.assembly.rule_projection import RuleDeliveryEvidence
        from openbimagent.assembly.semantic_snapshot import SnapshotHost, expected_semantic_snapshot
        from openbimagent.deliver.ifc_ids import build_ifc_ids_package
        from openbimagent.utility.candidate import validate_utility_candidate

        validation = validate_utility_candidate({"solver_input": acceptance_payload()})
        if validation.compiled_ir is None:
            raise ValueError("candidate did not compile")
        compiled = validation.compiled_ir
        evidence = RuleDeliveryEvidence.from_validation(
            evidence_source="compiled_ir_evidence_only",
            rule_context_sha256=validation.rule_context_sha256,
            source_ir_sha256=compiled.canonical_sha256(),
            issues=[issue.model_dump(mode="json") for issue in validation.issues],
            checker_version="openbimagent.ifc-local-acceptance-1.1",
            candidate_sha256=None,
            validation_sha256=None,
            rules=validation.rule_context.municipal_rule_set,
        )
        snapshot = expected_semantic_snapshot(compiled, SnapshotHost.IFC)
        package = build_ifc_ids_package(
            snapshot,
            output_dir=attempt_dir / "delivery",
            ir=compiled,
            delivery_evidence=evidence,
        )
        schema = package.schema_report
        generation = {
            "status": "generated",
            "ifc_path": str(package.ifc_path),
            "ifc_sha256": hashlib.sha256(package.ifc_path.read_bytes()).hexdigest(),
            "ifc_bytes": package.ifc_path.stat().st_size,
        }
        summary = {
            "acceptance_root": str(root),
            "attempt_dir": str(attempt_dir),
            "kind": "ifc-local-acceptance",
            "synthetic_test_geometry": True,
            "not_real_project_data": True,
            "confirmation_claim": (
                "none: synthetic acceptance input; no human confirmation was performed"
            ),
            "reproduce": (
                "uv run python tools/ifc_local_acceptance.py --root "
                f"\"{root.as_posix()}\""
            ),
            "compiled_ir_sha256": compiled.canonical_sha256(),
            "candidate_sha256": validation.candidate_sha256,
            "rule_evidence": evidence.model_dump(mode="json"),
            "generation": generation,
            "ifc_path": generation["ifc_path"],
            "ifc_sha256": generation["ifc_sha256"],
            "ifc_bytes": generation["ifc_bytes"],
            "ids_path": str(package.ids_path),
            "ids_report_path": str(package.report_path),
            "ids_status": package.report.status.value,
            "ids_findings": len(package.report.findings),
            "schema_report_path": str(package.schema_report_path),
            "schema_status": schema.status.value if schema is not None else "not_run",
            "schema_issues": schema.issue_count if schema is not None else 0,
            "schema_duplicate_global_ids": (
                list(schema.duplicate_global_ids) if schema is not None else []
            ),
            "geometry_report_path": str(package.geometry_report_path),
            "geometry_status": package.geometry_report.status.value if package.geometry_report else "not_run",
            "geometry_segments": (
                [item.model_dump(mode="json") for item in package.geometry_report.segments]
                if package.geometry_report
                else []
            ),
            "layers": {
                "ifc_schema": schema.status.value if schema is not None else "not_run",
                "ids_semantic": package.report.status.value,
                "geometry_readback": (
                    package.geometry_report.status.value if package.geometry_report else "not_run"
                ),
                "domain_rules": {
                    "decision_status": evidence.decision_status,
                    "production_verification": evidence.production_verification,
                    "scope": "IR-bound deterministic checks; not an independent IFC compliance check",
                },
            },
        }
        passed, blockers = acceptance_verdict(summary)
        summary["verdict"] = {"passed": passed, "blocking_failures": blockers}
        (attempt_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except Exception as exc:  # noqa: BLE001 - failure report must be saved
        failure = {
            "acceptance_root": str(root),
            "attempt_dir": str(attempt_dir),
            "kind": "ifc-local-acceptance",
            "synthetic_test_geometry": True,
            "not_real_project_data": True,
            "confirmation_claim": (
                "none: synthetic acceptance input; no human confirmation was performed"
            ),
            "status": "failed",
            "error": str(exc),
            "verdict": {"passed": False, "blocking_failures": [f"generation failed: {exc}"]},
        }
        (attempt_dir / "summary.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
