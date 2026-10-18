"""M1.5 T7 benchmark 的失败关闭边界与可重复总验收测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from openbimagent.benchmark.m1_5_t7 import (
    BenchmarkArtifactError,
    BenchmarkConclusion,
    build_benchmark_scenarios,
    run_m1_5_t7_benchmark,
    verify_benchmark_artifact,
)


@pytest.mark.parametrize(
    ("scenario_id", "conclusion"),
    [
        ("B4", BenchmarkConclusion.FAIL),
        ("B5", BenchmarkConclusion.FAIL),
        ("B6", BenchmarkConclusion.FAIL),
        ("B7", BenchmarkConclusion.UNKNOWN),
        ("B8", BenchmarkConclusion.REVIEW_REQUIRED),
    ],
)
def test_t7_negative_scenarios_fail_closed(tmp_path, scenario_id, conclusion) -> None:
    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=(scenario_id,),
        repetitions=2,
    )
    scenario = report.scenario(scenario_id)
    assert scenario.conclusion is conclusion
    assert scenario.observed_status == scenario.expected_status
    assert scenario.failure_reason
    if scenario_id == "B6":
        assert "有向环路" in scenario.failure_reason
    assert scenario.performance.solver_runs == 2
    assert verify_benchmark_artifact(scenario.artifact_path).scenario_id == scenario_id


def test_t7_missing_or_ambiguous_evidence_never_becomes_pass(tmp_path) -> None:
    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=("B7", "B8"),
        repetitions=2,
    )
    assert {item.scenario_id: item.conclusion for item in report.scenarios} == {
        "B7": BenchmarkConclusion.UNKNOWN,
        "B8": BenchmarkConclusion.REVIEW_REQUIRED,
    }


def test_t7_artifact_detects_frozen_input_or_result_tampering(tmp_path) -> None:
    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=("B1",),
        repetitions=2,
    )
    path = report.scenario("B1").artifact_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"]["request_id"] = "tampered"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(BenchmarkArtifactError, match="input_sha256"):
        verify_benchmark_artifact(path)


def test_t7_artifact_schema_rejects_unknown_envelope_fields(tmp_path) -> None:
    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=("B8",),
        repetitions=2,
    )
    path = report.scenario("B8").artifact_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(BenchmarkArtifactError, match="Schema Gate|unexpected"):
        verify_benchmark_artifact(path)


def test_t7_scenario_registry_is_complete_and_frozen() -> None:
    scenarios = build_benchmark_scenarios()
    assert tuple(item.scenario_id for item in scenarios) == tuple(f"B{index}" for index in range(1, 11))
    assert scenarios[-2].node_count >= 25
    assert scenarios[-1].node_count >= 100
    assert all(item.expected_status and item.rule_identity_sha256 for item in scenarios)
    for scenario in scenarios:
        if scenario.scenario_id == "B8":
            continue
        node_ids = {item["node_id"] for item in scenario.input_payload["nodes"]}
        segment_ids = {item["segment_id"] for item in scenario.input_payload["segments"]}
        assert node_ids.isdisjoint(segment_ids)


def test_t7_synonym_order_repeat_resume_and_offline_semantics(tmp_path) -> None:
    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=("B1", "B2", "B3", "B9", "B10"),
        repetitions=2,
    )
    for scenario in report.scenarios:
        assert scenario.conclusion is BenchmarkConclusion.PASS
        assert scenario.determinism.canonical_hash_stable is True
        assert scenario.determinism.repeated_execution_idempotent is True
        assert scenario.recovery.blender_resume_ok is True
        assert scenario.recovery.vectorworks_resume_ok is True
        assert scenario.semantic.offline_dual_host_equal is True
        assert scenario.semantic.ifc_ids_ok is True
        assert scenario.performance.duration_ms >= 0.0
        assert scenario.performance.solver_runs == 2


def test_t7_reuses_output_directory_without_stale_recovery_state(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "benchmark"
    first = run_m1_5_t7_benchmark(
        output_dir=output_dir,
        scenario_ids=("B1",),
        repetitions=2,
    )

    def reject_unlink(*args, **kwargs):
        raise AssertionError("benchmark recovery 不得依赖文件删除")

    monkeypatch.setattr(Path, "unlink", reject_unlink)
    second = run_m1_5_t7_benchmark(
        output_dir=output_dir,
        scenario_ids=("B1",),
        repetitions=2,
    )
    assert first.overall_status is BenchmarkConclusion.PASS
    assert second.overall_status is BenchmarkConclusion.PASS
    assert second.scenario("B1").recovery.blender_resume_ok is True
    assert second.scenario("B1").recovery.vectorworks_resume_ok is True


def test_t7_expected_status_cannot_be_forged_to_pass(tmp_path) -> None:
    scenarios = list(build_benchmark_scenarios())
    target = scenarios[6]
    scenarios[6] = target.model_copy(update={"expected_status": "PASS"})
    with pytest.raises(BenchmarkArtifactError, match="期望状态|expected"):
        run_m1_5_t7_benchmark(
            output_dir=tmp_path / "benchmark",
            scenarios=tuple(scenarios),
            scenario_ids=("B7",),
            repetitions=2,
        )


def test_t7_frozen_canonical_identity_cannot_be_forged(tmp_path) -> None:
    scenarios = list(build_benchmark_scenarios())
    target = scenarios[0]
    scenarios[0] = target.model_copy(update={"expected_canonical_sha256": "0" * 64})
    with pytest.raises(BenchmarkArtifactError, match="canonical SHA-256"):
        run_m1_5_t7_benchmark(
            output_dir=tmp_path / "benchmark",
            scenarios=tuple(scenarios),
            scenario_ids=("B1",),
            repetitions=2,
        )


def test_t7_reordered_registry_does_not_change_scenario_identity() -> None:
    scenarios = build_benchmark_scenarios()
    reversed_scenarios = tuple(reversed(deepcopy(scenarios)))
    assert {item.scenario_id: item.input_sha256 for item in scenarios} == {
        item.scenario_id: item.input_sha256 for item in reversed_scenarios
    }


def test_t7_ir_schema_versions_reference_immutable_snapshots() -> None:
    from openbimagent.benchmark.m1_5_t7 import (
        COMPILED_UTILITY_IR_SCHEMA_SHA256,
        CURRENT_IR_SCHEMA_VERSION,
        IR_SCHEMA_SNAPSHOTS,
        verify_ir_schema,
    )

    assert CURRENT_IR_SCHEMA_VERSION == "1.1"
    assert set(IR_SCHEMA_SNAPSHOTS) == {"1.0", "1.1"}
    # v1.0 是 git HEAD 归档快照，必须保持不可变身份。
    assert verify_ir_schema("1.0") == (
        "6230e208c3ce24ce59e3d205472763b82e6d5b2e26094ead60db36fb29f5287e"
    )
    assert verify_ir_schema("1.1") == COMPILED_UTILITY_IR_SCHEMA_SHA256
    with pytest.raises(BenchmarkArtifactError, match="未知"):
        verify_ir_schema("9.9")


def test_t7_archived_v10_identity_is_not_overwritten() -> None:
    from openbimagent.benchmark import m1_5_t7

    assert m1_5_t7._FROZEN_INPUT_SHA256_V1_0["B1"] == (
        "03f61c3baafaad0a338b88148e67ef51eb94d907a24fead1f7e23097813a14df"
    )
    assert m1_5_t7._FROZEN_INPUT_SHA256["B1"] == (
        "59b6fdfa2d3eeb0deddc202356b94d83195db29332b389442d99061ec1172c0e"
    )
    assert m1_5_t7._FROZEN_INPUT_SHA256_V1_0["B1"] != m1_5_t7._FROZEN_INPUT_SHA256["B1"]
    # B8 fixture 身份保持 v1.0 不变，冻结规则束与输入哈希因此继续有效。
    assert (
        m1_5_t7._FROZEN_INPUT_SHA256_V1_0["B8"]
        == m1_5_t7._FROZEN_INPUT_SHA256["B8"]
    )


def test_t7_schema_snapshot_tampering_is_rejected(tmp_path: Path) -> None:
    from openbimagent.benchmark import m1_5_t7

    path = tmp_path / "schema.json"
    path.write_bytes(b"{}")
    with pytest.raises(BenchmarkArtifactError, match="SHA-256"):
        m1_5_t7._verify_schema_file(path, "0" * 64, "test schema")
    with pytest.raises(BenchmarkArtifactError, match="不可读"):
        m1_5_t7._verify_schema_file(tmp_path / "missing.json", "0" * 64, "test schema")


def test_t7_artifact_declares_ir_schema_identity(tmp_path) -> None:
    from openbimagent.benchmark.m1_5_t7 import (
        BENCHMARK_VERSION,
        COMPILED_UTILITY_IR_SCHEMA_SHA256,
        CURRENT_IR_SCHEMA_VERSION,
    )

    report = run_m1_5_t7_benchmark(
        output_dir=tmp_path / "benchmark",
        scenario_ids=("B8",),
        repetitions=2,
    )
    payload = json.loads(report.scenario("B8").artifact_path.read_text(encoding="utf-8"))
    assert payload["artifact_version"] == BENCHMARK_VERSION == "1.1"
    definition = payload["result"]["scenario_definition"]
    assert definition["ir_schema_version"] == CURRENT_IR_SCHEMA_VERSION
    assert definition["ir_schema_sha256"] == COMPILED_UTILITY_IR_SCHEMA_SHA256
