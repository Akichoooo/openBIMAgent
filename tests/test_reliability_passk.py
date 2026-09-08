"""A2 pass^k 可靠性指标测试：确定性内核 pass^k == pass^1（零方差）。"""

from __future__ import annotations

import pytest

from openbimagent.benchmark.reliability_passk import run_passk


def test_passk_deterministic_kernel_zero_variance(tmp_path) -> None:
    # 单轻量场景 B1，k=2 独立重跑：确定性内核应 pass^1==pass^2==100%
    report = run_passk(("B1",), k=2, work_dir=tmp_path, repetitions=2)
    assert report.k == 2
    assert report.measured is True
    assert report.pass_at_1 == 100.0
    assert report.pass_at_k == 100.0
    assert report.determinism_confirmed is True
    assert report.provenance
    # 明细：B1 两次独立运行都成功
    assert report.per_scenario[0].successes == 2


def test_passk_rejects_invalid_k(tmp_path) -> None:
    with pytest.raises(ValueError):
        run_passk(("B1",), k=0, work_dir=tmp_path)


def test_passk_rejects_empty_scenarios(tmp_path) -> None:
    with pytest.raises(ValueError):
        run_passk((), k=2, work_dir=tmp_path)
