"""Pressure window of a wellbore and its offsets, from what the graph holds today.

All values are equivalent mud weights in sg against measured depth:
  - mud weight used: SODIR mud checks, and mud weights stated in the reports at events;
  - fracture side: leak-off tests at casing shoes (SODIR), and losses with a report-stated
    mud weight (the well took mud at or below that weight plus circulating pressure);
  - pore side: kicks with a report-stated mud weight (the formation flowed against it).
Pore pressure itself (RFT / MDT points, prognosis curves) is not extracted from the reports
yet, so the pore side here is only where a kick marks it.
"""

from __future__ import annotations

from typing import Any

from wellgraph.graph.store import GraphStore

MAX_OFFSETS = 80  # nearest wellbores with any pressure data
SG_RANGE = (0.8, 2.6)  # values outside are unit errors in the source tables
BIN_M = 500.0


def window(store: GraphStore, name: str, range_km: float = 10.0) -> dict[str, Any]:
    well = store.resolve_wellbore(name)
    me = int(well["wellbore_id"])
    range_km = min(max(float(range_km), 0.0), 100.0)
    offsets: list[dict[str, Any]] = []
    if range_km and well.get("lat") is not None and well.get("lon") is not None:
        origin = [well["lat"], well["lon"]]
        offsets = store._rows(f"""
            select w.wellbore_id, w.name, w.wellbore_type, round(haversine_km(?, ?, w.lat, w.lon), 2) as distance_km
            from wb w
            where w.wellbore_id <> {me} and isfinite(w.lat) and isfinite(w.lon)
              and haversine_km(?, ?, w.lat, w.lon) <= ?
              and (w.wellbore_id in (select wellbore_id from mud_check)
                   or w.wellbore_id in (select wellbore_id from hole_section where lot_mud_density_sg is not null)
                   or w.wellbore_id in (select wellbore_id from event where mud_weight_source = 'report'
                                        and event_type in ('kick', 'lost_circulation')))
            order by distance_km, w.name limit {MAX_OFFSETS}
        """, origin + origin + [range_km])
    names = {me: well["name"], **{int(o["wellbore_id"]): o["name"] for o in offsets}}
    ids = ", ".join(str(i) for i in names)
    lo, hi = SG_RANGE
    mud = store._rows(f"""
        select wellbore_id, md, mud_weight_sg as sg from mud_check
        where wellbore_id in ({ids}) and md is not null and mud_weight_sg between {lo} and {hi}
        order by wellbore_id, md
    """)
    lot = store._rows(f"""
        select wellbore_id, casing_depth_md as md, lot_mud_density_sg as sg, casing_diameter_in, hole_diameter_in
        from hole_section
        where wellbore_id in ({ids}) and casing_depth_md is not null and lot_mud_density_sg between {lo} and {hi}
        order by wellbore_id, md
    """)
    events = store._rows(f"""
        select wellbore_id, event_id, event_type, coalesce(top_md, context_top_md) as md, mud_weight_sg as sg,
               mud_weight_kind, depth_basis, hole_diameter_in, formations[1] as unit, description
        from event
        where wellbore_id in ({ids}) and event_type in ('kick', 'lost_circulation')
          and mud_weight_source = 'report' and mud_weight_sg between {lo} and {hi}
          and coalesce(top_md, context_top_md) is not null
        order by wellbore_id, md
    """)
    shoes = store._rows(f"""
        select hole_diameter_in, casing_diameter_in, casing_depth_md, base_md from hole_section
        where wellbore_id = {me} order by coalesce(casing_depth_md, base_md)
    """)
    tops = store._rows(f"""
        select formation, top_md, base_md from formation_top
        where wellbore_id = {me} and level = 'GROUP' order by top_md
    """)

    def split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        mine, others = [], []
        for r in rows:
            wid = int(r.pop("wellbore_id"))
            r["wellbore"] = names[wid]
            (mine if wid == me else others).append(r)
        return mine, others

    mud_me, mud_off = split(mud)
    lot_me, lot_off = split(lot)
    ev_me, ev_off = split(events)

    # what the wells in range used and saw, per depth interval
    deepest = max([r["md"] for r in mud + lot + events] or [0.0])
    bins = []
    start = 0.0
    while start < deepest:
        end = start + BIN_M
        m = [r for r in mud if start <= r["md"] < end]
        t = [r for r in lot if start <= r["md"] < end]
        here = [r for r in events if start <= r["md"] < end]
        kicks = [r for r in here if r["event_type"] == "kick"]
        losses = [r for r in here if r["event_type"] == "lost_circulation"]
        if m or t or kicks or losses:
            weights = sorted(r["sg"] for r in m)
            bins.append({
                "from_md": start, "to_md": end,
                "mud_checks": len(m), "mud_wellbores": len({r["wellbore"] for r in m}),
                "mud_min_sg": weights[0] if weights else None,
                "mud_median_sg": weights[len(weights) // 2] if weights else None,
                "mud_max_sg": weights[-1] if weights else None,
                "lot_tests": len(t), "lot_min_sg": min((r["sg"] for r in t), default=None),
                "lot_max_sg": max((r["sg"] for r in t), default=None),
                "kicks": len(kicks), "kick_max_sg": max((r["sg"] for r in kicks), default=None),
                "losses": len(losses), "loss_min_sg": min((r["sg"] for r in losses), default=None),
            })
        start = end
    return {
        "wellbore": well["name"], "wellbore_type": well.get("wellbore_type"),
        "total_depth_md": well.get("total_depth_md"), "water_depth": well.get("water_depth"),
        "range_km": range_km, "ranges_km": [0, 5, 10, 20, 50],
        "offsets": [{"name": o["name"], "type": o["wellbore_type"], "distance_km": o["distance_km"]} for o in offsets],
        "offsets_capped": len(offsets) == MAX_OFFSETS,
        "well": {"mud": mud_me, "lot": lot_me, "events": ev_me, "shoes": shoes, "tops": tops},
        "offset": {"mud": mud_off, "lot": lot_off, "events": ev_off},
        "bins": bins, "bin_m": BIN_M,
    }
