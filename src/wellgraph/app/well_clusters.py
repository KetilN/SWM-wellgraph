"""Well-clusters of cored wellbores and how well their horizontal permeability agrees.

Cluster rule: wellbores with Klinkenberg-corrected horizontal permeability are grouped so
that no two wellbores in a cluster are `MAX_DISTANCE_KM` or more apart (complete linkage:
the closest wellbores or groups merge first, and a merge is only allowed while every pair
stays under the limit). Distances are haversine on SODIR decimal degrees.

Cluster value: for every pair of wellbores in the cluster and every formation both have
cored (at least `MIN_SAMPLES` Klinkenberg plugs each), pair the two wellbore means of
log10 permeability. The value is the Pearson correlation over all those pairs, each entered
both ways round so the result does not depend on wellbore order. Clusters without enough
shared formations get no value and a reason instead. SODIR's placeholder units (no formal
name, undifferentiated) do not count as a shared formation.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from itertools import combinations
from typing import Any

from wellgraph.graph.store import GraphStore

MAX_DISTANCE_KM = 20.0
MIN_SAMPLES = 5  # plugs per wellbore and formation before its mean is used
MIN_PAIRS = 6  # wellbore-pair x formation points before a correlation is reported
MIN_FORMATIONS = 2  # distinct shared formations before a correlation is reported
EARTH_RADIUS_KM = 6371.0088
# SODIR placeholders for rock without a named unit: not a formation two wellbores share.
PLACEHOLDER_FORMATIONS = ("NO FORMAL NAME", "UNDIFFERENTIATED")

KLINKENBERG = "s.kh_method = 'klinkenberg' and isfinite(s.kh_md) and s.kh_md > 0"
REASONS = {
    "single_well": "Only one wellbore in the cluster",
    "no_shared_formation": f"No formation cored by two wellbores ({MIN_SAMPLES}+ plugs each)",
    "too_few_shared": f"Fewer than {MIN_PAIRS} shared wellbore-formation pairs "
                      f"or fewer than {MIN_FORMATIONS} shared formations",
    "no_spread": "Shared formation means do not vary",
}

_cache: dict[str, dict[str, Any]] = {}


def complete_linkage(n: int, edges: list[tuple[int, int, float]], limit: float) -> list[list[int]]:
    """Groups of 0..n-1 in which every pair is closer than `limit`.

    `edges` holds every pair closer than `limit` as (i, j, distance). A missing pair counts
    as too far, which is all complete linkage needs to know.
    """
    link: dict[int, dict[int, float]] = defaultdict(dict)
    heap: list[tuple[float, int, int]] = []
    for i, j, d in edges:
        if i != j and d < limit:
            link[i][j] = link[j][i] = d
            heap.append((d, min(i, j), max(i, j)))
    heapq.heapify(heap)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    next_id = n
    while heap:
        d, a, b = heapq.heappop(heap)
        if a not in members or b not in members or link[a].get(b) != d:
            continue
        merged = next_id
        next_id += 1
        members[merged] = members.pop(a) + members.pop(b)
        near_a, near_b = link.pop(a), link.pop(b)
        for other in near_a.keys() | near_b.keys():
            if other in (a, b):
                continue
            link[other].pop(a, None)
            link[other].pop(b, None)
            if other in near_a and other in near_b:
                far = max(near_a[other], near_b[other])
                link[merged][other] = link[other][merged] = far
                heapq.heappush(heap, (far, other, merged))
    return sorted((sorted(group) for group in members.values()), key=lambda g: g[0])


def _haversine(a: dict[str, Any], b: dict[str, Any]) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def correlation(means: dict[int, dict[str, float]]) -> dict[str, Any]:
    """Between-wellbore correlation from {wellbore: {formation: mean log10 kh}}."""
    if len(means) < 2:
        return {"r": None, "reason": "single_well", "pairs": 0, "formations": [], "points": []}
    xs: list[float] = []
    ys: list[float] = []
    points: list[dict[str, Any]] = []
    for a, b in combinations(sorted(means), 2):
        for formation in sorted(means[a].keys() & means[b].keys()):
            u, v = means[a][formation], means[b][formation]
            xs += [u, v]
            ys += [v, u]
            points.append({"formation": formation, "a": a, "b": b, "log_a": u, "log_b": v})
    formations = sorted({p["formation"] for p in points})
    out: dict[str, Any] = {"r": None, "reason": None, "pairs": len(points),
                           "formations": formations, "points": points}
    if not points:
        out["reason"] = "no_shared_formation"
    elif len(points) < MIN_PAIRS or len(formations) < MIN_FORMATIONS:
        out["reason"] = "too_few_shared"
    else:
        out["r"] = _pearson(xs, ys)
        if out["r"] is None:
            out["reason"] = "no_spread"
    return out


def clusters(store: GraphStore) -> dict[str, Any]:
    key = str(store.directory)
    if key not in _cache:
        _cache[key] = _build(store)
    return _cache[key]


def _build(store: GraphStore) -> dict[str, Any]:
    wells = store._rows(f"""
        select s.wellbore_id, any_value(w.name) as name, any_value(w.field) as field,
               any_value(w.lat) as lat, any_value(w.lon) as lon, count(*) as samples,
               median(s.kh_md) as kh_median_md
        from core_sample s join wellbore w using (wellbore_id)
        where {KLINKENBERG} and isfinite(w.lat) and isfinite(w.lon)
        group by s.wellbore_id order by s.wellbore_id
    """)
    without_position = store._scalar(f"""
        select count(distinct s.wellbore_id) from core_sample s
        left join wellbore w using (wellbore_id)
        where {KLINKENBERG} and (w.lat is null or w.lon is null)
    """)
    cored = store._scalar("select count(distinct wellbore_id) from core_sample")
    edges = []
    for i, j in combinations(range(len(wells)), 2):
        if abs(wells[i]["lat"] - wells[j]["lat"]) > 0.2:  # 0.2 degrees of latitude is 22 km
            continue
        d = _haversine(wells[i], wells[j])
        if d < MAX_DISTANCE_KM:
            edges.append((i, j, d))
    means: dict[int, dict[str, float]] = defaultdict(dict)
    for row in store._rows(f"""
        select s.wellbore_id, s.formation, avg(log10(s.kh_md)) as mean_log, count(*) as n
        from core_sample s
        where {KLINKENBERG} and nullif(trim(s.formation), '') is not null
          and upper(trim(s.formation)) not in {PLACEHOLDER_FORMATIONS}
        group by 1, 2 having count(*) >= {MIN_SAMPLES}
    """):
        means[row["wellbore_id"]][row["formation"]] = row["mean_log"]

    out = []
    for number, group in enumerate(complete_linkage(len(wells), edges, MAX_DISTANCE_KM), 1):
        members = [wells[i] for i in group]
        ids = {w["wellbore_id"]: w["name"] for w in members}
        result = correlation({i: means.get(i, {}) for i in ids})
        lat = sum(w["lat"] for w in members) / len(members)
        lon = sum(w["lon"] for w in members) / len(members)
        centre = {"lat": lat, "lon": lon}
        fields = sorted({w["field"] for w in members if w["field"]})
        out.append({
            "id": number, "lat": lat, "lon": lon,
            "radius_km": max(_haversine(centre, w) for w in members),
            "span_km": max((_haversine(a, b) for a, b in combinations(members, 2)), default=0.0),
            "wellbores": len(members), "samples": sum(w["samples"] for w in members),
            "fields": fields,
            "r": result["r"], "reason": result["reason"],
            "reason_text": REASONS.get(result["reason"]),
            "pairs": result["pairs"], "formations": result["formations"],
            "points": [{"formation": p["formation"], "a": ids[p["a"]], "b": ids[p["b"]],
                        "log_a": p["log_a"], "log_b": p["log_b"]} for p in result["points"]],
            "wells": [{"name": w["name"], "lat": w["lat"], "lon": w["lon"],
                       "samples": w["samples"], "kh_median_md": w["kh_median_md"],
                       "formations": len(means.get(w["wellbore_id"], {}))} for w in members],
        })
    valued = [c for c in out if c["r"] is not None]
    reasons: dict[str, int] = defaultdict(int)
    for c in out:
        if c["reason"]:
            reasons[c["reason"]] += 1
    return {
        "definition": {
            "max_distance_km": MAX_DISTANCE_KM, "min_samples": MIN_SAMPLES,
            "min_pairs": MIN_PAIRS, "min_formations": MIN_FORMATIONS,
            "excluded_formations": list(PLACEHOLDER_FORMATIONS),
            "measurement": "Klinkenberg-corrected horizontal permeability, log10 of mD",
        },
        "summary": {
            "cored_wellbores": cored, "wellbores": len(wells),
            "wellbores_without_position": without_position,
            "clusters": len(out), "with_value": len(valued),
            "wellbores_in_valued_clusters": sum(c["wellbores"] for c in valued),
            "single_well_clusters": sum(c["wellbores"] == 1 for c in out),
            "largest_cluster": max((c["wellbores"] for c in out), default=0),
            "largest_span_km": max((c["span_km"] for c in out), default=0.0),
            "no_value": {REASONS[k]: v for k, v in sorted(reasons.items())},
        },
        "clusters": out,
    }
