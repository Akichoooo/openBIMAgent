"""M1.5 T7 可重复领域 benchmark 与失败关闭验收。

B1-B10 冻结输入、期望工程状态、规则身份与 canonical hash。runner 只调用现有
Solver、typed host plan、离线 executor、SemanticSnapshot 和 IFC/IDS 主链；离线
Vectorworks 证据在工件中明确标记为 offline，不用于关闭真实 G6。
"""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from openbimagent.assembly.blender_plan import (
    BlenderBuilder,
    BlenderReceiptStatus,
    FakeBlenderExecutor,
)
from openbimagent.assembly.rule_projection import RuleProjectionIdentity
from openbimagent.assembly.semantic_snapshot import (
    FakeBlenderSemanticExecutor,
    FakeVectorworksSemanticExecutor,
    compare_semantic_snapshots,
)
from openbimagent.assembly.vectorworks_plan import (
    FakeVectorworksExecutor,
    ReceiptStatus,
    VectorworksBuilder,
)
from openbimagent.deliver.ifc_ids import build_ifc_ids_package
from openbimagent.domain_gate import GateStatus
from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility import (
    EvidenceRuleSelectionStatus,
    RouteSolveStatus,
    UtilitySolverError,
    build_clearance_exception_approval,
    build_municipal_rule_evidence_bundle,
    build_verified_municipal_rule,
    compile_municipal_rule_evidence_bundle,
    compile_municipal_rule_set,
    select_municipal_rule,
    solve_grid_route_t6,
    solve_hydraulic_network,
    solve_network_gravity_utility,
)

BENCHMARK_VERSION = "1.0"
COMPILED_UTILITY_IR_SCHEMA_SHA256 = "6230e208c3ce24ce59e3d205472763b82e6d5b2e26094ead60db36fb29f5287e"
_FROZEN_RULE_IDENTITY_SHA256 = "d0ce27a57125bb35a292b84e684d7527d06c1cc80ff389bc53efab5e974028f6"
_EXPECTED = {
    "B1": "PASS",
    "B2": "PASS",
    "B3": "PASS",
    "B4": "FAIL",
    "B5": "FAIL",
    "B6": "FAIL",
    "B7": "UNKNOWN",
    "B8": "REVIEW_REQUIRED",
    "B9": "PASS",
    "B10": "PASS",
}
_FROZEN_INPUT_SHA256 = {
    "B1": "03f61c3baafaad0a338b88148e67ef51eb94d907a24fead1f7e23097813a14df",
    "B2": "c1a90eb5ed0c7e659aa6bd40bd56db58800e2f168a936152cbbc01f614eb9114",
    "B3": "6c5df6d28c59988430a19859e73f99a3c8c746a5184d383a8adba6a3183f645c",
    "B4": "6137143059b57a671f2a7401edcb851f6d9380f92e5f09363b4a80e008cf032c",
    "B5": "c3048dead87907a5c4cab4f2572a447be9b79c550a03dad0477055c56eafc875",
    "B6": "b1d1a3326b712a355a1b3f9faa54eb60276b768292142ccb0558018b03cb077c",
    "B7": "0faf16887ac8ee427354c35db0b6b886cc58588e643cd3e3ccdaa141216723a0",
    "B8": "fca823250835e2799eaa648a67658affda3c542a8b225d78b6c5c72ebcc21f58",
    "B9": "cfeeaa2a0e0a3fb30cc0c313442efd943833938a910334fef5daed201031c0a5",
    "B10": "8dd2c75c0d7d4456ea4a8fa9a7b3824336ccdfcaf26538fbc3a5c9d290c3bcf4",
}
_EXPECTED_FAILURE_MARKER = {
    "B1": "",
    "B2": "",
    "B3": "",
    "B4": "抬升冲突",
    "B5": "不连通子图",
    "B6": "有向环路",
    "B7": "collision_context",
    "B8": "多个 T6 规则同时适用",
    "B9": "",
    "B10": "",
}
_FROZEN_CANONICAL_SHA256 = {
    "B1": "bdef739bbcbd05e599ff4ed9baacf5c7eab8745cc96d9b53014e57ec33535e20",
    "B2": "8bd37ae8a76dab5102920ccfc845199cdb0780c0aee26fdfcd2467d36a53be02",
    "B3": "5e672a929c6e03a52e5d5a97b200536ad435ab8d59cc25890140be5e4ec1553c",
    "B4": None,
    "B5": None,
    "B6": None,
    "B7": "01609bfbdaf55bdd4a77da5ecdcbbd53c534489c94ac7b64c916109c6582d49e",
    "B8": "47aacb26cdd8dc44dcdca3ac5e9727a7bae557bb6554ed156fe511f3b713536b",
    "B9": "5dd6361daee0a335f0448631b8bb3b5df5c7255fec0da00422b079e8718808a4",
    "B10": "3944e94c620686dc087f5503e8f658631ee1998da18605b2c9ee303c734a2952",
}


class BenchmarkArtifactError(ValueError):
    """benchmark 工件身份、状态或 hash 漂移。"""


class BenchmarkConclusion(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SKIPPED = "SKIPPED"


class BenchmarkScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^B(?:10|[1-9])$")
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    input_version: str = Field(pattern=r"^1\.0$")
    expected_status: str = Field(pattern=r"^(PASS|FAIL|UNKNOWN|REVIEW_REQUIRED)$")
    expected_failure_reason: str
    rule_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_payload: dict[str, Any]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_canonical_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    node_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    coverage: tuple[str, ...]


class DeterminismMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_hash_stable: bool
    repeated_execution_idempotent: bool
    canonical_sha256: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{64})?$")
    repeated_runs: int = Field(ge=1)


class RecoveryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str
    blender_resume_ok: bool
    vectorworks_resume_ok: bool
    failure_injected_after_operations: int = Field(ge=1)


class SemanticMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_kind: str = "offline_compatibility"
    offline_dual_host_equal: bool
    compared_object_count: int = Field(ge=0)
    ifc_ids_ok: bool
    real_vectorworks_g6_closed: bool = False


class PerformanceMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_ms: float = Field(ge=0)
    solver_runs: int = Field(ge=1)
    node_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)


class BenchmarkScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    expected_status: str
    observed_status: str
    conclusion: BenchmarkConclusion
    failure_reason: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    determinism: DeterminismMetrics
    recovery: RecoveryMetrics
    semantic: SemanticMetrics
    performance: PerformanceMetrics
    artifact_path: Path


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_version: str = BENCHMARK_VERSION
    gate: str = "M1.5 T7"
    overall_status: BenchmarkConclusion
    scenarios: tuple[BenchmarkScenarioResult, ...]
    report_path: Path
    real_vectorworks_g6_status: str = "DEFERRED / IN PROGRESS"

    def scenario(self, scenario_id: str) -> BenchmarkScenarioResult:
        matches = [item for item in self.scenarios if item.scenario_id == scenario_id]
        if len(matches) != 1:
            raise KeyError(f"benchmark report 无法唯一定位 {scenario_id}")
        return matches[0]


class VerifiedBenchmarkArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    input_sha256: str
    result_sha256: str
    artifact_sha256: str


def build_benchmark_scenarios() -> tuple[BenchmarkScenario, ...]:
    schema_path = Path(__file__).resolve().parents[3] / "schemas" / "compiled_utility_ir.schema.json"
    try:
        schema_sha256 = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BenchmarkArtifactError(f"CompiledUtilityIR Schema 不可读: {schema_path}: {exc}") from exc
    if schema_sha256 != COMPILED_UTILITY_IR_SCHEMA_SHA256:
        raise BenchmarkArtifactError(
            "CompiledUtilityIR Schema SHA-256 漂移: "
            f"{schema_sha256} != {COMPILED_UTILITY_IR_SCHEMA_SHA256}"
        )
    bundle = compile_municipal_rule_evidence_bundle()
    rule_identity = bundle.canonical_sha256
    if rule_identity != _FROZEN_RULE_IDENTITY_SHA256:
        raise BenchmarkArtifactError(
            "T7 冻结 MunicipalRuleEvidenceBundle SHA-256 漂移: "
            f"{rule_identity} != {_FROZEN_RULE_IDENTITY_SHA256}"
        )
    payloads = {
        "B1": _series_payload(),
        "B2": _branch_payload(),
        "B3": _merge_payload(),
        "B4": _anchor_conflict_payload(),
        "B5": _disconnected_payload(),
        "B6": _cycle_payload(),
        "B7": _missing_evidence_payload(),
        "B8": _ambiguous_rule_payload(),
        "B9": _diamond_chain_payload(diamonds=6, request_id="benchmark-b9-26-nodes"),
        "B10": _diamond_chain_payload(diamonds=25, request_id="benchmark-b10-102-nodes"),
    }
    metadata = {
        "B1": ("3井串联", "normal", "", ("series", "route_bend", "complex_surface", "clearance_approval", "design_check_hydraulics")),
        "B2": ("单源分支", "normal", "", ("branch", "design_check_hydraulics")),
        "B3": ("双源汇流", "normal", "", ("merge", "design_check_hydraulics")),
        "B4": ("固定锚点抬升冲突", "conflict", "下游固定锚点高于来流管底", ("fixed_anchor_conflict",)),
        "B5": ("同系统断网", "invalid_topology", "同一 system 存在不连通子图", ("disconnected",)),
        "B6": ("重力有向环路", "invalid_topology", "重力网络不允许有向环路", ("gravity_cycle",)),
        "B7": ("碰撞证据缺失", "missing_evidence", "collision_context 未执行，clash_free 保持 UNKNOWN", ("missing_evidence",)),
        "B8": ("规则无法唯一选择", "ambiguous_rule", "两条 production 规则同时适用", ("rule_ambiguity",)),
        "B9": ("26节点中型网络", "scale", "", ("branch", "merge", "at_least_25_nodes", "design_check_hydraulics")),
        "B10": ("102节点较大网络", "scale", "", ("branch", "merge", "at_least_100_nodes", "design_check_hydraulics")),
    }
    scenarios: list[BenchmarkScenario] = []
    for scenario_id in _EXPECTED:
        payload = payloads[scenario_id]
        name, category, reason, coverage = metadata[scenario_id]
        node_count = len(payload.get("nodes", ()))
        segment_count = len(payload.get("segments", ()))
        input_sha256 = _canonical_sha256(payload)
        if input_sha256 != _FROZEN_INPUT_SHA256[scenario_id]:
            raise BenchmarkArtifactError(
                f"{scenario_id} 冻结输入 SHA-256 漂移: "
                f"{input_sha256} != {_FROZEN_INPUT_SHA256[scenario_id]}"
            )
        scenarios.append(
            BenchmarkScenario(
                scenario_id=scenario_id,
                name=name,
                category=category,
                input_version=BENCHMARK_VERSION,
                expected_status=_EXPECTED[scenario_id],
                expected_failure_reason=reason,
                rule_identity_sha256=rule_identity,
                input_payload=payload,
                input_sha256=input_sha256,
                expected_canonical_sha256=_FROZEN_CANONICAL_SHA256[scenario_id],
                node_count=node_count,
                segment_count=segment_count,
                coverage=coverage,
            )
        )
    return tuple(scenarios)


def run_m1_5_t7_benchmark(
    *,
    output_dir: Path,
    scenarios: tuple[BenchmarkScenario, ...] | None = None,
    scenario_ids: Iterable[str] | None = None,
    repetitions: int = 3,
) -> BenchmarkReport:
    if repetitions < 2:
        raise BenchmarkArtifactError("T7 determinism 至少需要 2 次重复执行")
    registry = scenarios or build_benchmark_scenarios()
    _validate_registry(registry)
    selected_ids = tuple(scenario_ids or _EXPECTED)
    if len(selected_ids) != len(set(selected_ids)):
        raise BenchmarkArtifactError("scenario_ids 不能重复")
    indexed = {item.scenario_id: item for item in registry}
    unknown = sorted(set(selected_ids) - set(indexed))
    if unknown:
        raise BenchmarkArtifactError(f"未知 benchmark scenarios: {unknown}")

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    recovery = _verify_representative_recovery(root / "recovery")
    results = tuple(
        _run_scenario(indexed[scenario_id], root=root, repetitions=repetitions, recovery=recovery)
        for scenario_id in selected_ids
    )
    overall = (
        BenchmarkConclusion.PASS
        if all(
            _scenario_acceptance_ok(item, expected_failure_marker=_EXPECTED_FAILURE_MARKER[item.scenario_id])
            for item in results
        )
        else BenchmarkConclusion.FAIL
    )
    report_path = root / "m1_5_t7_benchmark_report.json"
    report = BenchmarkReport(
        overall_status=overall,
        scenarios=results,
        report_path=report_path,
    )
    _write_json(report_path, report.model_dump(mode="json"))
    return report


def _scenario_acceptance_ok(
    result: BenchmarkScenarioResult,
    *,
    expected_failure_marker: str,
) -> bool:
    if result.expected_status != result.observed_status:
        return False
    if expected_failure_marker and expected_failure_marker not in result.failure_reason:
        return False
    rejected_before_ir = result.expected_status == "FAIL" and result.determinism.canonical_sha256 is None
    if not result.determinism.canonical_hash_stable and not rejected_before_ir:
        return False
    if not result.determinism.repeated_execution_idempotent and result.expected_status != "UNKNOWN":
        return False
    if not (result.recovery.blender_resume_ok and result.recovery.vectorworks_resume_ok):
        return False
    if result.expected_status == "PASS":
        return result.semantic.offline_dual_host_equal and result.semantic.ifc_ids_ok
    return True


def verify_benchmark_artifact(path: Path) -> VerifiedBenchmarkArtifact:
    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkArtifactError(f"benchmark 工件不可读: {artifact_path}: {exc}") from exc
    try:
        SchemaGate().gate_or_fix("m1_5_t7_benchmark_artifact", payload)
    except SchemaGateError as exc:
        raise BenchmarkArtifactError(f"benchmark 工件未通过 Schema Gate: {exc}") from exc
    input_sha = _canonical_sha256(payload["input"])
    if input_sha != payload["input_sha256"]:
        raise BenchmarkArtifactError("benchmark input_sha256 不匹配")
    result_sha = _canonical_sha256(payload["result"])
    if result_sha != payload["result_sha256"]:
        raise BenchmarkArtifactError("benchmark result_sha256 不匹配")
    canonical = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    artifact_sha = _canonical_sha256(canonical)
    if artifact_sha != payload["artifact_sha256"]:
        raise BenchmarkArtifactError("benchmark artifact_sha256 不匹配")
    return VerifiedBenchmarkArtifact(
        scenario_id=payload["scenario_id"],
        input_sha256=input_sha,
        result_sha256=result_sha,
        artifact_sha256=artifact_sha,
    )


def _run_scenario(
    scenario: BenchmarkScenario,
    *,
    root: Path,
    repetitions: int,
    recovery: RecoveryMetrics,
) -> BenchmarkScenarioResult:
    if scenario.expected_status != _EXPECTED[scenario.scenario_id]:
        raise BenchmarkArtifactError(
            f"{scenario.scenario_id} 期望状态 expected_status 被篡改: "
            f"{scenario.expected_status} != {_EXPECTED[scenario.scenario_id]}"
        )
    if _canonical_sha256(scenario.input_payload) != scenario.input_sha256:
        raise BenchmarkArtifactError(f"{scenario.scenario_id} input_sha256 不匹配")

    started = time.perf_counter()
    hashes: list[str] = []
    observed = "FAIL"
    failure_reason = ""
    idempotent = False
    semantic = SemanticMetrics(
        offline_dual_host_equal=False,
        compared_object_count=0,
        ifc_ids_ok=False,
    )
    auxiliary: dict[str, Any] = {}
    solver_runs = 0

    if scenario.scenario_id == "B8":
        observed, failure_reason, hashes = _run_ambiguous_rule_scenario(repetitions)
        solver_runs = repetitions
        idempotent = True
        semantic = SemanticMetrics(
            offline_dual_host_equal=False,
            compared_object_count=0,
            ifc_ids_ok=False,
        )
    else:
        try:
            compiled_runs = []
            solved_runs = []
            for run_index in range(repetitions):
                payload = deepcopy(scenario.input_payload)
                if run_index % 2:
                    payload["nodes"].reverse()
                    payload["segments"].reverse()
                solver_runs += 1
                solved = solve_network_gravity_utility(payload)
                solved_runs.append(solved)
                compiled_runs.append(solved.compiled_ir)
                hashes.append(solved.compiled_ir.canonical_sha256())
            compiled = compiled_runs[0]
            observed = solved_runs[0].domain_gate.status.value
            if observed == GateStatus.PASS.value:
                hydraulic = solve_hydraulic_network(compiled, _hydraulic_payload(compiled, scenario.input_payload))
                if hydraulic.hydraulics_in_spec != "pass":
                    observed = hydraulic.hydraulics_in_spec.upper()
                    failure_reason = "design/check 水力工况未通过"
                evaluation = hydraulic.rule_evaluation(
                    compiled_ir=compiled,
                    rule_evidence_bundle=compile_municipal_rule_evidence_bundle(),
                )
                rule_identity = RuleProjectionIdentity.from_rule_evaluation(evaluation)
                blender_plan = BlenderBuilder().build(compiled, rule_identity=rule_identity)
                vectorworks_plan = VectorworksBuilder().build(compiled, rule_identity=rule_identity)
                blender_executor = FakeBlenderExecutor()
                vectorworks_executor = FakeVectorworksExecutor()
                blender_receipt = blender_executor.execute_plan(blender_plan)
                vectorworks_receipt = vectorworks_executor.execute_plan(vectorworks_plan)
                blender_calls = blender_executor.apply_calls
                vectorworks_calls = vectorworks_executor.apply_calls
                replay_blender = blender_executor.execute_plan(blender_plan)
                replay_vectorworks = vectorworks_executor.execute_plan(vectorworks_plan)
                idempotent = (
                    blender_receipt.status is BlenderReceiptStatus.COMPLETED
                    and vectorworks_receipt.status is ReceiptStatus.COMPLETED
                    and replay_blender.receipt_id == blender_receipt.receipt_id
                    and replay_vectorworks.receipt_id == vectorworks_receipt.receipt_id
                    and blender_executor.apply_calls == blender_calls
                    and vectorworks_executor.apply_calls == vectorworks_calls
                )
                blender_snapshot = FakeBlenderSemanticExecutor().execute(compiled, rule_identity=rule_identity)
                vectorworks_snapshot = FakeVectorworksSemanticExecutor().execute(compiled, rule_identity=rule_identity)
                comparison = compare_semantic_snapshots(blender_snapshot, vectorworks_snapshot)
                package = build_ifc_ids_package(
                    blender_snapshot,
                    output_dir=root / scenario.scenario_id / "ifc_ids",
                )
                semantic = SemanticMetrics(
                    offline_dual_host_equal=comparison.ok,
                    compared_object_count=comparison.compared_object_count,
                    ifc_ids_ok=package.report.ok,
                )
                if not (idempotent and comparison.ok and package.report.ok):
                    observed = "FAIL"
                    failure_reason = "typed host 幂等、离线语义或 IFC/IDS 验收失败"
                if scenario.scenario_id == "B1":
                    auxiliary["route_clearance"] = _verify_route_and_clearance_approval()
            elif observed == GateStatus.UNKNOWN.value:
                failure_reason = "collision_context 缺失，clash_free 无执行证据"
        except (UtilitySolverError, ValueError) as exc:
            observed = "FAIL"
            failure_reason = str(exc)
            if scenario.expected_status == "FAIL":
                repeated_failures = [failure_reason]
                for run_index in range(1, repetitions):
                    payload = deepcopy(scenario.input_payload)
                    if run_index % 2:
                        payload["nodes"].reverse()
                        payload["segments"].reverse()
                    solver_runs += 1
                    try:
                        solve_network_gravity_utility(payload)
                    except (UtilitySolverError, ValueError) as repeated_exc:
                        repeated_failures.append(str(repeated_exc))
                    else:
                        raise BenchmarkArtifactError(
                            f"{scenario.scenario_id} 失败场景重复执行意外成功"
                        )
                if len(set(repeated_failures)) != 1:
                    raise BenchmarkArtifactError(
                        f"{scenario.scenario_id} 失败原因在重复/乱序执行中不稳定"
                    )
                idempotent = True

    if not failure_reason and observed != "PASS":
        failure_reason = scenario.expected_failure_reason or f"observed={observed}"
    conclusion = BenchmarkConclusion(observed)
    stable = bool(hashes) and len(set(hashes)) == 1
    if scenario.scenario_id == "B8":
        stable = len(set(hashes)) == 1
    actual_canonical = hashes[0] if hashes else None
    if actual_canonical != scenario.expected_canonical_sha256:
        raise BenchmarkArtifactError(
            f"{scenario.scenario_id} canonical SHA-256 漂移: "
            f"{actual_canonical} != {scenario.expected_canonical_sha256}"
        )
    duration_ms = (time.perf_counter() - started) * 1000.0
    result_payload = {
        "scenario_id": scenario.scenario_id,
        "scenario_definition": {
            "name": scenario.name,
            "category": scenario.category,
            "input_version": scenario.input_version,
            "expected_status": scenario.expected_status,
            "expected_failure_reason": scenario.expected_failure_reason,
            "expected_failure_marker": _EXPECTED_FAILURE_MARKER[scenario.scenario_id],
            "expected_canonical_sha256": scenario.expected_canonical_sha256,
            "rule_identity_sha256": scenario.rule_identity_sha256,
            "coverage": list(scenario.coverage),
        },
        "expected_status": scenario.expected_status,
        "observed_status": observed,
        "conclusion": conclusion.value,
        "failure_reason": failure_reason,
        "rule_identity_sha256": scenario.rule_identity_sha256,
        "determinism": {
            "canonical_hash_stable": stable,
            "repeated_execution_idempotent": idempotent,
            "canonical_sha256": actual_canonical,
            "repeated_runs": repetitions,
        },
        "recovery": recovery.model_dump(mode="json"),
        "semantic": semantic.model_dump(mode="json"),
        "performance": {
            "duration_ms": duration_ms,
            "solver_runs": solver_runs,
            "node_count": scenario.node_count,
            "segment_count": scenario.segment_count,
        },
        "coverage": list(scenario.coverage),
        "auxiliary": auxiliary,
    }
    result_sha = _canonical_sha256(result_payload)
    artifact_payload = {
        "artifact_version": BENCHMARK_VERSION,
        "scenario_id": scenario.scenario_id,
        "input": scenario.input_payload,
        "input_sha256": scenario.input_sha256,
        "result": result_payload,
        "result_sha256": result_sha,
    }
    artifact_payload["artifact_sha256"] = _canonical_sha256(artifact_payload)
    artifact_path = root / scenario.scenario_id / "benchmark_artifact.json"
    _write_json(artifact_path, artifact_payload)
    verify_benchmark_artifact(artifact_path)
    return BenchmarkScenarioResult(
        scenario_id=scenario.scenario_id,
        expected_status=scenario.expected_status,
        observed_status=observed,
        conclusion=conclusion,
        failure_reason=failure_reason,
        input_sha256=scenario.input_sha256,
        result_sha256=result_sha,
        rule_identity_sha256=scenario.rule_identity_sha256,
        determinism=DeterminismMetrics(**result_payload["determinism"]),
        recovery=recovery,
        semantic=semantic,
        performance=PerformanceMetrics(**result_payload["performance"]),
        artifact_path=artifact_path,
    )


def _validate_registry(registry: tuple[BenchmarkScenario, ...]) -> None:
    ids = [item.scenario_id for item in registry]
    if len(ids) != len(set(ids)):
        raise BenchmarkArtifactError("benchmark scenario_id 不能重复")
    if set(ids) != set(_EXPECTED):
        raise BenchmarkArtifactError(f"benchmark registry 必须完整包含 B1-B10，实际={sorted(ids)}")
    for item in registry:
        if item.expected_status != _EXPECTED[item.scenario_id]:
            raise BenchmarkArtifactError(
                f"{item.scenario_id} 期望状态被修改: {item.expected_status} != {_EXPECTED[item.scenario_id]}"
            )
        if item.rule_identity_sha256 != _FROZEN_RULE_IDENTITY_SHA256:
            raise BenchmarkArtifactError(f"{item.scenario_id} 规则身份 SHA-256 被修改")
        if item.expected_canonical_sha256 != _FROZEN_CANONICAL_SHA256[item.scenario_id]:
            raise BenchmarkArtifactError(f"{item.scenario_id} canonical SHA-256 期望值被修改")
        if _canonical_sha256(item.input_payload) != item.input_sha256:
            raise BenchmarkArtifactError(f"{item.scenario_id} input_sha256 不匹配")
        if item.scenario_id != "B8":
            node_ids = {node["node_id"] for node in item.input_payload["nodes"]}
            segment_ids = {segment["segment_id"] for segment in item.input_payload["segments"]}
            overlap = sorted(node_ids & segment_ids)
            if overlap:
                raise BenchmarkArtifactError(
                    f"{item.scenario_id} node/segment 稳定 ID 必须跨类型唯一: {overlap}"
                )


def _run_ambiguous_rule_scenario(repetitions: int) -> tuple[str, str, list[str]]:
    base = compile_municipal_rule_evidence_bundle()
    rule = base.rule("MU-CLEAR-001:building")
    duplicate = build_verified_municipal_rule(
        **{
            **rule.model_dump(mode="python", exclude={"canonical_sha256"}),
            "rule_id": "MU-CLEAR-001:building-benchmark-duplicate",
        }
    )
    ambiguous = build_municipal_rule_evidence_bundle(
        bundle_id="municipal-benchmark-ambiguous",
        source_path="benchmark/B8",
        source_sha256="8" * 64,
        compiler_name="openbimagent-t7-benchmark",
        compiler_version=BENCHMARK_VERSION,
        rules=(rule, duplicate),
    )
    hashes: list[str] = []
    details: list[str] = []
    for _ in range(repetitions):
        selection = select_municipal_rule(
            ambiguous,
            rule_type=rule.rule_type,
            facts={"design_system": "wastewater", "obstacle_category": "building"},
        )
        if selection.status is not EvidenceRuleSelectionStatus.AMBIGUOUS:
            raise BenchmarkArtifactError("B8 规则歧义没有失败关闭")
        hashes.append(ambiguous.canonical_sha256)
        details.append(selection.detail)
    return "REVIEW_REQUIRED", details[0], hashes


def _verify_representative_recovery(root: Path) -> RecoveryMetrics:
    root.mkdir(parents=True, exist_ok=True)
    compiled = solve_network_gravity_utility(_series_payload()).compiled_ir
    blender_plan = BlenderBuilder().build(compiled)
    vectorworks_plan = VectorworksBuilder().build(compiled)
    fail_after = 3

    blender_state = root / "blender_state.json"
    vectorworks_state = root / "vectorworks_state.json"
    _reset_fake_host_state(blender_state, host_data_key="properties")
    _reset_fake_host_state(vectorworks_state, host_data_key="records")

    partial_blender = FakeBlenderExecutor(
        fail_after_operations=fail_after,
        state_path=blender_state,
    ).execute_plan(blender_plan)
    resumed_blender_executor = FakeBlenderExecutor(state_path=blender_state)
    resumed_blender = resumed_blender_executor.execute_plan(blender_plan)
    blender_ok = (
        partial_blender.status is BlenderReceiptStatus.PARTIAL
        and resumed_blender.status is BlenderReceiptStatus.COMPLETED
        and len(resumed_blender_executor.objects) > 0
    )

    partial_vectorworks = FakeVectorworksExecutor(
        fail_after_operations=fail_after,
        state_path=vectorworks_state,
    ).execute_plan(vectorworks_plan)
    resumed_vectorworks_executor = FakeVectorworksExecutor(state_path=vectorworks_state)
    resumed_vectorworks = resumed_vectorworks_executor.execute_plan(vectorworks_plan)
    vectorworks_ok = (
        partial_vectorworks.status is ReceiptStatus.PARTIAL
        and resumed_vectorworks.status is ReceiptStatus.COMPLETED
        and len(resumed_vectorworks_executor.objects) > 0
    )
    return RecoveryMetrics(
        scope="representative B1 durable fake-host checkpoint/restart; offline evidence only",
        blender_resume_ok=blender_ok,
        vectorworks_resume_ok=vectorworks_ok,
        failure_injected_after_operations=fail_after,
    )


def _reset_fake_host_state(path: Path, *, host_data_key: str) -> None:
    if host_data_key not in {"properties", "records"}:
        raise BenchmarkArtifactError(f"不支持的 fake-host state key: {host_data_key}")
    try:
        _write_json(
            path,
            {
                "state_version": "1.0",
                "objects": {},
                host_data_key: {},
                "connections": {},
                "applied": {},
                "plan_hashes": {},
                "receipts": {},
            },
        )
    except OSError as exc:
        raise BenchmarkArtifactError(f"无法重置 benchmark recovery state: {path}: {exc}") from exc


def _verify_route_and_clearance_approval() -> dict[str, Any]:
    bundle = compile_municipal_rule_evidence_bundle()
    rule = bundle.rule("MU-CLEAR-001:building")
    approved_at = datetime(2026, 8, 1, tzinfo=UTC)
    approval = build_clearance_exception_approval(
        exception_id="EXC-T7-B1-001",
        rule_set_sha256=bundle.canonical_sha256,
        rule_sha256=rule.canonical_sha256,
        original_rule_id=rule.rule_id,
        original_clearance_m=2.5,
        approved_clearance_m=2.0,
        safety_measures=("增设防护套管",),
        rationale="benchmark 路线受既有构筑物约束。",
        risks=("检修空间缩小",),
        approver_id="benchmark-chief-engineer",
        approver_role="chief_engineer",
        approver_authorities=("approve_clearance_reduction",),
        valid_scope={
            "project_id": "benchmark-t7",
            "subject_ids": ("building-b1",),
            "rule_ids": (rule.rule_id,),
        },
        approved_at=approved_at,
        expires_at=approved_at + timedelta(days=30),
        approval_status="approved",
        audit_references=("approval://benchmark-t7/EXC-T7-B1-001",),
    )
    width, height = 11, 5
    cells = [
        {"x_index": x_index, "y_index": y_index}
        for x_index in range(width)
        for y_index in range(height)
    ]
    route_input = {
        "protocol_version": "0.1",
        "request_id": "benchmark-b1-route",
        "source_ir_sha256": "1" * 64,
        "municipal_rule_set_sha256": compile_municipal_rule_set().canonical_sha256,
        "coordinate_reference": _coordinate_reference(),
        "grid": {
            "origin_x_m": 0.0,
            "origin_y_m": 0.0,
            "resolution_m": 1.0,
            "width": width,
            "height": height,
        },
        "start": {"node_id": "b1-n0", "cell": {"x_index": 0, "y_index": 0}, "invert_anchor_m": 10.0},
        "end": {"node_id": "b1-n1", "cell": {"x_index": 10, "y_index": 0}},
        "allowed_cells": cells,
        "surface_samples": [
            {
                "cell": cell,
                "ground_elevation_m": 12.0 + 0.05 * cell["x_index"] + 0.02 * cell["y_index"],
            }
            for cell in cells
        ],
        "obstacles": [
            {
                "obstacle_id": "building-b1",
                "kind": "aabb",
                "category": "building",
                "min_corner": {"x_m": 4.8, "y_m": -0.2, "z_m": 0.0},
                "max_corner": {"x_m": 5.2, "y_m": 0.2, "z_m": 20.0},
            }
        ],
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
        "max_candidates": 3,
        "max_search_expansions": 100000,
    }
    route = solve_grid_route_t6(
        route_input,
        rule_evidence_bundle=bundle,
        project_id="benchmark-t7",
        evaluated_at=approved_at + timedelta(days=1),
        exception_approvals={"building-b1": approval},
    )
    selected = route.route_result.selected_candidate()
    if route.route_result.status is not RouteSolveStatus.FEASIBLE or selected.turn_count < 2:
        raise BenchmarkArtifactError("B1 路线折点/复杂标高场景未形成可行确定性路线")
    constraint = route.obstacle_constraints[0]
    if constraint.exception_approval_sha256 != approval.canonical_sha256:
        raise BenchmarkArtifactError("B1 净距审批身份未精确绑定")
    return {
        "route_result_sha256": route.route_result.canonical_sha256(),
        "selected_candidate_sha256": _canonical_sha256(selected.model_dump(mode="json")),
        "turn_count": selected.turn_count,
        "surface_sample_count": len(route_input["surface_samples"]),
        "exception_approval_sha256": approval.canonical_sha256,
        "original_clearance_m": constraint.original_clearance_m,
        "effective_clearance_m": constraint.effective_clearance_m,
    }


def _hydraulic_payload(compiled, network_payload: dict[str, Any]) -> dict[str, Any]:
    flows = _balanced_flows(network_payload, total_flow=0.024)
    return {
        "protocol_version": "0.1",
        "request_id": f"{network_payload['request_id']}-hydraulics",
        "source_ir_sha256": compiled.canonical_sha256(),
        "rule_evidence_bundle_sha256": compile_municipal_rule_evidence_bundle().canonical_sha256,
        "calculation_model": "manning_uniform_open_channel_si",
        "roughness_inputs": [
            {
                "segment_id": segment.segment_id,
                "manning_n": 0.013,
                "provenance": "designer_input",
                "source_reference": "M1.5 T7 frozen benchmark input",
            }
            for segment in compiled.segments
        ],
        "scenarios": [
            {
                "scenario_id": scenario_type,
                "scenario_type": scenario_type,
                "segment_flows": [
                    {"segment_id": segment_id, "flow_m3_s": flow}
                    for segment_id, flow in sorted(flows.items())
                ],
            }
            for scenario_type in ("design", "check")
        ],
    }


def _balanced_flows(payload: dict[str, Any], *, total_flow: float) -> dict[str, float]:
    nodes = {item["node_id"] for item in payload["nodes"]}
    incoming = {node_id: [] for node_id in nodes}
    outgoing = {node_id: [] for node_id in nodes}
    for segment in payload["segments"]:
        incoming[segment["end_node_id"]].append(segment["segment_id"])
        outgoing[segment["start_node_id"]].append(segment["segment_id"])
    segment_by_id = {item["segment_id"]: item for item in payload["segments"]}
    sources = sorted(node_id for node_id in nodes if not incoming[node_id])
    node_inflow = {node_id: 0.0 for node_id in nodes}
    for source in sources:
        node_inflow[source] = total_flow / len(sources)
    flows: dict[str, float] = {}
    pending = set(nodes)
    while pending:
        ready = sorted(
            node_id
            for node_id in pending
            if all(segment_id in flows for segment_id in incoming[node_id])
        )
        if not ready:
            raise BenchmarkArtifactError("水力流量生成检测到环路")
        for node_id in ready:
            if incoming[node_id]:
                node_inflow[node_id] = sum(flows[segment_id] for segment_id in incoming[node_id])
            targets = sorted(outgoing[node_id])
            if targets:
                each = node_inflow[node_id] / len(targets)
                for segment_id in targets:
                    flows[segment_id] = each
            pending.remove(node_id)
    if set(flows) != set(segment_by_id):
        raise BenchmarkArtifactError("水力流量没有覆盖全部 segment")
    return flows


def _series_payload() -> dict[str, Any]:
    return _network_payload(
        request_id="benchmark-b1-series",
        nodes=[
            _node("b1-n0", "manhole", 0.0, 0.0, anchor=10.0),
            _node("b1-n1", "manhole", 10.0, 0.0),
            _node("b1-n2", "manhole", 20.0, 0.0),
        ],
        segments=[
            _segment("b1-s0", "b1-n0", "b1-n1"),
            _segment("b1-s1", "b1-n1", "b1-n2"),
        ],
    )


def _branch_payload() -> dict[str, Any]:
    return _network_payload(
        request_id="benchmark-b2-branch",
        nodes=[
            _node("b2-source", "manhole", 0.0, 0.0, anchor=10.0),
            _node("b2-junction", "junction", 10.0, 0.0),
            _node("b2-out-a", "manhole", 20.0, 5.0),
            _node("b2-out-b", "manhole", 20.0, -5.0),
        ],
        segments=[
            _segment("b2-in", "b2-source", "b2-junction"),
            _segment("b2-a", "b2-junction", "b2-out-a"),
            _segment("b2-b", "b2-junction", "b2-out-b"),
        ],
    )


def _merge_payload() -> dict[str, Any]:
    return _network_payload(
        request_id="benchmark-b3-merge",
        nodes=[
            _node("b3-source-a", "manhole", 0.0, 5.0, anchor=10.0),
            _node("b3-source-b", "manhole", 0.0, -5.0, anchor=10.0),
            _node("b3-junction", "junction", 10.0, 0.0),
            _node("b3-out", "manhole", 20.0, 0.0),
        ],
        segments=[
            _segment("b3-a", "b3-source-a", "b3-junction"),
            _segment("b3-b", "b3-source-b", "b3-junction"),
            _segment("b3-out-segment", "b3-junction", "b3-out"),
        ],
    )


def _anchor_conflict_payload() -> dict[str, Any]:
    payload = _series_payload()
    payload["request_id"] = "benchmark-b4-anchor-conflict"
    payload["nodes"][1]["invert_anchor_m"] = 10.1
    return payload


def _disconnected_payload() -> dict[str, Any]:
    payload = _series_payload()
    payload["request_id"] = "benchmark-b5-disconnected"
    payload["nodes"].extend(
        [
            _node("b5-isolated-a", "manhole", 100.0, 0.0, anchor=10.0),
            _node("b5-isolated-b", "manhole", 110.0, 0.0),
        ]
    )
    payload["segments"].append(_segment("b5-isolated", "b5-isolated-a", "b5-isolated-b"))
    return payload


def _cycle_payload() -> dict[str, Any]:
    payload = _series_payload()
    payload["request_id"] = "benchmark-b6-cycle"
    payload["segments"].append(_segment("b6-cycle", "b1-n2", "b1-n0"))
    return payload


def _missing_evidence_payload() -> dict[str, Any]:
    payload = _series_payload()
    payload["request_id"] = "benchmark-b7-missing-evidence"
    payload["collision_context"] = None
    return payload


def _ambiguous_rule_payload() -> dict[str, Any]:
    return {
        "protocol_version": BENCHMARK_VERSION,
        "request_id": "benchmark-b8-rule-ambiguity",
        "rule_type": "structure_clearance",
        "facts": {"design_system": "wastewater", "obstacle_category": "building"},
        "duplicate_rule_identity": "MU-CLEAR-001:building-benchmark-duplicate",
    }


def _diamond_chain_payload(*, diamonds: int, request_id: str) -> dict[str, Any]:
    nodes = [_node(f"{request_id}-source", "manhole", 0.0, 0.0, anchor=10.0)]
    segments: list[dict[str, Any]] = []
    previous = nodes[0]["node_id"]
    for index in range(diamonds):
        base = index * 40.0
        split = f"{request_id}-split-{index:02d}"
        branch_a = f"{request_id}-a-{index:02d}"
        branch_b = f"{request_id}-b-{index:02d}"
        merge = f"{request_id}-merge-{index:02d}"
        nodes.extend(
            [
                _node(split, "junction", base + 10.0, 0.0),
                _node(branch_a, "manhole", base + 20.0, 5.0),
                _node(branch_b, "manhole", base + 20.0, -5.0),
                _node(merge, "junction", base + 30.0, 0.0),
            ]
        )
        segments.extend(
            [
                _segment(f"{request_id}-in-{index:02d}", previous, split),
                _segment(f"{request_id}-split-a-{index:02d}", split, branch_a),
                _segment(f"{request_id}-split-b-{index:02d}", split, branch_b),
                _segment(f"{request_id}-merge-a-{index:02d}", branch_a, merge),
                _segment(f"{request_id}-merge-b-{index:02d}", branch_b, merge),
            ]
        )
        previous = merge
    terminal = f"{request_id}-terminal"
    nodes.append(_node(terminal, "manhole", diamonds * 40.0, 0.0))
    segments.append(_segment(f"{request_id}-terminal-segment", previous, terminal))
    return _network_payload(request_id=request_id, nodes=nodes, segments=segments)


def _network_payload(*, request_id: str, nodes: list[dict[str, Any]], segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol_version": "0.1",
        "request_id": request_id,
        "source_ir_sha256": hashlib.sha256(f"source:{request_id}".encode()).hexdigest(),
        "coordinate_reference": _coordinate_reference(),
        "system_id": "sys-wastewater",
        "system_name": "T7 污水重力系统",
        "nodes": nodes,
        "segments": segments,
        "collision_context": {"coverage": "complete", "obstacles": []},
    }


def _coordinate_reference() -> dict[str, Any]:
    return {
        "crs_id": "LOCAL:PROJECT-M",
        "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
        "horizontal_unit": "m",
        "vertical_unit": "m",
        "vertical_datum": "project datum",
    }


def _node(node_id: str, node_type: str, x_m: float, y_m: float, *, anchor: float | None = None) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "x_m": x_m,
        "y_m": y_m,
        "ground_elevation_m": 12.0 + 0.0005 * x_m + 0.0002 * abs(y_m),
        "invert_anchor_m": anchor,
    }


def _segment(segment_id: str, start: str, end: str) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "start_node_id": start,
        "end_node_id": end,
        "diameter_mm": 300.0,
        "material": "concrete",
        "design_slope": 0.003,
        "surface_context": "driveway",
    }


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "BENCHMARK_VERSION",
    "COMPILED_UTILITY_IR_SCHEMA_SHA256",
    "BenchmarkArtifactError",
    "BenchmarkConclusion",
    "BenchmarkReport",
    "BenchmarkScenario",
    "BenchmarkScenarioResult",
    "build_benchmark_scenarios",
    "run_m1_5_t7_benchmark",
    "verify_benchmark_artifact",
]
