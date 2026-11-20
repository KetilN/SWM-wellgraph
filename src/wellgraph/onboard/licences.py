"""Active production licences with their operator and licensees, as one GeoJSON map layer.

    PYTHONPATH=src python -m wellgraph.onboard.licences

Outlines come from the SODIR FactMaps feature service (layer 3000, simplified to about
200 m); operator and licensees come from the FactPages table `licence_licensee_hst` (rows
without an end date are the current holders). Both are open data, NLOD 2.0. The result is
written next to the other map layers and holds, per licence: name, main area, phase, date
granted, operator and `licensees` as [company, interest in %] pairs, largest first.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wellgraph.onboard.sodir import URL as TABLE_URL

LAYER = "https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer/3000/query"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "geo" / "licences.geojson"
PAGE = 1000


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "wellgraph"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _date(value: Any) -> str | None:
    """The service gives dates as milliseconds since 1970."""
    if isinstance(value, (int, float)):
        return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)).date().isoformat()
    return value if isinstance(value, str) and value else None


def fetch_outlines() -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode({
            "where": "prlActive='Y'", "outSR": 4326, "geometryPrecision": 4,
            "maxAllowableOffset": 0.002, "f": "geojson", "resultOffset": len(features),
            "resultRecordCount": PAGE, "orderByFields": "OBJECTID",
            "outFields": "prlName,prlNpdidLicence,prlMainArea,prlPhaseCurrent,prlDateGranted,cmpLongName",
        })
        page = json.loads(_get(f"{LAYER}?{query}"))
        features += page["features"]
        if len(page["features"]) < PAGE:
            return features


def current_licensees(table_csv: str) -> dict[int, list[list[Any]]]:
    """{licence id: [[company, interest %], ...]} from rows of the history table still valid."""
    out: dict[int, list[list[Any]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(table_csv.lstrip("﻿"))):
        if (row["prlLicenseeDateValidTo"] or "").strip():
            continue
        out[int(row["prlNpdidLicence"])].append(
            [row["cmpLongName"].strip(), round(float(row["prlLicenseeInterest"] or 0), 3)])
    for holders in out.values():
        holders.sort(key=lambda h: (-h[1], h[0]))
    return out


def build(features: list[dict[str, Any]], licensees: dict[int, list[list[Any]]]) -> dict[str, Any]:
    out = []
    for f in features:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        granted = p.get("prlDateGranted")
        out.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "name": p["prlName"], "area": p.get("prlMainArea"), "phase": p.get("prlPhaseCurrent"),
            "granted": _date(granted),
            "operator": (p.get("cmpLongName") or "").strip() or None,
            "licensees": licensees.get(int(p["prlNpdidLicence"]), []),
        }})
    out.sort(key=lambda f: f["properties"]["name"])
    return {"type": "FeatureCollection", "features": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.onboard.licences", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    table = _get(TABLE_URL.format(table="licence_licensee_hst")).decode("utf-8")
    layer = build(fetch_outlines(), current_licensees(table))
    Path(args.out).write_text(json.dumps(layer, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    holders = {h[0] for f in layer["features"] for h in f["properties"]["licensees"]}
    print(f"{len(layer['features'])} active licences, {len(holders)} companies -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
