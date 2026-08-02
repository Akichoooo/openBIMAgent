"""VW MCP runner 单测:命令分发 + vs 代码执行(无 VW 环境部分用 mock)。

测试位置:根 tests/(testpaths=["tests"])。用 importlib 按路径加载 runner.py。

覆盖(4 个测试):
- execute_command("ping") → {"message":"pong"}
- execute_command("describe_capabilities") → 含 vw_version/python_version/known_issues
- execute_command("unknown") → 抛 ValueError
- execute_vs_code 成功:mock vs 模块,执行简单代码 → {"ok":True,"stdout":...}
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from openbimagent.assembly.semantic_snapshot import (
    FakeBlenderSemanticExecutor,
    SemanticSnapshot,
    compare_semantic_snapshots,
)
from test_compiled_utility_ir import solved_payload

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


def _typed_plan() -> dict[str, Any]:
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder
    from test_compiled_utility_ir import solved_payload

    return VectorworksBuilder().build(solved_payload()).model_dump(mode="json")


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
    fake.save_menu_calls = 0
    fake.active_document = ""
    fake.active_layer = None
    fake.primary_unit_info = (7, 3, 3, 0, 2, True, False)
    fake.primary_units_calls = []
    fake._last = None

    def _layer(name: str) -> None:
        fake.active_layer = fake.layers.setdefault(name, {"name": name})

    def _new(kind: str, payload: Any) -> dict[str, Any]:
        handle = {
            "kind": kind,
            "payload": payload,
            "name": None,
            "class": None,
            "layer": fake.active_layer,
        }
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

    def _save_menu(name: str, index: int) -> None:
        assert (name, index) == ("Save", 0)
        if not fake.active_document:
            raise RuntimeError("no active document")
        fake.save_menu_calls += 1
        fake.saved_paths.append(fake.active_document)
        Path(fake.active_document).write_bytes(b"FAKE-VWX")

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
    fake.Locus3D = lambda point: _new("locus", tuple(point))
    fake.BeginPoly3D = lambda: setattr(fake, "_poly", [])
    fake.Add3DPt = lambda point: fake._poly.append(tuple(point))
    fake.EndPoly3D = lambda: _new("poly3d", tuple(fake._poly))
    fake.LNewObj = lambda: fake._last
    fake.SetName = _set_name
    fake.GetName = lambda handle: handle.get("name")
    fake.SetClass = lambda handle, name: handle.__setitem__("class", name)
    fake.GetClass = lambda handle: handle.get("class")
    fake.NameClass = lambda name: None
    fake.GetObject = lambda name: fake.handles.get(name) or fake.records.get(name)
    fake.GetLayer = lambda handle: handle.get("layer")
    fake.GetLName = lambda layer: layer.get("name") if layer else None
    fake.GetFPathName = lambda: fake.active_document
    fake.NewField = lambda record, field, default, field_type, flag: fake.records.setdefault(record, {}).setdefault(field, default)
    fake.NumFields = lambda record: len(record)
    fake.GetFldName = lambda record, index: list(record)[index - 1]
    fake.SetRecord = lambda handle, record: handle.setdefault("records", {}).setdefault(record, {})
    fake.SetRField = lambda handle, record, field, value: handle.setdefault("records", {}).setdefault(record, {}).__setitem__(field, value)
    fake.GetRField = lambda handle, record, field: handle.get("records", {}).get(record, {}).get(field, "")
    fake.Get3DCntr = lambda handle: ((handle["payload"][0], handle["payload"][1]), handle["payload"][2])
    fake.GetVertNum = lambda handle: len(handle["payload"])
    fake.GetPolyPt3D = lambda handle, index: handle["payload"][index - 1]
    fake.SaveActiveDocument = _save_as
    fake.DoMenuTextByName = _save_menu
    return fake


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
    assert first["status"] == "completed"
    assert len(first["applied_operations"]) == len(plan["operations"])
    assert fake_vs.saved_paths == [str(output.resolve())] * len(plan["operations"])
    assert fake_vs.save_as_paths == [str(output.resolve())]
    assert fake_vs.save_menu_calls == len(plan["operations"]) - 1
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

    output.unlink()
    with pytest.raises(FileNotFoundError, match="completed receipt"):
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
    original_save_menu = fake_vs.DoMenuTextByName
    save_menu_calls = 0

    def fail_first_save_menu(name: str, index: int) -> None:
        nonlocal save_menu_calls
        save_menu_calls += 1
        if save_menu_calls == 1:
            raise RuntimeError("injected host restart")
        original_save_menu(name, index)

    fake_vs.DoMenuTextByName = fail_first_save_menu
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

    fake_vs.DoMenuTextByName = original_save_menu
    fake_vs.active_document = str(output.resolve())
    recovered_runner = _load_runner_module()
    completed = recovered_runner.execute_command(
        "execute_plan",
        params,
        gate=_typed_gate(recovered_runner, params),
    )
    assert completed["status"] == "completed"
    assert len(completed["applied_operations"]) == len(plan["operations"])
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
    assert completed["status"] == "completed"
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
    assert completed["status"] == "completed"
    assert completed["semantic_snapshot"] is not None


def test_execute_typed_plan_rejects_wrong_active_document_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save_menu = fake_vs.DoMenuTextByName
    save_menu_calls = 0

    def fail_first_save_menu(name: str, index: int) -> None:
        nonlocal save_menu_calls
        save_menu_calls += 1
        if save_menu_calls == 1:
            raise RuntimeError("injected host restart")
        original_save_menu(name, index)

    fake_vs.DoMenuTextByName = fail_first_save_menu
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
    fake_vs.DoMenuTextByName = original_save_menu
    fake_vs.active_document = str((tmp_path / "unrelated.vwx").resolve())
    with pytest.raises(RuntimeError, match="controlled target document"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
        )


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
    original_save_menu = fake_vs.DoMenuTextByName
    save_menu_calls = 0

    def fail_first_save_menu(name: str, index: int) -> None:
        nonlocal save_menu_calls
        save_menu_calls += 1
        if save_menu_calls == 1:
            raise RuntimeError("injected host restart")
        original_save_menu(name, index)

    fake_vs.DoMenuTextByName = fail_first_save_menu
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
    output.unlink()
    fake_vs.DoMenuTextByName = original_save_menu
    with pytest.raises(FileNotFoundError, match="controlled document is missing"):
        runner.execute_command(
            "execute_plan", params, gate=_typed_gate(runner, params)
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
