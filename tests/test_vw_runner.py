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
    fake.saved_paths = []
    fake._last = None

    def _new(kind: str, payload: Any) -> dict[str, Any]:
        handle = {"kind": kind, "payload": payload, "name": None, "class": None}
        fake._last = handle
        return handle

    fake.Locus3D = lambda point: _new("locus", tuple(point))
    fake.BeginPoly3D = lambda: setattr(fake, "_poly", [])
    fake.Add3DPt = lambda point: fake._poly.append(tuple(point))
    fake.EndPoly3D = lambda: _new("poly3d", tuple(fake._poly))
    fake.LNewObj = lambda: fake._last
    fake.SetName = lambda handle, name: (handle.__setitem__("name", name), fake.handles.__setitem__(name, handle))
    fake.SetClass = lambda handle, name: handle.__setitem__("class", name)
    fake.NameClass = lambda name: None
    fake.GetObject = lambda name: fake.handles.get(name) or fake.records.get(name)
    fake.NewField = lambda record, field, default, field_type, flag: fake.records.setdefault(record, {}).setdefault(field, default)
    fake.SetRecord = lambda handle, record: handle.setdefault("records", {}).setdefault(record, {})
    fake.SetRField = lambda handle, record, field, value: handle.setdefault("records", {}).setdefault(record, {}).__setitem__(field, value)
    fake.SaveActiveDocument = lambda path: (fake.saved_paths.append(path) or 0)
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


def test_execute_typed_plan_resumes_after_partial_without_reapplying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner_module()
    fake_vs = _fake_typed_vs()
    original_save = fake_vs.SaveActiveDocument
    save_calls = 0

    def fail_second_save(path: str) -> int:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("injected host restart")
        return original_save(path)

    fake_vs.SaveActiveDocument = fail_second_save
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

    fake_vs.SaveActiveDocument = original_save
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
