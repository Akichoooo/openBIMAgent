"""VW MCP runner 单测:命令分发 + vs 代码执行(无 VW 环境部分用 mock)。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 runner.py。

覆盖(4 个测试):
- execute_command("ping") → {"message":"pong"}
- execute_command("describe_capabilities") → 含 vw_version/python_version/known_issues
- execute_command("unknown") → 抛 ValueError
- execute_vs_code 成功:mock vs 模块,执行简单代码 → {"ok":True,"stdout":...}
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from openbimagent.assembly.rule_projection import RuleProjectionIdentity
from openbimagent.assembly.semantic_snapshot import (
    FakeBlenderSemanticExecutor,
    SemanticSnapshot,
    compare_semantic_snapshots,
)
from test_compiled_utility_ir import solved_payload as legacy_solved_payload
from openbimagent.utility.geometry import upgrade_legacy_utility_ir


def solved_payload() -> dict:
    return upgrade_legacy_utility_ir(
        legacy_solved_payload(),
        dimensions_by_segment={"pipe-001": {"inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0}},
    ).model_dump(mode="json")

# ---------- 按路径加载 runner.py ----------

_RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers" / "vectorworks_mcp" / "runner.py"
)


def _load_runner_module() -> Any:
    spec = importlib.util.spec_from_file_location("vw_runner_test", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- 4 个测试 ----------


def test_execute_command_ping() -> None:
    """execute_command("ping") → {"message":"pong"}。"""
    runner = _load_runner_module()
    result = runner.execute_command("ping", {})
    assert result == {"message": "pong"}


def test_execute_command_describe_capabilities() -> None:
    """execute_command("describe_capabilities") → 含 vw_version/python_version/known_issues。"""
    runner = _load_runner_module()
    result = runner.execute_command("describe_capabilities", {})
    assert "vw_version" in result
    assert "python_version" in result
    assert "known_issues" in result
    assert isinstance(result["known_issues"], list)
    assert len(result["known_issues"]) > 0
    # 无 VW 环境下 vw_version 应为 "unknown"
    assert result["vw_version"] == "unknown"
    # ArcByCenter 坑应在 known_issues 中
    issues_text = " ".join(result["known_issues"])
    assert "ArcByCenter" in issues_text


def test_execute_command_unknown() -> None:
    """execute_command("unknown") → 抛 ValueError。"""
    runner = _load_runner_module()
    with pytest.raises(ValueError, match="Unknown command"):
        runner.execute_command("unknown_cmd", {})


def _typed_plan(*, with_rule_identity: bool = False) -> dict[str, Any]:
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder

    identity = (
        RuleProjectionIdentity(
            rule_evidence_bundle_sha256="a" * 64,
            rule_evaluation_sha256="b" * 64,
            rule_decision_status="fail",
            production_verification="eligible",
        )
        if with_rule_identity
        else None
    )
    return VectorworksBuilder().build(
        solved_payload(),
        rule_identity=identity,
    ).model_dump(mode="json")


def _typed_gate(runner: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "requires_approval": True,
        "approved": True,
        "params_hash": runner._semantic_params_hash(params),
    }


def _fake_typed_vs() -> types.ModuleType:
    fake = types.ModuleType("vs")
    fake.handles = {}
    fake.records = {}
    fake.connections = {}
    fake.layers = {}
    fake.saved_paths = []
    fake.save_as_paths = []
    fake.active_document = ""
    fake.active_layer = None
    fake.primary_unit_info = (7, 3, 3, 0, 2, True, False)
    fake.primary_units_calls = []
    fake._last = None

    def _layer(name: str) -> None:
        fake.active_layer = fake.layers.setdefault(name, {"name": name, "objects": []})

    def _new(kind: str, payload: Any) -> dict[str, Any]:
        handle = {
            "kind": kind,
            "payload": payload,
            "name": None,
            "class": None,
            "layer": fake.active_layer,
        }
        if fake.active_layer is not None:
            fake.active_layer["objects"].append(handle)
        fake._last = handle
        return handle

    def _set_name(handle: dict[str, Any], name: str) -> None:
        handle["name"] = name
        fake.handles[name] = handle

    def _save_as(path: str) -> int:
        resolved = str(Path(path).resolve())
        fake.save_as_paths.append(resolved)
        fake.saved_paths.append(resolved)
        fake.active_document = resolved
        Path(resolved).write_bytes(b"FAKE-VWX")
        return 0

    def _primary_units(
        style: int,
        precision: int,
        dimension_precision: int,
        unit_format: int,
        angle_precision: int,
        show_mark: bool,
        display_fraction: bool,
    ) -> None:
        values = (
            style,
            precision,
            dimension_precision,
            unit_format,
            angle_precision,
            show_mark,
            display_fraction,
        )
        fake.primary_units_calls.append(values)
        fake.primary_unit_info = values

    fake.GetPrimaryUnitInfo = lambda: fake.primary_unit_info
    fake.PrimaryUnits = _primary_units
    fake.Layer = _layer

    def _begin_group() -> None:
        fake._group_stack = getattr(fake, "_group_stack", [])
        fake._locus_position = None
        fake._group_stack.append(fake.active_layer)

    def _end_group() -> None:
        group = _new("group", ())
        group["payload"] = fake._locus_position or (0.0, 0.0, 0.0)
        fake._group_stack.pop()

    fake.BeginGroup = _begin_group
    fake.EndGroup = _end_group
    fake.Locus3D = lambda point: (
        _new("locus", tuple(point)),
        setattr(fake, "_locus_position", tuple(point)),
    )[1]  # returns None like real API
    fake._mesh_faces = None
    fake._mesh_vertices = []

    def _begin_mesh() -> None:
        fake._mesh_faces = []
        fake._mesh_vertices = []

    def _end_mesh() -> None:
        mesh = _new("mesh", tuple(fake._mesh_vertices))
        mesh["type_n"] = 40
        mesh["faces"] = tuple(fake._mesh_faces or [])
        fake._mesh_faces = None
        fake._mesh_vertices = []

    def _begin_poly3d() -> None:
        fake._poly = []

    def _add3dpt(point: Any) -> None:
        fake._poly.append(tuple(point))

    def _end_poly3d() -> None:
        if fake._mesh_faces is not None:
            face = tuple(fake._poly)
            fake._mesh_faces.append(face)
            for point in face:
                if point not in fake._mesh_vertices:
                    fake._mesh_vertices.append(point)
            fake._poly = []
            return
        _new("poly3d", tuple(fake._poly))
        fake._poly = []

    fake.BeginMesh = _begin_mesh
    fake.EndMesh = _end_mesh
    fake.BeginPoly3D = _begin_poly3d
    fake.Add3DPt = _add3dpt
    fake.EndPoly3D = _end_poly3d
    fake.LNewObj = lambda: fake._last
    fake.SetName = _set_name
    fake.GetTypeN = lambda handle: handle.get("type_n", 0) if handle.get("type_n", 0) else 5
    fake.GetName = lambda handle: handle.get("name")
    fake.SetClass = lambda handle, name: handle.__setitem__("class", name)
    fake.GetClass = lambda handle: handle.get("class")
    fake.NameClass = lambda name: None
    fake.GetObject = lambda name: fake.handles.get(name) or fake.records.get(name)
    fake.GetObjectUuid = lambda handle: str(id(handle))

    def _for_each_object(callback: Any, criteria: str) -> None:
        if criteria == "(ALL)":
            for layer in fake.layers.values():
                for handle in layer["objects"]:
                    callback(handle)
            return
        match = re.fullmatch(r"\(\(L='([^']+)'\) & \(N='([^']+)'\)\)", criteria)
        assert match is not None, criteria
        layer_name, object_name = match.groups()
        handle = fake.handles.get(object_name)
        if handle is not None and handle.get("layer", {}).get("name") == layer_name:
            callback(handle)

    fake.ForEachObject = _for_each_object
    fake.GetParent = lambda handle: (_ for _ in ()).throw(
        AssertionError("layer validation must not traverse parent handles")
    )
    fake.GetLName = lambda layer: (_ for _ in ()).throw(
        AssertionError("layer validation must not inspect layer handles")
    )
    fake.FInLayer = lambda layer: (_ for _ in ()).throw(
        AssertionError("layer validation must not traverse layer objects")
    )
    fake.NextObj = lambda handle: (_ for _ in ()).throw(
        AssertionError("layer validation must not traverse object handles")
    )
    fake.GetFPathName = lambda: fake.active_document
    fake.NewField = lambda record, field, default, field_type, flag: fake.records.setdefault(record, {}).setdefault(field, default)
    fake.NumFields = lambda record: len(record)
    fake.GetFldName = lambda record, index: list(record)[index - 1]
    fake.SetRecord = lambda handle, record: handle.setdefault("records", {}).setdefault(record, {})
    fake.SetRField = lambda handle, record, field, value: handle.setdefault("records", {}).setdefault(record, {}).__setitem__(field, value)
    fake.GetRField = lambda handle, record, field: handle.get("records", {}).get(record, {}).get(field, "")
    fake.Get3DCntr = lambda handle: (
    (handle["payload"][0], handle["payload"][1]),
    handle["payload"][2],
) if len(handle["payload"]) >= 3 else ((0.0, 0.0), 0.0)
    fake.GetVertNum = lambda handle: len(handle["payload"])
    fake.GetPolyPt3D = lambda handle, index: handle["payload"][index - 1]
    fake.GetMeshVertsCnt = lambda handle: len(handle["payload"]) if handle.get("kind") == "mesh" else 0
    fake.GetMeshVertex = lambda handle, index: handle["payload"][index]
    fake.SaveActiveDocument = _save_as
    return fake


def _inject_save_failure_on_second_call(fake_vs: types.ModuleType) -> Any:
    """Fail the save after the second operation, then restore via return value."""
    original = fake_vs.SaveActiveDocument
    calls = 0

    def flaky(path: str) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected host restart")
        return original(path)

    fake_vs.SaveActiveDocument = flaky
    return original


def test_assert_object_layer_uses_one_bounded_criteria_query() -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.Layer("M1-Municipal-Utility")
    fake_vs.Locus3D((0.0, 0.0, 0.0))
    handle = fake_vs.LNewObj()
    fake_vs.SetName(handle, "VW_M1_test")
    calls: list[str] = []
    real_for_each = fake_vs.ForEachObject

    def counted(callback: Any, criteria: str) -> None:
        calls.append(criteria)
        real_for_each(callback, criteria)

    fake_vs.ForEachObject = counted
    runner._assert_object_layer(fake_vs, handle, "M1-Municipal-Utility")
    assert calls == ["((L='M1-Municipal-Utility') & (N='VW_M1_test'))"]


def test_assert_object_layer_rejects_wrong_layer_without_handle_traversal() -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.Layer("Escaped-Layer")
    fake_vs.Locus3D((0.0, 0.0, 0.0))
    handle = fake_vs.LNewObj()
    fake_vs.SetName(handle, "VW_M1_foreign")
    with pytest.raises(PermissionError, match="not contained by authorized layer"):
        runner._assert_object_layer(fake_vs, handle, "M1-Municipal-Utility")


def test_assert_object_layer_requires_named_object() -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.Layer("M1-Municipal-Utility")
    fake_vs.Locus3D((0.0, 0.0, 0.0))
    handle = fake_vs.LNewObj()
    with pytest.raises(RuntimeError, match="对象名称为空"):
        runner._assert_object_layer(fake_vs, handle, "M1-Municipal-Utility")


def test_assert_object_layer_requires_bounded_query_api() -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.Layer("M1-Municipal-Utility")
    fake_vs.Locus3D((0.0, 0.0, 0.0))
    handle = fake_vs.LNewObj()
    fake_vs.SetName(handle, "VW_M1_test")
    del fake_vs.ForEachObject
    with pytest.raises(RuntimeError, match="ForEachObject 不可用"):
        runner._assert_object_layer(fake_vs, handle, "M1-Municipal-Utility")


def test_criteria_atom_rejects_unbounded_syntax() -> None:
    runner = _load_runner_module()
    with pytest.raises(ValueError, match="not allowlisted"):
        runner._criteria_atom("bad' | ALL")


def test_execute_typed_plan_is_idempotent_and_persists_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "case.vwx"
    plan = _typed_plan()

    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    first = runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(runner, params),
    )
    assert first["status"] == "partial"
    assert first["geometry_status"] == "dimension_verified_topology_unverified"
    assert any("topology" in caveat for caveat in first["geometry_caveats"])
    assert len(first["applied_operations"]) == len(plan["operations"])
    assert fake_vs.saved_paths == [str(output.resolve())] * len(plan["operations"])
    assert fake_vs.save_as_paths == [str(output.resolve())] * len(plan["operations"])
    assert fake_vs.primary_unit_info[0] == 9
    assert fake_vs.primary_units_calls == [(9, 3, 3, 0, 2, True, False)]
    assert {layer["name"] for layer in fake_vs.layers.values()} == {"M1-Municipal-Utility"}
    assert {
        handle["layer"]["name"]
        for handle in fake_vs.handles.values()
    } == {"M1-Municipal-Utility"}
    municipal_fields = set(fake_vs.records["OpenBIMAgent_MunicipalUtility"])
    assert {
        "StableObjectID",
        "Domain_ground_elevation_m",
        "DiameterMM",
        "Material",
        "Slope",
    } <= municipal_fields
    snapshot = SemanticSnapshot.model_validate(first["semantic_snapshot"])
    assert snapshot.source_ir_sha256 == plan["compiled_ir_sha256"]
    assert {item.stable_id for item in snapshot.objects} == {
        "sys-sewage", "mh-001", "mh-001-out", "mh-002", "mh-002-in", "pipe-001"
    }
    comparison = compare_semantic_snapshots(
        FakeBlenderSemanticExecutor().execute(solved_payload()),
        snapshot,
    )
    assert comparison.ok, comparison.model_dump(mode="json")
    sidecar = output.with_suffix(".vwx.openbimagent.json")
    assert sidecar.is_file()
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    assert state["canonical_sha256"] == plan["canonical_sha256"]

    second = runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(runner, params),
    )
    assert second == first
    assert fake_vs.saved_paths == [str(output.resolve())] * len(plan["operations"])

    isolated = output.rename(tmp_path / "case.vwx.missing")
    assert isolated.is_file()
    assert not output.exists()
    with pytest.raises(FileNotFoundError, match="controlled document is missing"):
        runner.execute_command(
            "execute_plan",
            params,
            gate=_typed_gate(runner, params),
        )


def test_execute_typed_plan_resumes_after_partial_without_reapplying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save_as = _inject_save_failure_on_second_call(fake_vs)
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "partial.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }

    partial = runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(runner, params),
    )
    assert partial["status"] == "partial"
    assert len(partial["applied_operations"]) == 1
    first_object_count = len(fake_vs.handles)

    fake_vs.SaveActiveDocument = original_save_as
    fake_vs.active_document = str(output.resolve())
    recovered_runner = _load_runner_module()
    recovered = recovered_runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(recovered_runner, params),
    )
    assert recovered["status"] == "partial"
    assert recovered["geometry_status"] == "dimension_verified_topology_unverified"
    assert len(recovered["applied_operations"]) == len(plan["operations"])
    assert len(fake_vs.handles) >= first_object_count
    assert sum(1 for name in fake_vs.handles if name == plan["operations"][0]["name"]) == 1


def test_execute_typed_plan_recovers_save_before_sidecar_ack_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "save-before-ack.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    real_write_state = runner._write_execution_state
    writes = 0

    def crash_after_first_save(path: Path, state: dict[str, Any]) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise SystemExit("injected crash after Vectorworks save before sidecar acknowledgement")
        real_write_state(path, state)

    runner._write_execution_state = crash_after_first_save
    with pytest.raises(SystemExit, match="after Vectorworks save"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )
    state = json.loads(
        output.with_suffix(".vwx.openbimagent.json").read_text(encoding="utf-8")
    )
    assert state["applied_operation_ids"] == []
    assert output.is_file()
    assert fake_vs.active_document == str(output.resolve())

    runner._write_execution_state = real_write_state
    completed = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert completed["status"] == "partial"
    assert completed["geometry_status"] == "dimension_verified_topology_unverified"
    assert len(fake_vs.handles) == 6


def test_execute_typed_plan_requires_complete_host_facts_for_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    real_get_r_field = fake_vs.GetRField

    def missing_source_path(handle: Any, record: str, field: str) -> Any:
        if record == "OpenBIMAgent_MunicipalUtility" and field == "SourceIRPath":
            return ""
        return real_get_r_field(handle, record, field)

    fake_vs.GetRField = missing_source_path
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "missing-host-facts.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    partial = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert partial["status"] == "partial"
    assert partial["semantic_snapshot"] is None
    assert partial["errors"] and "semantic_projection" in partial["errors"][0]

    fake_vs.GetRField = real_get_r_field
    completed = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert completed["status"] == "partial"
    assert completed["geometry_status"] == "dimension_verified_topology_unverified"
    assert completed["semantic_snapshot"] is not None


def test_execute_typed_plan_rejects_wrong_active_document_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save_as = _inject_save_failure_on_second_call(fake_vs)
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "wrong-document.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    partial = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert partial["status"] == "partial"
    fake_vs.SaveActiveDocument = original_save_as
    fake_vs.active_document = str((tmp_path / "unrelated.vwx").resolve())
    with pytest.raises(RuntimeError, match="controlled target document"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )


@pytest.mark.parametrize("placeholder", ["Untitled-1", "Untitled 1", "untitled", "Untitled-12"])
def test_execute_typed_plan_accepts_unnamed_blank_document_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placeholder: str
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.active_document = placeholder
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "first-execution.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    completed = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert completed["status"] == "partial"
    assert completed["geometry_status"] == "dimension_verified_topology_unverified"
    assert fake_vs.save_as_paths == [str(output.resolve())] * len(plan["operations"])
    assert output.is_file()


def test_execute_typed_plan_rejects_named_document_before_first_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    fake_vs.active_document = str((tmp_path / "unrelated.vwx").resolve())
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "first-execution.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(RuntimeError, match="unnamed blank document"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )
    assert fake_vs.handles == {}
    assert fake_vs.primary_units_calls == []
    assert not output.exists()


def test_execute_typed_plan_rejects_missing_controlled_file_on_partial_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save_as = _inject_save_failure_on_second_call(fake_vs)
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "missing-document.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    partial = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert partial["status"] == "partial"
    isolated = output.rename(tmp_path / "missing-document.vwx.missing")
    assert isolated.is_file()
    assert not output.exists()
    fake_vs.SaveActiveDocument = original_save_as
    with pytest.raises(FileNotFoundError, match="controlled document is missing"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )


def test_execute_typed_plan_fails_closed_when_host_save_silently_skips_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save_as = fake_vs.SaveActiveDocument
    writes = 0

    def silent_after_first(path: str) -> int:
        nonlocal writes
        writes += 1
        if writes >= 2:
            return 0
        return original_save_as(path)

    fake_vs.SaveActiveDocument = silent_after_first
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "silent-save.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    partial = runner.execute_command(
        "execute_plan", params, gate=_typed_gate(runner, params)
    )
    assert partial["status"] == "partial"
    assert len(partial["applied_operations"]) == 1
    assert any(
        "did not write the controlled document" in error
        for error in partial["errors"]
    )


def test_execute_typed_plan_fails_closed_when_meter_units_cannot_be_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()

    def ignore_primary_units(*args: Any) -> None:
        del args

    fake_vs.PrimaryUnits = ignore_primary_units
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "wrong-units.vwx"
    plan = _typed_plan()
    params = {
        "plan": plan,
        "output_path": str(output),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(RuntimeError, match="units must be meters"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )
    assert not output.exists()
    state = json.loads(
        output.with_suffix(".vwx.openbimagent.json").read_text(encoding="utf-8")
    )
    assert state["applied_operation_ids"] == []


def test_execute_typed_plan_rejects_missing_or_tampered_runner_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    monkeypatch.setitem(sys.modules, "vs", _fake_typed_vs())
    params = {
        "plan": _typed_plan(),
        "output_path": str(tmp_path / "gate.vwx"),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(PermissionError, match="缺少"):
        runner.execute_command("execute_plan", params)
    with pytest.raises(PermissionError, match="未获"):
        runner.execute_command(
            "execute_plan",
            params,
            gate={"requires_approval": True, "approved": False, "params_hash": "x"},
        )
    with pytest.raises(PermissionError, match="hash"):
        runner.execute_command(
            "execute_plan",
            params,
            gate={"requires_approval": True, "approved": True, "params_hash": "tampered"},
        )


def test_execute_typed_plan_projects_and_validates_rule_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan(with_rule_identity=True)
    params = {
        "plan": plan,
        "output_path": str(tmp_path / "rule-identity.vwx"),
        "authorized_root": str(tmp_path),
    }
    receipt = runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(runner, params),
    )
    snapshot = SemanticSnapshot.model_validate(receipt["semantic_snapshot"])
    assert snapshot.rule_identity is not None
    assert snapshot.rule_identity.rule_evaluation_sha256 == "b" * 64
    assert all(
        item.domain_properties["rule_decision_status"] == "fail"
        for item in snapshot.objects
    )

    missing_approval = json.loads(json.dumps(plan))
    missing_approval["rule_identity"]["exception_approval_id"] = "approval-1"
    with pytest.raises(ValueError, match="例外审批"):
        runner._validate_typed_plan(missing_approval)

    false_pass = json.loads(json.dumps(plan))
    false_pass["rule_identity"]["rule_decision_status"] = "pass"
    false_pass["rule_identity"]["production_verification"] = "review_required"
    with pytest.raises(ValueError, match="PASS/FAIL"):
        runner._validate_typed_plan(false_pass)

    drifted = json.loads(json.dumps(plan))
    field = next(
        item
        for operation in drifted["operations"]
        if operation["operation"] == "set_record"
        for item in operation["record_fields"]
        if item["field_name"] == "Domain_rule_evaluation_sha256"
    )
    field["value"] = "c" * 64
    drifted["canonical_sha256"] = runner._canonical_plan_sha256(drifted)
    drifted["idempotency_key"] = f"vw-plan:{drifted['canonical_sha256']}"
    with pytest.raises(ValueError, match="rule_identity"):
        runner._validate_typed_plan(drifted)


def test_execute_typed_plan_rejects_escape_overwrite_and_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    monkeypatch.setitem(sys.modules, "vs", _fake_typed_vs())
    plan = _typed_plan()

    escape_params = {
        "plan": plan,
        "output_path": str(tmp_path.parent / "escape.vwx"),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(ValueError, match="授权根目录"):
        runner.execute_command(
            "execute_plan",
            escape_params,
            gate=_typed_gate(runner, escape_params),
        )

    existing = tmp_path / "existing.vwx"
    existing.write_bytes(b"pre-existing")
    existing_params = {
        "plan": plan,
        "output_path": str(existing),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        runner.execute_command(
            "execute_plan",
            existing_params,
            gate=_typed_gate(runner, existing_params),
        )

    escaped_layer = json.loads(json.dumps(plan))
    create = next(
        item for item in escaped_layer["operations"] if item["operation"] == "create_object"
    )
    create["layer_name"] = "Escaped-Layer"
    escaped_layer["canonical_sha256"] = runner._canonical_plan_sha256(escaped_layer)
    escaped_layer["idempotency_key"] = f"vw-plan:{escaped_layer['canonical_sha256']}"
    escaped_params = {
        "plan": escaped_layer,
        "output_path": str(tmp_path / "escaped-layer.vwx"),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(ValueError, match="authorized Vectorworks layer"):
        runner.execute_command(
            "execute_plan",
            escaped_params,
            gate=_typed_gate(runner, escaped_params),
        )

    tampered = dict(plan)
    tampered["canonical_sha256"] = "0" * 64
    tampered_params = {
        "plan": tampered,
        "output_path": str(tmp_path / "tampered.vwx"),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(ValueError, match="canonical_sha256"):
        runner.execute_command(
            "execute_plan",
            tampered_params,
            gate=_typed_gate(runner, tampered_params),
        )

    invalid_compiled_hash = dict(plan)
    invalid_compiled_hash["compiled_ir_sha256"] = "not-a-sha256"
    invalid_hash_params = {
        "plan": invalid_compiled_hash,
        "output_path": str(tmp_path / "invalid-hash.vwx"),
        "authorized_root": str(tmp_path),
    }
    with pytest.raises(ValueError, match="compiled_ir_sha256"):
        runner.execute_command(
            "execute_plan",
            invalid_hash_params,
            gate=_typed_gate(runner, invalid_hash_params),
        )


def test_execute_vs_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_vs_code 成功:mock vs 模块,执行简单代码 → {"ok":True,"stdout":...}。

    无 VW 环境,vs 模块不存在,用 monkeypatch 注入 fake vs 模块。
    """
    runner = _load_runner_module()

    # 构造 fake vs 模块
    fake_vs = types.ModuleType("vs")
    captured: dict[str, Any] = {}

    def _message(text: str) -> str:
        captured["message"] = text
        return text

    fake_vs.Message = _message  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vs", fake_vs)

    result = runner.execute_vs_code("vs.Message('hello')")

    assert result["ok"] is True
    assert "stdout" in result
    assert result["error"] if not result["ok"] else True  # ok 时无 error 键
    assert captured.get("message") == "hello"


def test_resolve_ipc_dirs_fixed_root_and_env_override(tmp_path, monkeypatch) -> None:
    """IPC 目录约定：默认固定根（免 VW CWD 依赖），env 可覆盖。"""
    runner = _load_runner_module()
    default_root, jobs, results, heartbeat = runner.resolve_ipc_dirs()
    assert jobs == default_root / "jobs"
    assert results == default_root / "results"
    assert heartbeat == default_root / "runner_heartbeat.json"
    assert default_root.is_absolute()

    monkeypatch.setenv("OPENBIMAGENT_VW_IPC_ROOT", str(tmp_path))
    root2, jobs2, _, hb2 = runner.resolve_ipc_dirs()
    assert root2 == tmp_path
    assert jobs2 == tmp_path / "jobs"
    assert hb2 == tmp_path / "runner_heartbeat.json"


def test_write_heartbeat_roundtrip(tmp_path) -> None:
    """心跳文件可被外部探测（时间戳 + 启动时间 + VW 版本回退 unknown）。"""
    import time as _time
    from datetime import datetime

    runner = _load_runner_module()
    hb = tmp_path / "runner_heartbeat.json"
    before = _time.time()
    runner.write_heartbeat(hb, datetime.now())
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["ts"] >= before
    assert data["vw_version"] in {"unknown"} or data["vw_version"].startswith("VectorWorks")
    assert "started_at" in data
    assert "ipc_root" not in data
    assert "stopped_reason" not in data


def test_write_heartbeat_declares_ipc_root_and_stop_reason(tmp_path) -> None:
    """心跳声明 runner 实际轮询的 IPC 根；受控停止后写入 stopped_reason。"""
    from datetime import datetime

    runner = _load_runner_module()
    hb = tmp_path / "runner_heartbeat.json"
    runner.write_heartbeat(hb, datetime.now(), ipc_root=tmp_path)
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["ipc_root"] == str(tmp_path.resolve())
    runner.write_heartbeat(
        hb, datetime.now(), ipc_root=tmp_path, stopped_reason="stop file observed"
    )
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["stopped_reason"] == "stop file observed"


def test_run_session_stops_when_stop_file_exists(tmp_path) -> None:
    """stop 文件是正常停止方式：不轮询 job、不中断宿主，写心跳后退出。"""
    runner = _load_runner_module()
    ipc = tmp_path / "ipc"
    ipc.mkdir()
    stop_file = ipc / "stop_runner"
    stop_file.write_text("stop", encoding="utf-8")
    jobs = ipc / "jobs"
    jobs.mkdir()
    (jobs / "never-processed.json").write_text("{}", encoding="utf-8")
    reason = runner.run_session(
        ipc_root=ipc, stop_file=stop_file, sleep=lambda _seconds: None
    )
    assert "stop file" in reason
    assert (jobs / "never-processed.json").is_file()
    heartbeat = json.loads((ipc / "runner_heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["stopped_reason"] == reason
    assert heartbeat["ipc_root"] == str(ipc.resolve())
    log_text = (ipc / "runner_log.txt").read_text(encoding="utf-8")
    assert "stopped reason=" in log_text


def test_run_session_is_bounded_by_max_seconds(tmp_path) -> None:
    """有界会话：到时自动退出（等当前轮询轮次结束，不强杀宿主）。"""
    runner = _load_runner_module()
    ipc = tmp_path / "ipc"
    ticks = {"value": 0.0}

    def clock() -> float:
        ticks["value"] += 10.0
        return ticks["value"]

    reason = runner.run_session(
        ipc_root=ipc,
        max_seconds=60.0,
        sleep=lambda _seconds: None,
        clock=clock,
    )
    assert "bounded session limit reached" in reason
    heartbeat = json.loads((ipc / "runner_heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["stopped_reason"] == reason


def _pipe_operation(plan: dict[str, Any]) -> dict[str, Any]:
    return next(
        operation
        for operation in plan["operations"]
        if operation.get("operation") == "create_object"
        and operation.get("object_type") == "pipe_segment"
    )


def test_geometry_snapshot_measures_actual_mesh_vertices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    output = tmp_path / "geometry.vwx"
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(output), "authorized_root": str(tmp_path)}
    receipt = runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    # Dimensions verify from actual vertices; closed-solid topology has no
    # non-destructive host readback API and must stay unverified.
    assert receipt["status"] == "partial"
    assert receipt["geometry_status"] == "dimension_verified_topology_unverified"
    assert any("topology" in caveat for caveat in receipt["geometry_caveats"])
    geometry = receipt["geometry_snapshot"]
    assert geometry["status"] == "dimension_verified_topology_unverified"
    assert geometry["topology_status"] == "unverified"
    assert geometry["readback_tolerance_m"] == 1e-5
    assert "not a surface accuracy" in geometry["readback_tolerance_basis"]
    item = geometry["objects"][0]
    assert item["dimension_status"] == "verified"
    assert item["topology_status"] == "unverified"
    assert item["topology_checks"] == {}
    assert "no non-destructive mesh face/edge readback" in item["topology_reason"]
    assert item["dimension_checks"]["object_type_mesh"] is True
    assert set(item["dimension_checks"].values()) == {True}
    assert item["measured"]["vertex_count"] == 96
    assert item["measured"]["ring_vertex_counts"] == {
        "outer_start": 24, "inner_start": 24, "outer_end": 24, "inner_end": 24,
    }
    assert item["measured"]["inner_diameter_mm"] == pytest.approx(300.0)
    assert item["measured"]["outer_diameter_mm"] == pytest.approx(360.0)
    assert item["measured"]["axis_start_m"]["z_m"] == pytest.approx(10.15)
    assert item["measured"]["axis_end_m"]["z_m"] == pytest.approx(10.12)
    assert item["discretization"]["circumferential_segments"] == 24
    assert item["discretization"]["is_engineering_tolerance"] is False
    sagitta = 1.0 - math.cos(math.pi / 24)
    assert item["discretization"]["radial_deviation_mm"]["inner"] == pytest.approx(
        150.0 * sagitta, abs=1e-5
    )
    assert item["discretization"]["radial_deviation_mm"]["outer"] == pytest.approx(
        180.0 * sagitta, abs=1e-5
    )


def test_geometry_readback_detects_moved_mesh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "moved.vwx"), "authorized_root": str(tmp_path)}
    runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    handle = fake_vs.handles[_pipe_operation(plan)["name"]]
    handle["payload"] = tuple((x + 0.1, y, z) for x, y, z in handle["payload"])
    geometry = runner._project_geometry_snapshot(plan, fake_vs)
    assert geometry["status"] == "failed"
    item = geometry["objects"][0]
    assert item["dimension_status"] == "failed"
    assert item["status"] == "failed"
    assert item["dimension_checks"]["axis_start"] is False or item["dimension_checks"]["axis_end"] is False
    assert item["measured"] is None or item["measured"]["max_centroid_error_mm"] > 1.0


def test_geometry_readback_detects_scaled_mesh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "scaled.vwx"), "authorized_root": str(tmp_path)}
    runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    handle = fake_vs.handles[_pipe_operation(plan)["name"]]
    handle["payload"] = tuple((x * 1.05, y * 1.05, z * 1.05) for x, y, z in handle["payload"])
    geometry = runner._project_geometry_snapshot(plan, fake_vs)
    assert geometry["status"] == "failed"
    item = geometry["objects"][0]
    assert item["dimension_status"] == "failed"
    assert item["dimension_checks"]["inner_diameter"] is False or item["dimension_checks"]["outer_diameter"] is False


def test_missing_mesh_with_stale_records_cannot_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "missing.vwx"), "authorized_root": str(tmp_path)}
    runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    pipe = _pipe_operation(plan)
    del fake_vs.handles[pipe["name"]]  # records/layer entries remain stale
    geometry = runner._project_geometry_snapshot(plan, fake_vs)
    assert geometry["status"] == "failed"
    assert geometry["objects"][0]["dimension_checks"]["object_exists"] is False
    assert geometry["objects"][0]["dimension_status"] == "failed"
    with pytest.raises(ValueError, match="missing object|measured pipe axis"):
        runner._project_semantic_snapshot(plan, fake_vs, geometry)


def test_vertex_only_readback_without_face_api_never_claims_topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无面读回 API 的宿主上，顶点被保留的面删除/反转/重复都无法伪造闭合结论。"""
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "vertex-only.vwx"), "authorized_root": str(tmp_path)}
    runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    mesh = fake_vs.handles[_pipe_operation(plan)["name"]]
    mesh["faces"] = tuple(mesh["faces"])[:-1]  # a face is gone; vertices stay intact
    geometry = runner._project_geometry_snapshot(plan, fake_vs)
    assert geometry["status"] == "dimension_verified_topology_unverified"
    item = geometry["objects"][0]
    assert item["dimension_status"] == "verified"
    assert item["topology_status"] == "unverified"
    assert item["topology_checks"] == {}
    assert item["status"] != "verified"


def _enable_host_face_readback(runner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "_read_host_mesh_faces",
        lambda vs, handle: list(handle.get("faces") or []),
    )


def test_face_readback_verifies_intact_closed_mesh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主提供非破坏性面读回时，闭合网格通过拓扑检查并给出完整验证。"""
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    _enable_host_face_readback(runner, monkeypatch)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "faces.vwx"), "authorized_root": str(tmp_path)}
    receipt = runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    assert receipt["status"] == "completed"
    assert receipt["geometry_status"] == "verified"
    assert receipt["geometry_caveats"] == []
    item = receipt["geometry_snapshot"]["objects"][0]
    assert item["topology_status"] == "verified"
    assert item["topology_face_count"] == 96
    assert set(item["topology_checks"].values()) == {True}


@pytest.mark.parametrize("mutation", ["deleted", "reversed", "duplicated"])
def test_topology_face_mutations_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """删除面/反转面/重复面的针对性负例：顶点集合不变，维度仍通过，拓扑必须失败。"""
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    _enable_host_face_readback(runner, monkeypatch)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / f"face-{mutation}.vwx"), "authorized_root": str(tmp_path)}
    runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    mesh = fake_vs.handles[_pipe_operation(plan)["name"]]
    faces = list(mesh["faces"])
    if mutation == "deleted":
        faces = faces[:-1]
    elif mutation == "reversed":
        faces[0] = tuple(reversed(faces[0]))
    else:
        faces = [*faces, faces[0]]
    mesh["faces"] = tuple(faces)
    geometry = runner._project_geometry_snapshot(plan, fake_vs)
    assert geometry["status"] == "failed"
    item = geometry["objects"][0]
    assert item["dimension_status"] == "verified"  # vertices alone cannot catch this
    assert item["topology_status"] == "failed"
    assert item["status"] == "failed"
    assert any("topology" in failure for failure in item["failures"])


def test_host_without_mesh_api_is_rejected_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    del fake_vs.BeginMesh
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    params = {"plan": plan, "output_path": str(tmp_path / "no-mesh.vwx"), "authorized_root": str(tmp_path)}
    with pytest.raises(RuntimeError, match="缺少直管实体所需的 API"):
        runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    assert fake_vs.saved_paths == []
    assert fake_vs.handles == {}
    assert fake_vs.primary_units_calls == []


@pytest.mark.parametrize("mutation", ["bad_dimensions", "non_straight"])
def test_invalid_pipe_geometry_is_rejected_before_host_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    plan = _typed_plan()
    pipe = _pipe_operation(plan)
    if mutation == "bad_dimensions":
        pipe["inner_diameter_mm"] = 400.0
        pipe["outer_diameter_mm"] = 360.0
    else:
        pipe["centerline"] = [
            pipe["centerline"][0],
            {"x_m": 5.0, "y_m": 1.0, "z_m": 10.0},
            pipe["centerline"][1],
        ]
    digest = runner._canonical_plan_sha256(plan)
    plan["canonical_sha256"] = digest
    plan["idempotency_key"] = f"vw-plan:{digest}"
    params = {"plan": plan, "output_path": str(tmp_path / f"{mutation}.vwx"), "authorized_root": str(tmp_path)}
    with pytest.raises(ValueError):
        runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    assert fake_vs.saved_paths == []
    assert fake_vs.handles == {}


def test_plan_oblique_and_sloped_straight_pipe_geometry_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder
    from openbimagent.utility.candidate import UtilityCandidate, validate_utility_candidate

    payload = {
        "protocol_version": "0.1",
        "request_id": "diagonal-pipe",
        "source_ir_sha256": "c" * 64,
        "coordinate_reference": {
            "crs_id": "LOCAL:DIAGONAL", "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m", "vertical_unit": "m", "vertical_datum": "test",
        },
        "system_id": "sys-diagonal", "system_name": "diagonal test",
        "nodes": [
            {"node_id": "A", "node_type": "manhole", "x_m": 0.0, "y_m": 0.0, "ground_elevation_m": 11.5, "invert_anchor_m": 10.0, "connection_radius_m": 0.6},
            {"node_id": "B", "node_type": "manhole", "x_m": 10.0, "y_m": 10.0, "ground_elevation_m": 11.5, "invert_anchor_m": None, "connection_radius_m": 0.6},
        ],
        "segments": [{
            "segment_id": "P-diag", "start_node_id": "A", "end_node_id": "B",
            "diameter_mm": 300.0, "material": "concrete", "design_slope": 0.003,
            "surface_context": "driveway", "inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0,
        }],
        "collision_context": {"coverage": "complete", "obstacles": []},
    }
    validation = validate_utility_candidate(UtilityCandidate(solver_input=payload))
    assert validation.executable and validation.compiled_ir is not None
    plan = VectorworksBuilder().build(validation.compiled_ir).model_dump(mode="json")
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    monkeypatch.setitem(sys.modules, "vs", fake_vs)
    params = {"plan": plan, "output_path": str(tmp_path / "diagonal.vwx"), "authorized_root": str(tmp_path)}
    receipt = runner.execute_command("execute_plan", params, gate=_typed_gate(runner, params))
    assert receipt["status"] == "partial"
    assert receipt["geometry_status"] == "dimension_verified_topology_unverified"
    measured = receipt["geometry_snapshot"]["objects"][0]["measured"]
    assert measured["inner_diameter_mm"] == pytest.approx(300.0)
    assert measured["outer_diameter_mm"] == pytest.approx(360.0)
    expected_length = (200.0 + (0.003 * 200 ** 0.5) ** 2) ** 0.5
    assert measured["axis_direction"]["x"] == pytest.approx(10.0 / expected_length, abs=1e-9)
    assert measured["axis_direction"]["y"] == pytest.approx(10.0 / expected_length, abs=1e-9)
    assert measured["axis_direction"]["z"] < 0
    assert measured["length_m"] == pytest.approx(expected_length, abs=1e-5)


def test_runner_source_runs_on_vectorworks_2024_host_python() -> None:
    """VW 2024 embeds Python 3.9; local tests run 3.12 and cannot catch newer APIs."""
    tree = ast.parse(_RUNNER_PATH.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "zip":
            if any(keyword.arg == "strict" for keyword in node.keywords):
                offenders.append(f"zip(strict=...) line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "pairwise":
            offenders.append(f"itertools.pairwise line {node.lineno}")
        if isinstance(node, ast.Match):
            offenders.append(f"match statement line {node.lineno}")
    assert not offenders, (
        "runner.py must stay compatible with the Vectorworks 2024 host Python (3.9): "
        + "; ".join(offenders)
    )


def test_runner_source_never_persists_through_menu_save() -> None:
    """VW2024 host evidence (2026-10-18): DoMenuTextByName("Save", 0) is a silent
    no-op on an already-named document - no write, no error, no dialog.  Every
    persistence point must go through the return-code checked SaveActiveDocument."""
    offenders: list[str] = []
    for lineno, line in enumerate(_RUNNER_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        code = line.split("#", 1)[0]
        if "DoMenuTextByName" in code:
            offenders.append(f"line {lineno}: {line.strip()}")
    assert not offenders, (
        "runner.py must not persist through menu commands (silent no-op on "
        "Vectorworks 2024): " + "; ".join(offenders)
    )
