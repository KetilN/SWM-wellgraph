"""Read models for the shelf-wide core cross-plot."""

from __future__ import annotations

import math
from typing import Any

from wellgraph.graph.store import GraphStore

POROSITY_BINS = 72
PERMEABILITY_BINS = 60
MAX_SERIES = 5
POINTS_PER_SERIES = 220
UNASSIGNED = "(unassigned)"

# Keep these column expressions fixed. Request values are passed as query parameters.
FILTER_COLUMNS = {
    "field": "nullif(trim(w.field), '')",
    "formation": "nullif(trim(s.formation), '')",
    "lithology": "nullif(trim(s.lithology), '')",
    "grain_size": "nullif(trim(s.grain_size), '')",
    "kh_method": "nullif(trim(s.kh_method), '')",
}
PAIRED = """
    from core_sample s left join wellbore w using (wellbore_id)
    where isfinite(s.porosity_pct) and s.porosity_pct > 0
      and isfinite(s.kh_md) and s.kh_md > 0
"""


def options(store: GraphStore) -> dict[str, list[str]]:
    columns = [
        f"list(distinct coalesce({column}, '{UNASSIGNED}') "
        f"order by coalesce({column}, '{UNASSIGNED}')) as {key}"
        for key, column in FILTER_COLUMNS.items()
    ]
    row = store._rows(f"select {', '.join(columns)} {PAIRED}")[0]
    return {key: values or [] for key, values in row.items()}


def crossplot(
    store: GraphStore, filters: dict[str, str | None], colour_by: str | None = None
) -> dict[str, Any]:
    if colour_by and colour_by not in FILTER_COLUMNS:
        raise ValueError("Unknown core cross-plot grouping.")
    where: list[str] = []
    parameters: list[str] = []
    for key, column in FILTER_COLUMNS.items():
        value = filters.get(key)
        if value == UNASSIGNED:
            where.append(f"{column} is null")
        elif value:
            where.append(f"{column} = ?")
            parameters.append(value)
    selected = " and ".join(where) or "true"
    rows = store._rows(
        f"""
        with core as (
            select s.wellbore_id, s.porosity_pct as phi, s.kh_md as kh, log10(s.kh_md) as log_kh,
                   ({selected}) as selected
            {PAIRED}
        ), bounds as (
            select greatest(10, ceil(max(phi) / 10) * 10) as phi_max,
                   coalesce(floor(min(log_kh)), 0) as log_min,
                   coalesce(ceil(max(log_kh)), 1) as log_top
            from core
        ), bins as (
            select c.*,
                   least({POROSITY_BINS - 1}, floor(phi / phi_max * {POROSITY_BINS}))::int as x,
                   least({PERMEABILITY_BINS - 1},
                     floor((log_kh - log_min) / greatest(1, log_top - log_min)
                           * {PERMEABILITY_BINS}))::int as y
            from core c cross join bounds
        ), counts as (
            select x, y, count(*) as pairs, count(*) filter (where selected) as matching,
                   count(distinct wellbore_id) filter (where selected) as wells,
                   median(phi) filter (where selected) as median_phi,
                   median(kh) filter (where selected) as median_kh
            from bins group by grouping sets ((x, y), ())
        )
        select counts.*, phi_max, log_min, greatest(log_min + 1, log_top) as log_max
        from counts cross join bounds
        order by x nulls first, y
        """,
        parameters,
    )
    summary = rows[0]
    series = []
    if colour_by:
        grouping = FILTER_COLUMNS[colour_by]
        series = store._rows(
            f"""
            with selected_core as (
                select {grouping} as category, s.wellbore_id, s.sample_id, s.porosity_pct as phi,
                       log10(s.kh_md) as log_kh
                {PAIRED} and ({selected})
            ), groups as (
                select category, count(*) as pairs, count(distinct wellbore_id) as wells,
                       regr_slope(log_kh, phi) as slope, regr_intercept(log_kh, phi) as intercept,
                       quantile_cont(phi, 0.1) as phi_p10, quantile_cont(phi, 0.9) as phi_p90
                from selected_core where category is not null
                group by category order by pairs desc, category limit {MAX_SERIES}
            ), preview as (
                select category, list([phi, log_kh] order by sample_id) as points
                from (
                    select category, sample_id, phi, log_kh from selected_core join groups using (category)
                    qualify row_number() over (
                        partition by category order by hash(sample_id), sample_id
                    ) <= {POINTS_PER_SERIES}
                ) group by category
            )
            select groups.*, preview.points from groups join preview using (category)
            order by pairs desc, category
            """,
            parameters,
        )
        for group in series:
            fit = (group["slope"], group["intercept"])
            if (
                group["pairs"] < 30
                or group["wells"] < 3
                or any(v is None or not math.isfinite(v) for v in fit)
            ):
                group["slope"] = group["intercept"] = None
    return {
        "bounds": {key: summary[key] for key in ("phi_max", "log_min", "log_max")},
        "resolution": [POROSITY_BINS, PERMEABILITY_BINS],
        "pairs": summary["pairs"],
        "matching": summary["matching"],
        "wellbores": summary["wells"],
        "median_phi": summary["median_phi"],
        "median_kh": summary["median_kh"],
        "bins": [[row["x"], row["y"], row["pairs"], row["matching"]] for row in rows[1:]],
        "series": series,
    }
