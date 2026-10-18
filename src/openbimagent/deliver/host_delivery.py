"""Fail closed on absent host readback, semantic drift, or missing output files."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from openbimagent.assembly.semantic_snapshot import (
    SemanticSnapshot,
    SnapshotHost,
    compare_semantic_snapshots,
    expected_semantic_snapshot,
)
from openbimagent.assembly.vectorworks_plan import VECTORWORKS_PIPE_MESH_SEGMENTS
from openbimagent.utility.contracts import CompiledUtilityIR
from openbimagent.utility.geometry import execution_geometry, resolve_dimensions


class HostDeliveryVerificationError(RuntimeError):
    """A claimed host delivery could not be verified."""

    def __init__(
        self,
        message: str,
        *,
        comparison_report: dict[str, Any] | None = None,
        kind: str = "protocol",
        geometry_failures: list[str] | None = None,
    ):
        super().__init__(message)
        self.comparison_report = comparison_report
        self.kind = kind
        self.geometry_failures = geometry_failures or []


def _readback_precision(value: Any) -> Any:
    """Host protocols publish numbers at six decimal places."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_readback_precision(item) for item in value]
    if isinstance(value, dict):
        return {key: _readback_precision(item) for key, item in value.items()}
    return value


def _point_distance(left: Any, right: Any) -> float:
    return (
        (float(left["x_m"]) - float(right["x_m"])) ** 2
        + (float(left["y_m"]) - float(right["y_m"])) ** 2
        + (float(left["z_m"]) - float(right["z_m"])) ** 2
    ) ** 0.5


_SUPPORTED_GEOMETRY_SNAPSHOT_VERSIONS = {"1.1"}
_REQUIRED_DIMENSION_CHECKS = (
    "object_type_mesh",
    "vertex_count_at_least_4_rings",
    "both_ends_present",
    "both_radii_present",
    "vertices_on_end_planes",
    "vertices_on_inner_outer_surfaces",
    "axis_start",
    "axis_end",
    "axis_direction",
    "length",
    "inner_diameter",
    "outer_diameter",
)
_REQUIRED_TOPOLOGY_CHECKS = (
    "face_count_matches",
    "no_missing_faces",
    "no_duplicate_or_extra_faces",
    "face_orientation_consistent",
    "edge_manifold",
)
_ALLOWED_DIMENSION_STATUS = {"verified", "failed", "unverified"}
_ALLOWED_TOPOLOGY_STATUS = {"verified", "failed", "unverified"}
_ALLOWED_MESH_OBJECT_STATUS = {
    "verified",
    "dimension_verified_topology_unverified",
    "failed",
    "unverified",
}


def _nonfinite_paths(payload: Any, path: str = "$") -> list[str]:
    """Return JSON paths of non-finite numbers (NaN/Infinity) or exotic types."""
    if isinstance(payload, bool) or payload is None or isinstance(payload, (str, int)):
        return []
    if isinstance(payload, float):
        return [] if math.isfinite(payload) else [path]
    if isinstance(payload, list):
        found: list[str] = []
        for index, item in enumerate(payload):
            found.extend(_nonfinite_paths(item, f"{path}[{index}]"))
        return found
    if isinstance(payload, dict):
        found = []
        for key, item in payload.items():
            found.extend(_nonfinite_paths(item, f"{path}.{key}"))
        return found
    return [f"{path}: unsupported type {type(payload).__name__}"]


def _expected_mesh_object_status(dimension_status: str, topology_status: str) -> str:
    if "failed" in {dimension_status, topology_status}:
        return "failed"
    if dimension_status == "verified" and topology_status == "verified":
        return "verified"
    if dimension_status == "verified" and topology_status == "unverified":
        return "dimension_verified_topology_unverified"
    return "unverified"


def _finite_number(value: Any, field: str, failures: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        failures.append(f"{field} is not a finite number: {value!r}")
        return None
    return float(value)


def _expected_radial_deviation_mm(segments: int, diameter_mm: float) -> float:
    return (diameter_mm / 2.0) * (1.0 - math.cos(math.pi / segments))


def _validate_mesh_object(
    stable_id: str,
    object_payload: dict[str, Any],
    ir: CompiledUtilityIR,
    tolerance: float,
) -> tuple[list[str], str, str]:
    """Validate one pipe object; return (failures, dimension_status, topology_status)."""
    failures: list[str] = []
    segment = next((item for item in ir.segments if item.segment_id == stable_id), None)
    if segment is None:
        return [f"Geometry snapshot contains unknown object {stable_id!r}"], "unverified", "unverified"
    dimension_status = object_payload.get("dimension_status")
    topology_status = object_payload.get("topology_status")
    object_status = object_payload.get("status")
    if dimension_status not in _ALLOWED_DIMENSION_STATUS:
        return (
            [f"{stable_id}: dimension_status is invalid: {dimension_status!r}"],
            "unverified",
            "unverified",
        )
    if topology_status not in _ALLOWED_TOPOLOGY_STATUS:
        return (
            [f"{stable_id}: topology_status is invalid: {topology_status!r}"],
            "unverified",
            "unverified",
        )
    if object_status not in _ALLOWED_MESH_OBJECT_STATUS:
        failures.append(f"{stable_id}: object status is invalid: {object_status!r}")
    elif object_status != _expected_mesh_object_status(dimension_status, topology_status):
        failures.append(
            f"{stable_id}: status {object_status!r} contradicts dimension_status="
            f"{dimension_status!r} / topology_status={topology_status!r}"
        )
    if object_payload.get("object_type") != "pipe_segment":
        failures.append(f"{stable_id}: object_type is not pipe_segment")
    if object_payload.get("geometry_kind") != "hollow_tube_mesh":
        failures.append(f"{stable_id}: geometry_kind is not hollow_tube_mesh")
    dimension_checks = object_payload.get("dimension_checks")
    if not isinstance(dimension_checks, dict):
        failures.append(f"{stable_id}: dimension_checks is missing or not an object")
        dimension_checks = {}
    if dimension_status == "verified":
        missing_checks = [name for name in _REQUIRED_DIMENSION_CHECKS if name not in dimension_checks]
        if missing_checks:
            failures.append(f"{stable_id}: required dimension checks are missing: {missing_checks}")
        failed_checks = [
            name for name in _REQUIRED_DIMENSION_CHECKS if dimension_checks.get(name) is not True
        ]
        if failed_checks:
            failures.append(f"{stable_id}: dimension checks are not all true: {failed_checks}")
    topology_checks = object_payload.get("topology_checks")
    if not isinstance(topology_checks, dict):
        failures.append(f"{stable_id}: topology_checks is missing or not an object")
        topology_checks = {}
    if topology_status == "verified":
        missing_checks = [name for name in _REQUIRED_TOPOLOGY_CHECKS if name not in topology_checks]
        if missing_checks:
            failures.append(f"{stable_id}: required topology checks are missing: {missing_checks}")
        failed_checks = [
            name for name in _REQUIRED_TOPOLOGY_CHECKS if topology_checks.get(name) is not True
        ]
        if failed_checks:
            failures.append(f"{stable_id}: topology checks are not all true: {failed_checks}")
        face_count = object_payload.get("topology_face_count")
        expected_face_count = 4 * VECTORWORKS_PIPE_MESH_SEGMENTS
        if isinstance(face_count, bool) or face_count != expected_face_count:
            failures.append(
                f"{stable_id}: topology claims verified but topology_face_count={face_count!r} "
                f"does not match {expected_face_count} readable faces"
            )
    if not isinstance(object_payload.get("failures"), list):
        failures.append(f"{stable_id}: failures list is missing")
    discretization = object_payload.get("discretization")
    if not isinstance(discretization, dict):
        failures.append(f"{stable_id}: discretization evidence is missing")
    else:
        segments = discretization.get("circumferential_segments")
        if segments != VECTORWORKS_PIPE_MESH_SEGMENTS:
            failures.append(
                f"{stable_id}: discretization segments {segments!r} differ from the "
                f"plan adapter resolution {VECTORWORKS_PIPE_MESH_SEGMENTS}"
            )
        if discretization.get("is_engineering_tolerance") is not False:
            failures.append(
                f"{stable_id}: discretization must be labelled as a mesh property, not an "
                "engineering tolerance"
            )
        inner_diameter_ir, outer_diameter_ir = resolve_dimensions(
            segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
        )
        if not isinstance(discretization.get("basis"), str) or not discretization["basis"].strip():
            failures.append(f"{stable_id}: discretization basis is missing")
        deviations = discretization.get("radial_deviation_mm")
        if not isinstance(deviations, dict):
            failures.append(f"{stable_id}: discretization radial deviations are missing")
        else:
            for name, diameter in (("inner", inner_diameter_ir), ("outer", outer_diameter_ir)):
                reported = deviations.get(name)
                if diameter is None or isinstance(reported, bool) or not isinstance(reported, (int, float)):
                    failures.append(f"{stable_id}: discretization {name} deviation is invalid: {reported!r}")
                    continue
                expected = _expected_radial_deviation_mm(segments or VECTORWORKS_PIPE_MESH_SEGMENTS, float(diameter))
                if not math.isfinite(float(reported)) or abs(float(reported) - expected) > 1e-5:
                    failures.append(
                        f"{stable_id}: discretization {name} deviation {reported!r} does not match "
                        f"the reported mesh resolution (expected ~{expected:.6f} mm)"
                    )
    measured = object_payload.get("measured")
    if not isinstance(measured, dict):
        if dimension_status != "unverified":
            failures.append(f"{stable_id}: measured geometry is missing")
        return failures, dimension_status, topology_status
    vertex_count = measured.get("vertex_count")
    if isinstance(vertex_count, bool) or not isinstance(vertex_count, int) or vertex_count < 1:
        failures.append(f"{stable_id}: measured vertex_count is invalid: {vertex_count!r}")
    index_base = measured.get("vertex_index_base")
    if isinstance(index_base, bool) or index_base not in (0, 1):
        failures.append(f"{stable_id}: measured vertex_index_base is invalid: {index_base!r}")
    ring_counts = measured.get("ring_vertex_counts")
    expected_segments = discretization.get("circumferential_segments") if isinstance(discretization, dict) else None
    if not isinstance(ring_counts, dict):
        failures.append(f"{stable_id}: measured ring_vertex_counts is missing")
    else:
        for ring in ("outer_start", "inner_start", "outer_end", "inner_end"):
            count = ring_counts.get(ring)
            if isinstance(count, bool) or not isinstance(count, int) or count != expected_segments:
                failures.append(
                    f"{stable_id}: measured {ring} ring count {count!r} differs from the "
                    f"expected {expected_segments!r}"
                )
    direction = measured.get("axis_direction")
    if not isinstance(direction, dict):
        failures.append(f"{stable_id}: measured axis_direction is missing")
    else:
        for axis in ("x", "y", "z"):
            _finite_number(direction.get(axis), f"{stable_id}.measured.axis_direction.{axis}", failures)
    numbers: dict[str, float] = {}
    for field in ("length_m", "inner_diameter_mm", "outer_diameter_mm"):
        number = _finite_number(measured.get(field), f"{stable_id}.measured.{field}", failures)
        if number is not None:
            numbers[field] = number
    points: dict[str, dict[str, float]] = {}
    for field in ("axis_start_m", "axis_end_m"):
        point = measured.get(field)
        if not isinstance(point, dict):
            failures.append(f"{stable_id}: measured {field} is missing")
            continue
        coords: dict[str, float] = {}
        complete = True
        for axis in ("x_m", "y_m", "z_m"):
            number = _finite_number(point.get(axis), f"{stable_id}.measured.{field}.{axis}", failures)
            if number is None:
                complete = False
            else:
                coords[axis] = number
        if complete:
            points[field] = coords
    axis_points, outer_diameter_ir = execution_geometry(segment)
    inner_diameter_ir, outer_diameter_ir = resolve_dimensions(
        segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm
    )
    if inner_diameter_ir is None or len(axis_points) != 2:
        failures.append(
            f"{stable_id}: IR does not contain a straight pipe with explicit inner/outer diameters"
        )
        return failures, dimension_status, topology_status
    expected_start = axis_points[0].model_dump()
    expected_end = axis_points[1].model_dump()
    expected_length = _point_distance(expected_start, expected_end)
    if "axis_start_m" in points and _point_distance(points["axis_start_m"], expected_start) > tolerance:
        failures.append(f"{stable_id}: measured axis start differs from IR")
    if "axis_end_m" in points and _point_distance(points["axis_end_m"], expected_end) > tolerance:
        failures.append(f"{stable_id}: measured axis end differs from IR")
    if "length_m" in numbers and abs(numbers["length_m"] - expected_length) > tolerance:
        failures.append(f"{stable_id}: measured length differs from IR")
    if "inner_diameter_mm" in numbers and abs(numbers["inner_diameter_mm"] - inner_diameter_ir) > tolerance * 1000:
        failures.append(f"{stable_id}: measured inner diameter differs from IR")
    if "outer_diameter_mm" in numbers and abs(numbers["outer_diameter_mm"] - outer_diameter_ir) > tolerance * 1000:
        failures.append(f"{stable_id}: measured outer diameter differs from IR")
    return failures, dimension_status, topology_status


def _derive_geometry_status(
    dimension_statuses: dict[str, str],
    topology_statuses: dict[str, str],
    expected_segment_ids: list[str],
    failures: list[str],
) -> dict[str, Any]:
    if not expected_segment_ids:
        return {
            "dimension_verified": True,
            "topology_status": "not_applicable",
            "geometry_status": "not_applicable",
            "geometry_verified": True,
        }
    all_dimension_verified = (
        not failures
        and all(dimension_statuses.get(sid) == "verified" for sid in expected_segment_ids)
    )
    any_dimension_failed = any(status == "failed" for status in dimension_statuses.values())
    topology_values = [topology_statuses.get(sid, "unverified") for sid in expected_segment_ids]
    if all(value == "verified" for value in topology_values):
        topology_status = "verified"
    elif any(value == "failed" for value in topology_values):
        topology_status = "failed"
    else:
        topology_status = "unverified"
    if any_dimension_failed or topology_status == "failed":
        geometry_status = "failed"
    elif all_dimension_verified and topology_status == "verified":
        geometry_status = "verified"
    elif all_dimension_verified and topology_status == "unverified":
        geometry_status = "dimension_verified_topology_unverified"
    else:
        geometry_status = "unverified"
    return {
        "dimension_verified": all_dimension_verified
        and geometry_status in {"verified", "dimension_verified_topology_unverified"},
        "topology_status": topology_status,
        "geometry_status": geometry_status,
        "geometry_verified": geometry_status == "verified",
    }


def _verify_vectorworks_mesh_geometry(
    ir: CompiledUtilityIR,
    geometry_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Strictly validate the host geometry snapshot, then derive the verdict.

    The host's own status strings are treated as claims, not evidence: the
    snapshot must be structurally consistent, every number finite, every check
    present in the raw evidence, and the dimensions are re-compared against the
    compiled IR with the snapshot's own readback tolerance. Dimension
    verification and closed-solid topology verification stay separate; a
    topology claim without matching face evidence can never pass.
    """
    failures: list[str] = []
    if not isinstance(geometry_snapshot, dict):
        return {
            "dimension_verified": False,
            "topology_status": "unverified",
            "geometry_status": "unverified",
            "geometry_verified": False,
            "failures": ["Host receipt has no geometry snapshot"],
        }
    version = geometry_snapshot.get("geometry_snapshot_version")
    if version not in _SUPPORTED_GEOMETRY_SNAPSHOT_VERSIONS:
        return {
            "dimension_verified": False,
            "topology_status": "unverified",
            "geometry_status": "unverified",
            "geometry_verified": False,
            "failures": [
                f"unsupported geometry_snapshot_version {version!r}; this validator accepts "
                + ", ".join(sorted(_SUPPORTED_GEOMETRY_SNAPSHOT_VERSIONS))
                + " (the 1.0 format conflates dimension and topology verification and is rejected)"
            ],
        }
    if geometry_snapshot.get("host") != "vectorworks":
        failures.append("Geometry snapshot host is not vectorworks")
    if geometry_snapshot.get("source_ir_sha256") != ir.canonical_sha256():
        failures.append("Geometry snapshot source IR SHA does not match")
    if geometry_snapshot.get("units") != "m":
        failures.append("Geometry snapshot units are not metres")
    tolerance = geometry_snapshot.get("readback_tolerance_m")
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(float(tolerance))
        or not 0 < float(tolerance) <= 1e-3
    ):
        failures.append(f"Geometry snapshot readback tolerance is invalid: {tolerance!r}")
        tolerance = 1e-5
    else:
        tolerance = float(tolerance)
    tolerance_basis = geometry_snapshot.get("readback_tolerance_basis")
    if not isinstance(tolerance_basis, str) or not tolerance_basis.strip():
        failures.append("Geometry snapshot readback tolerance basis is missing")
    for path in _nonfinite_paths(geometry_snapshot)[:8]:
        failures.append(f"Geometry snapshot contains a non-finite value at {path}")
    objects = geometry_snapshot.get("objects")
    if not isinstance(objects, list):
        failures.append("Geometry snapshot objects are missing")
        return {
            "dimension_verified": False,
            "topology_status": "unverified",
            "geometry_status": "unverified",
            "geometry_verified": False,
            "failures": failures,
        }
    expected_segment_ids = sorted(segment.segment_id for segment in ir.segments)
    dimension_statuses: dict[str, str] = {}
    topology_statuses: dict[str, str] = {}
    seen: set[str] = set()
    for object_payload in objects:
        if not isinstance(object_payload, dict):
            failures.append("Geometry object entry is not an object")
            continue
        stable_id = object_payload.get("stable_id")
        if not isinstance(stable_id, str) or not stable_id:
            failures.append(f"Geometry object entry has an invalid stable_id: {stable_id!r}")
            continue
        if stable_id in seen:
            failures.append(f"Geometry snapshot contains duplicate object {stable_id!r}")
            continue
        seen.add(stable_id)
        object_failures, dimension_status, topology_status = _validate_mesh_object(
            stable_id, object_payload, ir, tolerance
        )
        failures.extend(object_failures)
        dimension_statuses[stable_id] = dimension_status
        topology_statuses[stable_id] = topology_status
    missing = sorted(set(expected_segment_ids) - seen)
    if missing:
        failures.append(f"Geometry snapshot is missing pipe segments: {missing}")
    derived = _derive_geometry_status(
        dimension_statuses, topology_statuses, expected_segment_ids, failures
    )
    claimed_status = geometry_snapshot.get("status")
    if isinstance(claimed_status, str) and claimed_status != derived["geometry_status"]:
        failures.append(
            f"Geometry snapshot claims status {claimed_status!r} but independent validation "
            f"derives {derived['geometry_status']!r}"
        )
    claimed_topology = geometry_snapshot.get("topology_status")
    if isinstance(claimed_topology, str) and claimed_topology != derived["topology_status"]:
        failures.append(
            f"Geometry snapshot claims topology_status {claimed_topology!r} but independent "
            f"validation derives {derived['topology_status']!r}"
        )
    if failures:
        derived["dimension_verified"] = False
        derived["geometry_verified"] = False
        if derived["geometry_status"] == "verified":
            derived["geometry_status"] = "unverified"
    derived["failures"] = failures
    return derived


def verify_host_delivery(
    ir: Any,
    receipt: Any,
    *,
    host: SnapshotHost | str,
    output_path: str | Path | None = None,
    plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify receipt against independent IR projection and the actual saved file.

    Semantic readback covers IDs, systems, topology and properties. Vectorworks
    geometry readback is a separate measurement from actual mesh vertices.
    """
    payload = receipt if isinstance(receipt, dict) else receipt.model_dump(mode="json")
    status = payload.get("status")
    if status not in {"completed", "partial"}:
        raise HostDeliveryVerificationError("Host delivery is not completed or partial")
    if plan_sha256 is not None and payload.get("canonical_sha256") != plan_sha256:
        raise HostDeliveryVerificationError("Host receipt plan SHA does not match the requested plan")
    reported_path = payload.get("output_path")
    if not reported_path:
        raise HostDeliveryVerificationError("Host receipt has no output path")
    target = Path(reported_path).resolve()
    if output_path is not None and target != Path(output_path).resolve():
        raise HostDeliveryVerificationError("Host receipt output path differs from the requested output")
    if not target.is_file() or target.stat().st_size == 0:
        raise HostDeliveryVerificationError(f"Host output is missing or empty: {target}")
    try:
        snapshot = SemanticSnapshot.model_validate(payload.get("semantic_snapshot"))
    except ValidationError as exc:
        raise HostDeliveryVerificationError(f"Host semantic readback is missing or invalid: {exc}") from exc
    if snapshot.host != SnapshotHost(host):
        raise HostDeliveryVerificationError("Host snapshot identity differs from the requested host")
    if not snapshot.canonical_sha256:
        raise HostDeliveryVerificationError("Host snapshot has no canonical SHA")
    expected = expected_semantic_snapshot(ir, host)
    # Compare both sides at the host readback precision. Blender domain custom
    # properties preserve raw doubles while geometric readback is rounded.
    # Keep the original returned snapshot intact in the delivery record.
    expected_payload = _readback_precision(expected.model_dump(mode="json"))
    expected_payload["canonical_sha256"] = ""
    expected = SemanticSnapshot.model_validate(expected_payload).finalized()
    comparison_payload = _readback_precision(snapshot.model_dump(mode="json"))
    comparison_payload["canonical_sha256"] = ""
    comparison_snapshot = SemanticSnapshot.model_validate(comparison_payload).finalized()
    report = compare_semantic_snapshots(expected, comparison_snapshot)
    if not report.ok:
        raise HostDeliveryVerificationError(
            "Host semantic readback differs from the compiled IR",
            comparison_report=report.model_dump(mode="json"),
            kind="semantic",
        )
    host_name = SnapshotHost(host).value
    geometry_verified = False
    geometry_status = "not_checked"
    geometry_failures: list[str] = []
    dimension_verified: bool | None = None
    topology_status = "not_checked"
    geometry_caveats: list[str] = []
    if host_name == "vectorworks":
        compiled = ir if isinstance(ir, CompiledUtilityIR) else CompiledUtilityIR.model_validate(ir)
        checked = _verify_vectorworks_mesh_geometry(compiled, payload.get("geometry_snapshot"))
        geometry_verified = checked["geometry_verified"]
        geometry_status = checked["geometry_status"]
        geometry_failures = checked["failures"]
        dimension_verified = checked["dimension_verified"]
        topology_status = checked["topology_status"]
        receipt_caveats = payload.get("geometry_caveats")
        if (
            geometry_status not in {"verified", "not_applicable"}
            and isinstance(receipt_caveats, (list, tuple))
            and all(isinstance(item, str) for item in receipt_caveats)
        ):
            geometry_caveats = list(receipt_caveats)
        if geometry_status == "dimension_verified_topology_unverified" and not geometry_caveats:
            geometry_caveats = [
                "mesh dimensions were verified against actual host vertices; closed-solid topology "
                "(face count, duplicate faces, shared-edge connectivity, face orientation) is not "
                "read back and stays unverified"
            ]
        if status == "completed" and not geometry_verified:
            raise HostDeliveryVerificationError(
                "Host receipt claims completion but mesh geometry is not verified: "
                + "; ".join(geometry_failures[:8]),
                kind="geometry",
                geometry_failures=geometry_failures,
            )
        if status == "partial" and geometry_verified:
            raise HostDeliveryVerificationError(
                "Host receipt is partial although semantic and mesh geometry both verify; "
                "partial status is inconsistent"
            )
    file_hash = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            file_hash.update(chunk)
    if host_name == "vectorworks":
        if geometry_verified:
            geometry_scope = (
                "vectorworks mesh vertices: inner/outer surfaces and annular end caps measured; "
                "closed-solid topology also read back and verified"
            )
        elif geometry_status == "dimension_verified_topology_unverified":
            geometry_scope = (
                "vectorworks mesh dimensions (inner/outer surfaces, end planes, axis, length) "
                "measured from actual vertices; closed-solid topology (face/edge readback) is not "
                "verified"
            )
        else:
            geometry_scope = "vectorworks mesh geometry attempted but not verified; no pass is claimed"
    else:
        geometry_scope = "beveled_axis_curve; no independent surface measurement"
    return {
        "semantic_snapshot": snapshot.model_dump(mode="json"),
        "semantic_comparison_report": report.model_dump(mode="json"),
        "semantic_verified": True,
        "geometry_snapshot": payload.get("geometry_snapshot"),
        "geometry_status": geometry_status,
        "geometry_verified": geometry_verified,
        "dimension_verified": dimension_verified,
        "topology_status": topology_status,
        "geometry_failures": geometry_failures,
        "geometry_caveats": geometry_caveats,
        "verification_status": (
            "verified"
            if status == "completed" and (host_name != "vectorworks" or geometry_verified)
            else "partial"
        ),
        "source_ir_sha256": snapshot.source_ir_sha256,
        "output_path": str(target),
        "output_bytes": target.stat().st_size,
        "output_sha256": file_hash.hexdigest(),
        "geometry_scope": geometry_scope,
        "readback_scope": (
            "host_reported_positions_axes_topology_and_properties + "
            "vectorworks_mesh_vertex_measurements"
            if host_name == "vectorworks"
            else "host_reported_positions_axes_topology_and_properties"
        ),
        "limitations": [
            "Semantic fields (stable ID, system, topology, material, nominal diameter) are host properties, not independent physical measurements.",
            "Vectorworks inner/outer diameters and axis are measured from actual mesh vertices within the 1e-5 m numeric readback tolerance, which is a protocol resolution bound, not a surface-accuracy or engineering acceptance tolerance; this does not establish native pipe/boolean solidity, wall material continuity, hydraulics or structural compliance.",
            "Closed-solid topology (face count, duplicate faces, shared-edge connectivity, face orientation) is not read back on Vectorworks 2024 and stays unverified; curved walls are polygonal meshes whose chordal deviation is reported per object as discretization evidence, not as an engineering tolerance.",
            "Node/manhole objects remain schematic and are not measured as pipe solids.",
            "The saved file was hashed, not independently reopened and inspected.",
        ],
    }
