"""Blender typed host adapter tests with a focused fake bpy; no real host writes."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openbimagent.assembly.blender_plan import BlenderBuilder
from openbimagent.assembly.semantic_snapshot import SemanticSnapshot
from test_compiled_utility_ir import solved_payload

_TYPED_PATH = (
    Path(__file__).resolve().parents[1]
    / "mcp_servers"
    / "blender_mcp"
    / "typed_plan.py"
)


def _load_typed_module() -> Any:
    spec = importlib.util.spec_from_file_location("blender_typed_plan_test", _TYPED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Point:
    def __init__(self) -> None:
        self._co = SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)

    @property
    def co(self) -> Any:
        return self._co

    @co.setter
    def co(self, value: tuple[float, float, float, float]) -> None:
        self._co = SimpleNamespace(x=value[0], y=value[1], z=value[2], w=value[3])


class _Points(list[_Point]):
    def __init__(self) -> None:
        super().__init__([_Point()])

    def add(self, count: int) -> None:
        self.extend(_Point() for _ in range(count))


class _Spline:
    def __init__(self) -> None:
        self.points = _Points()


class _Splines(list[_Spline]):
    def new(self, kind: str) -> _Spline:
        assert kind == "POLY"
        spline = _Spline()
        self.append(spline)
        return spline


class _Curve:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dimensions = "2D"
        self.resolution_u = 0
        self.bevel_depth = 0.0
        self.bevel_resolution = 0
        self.splines = _Splines()


class _Curves:
    def __init__(self) -> None:
        self.values: dict[str, _Curve] = {}

    def new(self, name: str, type: str) -> _Curve:
        assert type == "CURVE"
        value = _Curve(name)
        self.values[name] = value
        return value


class _Object(dict[str, Any]):
    def __init__(self, store: "_Objects", name: str, data: Any = None) -> None:
        super().__init__()
        self._store = store
        self._name = name
        self.data = data
        self.users_collection: list[_Collection] = []
        self.location = SimpleNamespace(x=0.0, y=0.0, z=0.0)

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._store.rename(self, value)
        self._name = value


class _Objects:
    def __init__(self) -> None:
        self.values: dict[str, _Object] = {}

    def __iter__(self):
        return iter(self.values.values())

    def get(self, name: str) -> _Object | None:
        return self.values.get(name)

    def new(self, name: str, data: Any) -> _Object:
        value = _Object(self, name, data)
        self.values[name] = value
        return value

    def rename(self, obj: _Object, name: str) -> None:
        self.values.pop(obj._name, None)
        self.values[name] = obj


class _CollectionObjects:
    def __init__(self, collection: "_Collection") -> None:
        self.collection = collection
        self.values: list[_Object] = []

    def link(self, obj: _Object) -> None:
        if obj not in self.values:
            self.values.append(obj)
        if self.collection not in obj.users_collection:
            obj.users_collection.append(self.collection)

    def unlink(self, obj: _Object) -> None:
        if obj in self.values:
            self.values.remove(obj)
        if self.collection in obj.users_collection:
            obj.users_collection.remove(self.collection)


class _Collection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects = _CollectionObjects(self)
        self.children = _Children()


class _Children(list[_Collection]):
    def link(self, collection: _Collection) -> None:
        if collection not in self:
            self.append(collection)


class _Collections:
    def __init__(self) -> None:
        self.values: dict[str, _Collection] = {}

    def __iter__(self):
        return iter(self.values.values())

    def get(self, name: str) -> _Collection | None:
        return self.values.get(name)

    def new(self, name: str) -> _Collection:
        value = _Collection(name)
        self.values[name] = value
        return value


class _Data:
    def __init__(self) -> None:
        self.objects = _Objects()
        self.collections = _Collections()
        self.curves = _Curves()


class _FakeBpy:
    def __init__(self, saved: dict[str, tuple[_Data, Any]] | None = None) -> None:
        self.data = _Data()
        self.context = SimpleNamespace(
            scene=SimpleNamespace(collection=_Collection("Scene Collection")),
            object=None,
        )
        self.saved = saved if saved is not None else {}
        self.save_calls: list[str] = []
        self.open_calls: list[str] = []
        self.fail_save_call: int | None = None
        self.ops = SimpleNamespace(
            mesh=SimpleNamespace(
                primitive_cylinder_add=self._cylinder,
                primitive_uv_sphere_add=self._sphere,
            ),
            wm=SimpleNamespace(
                save_as_mainfile=self._save,
                open_mainfile=self._open,
            ),
        )

    def _new_mesh(self, name: str, location: tuple[float, float, float]) -> None:
        obj = self.data.objects.new(name, SimpleNamespace())
        obj.location = SimpleNamespace(x=location[0], y=location[1], z=location[2])
        self.context.scene.collection.objects.link(obj)
        self.context.object = obj

    def _cylinder(self, **kwargs: Any) -> None:
        self._new_mesh("Cylinder", kwargs["location"])

    def _sphere(self, **kwargs: Any) -> None:
        self._new_mesh("Sphere", kwargs["location"])

    def _save(self, filepath: str, **kwargs: Any) -> set[str]:
        del kwargs
        self.save_calls.append(filepath)
        if self.fail_save_call == len(self.save_calls):
            raise RuntimeError("injected Blender save failure")
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FAKE-BLEND")
        self.saved[str(target.resolve())] = copy.deepcopy((self.data, self.context))
        return {"FINISHED"}

    def _open(self, filepath: str) -> set[str]:
        target = str(Path(filepath).resolve())
        self.open_calls.append(target)
        if target not in self.saved:
            raise RuntimeError("saved Blender scene missing")
        self.data, self.context = copy.deepcopy(self.saved[target])
        return {"FINISHED"}


def _plan() -> dict[str, Any]:
    return BlenderBuilder().build(solved_payload()).model_dump(mode="json")


def _execute(module: Any, fake: _FakeBpy, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    params = {
        "plan": _plan(),
        "output_path": str(tmp_path / "case.blend"),
        "authorized_root": str(tmp_path),
        "approved": True,
        "bpy_module": fake,
        "snapshot_fn": lambda tag: str(tmp_path / f"{tag}.blend"),
        "fork_version": "1.0.0-m1",
    }
    params.update(kwargs)
    return module.execute_typed_plan(**params)


def test_typed_host_executes_direct_operations_and_projects_semantics(tmp_path) -> None:
    module = _load_typed_module()
    fake = _FakeBpy()
    plan = _plan()
    receipt = _execute(module, fake, tmp_path, plan=plan)
    assert receipt["status"] == "completed"
    assert len(receipt["applied_operations"]) == len(plan["operations"])
    assert len(fake.save_calls) == len(plan["operations"])
    assert all("code" not in item for item in plan["operations"])
    snapshot = SemanticSnapshot.model_validate(receipt["semantic_snapshot"])
    assert snapshot.source_ir_sha256 == BlenderBuilder().build(solved_payload()).compiled_ir_sha256
    assert {item.stable_id for item in snapshot.objects} == {
        "sys-sewage", "mh-001", "mh-001-out", "mh-002", "mh-002-in", "pipe-001"
    }
    segment = next(item for item in snapshot.objects if item.stable_id == "pipe-001")
    assert segment.topology == ("mh-001-out", "mh-002-in")
    assert segment.diameter_mm == pytest.approx(300.0)
    assert segment.centerline[1].z_m == pytest.approx(9.97)
    assert Path(receipt["state_path"]).is_file()


def test_typed_host_repeated_execution_returns_stable_receipt(tmp_path) -> None:
    module = _load_typed_module()
    fake = _FakeBpy()
    first = _execute(module, fake, tmp_path)
    save_count = len(fake.save_calls)
    second = _execute(module, fake, tmp_path)
    assert second == first
    assert len(fake.save_calls) == save_count


def test_typed_host_completed_receipt_requires_controlled_file_and_identity(tmp_path) -> None:
    module = _load_typed_module()
    fake = _FakeBpy()
    receipt = _execute(module, fake, tmp_path)
    output = tmp_path / "case.blend"
    output.unlink()
    with pytest.raises(module.TypedPlanError, match="controlled Blender file is missing"):
        _execute(module, fake, tmp_path)

    output.write_bytes(b"RESTORED")
    sidecar = Path(receipt["state_path"])
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    state["receipt"]["output_path"] = str(tmp_path / "tampered.blend")
    sidecar.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(module.TypedPlanError, match="identity mismatch"):
        _execute(module, fake, tmp_path)


def test_typed_host_partial_failure_resumes_by_opening_controlled_file(tmp_path) -> None:
    module = _load_typed_module()
    shared: dict[str, tuple[_Data, Any]] = {}
    first_host = _FakeBpy(shared)
    first_host.fail_save_call = 3
    partial = _execute(module, first_host, tmp_path)
    assert partial["status"] == "partial"
    state = json.loads(Path(partial["state_path"]).read_text(encoding="utf-8"))
    applied_before = len(state["applied_operation_ids"])
    assert applied_before == 2

    restarted = _FakeBpy(shared)
    completed = _execute(module, restarted, tmp_path)
    assert completed["status"] == "completed"
    assert restarted.open_calls == [str((tmp_path / "case.blend").resolve())]
    assert len(restarted.save_calls) == len(_plan()["operations"]) - applied_before
    assert len(restarted.data.objects.values) == 6


def test_typed_host_rejects_missing_approval_scope_escape_and_existing_target(tmp_path) -> None:
    module = _load_typed_module()
    fake = _FakeBpy()
    with pytest.raises(module.TypedPlanError, match="approval"):
        _execute(module, fake, tmp_path, approved=False)
    with pytest.raises(module.TypedPlanError, match="escaped authorized root"):
        _execute(module, fake, tmp_path, output_path=str(tmp_path.parent / "outside.blend"))
    existing = tmp_path / "case.blend"
    existing.write_bytes(b"DO-NOT-OVERWRITE")
    with pytest.raises(module.TypedPlanError, match="refusing to overwrite"):
        _execute(module, fake, tmp_path)
    assert existing.read_bytes() == b"DO-NOT-OVERWRITE"


def test_typed_host_recovers_save_before_sidecar_ack_crash(tmp_path) -> None:
    module = _load_typed_module()
    shared: dict[str, tuple[_Data, Any]] = {}
    first_host = _FakeBpy(shared)
    real_write_state = module.write_state
    writes = 0

    def crash_after_first_save(path: Path, state: dict[str, Any]) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise SystemExit("injected crash after Blender save before sidecar acknowledgement")
        real_write_state(path, state)

    module.write_state = crash_after_first_save
    with pytest.raises(SystemExit, match="after Blender save"):
        _execute(module, first_host, tmp_path)
    state = json.loads((tmp_path / "case.blend.openbimagent.json").read_text(encoding="utf-8"))
    assert state["applied_operation_ids"] == []
    assert (tmp_path / "case.blend").is_file()

    module.write_state = real_write_state
    restarted = _FakeBpy(shared)
    completed = _execute(module, restarted, tmp_path)
    assert completed["status"] == "completed"
    assert restarted.open_calls == [str((tmp_path / "case.blend").resolve())]
    assert len(restarted.data.objects.values) == 6


def test_typed_host_rejects_hash_tampering_and_broken_topology(tmp_path) -> None:
    module = _load_typed_module()
    fake = _FakeBpy()
    plan = _plan()
    plan["canonical_sha256"] = "0" * 64
    with pytest.raises(module.TypedPlanError, match="canonical_sha256"):
        _execute(module, fake, tmp_path, plan=plan)

    plan = _plan()
    topology = next(item for item in plan["operations"] if item["operation"] == "connect_topology")
    topology["references"][1] = "missing-port"
    plan["canonical_sha256"] = module.canonical_plan_sha256(plan)
    plan["idempotency_key"] = f"blender-plan:{plan['canonical_sha256']}"
    with pytest.raises(module.TypedPlanError, match="unknown objects"):
        _execute(module, fake, tmp_path, plan=plan)


def test_semantic_projection_normalizes_host_float32_noise() -> None:
    module = _load_typed_module()
    assert module._semantic_number(9.970000267028809) == 9.97
    assert module._semantic_number(-0.00000001) == 0.0


def test_typed_host_source_never_routes_plan_through_exec_or_execute_code() -> None:
    source = _TYPED_PATH.read_text(encoding="utf-8")
    assert "exec(" not in source
    assert "execute_code" not in source
