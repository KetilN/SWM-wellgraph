"""Fetch and simplify the base-map layers the web app draws under the wells.

    PYTHONPATH=src python -m wellgraph.app.basemap [--land path/to/ne_50m_land.geojson]

Writes small GeoJSON files into `static/geo/` so the app needs no internet at run time:

    quadrants.geojson, blocks.geojson, fields.geojson   SODIR FactMaps (NLOD 2.0)
    land.geojson                                        Natural Earth 1:50m land (public domain),
                                                        clipped to the Norwegian shelf

The files are committed; rerun this only to refresh them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

GEO = Path(__file__).parent / "static" / "geo"
SODIR = "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/FeatureServer"
LAND_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
            "geojson/ne_50m_land.geojson")
BBOX = (-12.0, 52.0, 42.0, 83.0)  # west, south, east, north: the Norwegian shelf and around

LAYERS = {
    # name: (layer id, fields to keep as {source: short name}, simplification in degrees)
    "quadrants": (803, {"qadName": "name"}, 0.01),
    "blocks": (802, {"blcName": "name"}, 0.01),
    "fields": (502, {"fldName": "name", "fldHcType": "hc", "fldCurrentActivitySatus": "status"},
               0.002),
}


def fetch_layer(client: httpx.Client, layer_id: int, keep: dict[str, str], offset_deg: float):
    features, start = [], 0
    while True:
        params = {"where": "1=1", "outFields": ",".join(keep), "f": "geojson",
                  "geometryPrecision": 3, "resultOffset": start, "resultRecordCount": 1000,
                  "orderByFields": "OBJECTID"}
        if offset_deg:
            params["maxAllowableOffset"] = offset_deg
        page = client.get(f"{SODIR}/{layer_id}/query", params=params).raise_for_status().json()
        for feature in page.get("features", []):
            if not feature.get("geometry"):
                continue
            props = {short: (str(feature["properties"].get(src) or "").strip() or None)
                     for src, short in keep.items()}
            features.append({"type": "Feature", "properties": props,
                             "geometry": feature["geometry"]})
        got = len(page.get("features", []))
        if got < 1000:
            return features
        start += got


def _clip_ring(ring, bbox):
    """Sutherland-Hodgman clip of one ring against a rectangle."""
    west, south, east, north = bbox
    edges = [(0, west, True), (0, east, False), (1, south, True), (1, north, False)]
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    for axis, bound, keep_greater in edges:
        if not points:
            return []
        def inside(p, axis=axis, bound=bound, keep_greater=keep_greater):
            return p[axis] >= bound if keep_greater else p[axis] <= bound

        out = []
        for i, current in enumerate(points):
            previous = points[i - 1]
            if inside(current) != inside(previous):
                t = (bound - previous[axis]) / (current[axis] - previous[axis])
                out.append([previous[0] + t * (current[0] - previous[0]),
                            previous[1] + t * (current[1] - previous[1])])
            if inside(current):
                out.append(current)
        points = out
    if len(points) < 3:
        return []
    points = [[round(x, 3), round(y, 3)] for x, y in points]
    return [*points, points[0]]


def clip_land(collection: dict, bbox=BBOX) -> list[dict]:
    features = []
    for feature in collection["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" \
            else [geometry["coordinates"]]
        for polygon in polygons:
            outer = _clip_ring(polygon[0], bbox)
            if outer:
                holes = [h for h in (_clip_ring(r, bbox) for r in polygon[1:]) if h]
                features.append({"type": "Feature", "properties": {},
                                 "geometry": {"type": "Polygon", "coordinates": [outer, *holes]}})
    return features


def write(name: str, features: list[dict]) -> int:
    GEO.mkdir(parents=True, exist_ok=True)
    path = GEO / f"{name}.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.app.basemap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--land", help="Local copy of ne_50m_land.geojson (else downloaded).")
    args = parser.parse_args(argv)
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        for name, (layer_id, keep, offset_deg) in LAYERS.items():
            features = fetch_layer(client, layer_id, keep, offset_deg)
            print(f"{name}: {len(features)} features, {write(name, features) / 1024:.0f} KB")
        land = json.loads(Path(args.land).read_text()) if args.land \
            else client.get(LAND_URL).raise_for_status().json()
    features = clip_land(land)
    print(f"land: {len(features)} polygons, {write('land', features) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
