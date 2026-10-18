"""Shared pipe semantics and finite segment distances, in project metres.

Invert is the inside bottom elevation at each section. Axis elevation is invert
plus the inside radius. A capsule conservatively includes hemispherical end caps;
it is not an exact open ended pipe solid or a manhole connection model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbimagent.utility.contracts import CompiledUtilityIR, Coordinate3D, PipeSegment

Point = tuple[float, float, float]


def resolve_dimensions(inner: float | None, outer: float | None, wall: float | None) -> tuple[float | None, float | None]:
    """Derive only from explicitly supplied physical dimensions, never from DN."""
    if any(value is not None and (not math.isfinite(value) or value <= 0) for value in (inner, outer, wall)):
        raise ValueError("physical pipe dimensions must be finite and positive")
    if inner is not None and wall is not None:
        expected = inner + 2 * wall
        if outer is not None and not math.isclose(outer, expected, abs_tol=1e-6):
            raise ValueError("outer diameter must equal inner diameter + 2 * wall thickness")
        outer = expected
    if outer is not None and wall is not None and inner is None:
        inner = outer - 2 * wall
        if inner <= 0:
            raise ValueError("wall thickness leaves no positive inner diameter")
    if inner is not None and outer is not None and outer < inner:
        raise ValueError("outer diameter cannot be smaller than inner diameter")
    return inner, outer


def axis_centerline(segment: PipeSegment) -> tuple[Coordinate3D, ...] | None:
    """Return true axis coordinates, or None when inside diameter is unknown."""
    if segment.centerline_reference == "legacy_invert":
        return None
    if segment.centerline_reference == "axis":
        return segment.centerline
    inner, _ = resolve_dimensions(segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm)
    if inner is None:
        return None
    return tuple(point.model_copy(update={"z_m": point.z_m + inner / 2000.0}) for point in segment.centerline)


def execution_geometry(segment: PipeSegment) -> tuple[tuple[Coordinate3D, ...], float]:
    axis = axis_centerline(segment)
    _, outer = resolve_dimensions(segment.inner_diameter_mm, segment.outer_diameter_mm, segment.wall_thickness_mm)
    if axis is None or outer is None:
        raise ValueError(f"segment {segment.segment_id!r}: execution requires explicit geometry semantics and known inner/outer diameters; DN is not an outer diameter")
    return axis, outer


def execution_port_positions(ir: CompiledUtilityIR) -> dict[str, Coordinate3D]:
    positions = {}
    for segment in ir.segments:
        axis, _ = execution_geometry(segment)
        positions[segment.start_port_id] = axis[0]
        positions[segment.end_port_id] = axis[-1]
    return positions


def upgrade_legacy_utility_ir(payload: dict[str, Any], *, dimensions_by_segment: dict[str, dict[str, float]]) -> CompiledUtilityIR:
    """Explicit v1.0 migration. Caller supplies physical dimensions for every pipe."""
    from copy import deepcopy
    from openbimagent.utility.contracts import CompiledUtilityIR

    legacy = CompiledUtilityIR.model_validate(payload)
    if legacy.protocol_version != "1.0":
        raise ValueError("only compiled IR 1.0 can be upgraded")
    upgraded = deepcopy(payload)
    upgraded["protocol_version"] = "1.1"
    for segment in upgraded["segments"]:
        dimensions = dimensions_by_segment[segment["segment_id"]]
        if set(dimensions) - {"inner_diameter_mm", "outer_diameter_mm", "wall_thickness_mm"}:
            raise ValueError("migration accepts physical dimension fields only")
        segment.update(dimensions)
        segment["centerline_reference"] = "invert"
    result = CompiledUtilityIR.model_validate(upgraded)
    for segment in result.segments:
        execution_geometry(segment)
    return result


@dataclass(frozen=True)
class SegmentDistance:
    distance_m: float
    first_parameter: float
    second_parameter: float
    first_point: Point
    second_point: Point


@dataclass(frozen=True)
class SegmentBoxDistance:
    distance_m: float
    parameter: float
    segment_point: Point
    box_point: Point


def closest_segment_box_points(start: Point, end: Point, minimum: Point, maximum: Point) -> SegmentBoxDistance:
    """Exact finite-segment distance to the closed 3D box, not its AABB midpoint.

    Coordinate clamping is affine between crossings of the six box planes. The
    squared distance is quadratic on each interval, so its endpoints and its
    stationary point contain the global minimum, including degenerate segments.
    """
    if any(low > high for low, high in zip(minimum, maximum, strict=True)):
        raise ValueError("box minimum must not exceed maximum")
    direction = tuple(b - a for a, b in zip(start, end, strict=True))
    breaks = {0.0, 1.0}
    for origin, delta, low, high in zip(start, direction, minimum, maximum, strict=True):
        if abs(delta) > 1e-15:
            breaks.update(parameter for boundary in (low, high) if 0 < (parameter := (boundary - origin) / delta) < 1)
    ordered = sorted(breaks)
    candidates = set(ordered)
    for left, right in zip(ordered, ordered[1:], strict=False):
        midpoint = (left + right) / 2
        numerator = denominator = 0.0
        candidates.add(midpoint)
        for origin, delta, low, high in zip(start, direction, minimum, maximum, strict=True):
            value = origin + delta * midpoint
            boundary = low if value < low else high if value > high else None
            if boundary is not None:
                numerator += delta * (origin - boundary)
                denominator += delta * delta
        if denominator > 0:
            stationary = -numerator / denominator
            if left <= stationary <= right:
                candidates.add(stationary)
    results = []
    for parameter in candidates:
        point = tuple(a + parameter * d for a, d in zip(start, direction, strict=True))
        box_point = tuple(max(low, min(high, value)) for value, low, high in zip(point, minimum, maximum, strict=True))
        results.append(SegmentBoxDistance(math.dist(point, box_point), parameter, point, box_point))
    return min(results, key=lambda item: (item.distance_m, abs(item.parameter - 0.5)))


def closest_segment_points(p: Point, q: Point, r: Point, s: Point) -> SegmentDistance:
    """Exact closest pair for two closed 3D segments, including point segments."""
    def dot(a: Point, b: Point) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))
    def sub(a: Point, b: Point) -> Point:
        return tuple(x - y for x, y in zip(a, b, strict=True))  # type: ignore[return-value]
    d, e, v = sub(q, p), sub(s, r), sub(p, r)
    a, b, c, f, g = dot(d, d), dot(d, e), dot(e, e), dot(d, v), dot(e, v)
    def clamp(value: float) -> float:
        return max(0.0, min(1.0, value))
    if a <= 1e-24 and c <= 1e-24:
        t = u = 0.0
    elif a <= 1e-24:
        t, u = 0.0, clamp(g / c)
    elif c <= 1e-24:
        t, u = clamp(-f / a), 0.0
    else:
        def cross(x: Point, y: Point) -> Point:
            return x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]
        normal = cross(d, e)
        determinant = dot(normal, normal)
        t = clamp(-dot(cross(v, e), normal) / determinant) if determinant > 1e-28 * a * c else 0.0
        if determinant <= 1e-28 * a * c:
            # Parallel overlapping projections have infinitely many minima.
            # Prefer their midpoint so later code can distinguish interior contact.
            projection_r = -f / a
            projection_s = projection_r + b / a
            low, high = max(0.0, min(projection_r, projection_s)), min(1.0, max(projection_r, projection_s))
            if low <= high:
                t = (low + high) / 2.0
        u = (b * t + g) / c
        if u < 0:
            t, u = clamp(-f / a), 0.0
        elif u > 1:
            t, u = clamp((b - f) / a), 1.0
    first = tuple(x + t * dx for x, dx in zip(p, d, strict=True))
    second = tuple(x + u * dx for x, dx in zip(r, e, strict=True))
    return SegmentDistance(math.dist(first, second), t, u, first, second)  # type: ignore[arg-type]
