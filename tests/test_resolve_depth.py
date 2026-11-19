"""Depth joins: event depth -> hole section and formations. Synthetic rows only."""

from __future__ import annotations

import pytest

from wellgraph.graph.schema import DEPTH_REFS
from wellgraph.resolve.depth import (
    formations_at,
    formations_in_interval,
    hole_section_at,
    joins_for_event,
)

SECTIONS = [
    {"wellbore_id": 1, "hole_diameter_in": 36.0, "base_md": 200.0},
    {"wellbore_id": 1, "hole_diameter_in": 17.5, "base_md": 1200.0},
    {"wellbore_id": 1, "hole_diameter_in": 12.25, "base_md": 2950.0},
    {"wellbore_id": 1, "hole_diameter_in": 8.5, "base_md": 3200.0},
]

TOPS = [
    {"formation": "NORDLAND GP", "level": "GROUP", "parent": None, "top_md": 130.0, "base_md": 900.0},
    {"formation": "SHETLAND GP", "level": "GROUP", "parent": None,
     "top_md": 2400.0, "base_md": 2900.0},
    {"formation": "TOR FM", "level": "FORMATION", "parent": "SHETLAND GP",
     "top_md": 2400.0, "base_md": 2650.0},
    {"formation": "HOD FM", "level": "FORMATION", "parent": "SHETLAND GP",
     "top_md": 2650.0, "base_md": 2900.0},
    {"formation": "UPPER TOR MBR", "level": "MEMBER", "parent": "TOR FM",
     "top_md": 2400.0, "base_md": 2500.0},
]


def test_hole_section_inside_a_section():
    assert hole_section_at(SECTIONS, 100.0) == 36.0
    assert hole_section_at(SECTIONS, 1000.0) == 17.5
    assert hole_section_at(SECTIONS, 2500.0) == 12.25
    assert hole_section_at(SECTIONS, 3100.0) == 8.5


def test_hole_section_base_is_inclusive_top_is_exclusive():
    assert hole_section_at(SECTIONS, 1200.0) == 17.5
    assert hole_section_at(SECTIONS, 1200.01) == 12.25
    assert hole_section_at(SECTIONS, 3200.0) == 8.5


def test_hole_section_deeper_than_last_base_is_none():
    assert hole_section_at(SECTIONS, 3200.5) is None


def test_hole_section_missing_inputs():
    assert hole_section_at([], 1000.0) is None
    assert hole_section_at(None, 1000.0) is None
    assert hole_section_at(SECTIONS, None) is None
    assert hole_section_at(SECTIONS, 0.0) is None
    assert hole_section_at(SECTIONS, -5.0) is None
    assert hole_section_at(SECTIONS, float("nan")) is None


def test_hole_section_unsorted_and_duplicate_rows():
    rows = [SECTIONS[2], SECTIONS[0], SECTIONS[3], SECTIONS[1], dict(SECTIONS[1]), SECTIONS[2]]
    assert hole_section_at(rows, 1000.0) == 17.5
    assert hole_section_at(rows, 2500.0) == 12.25


def test_hole_section_rows_without_base_are_ignored():
    rows = [*SECTIONS, {"hole_diameter_in": 6.0, "base_md": None}]
    assert hole_section_at(rows, 3100.0) == 8.5
    assert hole_section_at(rows, 5000.0) is None


def test_hole_section_null_diameter_is_not_borrowed_from_a_neighbour():
    rows = [
        {"hole_diameter_in": 17.5, "base_md": 1200.0},
        {"hole_diameter_in": None, "base_md": 2950.0},
        {"hole_diameter_in": 8.5, "base_md": 3200.0},
    ]
    assert hole_section_at(rows, 2000.0) is None
    assert hole_section_at(rows, 3000.0) == 8.5


def test_hole_section_same_base_prefers_the_row_with_a_diameter():
    rows = [
        {"hole_diameter_in": None, "base_md": 1200.0},
        {"hole_diameter_in": 17.5, "base_md": 1200.0},
    ]
    assert hole_section_at(rows, 800.0) == 17.5


def test_hole_section_same_base_conflicting_diameters_takes_the_smaller():
    # pilot hole later opened up to the same depth: the report text decides, we pick the pilot
    rows = [
        {"hole_diameter_in": 26.0, "base_md": 1200.0},
        {"hole_diameter_in": 12.25, "base_md": 1200.0},
    ]
    assert hole_section_at(rows, 800.0) == 12.25


def test_hole_section_accepts_nan_as_null():
    rows = [{"hole_diameter_in": float("nan"), "base_md": 1200.0},
            {"hole_diameter_in": 12.25, "base_md": float("nan")}]
    assert hole_section_at(rows, 800.0) is None


def test_formations_at_orders_group_formation_member():
    assert formations_at(TOPS, 2450.0) == ["SHETLAND GP", "TOR FM", "UPPER TOR MBR"]
    assert formations_at(TOPS, 2600.0) == ["SHETLAND GP", "TOR FM"]
    assert formations_at(TOPS, 500.0) == ["NORDLAND GP"]


def test_formations_at_order_does_not_depend_on_row_order():
    assert formations_at(list(reversed(TOPS)), 2450.0) == [
        "SHETLAND GP", "TOR FM", "UPPER TOR MBR"]


def test_formations_at_gap_and_out_of_range():
    assert formations_at(TOPS, 1500.0) == []
    assert formations_at(TOPS, 50.0) == []
    assert formations_at(TOPS, 5000.0) == []
    assert formations_at([], 1000.0) == []
    assert formations_at(None, 1000.0) == []
    assert formations_at(TOPS, None) == []


def test_formations_at_boundary_is_inclusive_on_both_sides():
    # 2650 is the base of TOR and the top of HOD: both are returned, shallower first
    assert formations_at(TOPS, 2650.0) == ["SHETLAND GP", "TOR FM", "HOD FM"]


def test_formations_at_no_duplicates():
    assert formations_at([*TOPS, dict(TOPS[1])], 2700.0) == ["SHETLAND GP", "HOD FM"]


def test_null_base_ends_at_next_top_of_same_level():
    tops = [
        {"formation": "A GP", "level": "GROUP", "top_md": 100.0, "base_md": None},
        {"formation": "B GP", "level": "GROUP", "top_md": 900.0, "base_md": 1500.0},
        {"formation": "X FM", "level": "FORMATION", "top_md": 100.0, "base_md": None},
    ]
    assert formations_at(tops, 500.0) == ["A GP", "X FM"]
    # A GP stops where B GP starts; X FM has no deeper formation-level unit, so it is open-ended
    assert formations_at(tops, 1000.0) == ["B GP", "X FM"]
    assert formations_at(tops, 9000.0) == ["X FM"]


def test_null_top_is_skipped_and_unknown_level_sorts_last():
    tops = [
        {"formation": "NO TOP FM", "level": "FORMATION", "top_md": None, "base_md": 500.0},
        {"formation": "ODD UNIT", "level": None, "top_md": 100.0, "base_md": 500.0},
        {"formation": "A GP", "level": "group", "top_md": 100.0, "base_md": 500.0},
    ]
    assert formations_at(tops, 300.0) == ["A GP", "ODD UNIT"]


def test_formations_in_interval_overlap():
    assert formations_in_interval(TOPS, 2600.0, 2700.0) == ["SHETLAND GP", "TOR FM", "HOD FM"]
    assert formations_in_interval(TOPS, 800.0, 2450.0) == [
        "NORDLAND GP", "SHETLAND GP", "TOR FM", "UPPER TOR MBR"]
    assert formations_in_interval(TOPS, 1000.0, 2000.0) == []


def test_formations_in_interval_touching_a_boundary_does_not_count():
    # 2500-2650 lies in TOR; it only touches HOD at 2650 and UPPER TOR MBR at 2500
    assert formations_in_interval(TOPS, 2500.0, 2650.0) == ["SHETLAND GP", "TOR FM"]


def test_formations_in_interval_point_and_missing_base_equal_formations_at():
    assert formations_in_interval(TOPS, 2450.0, 2450.0) == formations_at(TOPS, 2450.0)
    assert formations_in_interval(TOPS, 2450.0, None) == formations_at(TOPS, 2450.0)
    assert formations_in_interval(TOPS, None, 2450.0) == formations_at(TOPS, 2450.0)
    assert formations_in_interval(TOPS, None, None) == []


def test_formations_in_interval_reversed_bounds_are_swapped():
    assert formations_in_interval(TOPS, 2700.0, 2600.0) == ["SHETLAND GP", "TOR FM", "HOD FM"]


# ------------------------------------------------- SODIR oddities (from the real load)


def test_hole_section_null_base_falls_back_to_casing_depth():
    rows = [
        {"hole_diameter_in": 17.5, "base_md": 1200.0, "casing_depth_md": 1195.0},
        {"hole_diameter_in": 12.25, "base_md": None, "casing_depth_md": 2945.0},
        {"hole_diameter_in": 8.5, "base_md": None, "casing_depth_md": None},
    ]
    assert hole_section_at(rows, 2000.0) == 12.25
    assert hole_section_at(rows, 2945.0) == 12.25
    assert hole_section_at(rows, 2946.0) is None  # the 8.5 in row has no depth at all


def test_subgroup_sorts_between_group_and_formation():
    tops = [
        {"formation": "X FM", "level": "FORMATION", "top_md": 100.0, "base_md": 500.0},
        {"formation": "X SUBGP", "level": "SUBGROUP", "top_md": 100.0, "base_md": 500.0},
        {"formation": "X MBR", "level": "MEMBER", "top_md": 100.0, "base_md": 500.0},
        {"formation": "X GP", "level": "GROUP", "top_md": 100.0, "base_md": 500.0},
    ]
    assert formations_at(tops, 300.0) == ["X GP", "X SUBGP", "X FM", "X MBR"]


def test_base_above_top_is_read_as_the_same_interval():
    tops = [{"formation": "FLIPPED FM", "level": "FORMATION", "top_md": 900.0, "base_md": 700.0}]
    assert formations_at(tops, 800.0) == ["FLIPPED FM"]
    assert formations_at(tops, 950.0) == []
    assert formations_in_interval(tops, 600.0, 750.0) == ["FLIPPED FM"]


# ------------------------------------------------------------- joins_for_event


@pytest.mark.parametrize("ref", ["MD", "ASSUMED_MD"])
def test_joins_apply_to_measured_depths(ref):
    assert joins_for_event(SECTIONS, TOPS, 2600.0, 2700.0, ref) == (
        12.25, ["SHETLAND GP", "TOR FM", "HOD FM"])
    assert joins_for_event(SECTIONS, TOPS, 2450.0, 2450.0, ref) == (
        12.25, ["SHETLAND GP", "TOR FM", "UPPER TOR MBR"])


@pytest.mark.parametrize("ref", ["TVD", "TVDSS", None, "nonsense"])
def test_no_joins_for_vertical_or_unknown_depth_references(ref):
    assert joins_for_event(SECTIONS, TOPS, 2600.0, 2700.0, ref) == (None, [])


def test_every_schema_depth_ref_is_handled():
    joined = {r for r in DEPTH_REFS if joins_for_event(SECTIONS, TOPS, 2450.0, 2450.0, r)[1]}
    assert joined == {"MD", "ASSUMED_MD"}


def test_interval_across_a_casing_point_takes_the_deeper_open_hole_section():
    assert joins_for_event(SECTIONS, TOPS, 1150.0, 1250.0, "MD")[0] == 12.25


def test_joins_with_missing_depths_or_tables():
    assert joins_for_event(SECTIONS, TOPS, None, None, "ASSUMED_MD") == (None, [])
    assert joins_for_event(SECTIONS, TOPS, 2450.0, None, "MD") == (
        12.25, ["SHETLAND GP", "TOR FM", "UPPER TOR MBR"])
    assert joins_for_event(None, None, 2450.0, 2450.0, "MD") == (None, [])
    assert joins_for_event(SECTIONS, TOPS, 9000.0, 9000.0, "MD") == (None, [])
