"""Local IFC acceptance tool: layered verdict, non-overwriting attempts, exit codes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "ifc_local_acceptance.py"
_SPEC = importlib.util.spec_from_file_location("ifc_local_acceptance", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def test_acceptance_verdict_requires_all_layers(tmp_path: Path) -> None:
    ifc_file = tmp_path / "case.ifc"
    ifc_file.write_bytes(b"IFC")
    import hashlib

    digest = hashlib.sha256(ifc_file.read_bytes()).hexdigest()
    good = {
        "layers": {
            "ifc_schema": "PASS",
            "ids_semantic": "PASS",
            "geometry_readback": "PASS",
        },
        "generation": {
            "status": "generated",
            "ifc_path": str(ifc_file),
            "ifc_sha256": digest,
        },
        "rule_evidence": {"identity_kind": "model_rule_evidence_aggregate", "evidence_aggregate_sha256": "a" * 64},
    }
    passed, blockers = tool.acceptance_verdict(good)
    assert passed is True and blockers == []

    for layer in ("ifc_schema", "ids_semantic", "geometry_readback"):
        broken = json.loads(json.dumps(good))
        broken["layers"][layer] = "FAIL"
        passed, blockers = tool.acceptance_verdict(broken)
        assert passed is False and any(layer in item for item in blockers)

    tampered = json.loads(json.dumps(good))
    tampered["generation"]["ifc_sha256"] = "b" * 64
    passed, blockers = tool.acceptance_verdict(tampered)
    assert passed is False and any("sha256" in item for item in blockers)

    no_rule_identity = json.loads(json.dumps(good))
    no_rule_identity["rule_evidence"] = {}
    passed, blockers = tool.acceptance_verdict(no_rule_identity)
    assert passed is False and any("rule evidence" in item for item in blockers)


def test_acceptance_runs_create_new_attempts_without_overwriting(tmp_path: Path) -> None:
    assert tool.main(["--root", str(tmp_path)]) == 0
    first = sorted(tmp_path.glob("attempt-*/summary.json"))
    assert len(first) == 1
    first_payload = json.loads(first[0].read_text(encoding="utf-8"))
    assert first_payload["verdict"]["passed"] is True
    assert first_payload["confirmation_claim"].startswith("none")
    assert first_payload["layers"]["ifc_schema"] == "PASS"

    assert tool.main(["--root", str(tmp_path)]) == 0
    second = sorted(tmp_path.glob("attempt-*/summary.json"))
    assert len(second) == 2
    assert json.loads(first[0].read_text(encoding="utf-8")) == first_payload
    latest = json.loads(second[-1].read_text(encoding="utf-8"))
    assert latest["verdict"]["passed"] is True
    assert latest["attempt_dir"] != first_payload["attempt_dir"]
    assert Path(latest["ifc_path"]).is_file()
    assert Path(latest["schema_report_path"]).is_file()
