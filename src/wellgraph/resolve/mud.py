"""Mud weight at a depth from the SODIR mud table (no model involved)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Values outside this range in the SODIR table are unit or typing errors (a few rows give
# ppg where sg is expected).
PLAUSIBLE_SG = (0.9, 2.6)
MAX_GAP_M = 300.0


def mud_weight_at(
    checks: Iterable[Mapping[str, Any]] | None, md: float | None, max_gap_m: float = MAX_GAP_M
) -> tuple[float, float] | None:
    """(mud weight in sg, distance in metres to the measurement) nearest to `md`.

    `checks` are `mud_check` rows of one wellbore (md, mud_weight_sg). Returns None when
    there is no depth, no plausible measurement, or the nearest one is further than
    `max_gap_m` away: mud weight changes along a section, so a distant value is not used.
    """
    if md is None:
        return None
    best: tuple[float, float] | None = None
    for check in checks or ():
        depth, weight = check.get("md"), check.get("mud_weight_sg")
        if depth is None or weight is None:
            continue
        if not PLAUSIBLE_SG[0] <= weight <= PLAUSIBLE_SG[1]:
            continue
        gap = abs(float(depth) - float(md))
        if gap <= max_gap_m and (best is None or gap < best[1]):
            best = (round(float(weight), 3), round(gap, 1))
    return best
