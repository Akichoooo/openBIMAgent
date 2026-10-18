"""Explicit physical pipe dimensions for host execution tests.

The legacy contract fixture stays in test_compiled_utility_ir to cover v1.0.
"""
from openbimagent.utility.geometry import upgrade_legacy_utility_ir
from test_compiled_utility_ir import solved_payload as legacy_solved_payload


def physical_solved_payload() -> dict:
    return upgrade_legacy_utility_ir(
        legacy_solved_payload(),
        dimensions_by_segment={"pipe-001": {"inner_diameter_mm": 300.0, "outer_diameter_mm": 360.0}},
    ).model_dump(mode="json")
