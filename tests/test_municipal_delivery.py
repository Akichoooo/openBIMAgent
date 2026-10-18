"""Focused host export verification and document protection; no live CAD actions."""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openbimagent.assembly.semantic_snapshot import (
    SemanticSnapshot,
    expected_semantic_snapshot,
)
from openbimagent.deliver.host_delivery import HostDeliveryVerificationError, verify_host_delivery
from openbimagent.utility.geometry import upgrade_legacy_utility_ir
from test_compiled_utility_ir import solved_payload
from test_vw_runner import _fake_typed_vs, _load_runner_module, _typed_gate


def _ir():
    return upgrade_legacy_utility_ir(
        solved_payload(),
        dimensions_by_segment={"pipe-001": {"inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0}},
    )


def _actual_vw_receipt(tmp_path, monkeypatch):
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder
    runner = _load_runner_module()
    fake = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake)
    plan = VectorworksBuilder().build(_ir())
    params = {"plan": plan.model_dump(mode="json"), "output_path": str(tmp_path / "case.vwx"), "authorized_root": str(tmp_path)}
    receipt = runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    return receipt, plan


def test_expected_projection_is_axis_based_and_nominal_diameter_is_metadata():
    ir = _ir()
    snapshot = expected_semantic_snapshot(ir, "vectorworks")
    segment = next(item for item in snapshot.objects if item.stable_id == "pipe-001")
    port = next(item for item in snapshot.objects if item.stable_id == "mh-001-out")
    assert segment.centerline[0].z_m == pytest.approx(10.15)
    assert segment.centerline[-1].z_m == pytest.approx(10.12)
    assert port.position == segment.centerline[0]
    assert segment.diameter_mm == 300.0
    assert segment.start_invert_m == 10.0
    # Explicit axis input gives the same physical projection without an extra radius.
    payload = ir.model_dump(mode="json")
    payload["segments"][0]["centerline_reference"] = "axis"
    payload["segments"][0]["centerline"] = [point.model_dump() for point in segment.centerline]
    axis_snapshot = expected_semantic_snapshot(payload, "vectorworks")
    assert next(item for item in axis_snapshot.objects if item.stable_id == "pipe-001").centerline == segment.centerline


def test_expected_projection_rejects_ambiguous_legacy_geometry():
    with pytest.raises(ValueError, match="explicit geometry"):
        expected_semantic_snapshot(solved_payload(), "blender")


def test_delivery_retains_actual_readback_report_and_file_sha(tmp_path, monkeypatch):
    receipt, plan = _actual_vw_receipt(tmp_path, monkeypatch)
    result = verify_host_delivery(_ir(), receipt, host="vectorworks", output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256)
    assert result["semantic_snapshot"] == receipt["semantic_snapshot"]
    assert result["semantic_comparison_report"]["status"] == "PASS"
    assert result["output_sha256"] == hashlib.sha256(b"FAKE-VWX").hexdigest()
    assert result["geometry_status"] == "dimension_verified_topology_unverified"
    assert result["dimension_verified"] is True
    assert result["topology_status"] == "unverified"
    assert result["geometry_verified"] is False
    assert result["geometry_caveats"]
    assert result["verification_status"] == "partial"
    measured = result["geometry_snapshot"]["objects"][0]["measured"]
    assert measured["inner_diameter_mm"] == pytest.approx(300.0)
    assert measured["outer_diameter_mm"] == pytest.approx(360.0)
    assert measured["ring_vertex_counts"] == {
        "outer_start": 24, "inner_start": 24, "outer_end": 24, "inner_end": 24,
    }
    assert "inner/outer surfaces" in result["geometry_scope"]


def test_readback_comparison_handles_raw_domain_double_precision(tmp_path, monkeypatch):
    receipt, _ = _actual_vw_receipt(tmp_path, monkeypatch)
    payload = receipt["semantic_snapshot"]
    payload["canonical_sha256"] = ""
    segment = next(item for item in payload["objects"] if item["stable_id"] == "pipe-001")
    segment["domain_properties"]["min_cover_depth_m"] += 1e-14
    receipt["semantic_snapshot"] = SemanticSnapshot.model_validate(payload).finalized().model_dump(mode="json")
    result = verify_host_delivery(_ir(), receipt, host="vectorworks")
    assert result["semantic_comparison_report"]["status"] == "PASS"
    assert result["semantic_snapshot"] == receipt["semantic_snapshot"]


@pytest.mark.parametrize("mutation", ["missing_file", "empty_file", "missing_readback", "drift", "wrong_ir", "wrong_path", "completed_claim", "wrong_plan"])
def test_delivery_fails_closed(tmp_path, monkeypatch, mutation):
    receipt, plan = _actual_vw_receipt(tmp_path, monkeypatch)
    receipt = deepcopy(receipt)
    output = tmp_path / "case.vwx"
    if mutation == "missing_file":
        output.unlink()
    elif mutation == "empty_file":
        output.write_bytes(b"")
    elif mutation == "missing_readback":
        receipt["semantic_snapshot"] = None
    elif mutation in {"drift", "wrong_ir"}:
        payload = receipt["semantic_snapshot"]
        payload["canonical_sha256"] = ""
        if mutation == "drift":
            next(item for item in payload["objects"] if item["stable_id"] == "pipe-001")["centerline"][0]["z_m"] += 0.1
        else:
            payload["source_ir_sha256"] = "f" * 64
        receipt["semantic_snapshot"] = SemanticSnapshot.model_validate(payload).finalized().model_dump(mode="json")
    elif mutation == "wrong_path":
        receipt["output_path"] = str(tmp_path / "other.vwx")
    elif mutation == "completed_claim":
        # The host cannot close the topology claim: completion must fail closed.
        receipt["status"] = "completed"
    elif mutation == "wrong_plan":
        receipt["canonical_sha256"] = "f" * 64
    with pytest.raises(HostDeliveryVerificationError) as caught:
        verify_host_delivery(_ir(), receipt, host="vectorworks", output_path=output, plan_sha256=plan.canonical_sha256)
    if mutation in {"drift", "wrong_ir"}:
        assert caught.value.comparison_report["status"] == "FAIL"


def test_vectorworks_executor_checks_transport_receipt_and_retains_snapshot(tmp_path, monkeypatch):
    from openbimagent.assembly.vectorworks_host_executor import execute_vectorworks_export, VectorworksHostExecutionError
    from openbimagent.assembly.vectorworks_plan import VectorworksExecutionReceipt
    from openbimagent.mcp_clients import vectorworks
    receipt, _ = _actual_vw_receipt(tmp_path, monkeypatch)
    ipc_root = tmp_path / "ipc"
    monkeypatch.setenv("OPENBIMAGENT_VW_JOBS_DIR", str(ipc_root / "jobs"))
    monkeypatch.setenv("OPENBIMAGENT_VW_RESULTS_DIR", str(ipc_root / "results"))
    monkeypatch.setenv("OPENBIMAGENT_VW_AUTHORIZED_ROOT", str(tmp_path))
    monkeypatch.delenv("OPENBIMAGENT_VW_IPC_ROOT", raising=False)
    class Client:
        def __init__(self, **kwargs):
            pass
        async def connect(self):
            pass
        async def close(self):
            pass
        async def describe_capabilities(self):
            return None
        async def execute_plan(self, *args, **kwargs):
            return VectorworksExecutionReceipt.model_validate(receipt)
    monkeypatch.setattr(vectorworks, "VectorworksMCPClient", Client)
    result = execute_vectorworks_export(_ir(), output_path=tmp_path / "case.vwx")
    assert result["semantic_snapshot"] == receipt["semantic_snapshot"]
    assert result["output_sha256"]
    assert result["status"] == "partial"
    assert result["dimension_verified"] is True
    assert result["topology_status"] == "unverified"
    assert result["geometry_verified"] is False
    receipt["semantic_snapshot"] = None
    with pytest.raises(VectorworksHostExecutionError, match="readback"):
        execute_vectorworks_export(_ir(), output_path=tmp_path / "case.vwx")


@pytest.mark.parametrize(
    "jobs_name,results_name,env_root,message",
    [
        ("queue", "results", None, "必须以 jobs 结尾"),
        ("jobs", "outbox", None, "必须以 results 结尾"),
        ("jobs", "results", "other-root", "IPC_ROOT"),
    ],
)
def test_vectorworks_executor_rejects_ipc_contract_mismatch(
    tmp_path, monkeypatch, jobs_name, results_name, env_root, message
):
    from openbimagent.assembly.vectorworks_host_executor import execute_vectorworks_export, VectorworksHostExecutionError
    monkeypatch.setenv("OPENBIMAGENT_VW_JOBS_DIR", str(tmp_path / jobs_name))
    monkeypatch.setenv("OPENBIMAGENT_VW_RESULTS_DIR", str(tmp_path / results_name))
    monkeypatch.setenv("OPENBIMAGENT_VW_AUTHORIZED_ROOT", str(tmp_path))
    if env_root is not None:
        monkeypatch.setenv("OPENBIMAGENT_VW_IPC_ROOT", str(tmp_path / env_root))
    else:
        monkeypatch.delenv("OPENBIMAGENT_VW_IPC_ROOT", raising=False)
    with pytest.raises(VectorworksHostExecutionError, match=message):
        execute_vectorworks_export(_ir(), output_path=tmp_path / "case.vwx")


def test_vectorworks_executor_fails_fast_on_stale_or_wrong_root_heartbeat(tmp_path, monkeypatch):
    import time as _time

    from openbimagent.assembly.vectorworks_host_executor import (
        VectorworksHostExecutionError,
        _check_runner_heartbeat,
    )
    ipc_root = tmp_path / "ipc"
    jobs = ipc_root / "jobs"
    jobs.mkdir(parents=True)
    heartbeat = ipc_root / "runner_heartbeat.json"
    heartbeat.write_text(
        json.dumps({"ts": _time.time(), "ipc_root": str(tmp_path / "other-ipc")}),
        encoding="utf-8",
    )
    with pytest.raises(VectorworksHostExecutionError, match="ipc_root"):
        _check_runner_heartbeat(jobs)
    heartbeat.write_text(json.dumps({"ts": _time.time() - 10_000}), encoding="utf-8")
    with pytest.raises(VectorworksHostExecutionError, match="心跳已过期"):
        _check_runner_heartbeat(jobs)
    heartbeat.write_text(
        json.dumps({"ts": _time.time(), "ipc_root": str(ipc_root)}), encoding="utf-8"
    )
    _check_runner_heartbeat(jobs)
    heartbeat.unlink()
    _check_runner_heartbeat(jobs)


@pytest.mark.parametrize("missing_readback", [False, True])
def test_blender_executor_checks_transport_receipt_and_retains_snapshot(tmp_path, monkeypatch, missing_readback):
    from openbimagent.assembly import blender_host_executor as executor
    from openbimagent.assembly.blender_plan import BlenderBuilder, BlenderExecutionReceipt
    from openbimagent.mcp_clients.blender import BlenderMCPClient
    from test_blender_typed_host import _FakeBpy, _execute, _load_typed_module
    plan = BlenderBuilder().build(_ir())
    receipt = _execute(_load_typed_module(), _FakeBpy(), tmp_path, plan=plan.model_dump(mode="json"))
    if missing_readback:
        receipt["semantic_snapshot"] = None
    class Client:
        async def connect(self):
            pass
        async def close(self):
            pass
        async def describe_capabilities(self):
            return None
        async def execute_plan(self, *args, **kwargs):
            return BlenderExecutionReceipt.model_validate(receipt)
    monkeypatch.setattr(BlenderMCPClient, "transport_socket", lambda **kwargs: Client())
    monkeypatch.setattr(executor.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(executor, "_wait_for_port", lambda *args: True)
    monkeypatch.setattr(executor.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=1))
    killed = []
    monkeypatch.setattr(executor, "_kill_tree", lambda proc: killed.append(proc.pid))
    executable = tmp_path / "mock-blender.exe"
    executable.write_bytes(b"not executed")
    if missing_readback:
        with pytest.raises(executor.BlenderHostExecutionError, match="readback"):
            executor.execute_blender_export(_ir(), output_path=tmp_path / "case.blend", blender_exe=executable, authorized_root=tmp_path)
    else:
        result = executor.execute_blender_export(_ir(), output_path=tmp_path / "case.blend", blender_exe=executable, authorized_root=tmp_path)
        assert result["semantic_snapshot"] == receipt["semantic_snapshot"]
        assert result["semantic_comparison_report"]["status"] == "PASS"
        assert result["output_sha256"]
    assert killed == [1]


@pytest.mark.parametrize("kind", ["locus", "group", "symbol", "hidden_other_layer_object"])
def test_vw_first_execution_refuses_nonempty_unnamed_documents(tmp_path, monkeypatch, kind):
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder
    runner = _load_runner_module()
    fake = _fake_typed_vs()
    fake.layers["hidden-other-layer"] = {"name": "hidden-other-layer", "objects": [{"kind": kind}]}
    monkeypatch.setitem(sys.modules, "vs", fake)
    params = {"plan": VectorworksBuilder().build(_ir()).model_dump(mode="json"), "output_path": str(tmp_path / "protect.vwx"), "authorized_root": str(tmp_path)}
    with pytest.raises(RuntimeError, match="empty unnamed"):
        runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    assert fake.saved_paths == []
    assert fake.primary_units_calls == []
    assert not (tmp_path / "protect.vwx").exists()
    assert fake.layers["hidden-other-layer"]["objects"] == [{"kind": kind}]


def test_vw_blank_document_check_requires_host_query():
    runner = _load_runner_module()
    with pytest.raises(RuntimeError, match="cannot prove blank"):
        runner._assert_blank_document(SimpleNamespace())


def test_partial_geometry_readback_is_reported_not_passed(tmp_path, monkeypatch):
    receipt, plan = _actual_vw_receipt(tmp_path, monkeypatch)
    receipt = deepcopy(receipt)
    receipt["status"] = "partial"
    receipt["geometry_status"] = "failed"
    receipt["geometry_snapshot"]["status"] = "failed"
    item = receipt["geometry_snapshot"]["objects"][0]
    item["dimension_status"] = "failed"
    item["dimension_checks"]["outer_diameter"] = False
    item["status"] = "failed"
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["semantic_verified"] is True
    assert result["dimension_verified"] is False
    assert result["geometry_verified"] is False
    assert result["geometry_status"] == "failed"
    assert result["verification_status"] == "partial"
    assert "not verified" in result["geometry_scope"]


def test_completed_receipt_with_failed_geometry_fails_closed(tmp_path, monkeypatch):
    receipt, plan = _actual_vw_receipt(tmp_path, monkeypatch)
    receipt = deepcopy(receipt)
    receipt["status"] = "completed"
    receipt["geometry_status"] = "failed"
    receipt["geometry_snapshot"]["status"] = "failed"
    item = receipt["geometry_snapshot"]["objects"][0]
    item["dimension_status"] = "failed"
    item["dimension_checks"]["outer_diameter"] = False
    item["status"] = "failed"
    with pytest.raises(HostDeliveryVerificationError, match="claims completion"):
        verify_host_delivery(
            _ir(), receipt, host="vectorworks",
            output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
        )


def _mutated_receipt(tmp_path, monkeypatch):
    receipt, plan = _actual_vw_receipt(tmp_path, monkeypatch)
    receipt = deepcopy(receipt)
    assert receipt["status"] == "partial"
    return receipt, plan


def test_topology_claim_without_face_evidence_is_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    item = receipt["geometry_snapshot"]["objects"][0]
    item["topology_status"] = "verified"
    item["topology_checks"] = {
        "face_count_matches": True,
        "no_missing_faces": True,
        "no_duplicate_or_extra_faces": True,
        "face_orientation_consistent": True,
        "edge_manifold": True,
    }
    item["status"] = "verified"
    receipt["geometry_snapshot"]["topology_status"] = "verified"
    receipt["geometry_snapshot"]["status"] = "verified"
    receipt["geometry_status"] = "verified"
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert result["dimension_verified"] is False
    assert any("topology" in failure for failure in result["geometry_failures"])


def test_topology_verification_with_face_evidence_is_accepted(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    item = receipt["geometry_snapshot"]["objects"][0]
    item["topology_status"] = "verified"
    item["topology_checks"] = {
        "face_count_matches": True,
        "no_missing_faces": True,
        "no_duplicate_or_extra_faces": True,
        "face_orientation_consistent": True,
        "edge_manifold": True,
    }
    item["topology_face_count"] = 96
    item["status"] = "verified"
    receipt["geometry_snapshot"]["topology_status"] = "verified"
    receipt["geometry_snapshot"]["status"] = "verified"
    receipt["geometry_status"] = "verified"
    receipt["status"] = "completed"
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["dimension_verified"] is True
    assert result["topology_status"] == "verified"
    assert result["geometry_verified"] is True
    assert result["geometry_status"] == "verified"
    assert result["verification_status"] == "verified"
    assert result["geometry_caveats"] == []


def test_duplicate_geometry_object_ids_are_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    objects = receipt["geometry_snapshot"]["objects"]
    objects.append(deepcopy(objects[0]))
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert any("duplicate" in failure for failure in result["geometry_failures"])


def test_nonfinite_geometry_values_are_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    receipt["geometry_snapshot"]["objects"][0]["measured"]["length_m"] = float("inf")
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert any("non-finite" in failure for failure in result["geometry_failures"])


def test_invalid_readback_tolerance_is_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    receipt["geometry_snapshot"]["readback_tolerance_m"] = 1.0
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert any("tolerance is invalid" in failure for failure in result["geometry_failures"])


def test_missing_required_dimension_check_is_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    del receipt["geometry_snapshot"]["objects"][0]["dimension_checks"]["inner_diameter"]
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert any("required dimension checks are missing" in failure for failure in result["geometry_failures"])


def test_legacy_geometry_snapshot_version_is_rejected(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    receipt["geometry_snapshot"]["geometry_snapshot_version"] = "1.0"
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert result["dimension_verified"] is False
    assert any("unsupported geometry_snapshot_version" in failure for failure in result["geometry_failures"])


def test_discretization_must_be_labelled_as_non_engineering(tmp_path, monkeypatch):
    receipt, plan = _mutated_receipt(tmp_path, monkeypatch)
    receipt["geometry_snapshot"]["objects"][0]["discretization"]["is_engineering_tolerance"] = True
    result = verify_host_delivery(
        _ir(), receipt, host="vectorworks",
        output_path=tmp_path / "case.vwx", plan_sha256=plan.canonical_sha256,
    )
    assert result["geometry_verified"] is False
    assert any("engineering tolerance" in failure for failure in result["geometry_failures"])
