"""Depth joins: attach hole section and formations to an event from its measured depth.

Pure functions over rows shaped like the `hole_section` and `formation_top` tables (dicts for
one wellbore). The LLM never decides these: they come from SODIR by depth.

Rules (see docs/extraction-design.md):
- Joins are done only for measured depths (depth_ref MD or ASSUMED_MD). TVD and TVDSS
  depths are not comparable with the SODIR MD tables: no hole section, no formations.
- Hole sections are ordered by base_md; each covers (previous base_md, base_md]. When
  base_md is NULL the casing_depth_md of the row is used instead. A depth below the deepest
  base, or in a section whose diameter is NULL, gives None.
- A formation unit contains a depth when top_md <= depth <= base_md (both inclusive), so a
  depth exactly on a boundary returns the unit above and the unit below. A row with base
  above top is read as the same interval the right way round.
- NULL base_md: the unit ends at the next deeper top of the same level; if there is none it
  is open-ended (runs to TD). NULL top_md: the row is skipped.
- Output order: GROUP, SUBGROUP, FORMATION, MEMBER, then shallowest first.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

LEVEL_ORDER = {"GROUP": 0, "SUBGROUP": 1, "FORMATION": 2, "MEMBER": 3}
_UNKNOWN_LEVEL = len(LEVEL_ORDER)
MD_DEPTH_REFS = ("MD", "ASSUMED_MD")  # the only references joined against SODIR tables

Row = Mapping[str, Any]


def _num(value: Any) -> float | None:
    """Float, or None for NULL / NaN / non-numeric."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def hole_section_at(sections: Iterable[Row] | None, md: float | None) -> float | None:
    """Hole diameter (inches) of the section containing measured depth `md`, or None."""
    depth = _num(md)
    if depth is None or depth <= 0 or not sections:
        return None
    by_base: dict[float, list[float]] = {}
    for row in sections:
        base = _num(row.get("base_md"))
        if base is None:  # SODIR has rows with no hole depth: the casing shoe is the best proxy
            base = _num(row.get("casing_depth_md"))
        if base is None:
            continue
        diameters = by_base.setdefault(base, [])
        diameter = _num(row.get("hole_diameter_in"))
        if diameter is not None:
            diameters.append(diameter)
    for base in sorted(by_base):
        if depth <= base:
            diameters = by_base[base]
            # Same base, different diameters (pilot hole opened up): take the smaller.
            return min(diameters) if diameters else None
    return None


def _units(tops: Iterable[Row] | None) -> list[tuple[int, float, float, str]]:
    """(level rank, top, base, name) per usable row, NULL bases resolved, sorted for output."""
    rows = []
    for row in tops or []:
        top = _num(row.get("top_md"))
        name = row.get("formation")
        if top is None or not name:
            continue
        base = _num(row.get("base_md"))
        if base is not None and base < top:  # one SODIR row has base above top
            top, base = base, top
        level = str(row.get("level") or "").upper()
        rows.append((LEVEL_ORDER.get(level, _UNKNOWN_LEVEL), top, base, name))
    out = []
    for rank, top, base, name in rows:
        if base is None:
            deeper = [t for r, t, _, _ in rows if r == rank and t > top]
            base = min(deeper) if deeper else math.inf
        out.append((rank, top, base, str(name)))
    return sorted(out)


def _names(units: list[tuple[int, float, float, str]]) -> list[str]:
    return list(dict.fromkeys(name for _, _, _, name in units))


def formations_at(tops: Iterable[Row] | None, md: float | None) -> list[str]:
    """Every unit, any level, containing `md`: GROUP, then FORMATION, then MEMBER."""
    depth = _num(md)
    if depth is None:
        return []
    return _names([u for u in _units(tops) if u[1] <= depth <= u[2]])


def formations_in_interval(
    tops: Iterable[Row] | None, top_md: float | None, base_md: float | None
) -> list[str]:
    """Every unit overlapping the interval. A unit that only touches an end does not count.

    A zero-length interval, or one with a missing end, is treated as a point (formations_at).
    Reversed bounds are swapped.
    """
    a, b = _num(top_md), _num(base_md)
    if a is None or b is None or a == b:
        return formations_at(tops, a if a is not None else b)
    lo, hi = min(a, b), max(a, b)
    return _names([u for u in _units(tops) if u[1] < hi and u[2] > lo])


def joins_for_event(
    sections: Iterable[Row] | None,
    tops: Iterable[Row] | None,
    top_md: float | None,
    base_md: float | None,
    depth_ref: str | None,
) -> tuple[float | None, list[str]]:
    """(hole_diameter_in, formations) for an event, ready for `to_event_row`.

    Only for measured depths: any depth_ref other than MD / ASSUMED_MD gives (None, []).
    The hole section is the one at the deepest point of the event (base_md, else top_md):
    when an interval crosses a casing point, the shallower part is behind casing and the
    open hole is the deeper section. Formations are every unit overlapping the interval.
    """
    if depth_ref not in MD_DEPTH_REFS:
        return None, []
    deepest = _num(base_md)
    if deepest is None:
        deepest = _num(top_md)
    return hole_section_at(sections, deepest), formations_in_interval(tops, top_md, base_md)
