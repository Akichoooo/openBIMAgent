import importlib.util
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# JY: This module is imported directly by the Vectorworks .vlb. Keep the import
# path extremely light; do not import heavy Design Agent dependencies here.
OPENBIMFORGE_RUN_ONCE_COMMAND = "__OPENBIMFORGE_RUN_ONCE__"
OPENBIMFORGE_ROOT = Path(__file__).resolve().parents[3]
OPENBIMFORGE_CORE_ROOT = OPENBIMFORGE_ROOT / "forge_core"
OPENBIMFORGE_RUNTIME_ROOT = Path(
    os.environ.get("OPENBIMFORGE_RUNTIME_ROOT", str(OPENBIMFORGE_ROOT / "forge_runtime"))
)
RUNTIME_HANDOFFS_ROOT = OPENBIMFORGE_RUNTIME_ROOT / "handoffs"
REAL_VS_INTERFACE = OPENBIMFORGE_ROOT / "forge_core" / "design_agent" / "vs_interface.py"


def _inject_paths():
    os.environ.setdefault("OPENBIMFORGE_ROOT", str(OPENBIMFORGE_ROOT))
    os.environ.setdefault("OPENBIMFORGE_RUNTIME_ROOT", str(OPENBIMFORGE_RUNTIME_ROOT))
    os.environ.setdefault("OPENBIMFORGE_OUTPUT_ROOT", str(RUNTIME_HANDOFFS_ROOT))
    os.environ.setdefault("TEXT2BIM_OUTPUT_ROOT", os.environ["OPENBIMFORGE_OUTPUT_ROOT"])
    for path in (OPENBIMFORGE_ROOT, OPENBIMFORGE_CORE_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def _write_probe(filename, data):
    try:
        RUNTIME_HANDOFFS_ROOT.mkdir(parents=True, exist_ok=True)
        (RUNTIME_HANDOFFS_ROOT / filename).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


_inject_paths()
_write_probe(
    "openbimforge_vs_interface_import_probe.json",
    {
        "timestamp": datetime.now().isoformat(),
        "proxy_file": __file__ if "__file__" in globals() else "NOT_DEFINED",
        "real_file": str(REAL_VS_INTERFACE),
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "version": sys.version,
        "path": sys.path[:20],
        "openbimforge_root_exists": OPENBIMFORGE_ROOT.exists(),
        "design_agent_exists": (OPENBIMFORGE_CORE_ROOT / "design_agent").exists(),
        "build_agent_exists": (OPENBIMFORGE_CORE_ROOT / "build_agent").exists(),
        "proxy_import": "success",
    },
)


def _load_real_vs_interface():
    _inject_paths()
    spec = importlib.util.spec_from_file_location("_openbimforge_real_vs_interface", REAL_VS_INTERFACE)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load real vs_interface from {REAL_VS_INTERFACE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_payload(payload_text):
    if not payload_text:
        return {}
    try:
        return json.loads(payload_text)
    except Exception:
        return {}


def _write_legacy_status(stage, extra=None):
    if stage.startswith("legacy_"):
        try:
            existing_path = RUNTIME_HANDOFFS_ROOT / "openbimforge_legacy_bridge_status.json"
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if existing.get("stage") in {"running_exact", "completed", "failed"}:
                existing["postStage"] = stage
                existing["postStageUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
                if extra:
                    existing["postStageExtra"] = extra
                _write_probe("openbimforge_legacy_bridge_status.json", existing)
                return existing
        except Exception:
            pass

    status = {
        "ok": stage not in ("failed", "exception"),
        "stage": stage,
        "bridge": "legacy_getAllPlantDataV2_proxy",
        "command": OPENBIMFORGE_RUN_ONCE_COMMAND,
        "handoffRoot": str(RUNTIME_HANDOFFS_ROOT),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        status.update(extra)
    _write_probe("openbimforge_legacy_bridge_status.json", status)
    return status


def _write_marker(marker_path, content):
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = marker_path.with_name(f"{marker_path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(marker_path)


def _run_openbimforge_once(payload_text=""):
    payload = _parse_payload(payload_text)
    handoff_root = Path(payload.get("handoffRoot") or RUNTIME_HANDOFFS_ROOT)
    payload_path = str(payload.get("payloadPath") or payload.get("handoffPath") or "").strip()
    handoff_root.mkdir(parents=True, exist_ok=True)
    _write_legacy_status("received", {"payloadKeys": sorted(payload.keys())})

    _inject_paths()
    from forge_core.build_agent.vectorworks_execute import run_handoff

    _write_legacy_status("runner_imported", {"payloadPath": payload_path})
    if payload_path:
        target_path = Path(payload_path)
        _write_legacy_status("running_exact", {"payloadPath": str(target_path)})
        running_path = Path(f"{target_path}.running")
        done_path = Path(f"{target_path}.done")
        failed_path = Path(f"{target_path}.failed")
        running_path.write_text(datetime.now().isoformat(), encoding="utf-8")
        try:
            result = run_handoff(str(target_path))
            results = [result]
            marker_path = done_path if result.get("ok") else failed_path
            _write_marker(marker_path, json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            try:
                running_path.unlink(missing_ok=True)
            except Exception:
                pass
    else:
        results = []
        _write_legacy_status(
            "idle_no_payload",
            {
                "reason": "Palette initialization does not execute directory scans. Exact payloadPath dispatch is required.",
                "payloadKeys": sorted(payload.keys()),
            },
        )
    summary = {
        "ok": True,
        "bridge": "legacy_getAllPlantDataV2_proxy",
        "command": OPENBIMFORGE_RUN_ONCE_COMMAND,
        "handoffRoot": str(handoff_root),
        "payloadPath": payload_path,
        "executedCount": len(results),
        "results": results,
    }
    _write_legacy_status("completed", {"executedCount": len(results), "results": results})
    return json.dumps(summary, ensure_ascii=False, indent=2), ""


def excute_webpalette_po_coder(input_str, chat_history):
    call_probe = {
        "timestamp": datetime.now().isoformat(),
        "input_str": repr(input_str),
        "is_trigger": str(input_str).strip() == OPENBIMFORGE_RUN_ONCE_COMMAND,
        "chat_history_len": len(chat_history) if chat_history else 0,
        "chat_history_preview": str(chat_history)[:500],
        "path": sys.path[:20],
        "proxy_file": __file__ if "__file__" in globals() else "NOT_DEFINED",
    }
    try:
        _inject_paths()
        from tool_agent_bridge.vectorworks_watch_runner import run_pending_handoffs  # noqa: F401
        call_probe["import_watch_runner"] = "success"
    except Exception:
        call_probe["import_watch_runner"] = "failed"
        call_probe["traceback"] = traceback.format_exc()
    _write_probe("openbimforge_vs_interface_call_probe.json", call_probe)

    if str(input_str).strip() == OPENBIMFORGE_RUN_ONCE_COMMAND:
        try:
            return _run_openbimforge_once(str(chat_history))
        except Exception as exc:
            _write_legacy_status(
                "failed",
                {"error": str(exc), "traceback": traceback.format_exc()},
            )
            return (
                json.dumps(
                    {
                        "ok": False,
                        "bridge": "legacy_getAllPlantDataV2_proxy",
                        "command": OPENBIMFORGE_RUN_ONCE_COMMAND,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
            )

    return _load_real_vs_interface().excute_webpalette_po_coder(input_str, chat_history)


def excute_webpalette_export(*args, **kwargs):
    _write_legacy_status("legacy_export_bypassed", {"argsCount": len(args)})
    return "break"


def excute_webpalette_checking_loop(*args, **kwargs):
    _write_legacy_status("legacy_checking_bypassed", {"argsCount": len(args)})
    return "break", ""


def excute_final_ifc_export(*args, **kwargs):
    _write_legacy_status("legacy_final_export_bypassed", {"argsCount": len(args)})
    return "break"


def excute_pure_checking(*args, **kwargs):
    _write_legacy_status("legacy_pure_checking_bypassed", {"argsCount": len(args)})
    return None


def excute_state_clean(*args, **kwargs):
    _write_legacy_status("legacy_state_clean_bypassed", {"argsCount": len(args)})
    return None
