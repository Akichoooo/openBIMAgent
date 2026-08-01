"""确定性 domain_gate：PASS/FAIL/UNKNOWN/SKIPPED 四态。"""

from openbimagent.domain_gate import GateStatus, evaluate_domain_gate


def test_skips_when_no_hard_requirements() -> None:
    report = evaluate_domain_gate({}, {})
    assert report.status is GateStatus.SKIPPED
    assert report.ok is True


def test_passes_only_with_explicit_true_evidence() -> None:
    report = evaluate_domain_gate(
        {"clash_free": True, "slope_in_spec": True, "optional": False},
        {
            "clash_free": {"ok": True, "source": "solver-clash-v1"},
            "slope_in_spec": True,
        },
    )
    assert report.status is GateStatus.PASS
    assert report.ok is True
    assert report.passed == ("clash_free", "slope_in_spec")


def test_failure_beats_unknown_and_yields_rework() -> None:
    report = evaluate_domain_gate(
        {"clash_free": True, "slope_in_spec": True},
        {"clash_free": {"ok": False, "detail": "pipe-A 与 foundation-1 相交"}},
    )
    assert report.status is GateStatus.FAIL
    assert report.ok is False
    assert "clash_free" in report.failed[0]
    assert report.unknown == ("slope_in_spec",)
    assert "修复领域硬约束" in (report.rework_instruction or "")


def test_missing_evidence_is_unknown_not_fake_pass() -> None:
    report = evaluate_domain_gate(
        {"clash_free": True, "slope_in_spec": True},
        {"clash_free": True},
    )
    assert report.status is GateStatus.UNKNOWN
    assert report.ok is False
    assert report.unknown == ("slope_in_spec",)
    assert "Solver" in (report.rework_instruction or "")
