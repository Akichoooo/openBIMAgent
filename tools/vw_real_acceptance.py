"""Vectorworks real-machine acceptance harness for the typed pipe-mesh delivery.

NOT part of the test suite. This script drives the real Vectorworks 2024 GUI
runner (``mcp_servers/vectorworks_mcp/runner.py``) through the same file-IPC and
typed-plan path the product uses, with per-run isolated directories.

Environment contract (set in-process, never globally):
  OPENBIMAGENT_VW_IPC_ROOT        <acceptance>/ipc
  OPENBIMAGENT_VW_JOBS_DIR        <acceptance>/ipc/jobs
  OPENBIMAGENT_VW_RESULTS_DIR     <acceptance>/ipc/results
  OPENBIMAGENT_VW_AUTHORIZED_ROOT <acceptance>/out

The Vectorworks process must inherit the same IPC root before the runner script
is loaded, so ``launch`` starts the executable from this process (env inherited).

Subcommands:
  prepare  create the isolated tree, print the manual VW steps
  launch   start VW (env inherited) and wait for a fresh runner heartbeat
  run      two-pipe export (horizontal + plan-oblique with vertical fall) and
           save receipt / geometry snapshot / semantic snapshot / summary
  reopen   re-open the saved .vwx (via VW exe argument; no vs.py document-open
           API exists) and re-run the same plan to re-measure the reopened file
  status   heartbeat + current summary classification
  stop     write the runner stop file and wait for the controlled stop
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VW_EXE = Path("D:/devloop/VectorWorks 24/Vectorworks2024.exe")
RUNNER_PATH = REPO_ROOT / "mcp_servers" / "vectorworks_mcp" / "runner.py"
HEARTBEAT_MAX_AGE_S = 120.0


def default_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "out" / "vw-acceptance" / stamp


def acceptance_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "ipc": root / "ipc",
        "jobs": root / "ipc" / "jobs",
        "results": root / "ipc" / "results",
        "out": root / "out",
        "heartbeat": root / "ipc" / "runner_heartbeat.json",
        "stop_file": root / "ipc" / "stop_runner",
        "summary": root / "summary.json",
    }


def configure_env(paths: dict[str, Path]) -> dict[str, str]:
    env = {
        "OPENBIMAGENT_VW_IPC_ROOT": str(paths["ipc"]),
        "OPENBIMAGENT_VW_JOBS_DIR": str(paths["jobs"]),
        "OPENBIMAGENT_VW_RESULTS_DIR": str(paths["results"]),
        "OPENBIMAGENT_VW_AUTHORIZED_ROOT": str(paths["out"]),
    }
    os.environ.update(env)
    for key in ("jobs", "results", "out"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return env


def read_heartbeat(paths: dict[str, Path]) -> dict:
    path = paths["heartbeat"]
    if not path.is_file():
        return {"present": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": True, "corrupt": str(exc)}
    age = time.time() - float(payload.get("ts", 0.0))
    payload.update({"present": True, "age_s": round(age, 1), "fresh": age <= HEARTBEAT_MAX_AGE_S})
    return payload


def print_heartbeat(paths: dict[str, Path]) -> dict:
    payload = read_heartbeat(paths)
    if not payload.get("present"):
        print("heartbeat: MISSING (runner not loaded / not polling this ipc root)")
    elif payload.get("corrupt"):
        print(f"heartbeat: CORRUPT {payload['corrupt']}")
    else:
        print(
            f"heartbeat: age={payload['age_s']}s fresh={payload.get('fresh')} "
            f"vw_version={payload.get('vw_version')!r} ipc_root={payload.get('ipc_root')!r} "
            f"stopped_reason={payload.get('stopped_reason')!r}"
        )
    return payload


def manual_steps(paths: dict[str, Path]) -> str:
    return (
        "\nManual step (GUI, operator):\n"
        "  1. In Vectorworks 2024 (started via this harness so it inherits the env):\n"
        "     Tools > Plug-ins > Run Script... (or localized equivalent)\n"
        f"  2. Select: {RUNNER_PATH}\n"
        "  3. The runner prints its ipc_root/stop_file and starts polling; the VW UI\n"
        "     becomes busy while the script thread polls (expected).\n"
        f"  Heartbeat appears at: {paths['heartbeat']}\n"
        "  Stop later with:  python tools/vw_real_acceptance.py stop --root <root>\n"
        "  (never force-kill or close the app for a normal stop)\n"
    )


def two_pipe_solved_payload() -> dict:
    """Solved-payload shaped input for upgrade_legacy_utility_ir.

    pipe-001: horizontal (slope 0). pipe-002: in-plan oblique with vertical
    fall (plan direction (8,6)/10, drop 0.4 m). DN300 concrete, explicit
    inner=300 mm / outer=360 mm per the physical-dimension contract.
    """
    return {
        "protocol_version": "1.0",
        "ir_id": "vw-acceptance-two-pipes",
        "source_ir_sha256": "b" * 64,
        "solver_name": "municipal-route-solver",
        "solver_version": "0.1.0",
        "coordinate_reference": {
            "crs_id": "LOCAL:VW-ACCEPTANCE",
            "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            "horizontal_unit": "m",
            "vertical_unit": "m",
            "vertical_datum": "project datum",
        },
        "systems": [
            {
                "system_id": "sys-sewage",
                "name": "gravity wastewater",
                "system_type": "wastewater",
                "flow_regime": "gravity",
                "ifc_class": "IfcDistributionSystem",
                "ifc_predefined_type": "WASTEWATER",
            }
        ],
        "nodes": [
            {
                "node_id": "mh-001",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 0.0, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-001-out",
                        "direction": "outlet",
                        "position": {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
            {
                "node_id": "mh-002",
                "system_id": "sys-sewage",
                "node_type": "manhole",
                "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-002-in",
                        "direction": "inlet",
                        "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    },
                    {
                        "port_id": "mh-002-out",
                        "direction": "outlet",
                        "position": {"x_m": 10.0, "y_m": 0.0, "z_m": 10.0},
                        "ifc_class": "IfcDistributionPort",
                    },
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
            {
                "node_id": "mh-003",
                "system_id": "sys-sewage",
                "node_type": "outlet",
                "position": {"x_m": 18.0, "y_m": 6.0, "z_m": 11.0},
                "ports": [
                    {
                        "port_id": "mh-003-in",
                        "direction": "inlet",
                        "position": {"x_m": 18.0, "y_m": 6.0, "z_m": 9.6},
                        "ifc_class": "IfcDistributionPort",
                    }
                ],
                "ground_elevation_m": 11.0,
                "ifc_class": "IfcDistributionChamberElement",
                "ifc_predefined_type": "MANHOLE",
            },
        ],
        "segments": [
            {
                "segment_id": "pipe-001",
                "system_id": "sys-sewage",
                "start_port_id": "mh-001-out",
                "end_port_id": "mh-002-in",
                "centerline": [
                    {"x_m": 0.0, "y_m": 0.0, "z_m": 10.0},
                    {"x_m": 10.0, "y_m": 0.0, "z_m": 10.0},
                ],
                "horizontal_length_m": 10.0,
                "start_invert_m": 10.0,
                "end_invert_m": 10.0,
                "slope": 0.0,
                "diameter_mm": 300.0,
                "material": "concrete",
                "min_cover_depth_m": 0.7,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            },
            {
                "segment_id": "pipe-002",
                "system_id": "sys-sewage",
                "start_port_id": "mh-002-out",
                "end_port_id": "mh-003-in",
                "centerline": [
                    {"x_m": 10.0, "y_m": 0.0, "z_m": 10.0},
                    {"x_m": 18.0, "y_m": 6.0, "z_m": 9.6},
                ],
                "horizontal_length_m": 10.0,
                "start_invert_m": 10.0,
                "end_invert_m": 9.6,
                "slope": 0.04,
                "diameter_mm": 300.0,
                "material": "concrete",
                "min_cover_depth_m": 0.7,
                "ifc_class": "IfcPipeSegment",
                "ifc_predefined_type": "RIGIDSEGMENT",
            },
        ],
    }


def build_ir() -> dict:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from openbimagent.utility.geometry import upgrade_legacy_utility_ir

    compiled = upgrade_legacy_utility_ir(
        two_pipe_solved_payload(),
        dimensions_by_segment={
            "pipe-001": {"inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0},
            "pipe-002": {"inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0},
        },
    )
    return compiled.model_dump(mode="json")


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def classify(result: dict) -> dict:
    snapshot = result.get("geometry_snapshot") or {}
    objects = snapshot.get("objects") or []
    per_object = []
    for item in objects:
        per_object.append(
            {
                "stable_id": item.get("stable_id"),
                "object_type": item.get("object_type"),
                "status": item.get("status"),
                "dimension_status": item.get("dimension_status"),
                "topology_status": item.get("topology_status"),
                "vertex_index_base": (item.get("measured") or {}).get("vertex_index_base"),
                "measured_inner_mm": (item.get("measured") or {}).get("inner_diameter_mm"),
                "measured_outer_mm": (item.get("measured") or {}).get("outer_diameter_mm"),
                "measured_length_m": (item.get("measured") or {}).get("length_m"),
                "vertex_sha256": (item.get("measured") or {}).get("vertex_sha256"),
                "failures": item.get("failures"),
            }
        )
    return {
        "status": result.get("status"),
        "host_status": result.get("host_status"),
        "geometry_status": result.get("geometry_status") or snapshot.get("status"),
        "snapshot_topology_status": snapshot.get("topology_status"),
        "geometry_verified": result.get("geometry_verified"),
        "geometry_caveats": result.get("geometry_caveats"),
        "readback_tolerance_m": snapshot.get("readback_tolerance_m"),
        "readback_tolerance_basis": snapshot.get("readback_tolerance_basis"),
        "objects": per_object,
        "output_path": result.get("output_path"),
    }


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_PATTERN.fullmatch(value))


def expected_plan_facts(ir: dict) -> dict[str, object]:
    """在发送前由同一 IR 推导期望构件身份（与执行器共用的 builder 路径）。"""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from openbimagent.assembly.vectorworks_plan import VectorworksBuilder
    from openbimagent.utility.contracts import CompiledUtilityIR

    compiled = CompiledUtilityIR.model_validate(ir)
    plan = VectorworksBuilder().build(compiled)
    semantic_ids = sorted(
        {
            *(system.system_id for system in compiled.systems),
            *(node.node_id for node in compiled.nodes),
            *(port.port_id for node in compiled.nodes for port in node.ports),
            *(segment.segment_id for segment in compiled.segments),
        }
    )
    return {
        "operation_count": len(plan.operations),
        "plan_sha256": plan.canonical_sha256,
        "pipe_ids": sorted(segment.segment_id for segment in compiled.segments),
        "semantic_ids": semantic_ids,
    }


def build_run_verdict(result: dict, expected: dict[str, object]) -> dict[str, object]:
    """分项判定一次构建是否为“验收通过”。

    partial 本身不是成功：执行完成、op 数、尺寸、语义、构件集合与顶点哈希
    必须逐项成立；闭合性（topology）只会记录为分级 unverified。
    """
    expected_pipes = sorted(str(item) for item in expected["pipe_ids"])
    expected_semantic = sorted(str(item) for item in expected["semantic_ids"])
    expected_operations = int(expected["operation_count"])
    expected_plan_sha = str(expected["plan_sha256"])

    snapshot = result.get("geometry_snapshot") or {}
    semantic_snapshot = result.get("semantic_snapshot") or {}
    comparison = result.get("semantic_comparison_report") or {}
    errors = list(result.get("errors") or [])
    geometry_failures = list(result.get("geometry_failures") or [])
    geometry_objects = list(snapshot.get("objects") or [])
    semantic_objects = list(semantic_snapshot.get("objects") or [])
    blockers: list[str] = []

    execution_status = result.get("status")
    execution_ok = execution_status in {"completed", "partial"} and not errors
    if execution_status not in {"completed", "partial"}:
        blockers.append(f"execution_status={execution_status!r} is not completed/partial")
    if errors:
        blockers.append(f"execution_errors present: {errors[:3]}")
    applied_operations = result.get("applied_operations")
    if applied_operations != expected_operations:
        blockers.append(f"applied_operations={applied_operations} != expected={expected_operations}")
    if result.get("plan_sha256") != expected_plan_sha:
        blockers.append("plan_sha256 does not match the sent IR/plan identity")

    geometry_ids = sorted(str(item.get("stable_id")) for item in geometry_objects)
    objects_complete = geometry_ids == expected_pipes
    no_duplicates = len(geometry_ids) == len(set(geometry_ids))
    if not objects_complete:
        blockers.append(f"geometry_objects={geometry_ids} != expected={expected_pipes}")
    if not no_duplicates:
        blockers.append("geometry_objects contain duplicate stable ids")
    semantic_ids = sorted(str(item.get("stable_id")) for item in semantic_objects)
    semantic_complete = semantic_ids == expected_semantic
    if not semantic_complete:
        blockers.append(f"semantic_objects={semantic_ids} != expected={expected_semantic}")

    dimension_ok = bool(result.get("dimension_verified")) and not geometry_failures
    if not dimension_ok:
        blockers.append("dimension verification did not pass")
    for item in geometry_objects:
        if item.get("dimension_status") != "verified":
            blockers.append(
                f"dimension_status={item.get('dimension_status')!r} for {item.get('stable_id')}"
            )
            break

    comparison_status = str(comparison.get("status") or "")
    semantic_ok = (
        bool(result.get("semantic_verified"))
        and comparison_status == "PASS"
        and comparison.get("compared_object_count") == len(expected_semantic)
    )
    if not semantic_ok:
        blockers.append(f"semantic verification did not pass (comparison={comparison_status!r})")

    vertex_hashes: dict[str, str] = {}
    for item in geometry_objects:
        stable_id = str(item.get("stable_id"))
        digest = (item.get("measured") or {}).get("vertex_sha256")
        if not _valid_sha256(digest):
            blockers.append(f"vertex_sha256 missing/invalid for {stable_id}")
        else:
            vertex_hashes[stable_id] = str(digest)
    hashes_complete = bool(vertex_hashes) and sorted(vertex_hashes) == expected_pipes
    if not hashes_complete:
        blockers.append("not every expected pipe segment has a valid vertex hash")

    output_path = Path(str(result.get("output_path") or ""))
    output_ok = (
        bool(str(result.get("output_path") or ""))
        and output_path.is_file()
        and _valid_sha256(result.get("output_sha256"))
        and int(result.get("output_bytes") or 0) > 0
    )
    if not output_ok:
        blockers.append("controlled output document is missing or unhashed")

    topology_status = str(
        result.get("topology_status") or snapshot.get("topology_status") or "unverified"
    )
    return {
        "passed": not blockers,
        "blocking_failures": blockers,
        "execution_status": execution_status,
        "execution_ok": execution_ok,
        "applied_operations": applied_operations,
        "expected_operations": expected_operations,
        "dimension_verified": dimension_ok,
        "semantic_verified": semantic_ok,
        "objects_complete": objects_complete,
        "no_duplicate_objects": no_duplicates,
        "vertex_hashes_complete": hashes_complete,
        "vertex_hashes": vertex_hashes,
        "topology_status": topology_status,
        "topology_graded_unverified": topology_status == "unverified",
        "output_verified": output_ok,
    }


def build_reopen_verdict(
    *,
    run_receipt: dict,
    run_classification: dict,
    reopen_result: dict,
    method: str,
    operator_confirmed: bool,
) -> dict[str, object]:
    """分项判定重开读回；空集合相等、缺对象/缺哈希/哈希不等均不得通过。"""
    blockers: list[str] = []
    first_objects = [
        item for item in (run_classification.get("objects") or [])
        if item.get("object_type") == "pipe_segment"
    ]
    expected_pipes = sorted(str(item.get("stable_id")) for item in first_objects)
    first_hashes: dict[str, str] = {}
    if not expected_pipes:
        blockers.append("run baseline has no expected pipe segments; empty-set equality is not a pass")
    for item in first_objects:
        stable_id = str(item.get("stable_id"))
        digest = item.get("vertex_sha256")
        if not _valid_sha256(digest):
            blockers.append(f"run baseline vertex_sha256 missing/invalid for {stable_id}")
        else:
            first_hashes[stable_id] = str(digest)

    execution_status = reopen_result.get("status")
    errors = list(reopen_result.get("errors") or [])
    if execution_status not in {"completed", "partial"}:
        blockers.append(f"reopen execution_status={execution_status!r}")
    if errors:
        blockers.append(f"reopen execution_errors present: {errors[:3]}")
    same_plan = (
        reopen_result.get("plan_id") == run_receipt.get("plan_id")
        and reopen_result.get("plan_sha256") == run_receipt.get("plan_sha256")
        and reopen_result.get("source_ir_sha256") == run_receipt.get("source_ir_sha256")
    )
    if not same_plan:
        blockers.append("reopen did not run the same IR/plan identity as the build")
    if reopen_result.get("applied_operations") != run_receipt.get("applied_operations"):
        blockers.append("reopen applied_operations differ from the build (plan not fully resumed)")

    reopen_snapshot = reopen_result.get("geometry_snapshot") or {}
    reopen_objects = list(reopen_snapshot.get("objects") or [])
    reopen_ids = sorted(str(item.get("stable_id")) for item in reopen_objects)
    objects_complete = reopen_ids == expected_pipes
    no_duplicates = len(reopen_ids) == len(set(reopen_ids))
    if not objects_complete:
        blockers.append(f"reopen geometry_objects={reopen_ids} != expected={expected_pipes}")
    if not no_duplicates:
        blockers.append("reopen geometry_objects contain duplicate stable ids")

    dimension_ok = bool(reopen_result.get("dimension_verified")) and not list(
        reopen_result.get("geometry_failures") or []
    )
    if not dimension_ok:
        blockers.append("reopen dimension verification did not pass")
    for item in reopen_objects:
        if item.get("dimension_status") != "verified":
            blockers.append(
                f"reopen dimension_status={item.get('dimension_status')!r} for {item.get('stable_id')}"
            )
            break

    run_semantic_ids = {
        str(item.get("stable_id"))
        for item in (run_receipt.get("semantic_snapshot") or {}).get("objects") or []
    }
    comparison = reopen_result.get("semantic_comparison_report") or {}
    comparison_status = str(comparison.get("status") or "")
    semantic_ok = (
        bool(reopen_result.get("semantic_verified"))
        and comparison_status == "PASS"
        and bool(run_semantic_ids)
        and comparison.get("compared_object_count") == len(run_semantic_ids)
    )
    if not semantic_ok:
        blockers.append(f"reopen semantic verification did not pass (comparison={comparison_status!r})")

    second_hashes: dict[str, str] = {}
    for item in reopen_objects:
        stable_id = str(item.get("stable_id"))
        digest = (item.get("measured") or {}).get("vertex_sha256")
        if not _valid_sha256(digest):
            blockers.append(f"reopen vertex_sha256 missing/invalid for {stable_id}")
        else:
            second_hashes[stable_id] = str(digest)
    hashes_complete = bool(second_hashes) and sorted(second_hashes) == expected_pipes
    if not hashes_complete:
        blockers.append("reopen readback does not cover every expected pipe with a valid hash")
    hash_mismatches = sorted(
        stable_id
        for stable_id in set(first_hashes) | set(second_hashes)
        if first_hashes.get(stable_id) != second_hashes.get(stable_id) or not first_hashes.get(stable_id)
    )
    hashes_match = hashes_complete and not hash_mismatches
    if hash_mismatches:
        blockers.append(f"vertex_sha256 mismatch for {hash_mismatches}")

    if method == "start-process-arg":
        disk_reopen_claimed = True
        confirmation_source = "automation: spawned host process with the document argument"
    elif method == "in-place-file-reopen":
        disk_reopen_claimed = bool(operator_confirmed)
        confirmation_source = (
            "operator: manual File > Close / File > Open, attested via --operator-confirmed"
            if operator_confirmed
            else "unconfirmed: manual close/reopen was not attested; runner liveness alone is not proof"
        )
    else:
        disk_reopen_claimed = False
        confirmation_source = (
            "none: --attach proves only active-document readback, not that the document was reopened from disk"
        )

    readback_verified = not blockers
    return {
        "passed": readback_verified,
        "readback_verified": readback_verified,
        "blocking_failures": blockers,
        "reopen_method": method,
        "disk_reopen_claimed": disk_reopen_claimed,
        "confirmation_source": confirmation_source,
        "same_plan": same_plan,
        "objects_complete": objects_complete,
        "no_duplicate_objects": no_duplicates,
        "dimension_verified": dimension_ok,
        "semantic_verified": semantic_ok,
        "vertex_hashes_complete": hashes_complete,
        "vertex_hashes_match": hashes_match,
        "vertex_hashes_run": first_hashes,
        "vertex_hashes_reopen": second_hashes,
        "topology_status": str(
            reopen_result.get("topology_status")
            or reopen_snapshot.get("topology_status")
            or "unverified"
        ),
    }


def _archive_artifacts(root: Path, tag: str) -> Path | None:
    """把当前产物复制到 history/ 下，避免新的运行覆盖既有真机证据。"""
    names = (
        "receipt.json",
        "geometry_snapshot.json",
        "semantic_snapshot.json",
        "receipt_reopen.json",
        "geometry_snapshot_reopen.json",
        "summary.json",
        "compiled_ir.json",
    )
    existing = [root / name for name in names if (root / name).is_file()]
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / "history" / f"{stamp}-{tag}"
    destination.mkdir(parents=True, exist_ok=True)
    for source in existing:
        shutil.copy2(source, destination / source.name)
    return destination


def print_run_verdict(verdict: dict) -> None:
    print(
        "run verdict: passed={passed} execution={execution_status} ops={applied_operations}/{expected_operations} "
        "dimension={dimension_verified} semantic={semantic_verified} topology={topology_status} output={output_verified}".format(
            **verdict
        )
    )
    for blocker in verdict["blocking_failures"]:
        print(f"  blocker: {blocker}")


def print_reopen_verdict(verdict: dict) -> None:
    print(
        "reopen verdict: readback_verified={readback_verified} same_plan={same_plan} "
        "objects_complete={objects_complete} no_duplicates={no_duplicate_objects} "
        "dimension={dimension_verified} semantic={semantic_verified} "
        "hashes_complete={vertex_hashes_complete} hashes_match={vertex_hashes_match} "
        "topology={topology_status}".format(**verdict)
    )
    print(
        f"disk reopen: claimed={verdict['disk_reopen_claimed']} "
        f"source={verdict['confirmation_source']}"
    )
    for blocker in verdict["blocking_failures"]:
        print(f"  blocker: {blocker}")


def cmd_prepare(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else default_root()
    paths = acceptance_paths(root)
    env = configure_env(paths)
    print(f"acceptance root: {root}")
    for key, value in env.items():
        print(f"  {key}={value}")
    print(manual_steps(paths))
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else default_root()
    paths = acceptance_paths(root)
    env = configure_env(paths)
    if not VW_EXE.is_file():
        print(f"ERROR: Vectorworks executable not found: {VW_EXE}")
        return 2
    print(f"acceptance root: {root}")
    for key, value in env.items():
        print(f"  {key}={value}")
    print(f"starting: {VW_EXE}")
    subprocess.Popen([str(VW_EXE)], env=os.environ.copy())
    deadline = time.time() + args.wait
    while time.time() < deadline:
        payload = read_heartbeat(paths)
        if payload.get("fresh"):
            print("runner heartbeat is fresh; runner is polling this ipc root")
            print_heartbeat(paths)
            return 0
        time.sleep(2.0)
    print_heartbeat(paths)
    print(manual_steps(paths))
    print(f"runner heartbeat not fresh after {args.wait:.0f}s; complete the manual step, then run: run/status")
    return 3


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    paths = acceptance_paths(root)
    configure_env(paths)
    heartbeat = print_heartbeat(paths)
    if not heartbeat.get("fresh"):
        print("ERROR: runner heartbeat missing/stale; load runner.py in VW first (see launch/prepare output)")
        return 3
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from openbimagent.assembly.vectorworks_host_executor import execute_vectorworks_export

    ir = build_ir()
    expected = expected_plan_facts(ir)
    target = paths["out"] / "two-pipes.vwx"
    archived = _archive_artifacts(root, "run")
    if archived is not None:
        print(f"previous artifacts archived: {archived}")
    write_json(root / "compiled_ir.json", ir)
    print(f"sending typed plan; output={target}")
    started = time.time()
    result = execute_vectorworks_export(ir, output_path=target)
    elapsed = time.time() - started
    verdict = build_run_verdict(result, expected)
    write_json(root / "receipt.json", result)
    if result.get("geometry_snapshot"):
        write_json(root / "geometry_snapshot.json", result["geometry_snapshot"])
    if result.get("semantic_snapshot"):
        write_json(root / "semantic_snapshot.json", result["semantic_snapshot"])
    summary = {
        "acceptance_root": str(root),
        "kind": "run",
        "elapsed_s": round(elapsed, 1),
        "heartbeat": heartbeat,
        "target": str(target),
        "target_size_bytes": target.stat().st_size if target.is_file() else None,
        "classification": classify(result),
        "result_errors": result.get("errors"),
        "plan_id": result.get("plan_id"),
        "plan_sha256": result.get("plan_sha256"),
        "run_verdict": verdict,
        "run_error": None if verdict["passed"] else "; ".join(verdict["blocking_failures"]),
        "archived_previous": str(archived) if archived is not None else None,
    }
    write_json(paths["summary"], summary)
    print(f"status={result.get('status')} host_status={result.get('host_status')} elapsed={elapsed:.1f}s")
    classification = summary["classification"]
    for item in classification["objects"]:
        print(
            f"  {item['stable_id']}: status={item['status']} dimension={item['dimension_status']} "
            f"topology={item['topology_status']} index_base={item['vertex_index_base']} "
            f"inner={item['measured_inner_mm']} outer={item['measured_outer_mm']} length={item['measured_length_m']}"
        )
    for caveat in classification["geometry_caveats"] or []:
        print(f"  caveat: {caveat}")
    if result.get("errors"):
        print("errors:")
        for error in result["errors"]:
            print(f"  {error}")
    print_run_verdict(verdict)
    return 0 if verdict["passed"] else 1


def cmd_reopen(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    paths = acceptance_paths(root)
    configure_env(paths)
    summary = json.loads(paths["summary"].read_text(encoding="utf-8")) if paths["summary"].is_file() else {}
    run_receipt_path = root / "receipt.json"
    if not run_receipt_path.is_file():
        print(f"ERROR: no build receipt to compare against: {run_receipt_path}")
        return 2
    run_receipt = json.loads(run_receipt_path.read_text(encoding="utf-8"))
    target = Path(summary.get("target") or paths["out"] / "two-pipes.vwx")
    if not target.is_file():
        print(f"ERROR: no saved document to reopen: {target}")
        return 2
    operator_confirmed = bool(getattr(args, "operator_confirmed", False))
    previous = read_heartbeat(paths) or {}
    previous_started_at = previous.get("started_at")
    if args.in_place:
        method = "in-place-file-reopen"
        print(
            "in-place reopen mode: waiting for a live runner session in the already-open "
            "Vectorworks window; the target document must have been closed and re-opened "
            "from disk inside that same window"
        )
        if not operator_confirmed:
            print(
                "NOTE: --operator-confirmed was not supplied; the manual close/reopen will not be "
                "claimed as a confirmed disk reopen (readback can still be verified)"
            )
    elif args.attach:
        method = "attach-live-session"
        print("attach mode: waiting for a live runner session in the already-open Vectorworks window")
        print("NOTE: attach proves only active-document readback, never a disk reopen")
    else:
        method = "start-process-arg"
        print(f"reopening via executable argument (no vs.py document-open API exists): {target}")
        subprocess.Popen([str(VW_EXE), str(target)], env=os.environ.copy())
    reattach = args.attach or args.in_place
    deadline = time.time() + args.settle
    fresh = None
    while time.time() < deadline:
        payload = read_heartbeat(paths) or {}
        started_at = payload.get("started_at")
        ts = payload.get("ts")
        age = (time.time() - float(ts)) if isinstance(ts, (int, float)) else None
        live = (
            bool(started_at)
            and not payload.get("stopped_reason")
            and age is not None
            and age < 120.0
        )
        if live and (reattach or started_at != previous_started_at):
            fresh = payload
            break
        time.sleep(2.0)
    if fresh is None:
        if args.in_place:
            expectation = "a live runner session after the in-place document reopen"
        elif args.attach:
            expectation = "a live runner session"
        else:
            expectation = "a fresh runner session after the reopen request"
        message = (
            f"no {expectation} detected within {args.settle:.0f}s; "
            "load runner.py inside the Vectorworks window (Tools > Plug-ins > Run Script)"
        )
        archived = _archive_artifacts(root, "reopen")
        write_json(root / "receipt_reopen.json", {"error": message})
        summary.update(
            {
                "kind": "reopen",
                "reopen_method": method,
                "readback_verified": False,
                "reopen_verified": False,
                "disk_reopen_confirmed": False,
                "reopen_confirmation_source": "none: no runner session detected",
                "reopen_verdict": None,
                "reopen_error": message,
                "archived_previous": str(archived) if archived is not None else None,
            }
        )
        write_json(paths["summary"], summary)
        print(f"reopen readback FAILED: {message}")
        return 1
    print_heartbeat(paths)
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from openbimagent.assembly.vectorworks_host_executor import execute_vectorworks_export

    ir = json.loads((root / "compiled_ir.json").read_text(encoding="utf-8"))
    try:
        result = execute_vectorworks_export(ir, output_path=target)
    except Exception as exc:  # record the honest failure; reopen is unverified
        archived = _archive_artifacts(root, "reopen")
        write_json(root / "receipt_reopen.json", {"error": str(exc)})
        summary.update(
            {
                "kind": "reopen",
                "reopen_method": method,
                "readback_verified": False,
                "reopen_verified": False,
                "disk_reopen_confirmed": False,
                "reopen_confirmation_source": "none: reopen readback did not complete",
                "reopen_verdict": None,
                "reopen_error": str(exc),
                "archived_previous": str(archived) if archived is not None else None,
            }
        )
        write_json(paths["summary"], summary)
        print(f"reopen readback FAILED: {exc}")
        print("recorded as readback_verified=false; the saved file was not re-opened/measured")
        return 1
    verdict = build_reopen_verdict(
        run_receipt=run_receipt,
        run_classification=summary.get("classification") or {},
        reopen_result=result,
        method=method,
        operator_confirmed=operator_confirmed,
    )
    reopen_verified = bool(verdict["readback_verified"]) and bool(verdict["disk_reopen_claimed"])
    archived = _archive_artifacts(root, "reopen")
    write_json(root / "receipt_reopen.json", result)
    if result.get("geometry_snapshot"):
        write_json(root / "geometry_snapshot_reopen.json", result["geometry_snapshot"])
    classification = classify(result)
    summary.update(
        {
            "kind": "reopen",
            "reopen_method": method,
            "readback_verified": verdict["readback_verified"],
            "reopen_verified": reopen_verified,
            "disk_reopen_confirmed": verdict["disk_reopen_claimed"],
            "reopen_confirmation_source": verdict["confirmation_source"],
            "reopen_classification": classification,
            "vertex_sha256_stable": verdict["vertex_hashes_match"],
            "vertex_sha256_run": verdict["vertex_hashes_run"],
            "vertex_sha256_reopen": verdict["vertex_hashes_reopen"],
            "reopen_verdict": verdict,
            "reopen_error": None if verdict["readback_verified"] else "; ".join(verdict["blocking_failures"]),
            "archived_previous": str(archived) if archived is not None else None,
        }
    )
    write_json(paths["summary"], summary)
    print(
        f"reopen status={result.get('status')} readback_verified={verdict['readback_verified']} "
        f"reopen_verified={reopen_verified}"
    )
    for item in classification["objects"]:
        print(
            f"  {item['stable_id']}: status={item['status']} dimension={item['dimension_status']} "
            f"topology={item['topology_status']} length={item['measured_length_m']}"
        )
    print_reopen_verdict(verdict)
    if verdict["readback_verified"] and not verdict["disk_reopen_claimed"]:
        print(
            "NOTE: readback was verified, but a disk reopen is not claimed; "
            "use the default spawn mode or --in-place --operator-confirmed for the reopen claim"
        )
    return 0 if reopen_verified else 1


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    paths = acceptance_paths(root)
    print(f"acceptance root: {root}")
    print_heartbeat(paths)
    if paths["summary"].is_file():
        summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
        classification = summary.get("classification") or {}
        print(f"summary kind={summary.get('kind')} status={classification.get('status')} "
              f"geometry={classification.get('geometry_status')} target={summary.get('target')}")
    else:
        print("no summary yet")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    paths = acceptance_paths(root)
    configure_env(paths)
    paths["stop_file"].write_text("stop", encoding="utf-8")
    print(f"stop file written: {paths['stop_file']}")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        payload = read_heartbeat(paths)
        if payload.get("stopped_reason"):
            print(f"runner stopped: {payload['stopped_reason']}")
            return 0
        time.sleep(1.0)
    print_heartbeat(paths)
    print("runner has not recorded a stop yet (a host call may still be running; do not force-kill)")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("prepare", cmd_prepare),
        ("launch", cmd_launch),
        ("run", cmd_run),
        ("reopen", cmd_reopen),
        ("status", cmd_status),
        ("stop", cmd_stop),
    ):
        item = sub.add_parser(name)
        item.add_argument("--root", default="", help="acceptance root (default: out/vw-acceptance/<utc-stamp>)")
        if name == "launch":
            item.add_argument("--wait", type=float, default=180.0, help="seconds to wait for a fresh heartbeat")
        if name == "reopen":
            item.add_argument("--settle", type=float, default=300.0, help="max seconds to wait for a fresh runner session after requesting reopen")
            item.add_argument("--attach", action="store_true", help="skip spawning Vectorworks; wait for a fresh runner session in an already-open window")
            item.add_argument("--in-place", action="store_true", help="document was closed and re-opened from disk in the already-open window; verify through the live session")
            item.add_argument("--operator-confirmed", action="store_true", help="attest that a human performed File > Close / File > Open for --in-place")
        if name == "stop":
            item.add_argument("--wait", type=float, default=30.0, help="seconds to wait for the stop ack")
        item.set_defaults(func=handler)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
