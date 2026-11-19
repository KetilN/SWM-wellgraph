"""Read-only access to the published graph (a folder of Parquet files).

DuckDB views are created over the Parquet files in an in-memory database, so nothing here
can modify the graph. Every query is parameterised.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import duckdb

from .schema import (
    ATTRIBUTION,
    DEPTH_REFS,
    EVENT_TYPES,
    MUD_WEIGHT_KINDS,
    SOURCE_DOI,
    SOURCE_URL,
    TABLES,
)

MAX_LIMIT = 200
MAX_PAGE_TEXT_CHARS = 8000
DEFAULT_LIMIT = 50
NO_EVENTS_NOTE = (
    "Drilling events have not been loaded into this graph yet; only backbone data "
    "(wellbores, formation tops, hole sections) is available."
)


class NotFound(LookupError):
    """A named thing (wellbore, event) is not in the graph."""


# --- input normalisation -------------------------------------------------------------

_FRACTION = re.compile(r"^\s*(\d+)?(?:[\s-]+)?(?:(\d+)\s*/\s*(\d+))?\s*$")


def parse_hole_size(value: str | float) -> float:
    """'12 1/4', '12-1/4\"', '12.25 in', 12.25 -> 12.25 (inches)."""
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r'(inches|inch|in\b|["”″])', "", str(value), flags=re.IGNORECASE).strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = _FRACTION.match(text)
    if not m or not (m.group(1) or m.group(2)):
        raise ValueError(f"Cannot read hole size {value!r}; use e.g. '12 1/4' or 12.25.")
    whole = float(m.group(1) or 0)
    frac = float(m.group(2)) / float(m.group(3)) if m.group(2) else 0.0
    return whole + frac


_UNIT_SUFFIX = [
    (re.compile(r"\bGROUP\b"), "GP"),
    (re.compile(r"\bFORMATION\b"), "FM"),
    (re.compile(r"\bMEMBER\b"), "MBR"),
]


def normalise_formation(name: str) -> str:
    """'Shetland Group' -> 'SHETLAND GP' (SODIR spelling)."""
    text = " ".join(name.upper().split())
    for pattern, short in _UNIT_SUFFIX:
        text = pattern.sub(short, text)
    return text


def _wellbore_key(name: str) -> str:
    """'99_1-2_A', '99/1-2A', '99/1-2 a' -> '99/1-2A'."""
    text = name.strip().upper()
    text = re.sub(r"^(\d+)_(\d+)", r"\1/\2", text)  # folder spelling of the block
    return re.sub(r"[\s_]+", "", text)


def _clamp(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


class GraphStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(
                f"Graph directory {self.directory} does not exist. Set WELLGRAPH_KG_DIR "
                "to the folder holding wellbore.parquet and the other graph tables."
            )
        self._lock = threading.Lock()
        self._con = duckdb.connect(":memory:")
        self.loaded: dict[str, bool] = {}
        for table, columns in TABLES.items():
            path = self.directory / f"{table}.parquet"
            self.loaded[table] = path.is_file()
            if path.is_file():
                escaped = str(path).replace("'", "''")
                self._con.execute(
                    f"create view {table} as select * from read_parquet('{escaped}')"
                )
                have = {r[0] for r in self._con.execute(f"describe {table}").fetchall()}
                missing = set(columns) - have
                if missing:
                    raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
            else:
                cols = ", ".join(f"{c} {t}" for c, t in columns.items())
                self._con.execute(f"create table {table} ({cols})")
        self._con.execute(
            """
            create macro haversine_km(lat1, lon1, lat2, lon2) as
              2 * 6371.0088 * asin(sqrt(
                pow(sin(radians(lat2 - lat1) / 2), 2)
                + cos(radians(lat1)) * cos(radians(lat2))
                * pow(sin(radians(lon2 - lon1) / 2), 2)))
            """
        )
        self._con.execute(
            "create view wb as select *, "
            "regexp_replace(upper(name), '[\\s_]+', '', 'g') as name_key from wellbore"
        )

    # --- helpers ---------------------------------------------------------------------

    def _rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._con.execute(sql, params or [])
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def _scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        with self._lock:
            return self._con.execute(sql, params or []).fetchone()[0]

    def resolve_wellbore(self, name: str) -> dict[str, Any]:
        key = _wellbore_key(name)
        rows = self._rows("select * exclude (name_key) from wb where name_key = ?", [key])
        if len(rows) == 1:
            return rows[0]
        # Report names can carry a technical-sidetrack suffix (T2, T3 ...) that SODIR does
        # not register as its own wellbore: fall back to the parent wellbore.
        parent = re.sub(r"T\d+$", "", key)
        if parent != key:
            rows = self._rows(
                "select * exclude (name_key) from wb where name_key = ?", [parent]
            )
            if len(rows) == 1:
                return {**rows[0], "matched_as_sidetrack_of": rows[0]["name"]}
        prefix = re.match(r"^[0-9/]+", key)
        like = (prefix.group(0) if prefix and prefix.group(0) else key[:4]) + "%"
        near = self._rows(
            "select name from wb where name_key like ? "
            "order by levenshtein(name_key, ?), name limit 8",
            [like, key],
        )
        hint = ", ".join(r["name"] for r in near) or "none in that block"
        raise NotFound(f"No wellbore named {name!r}. Closest names: {hint}.")

    def _origin(self, near_wellbore, lat, lon) -> tuple[float, float, int | None] | None:
        if near_wellbore:
            ref = self.resolve_wellbore(near_wellbore)
            if ref["lat"] is None or ref["lon"] is None:
                raise ValueError(f"Wellbore {ref['name']} has no coordinates.")
            return ref["lat"], ref["lon"], ref["wellbore_id"]
        if lat is not None and lon is not None:
            return float(lat), float(lon), None
        if lat is not None or lon is not None:
            raise ValueError("Give both lat and lon.")
        return None

    @staticmethod
    def _page(rows: list[dict], limit: int, total: int) -> dict[str, Any]:
        return {"count": len(rows), "total": total, "truncated": total > limit, "results": rows}

    # --- tools -----------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        counts = {t: self._scalar(f"select count(*) from {t}") for t in TABLES}
        bounds = self._rows(
            "select min(lat) min_lat, max(lat) max_lat, min(lon) min_lon, max(lon) max_lon "
            "from wellbore"
        )[0]
        by_type = self._rows(
            "select event_type, count(*) n from event group by 1 order by n desc"
        )
        return {
            "about": (
                "Knowledge graph of Norwegian continental shelf wellbores: SODIR backbone "
                "(wellbores, formation tops, hole sections) plus drilling events extracted "
                "from public end-of-well reports, each with a page-level citation."
            ),
            "counts": counts,
            "tables_loaded": self.loaded,
            "coverage": {
                "wellbores_with_events": self._scalar(
                    "select count(distinct wellbore_id) from event"
                ),
                "wellbores_with_documents": self._scalar(
                    "select count(distinct wellbore_id) from document"
                ),
                "blocks": [
                    r["block"]
                    for r in self._rows(
                        "select split_part(name, '-', 1) block, count(*) n from wellbore "
                        "group by 1 order by n desc limit 15"
                    )
                ],
                "bounding_box": bounds,
            },
            "event_types": EVENT_TYPES,
            "depth_refs": DEPTH_REFS,
            "mud_weight_kinds": MUD_WEIGHT_KINDS,
            "events_by_type": {r["event_type"]: r["n"] for r in by_type},
            "units": {
                "depth": (
                    "metres; measured depth (MD) for backbone tables. Event depths carry "
                    "depth_ref: ASSUMED_MD means the report wrote no reference; TVD / TVDSS "
                    "events are not joined to hole sections or formations"
                ),
                "mud_weight": (
                    "specific gravity (sg), converted; mud_weight_raw is as written and "
                    "mud_weight_kind says mud_in_hole, ecd or unknown"
                ),
                "hole_size": "inches",
                "distance": "km",
            },
            "entities": {
                table: {"columns": list(cols)} for table, cols in TABLES.items()
            },
            "relationships": [
                "wellbore PENETRATES formation (formation_top: top_md, base_md)",
                "hole_section SECTION_OF wellbore",
                "event OCCURRED_IN wellbore, IN_SECTION hole size, IN_FORMATION formations",
                "event EVIDENCED_BY document page (get_evidence)",
                "document DESCRIBES wellbore",
            ],
            "note": None if counts["event"] else NO_EVENTS_NOTE,
            "attribution": ATTRIBUTION,
        }

    def find_wells(
        self,
        block: str | None = None,
        field: str | None = None,
        operator: str | None = None,
        formation: str | None = None,
        wellbore_type: str | None = None,
        near_wellbore: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_km: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        limit = _clamp(limit)
        where, params = ["true"], []
        origin = self._origin(near_wellbore, lat, lon)
        distance = "null"
        if origin:
            distance = f"haversine_km({origin[0]!r}, {origin[1]!r}, w.lat, w.lon)"
            where.append(f"{distance} <= ?")
            params.append(float(radius_km if radius_km is not None else 20.0))
            if origin[2] is not None:
                where.append("w.wellbore_id <> ?")
                params.append(origin[2])
        if block:
            where.append("w.name_key like ?")
            params.append(_wellbore_key(block) + "%")
        if field:
            where.append("w.field ilike ?")
            params.append(f"%{field.strip()}%")
        if operator:
            where.append("w.operator ilike ?")
            params.append(f"%{operator.strip()}%")
        if wellbore_type:
            where.append("upper(w.wellbore_type) = ?")
            params.append(wellbore_type.strip().upper())
        if formation:
            where.append(
                "exists (select 1 from formation_top f where f.wellbore_id = w.wellbore_id "
                "and upper(f.formation) like ?)"
            )
            params.append(normalise_formation(formation) + "%")
        clause = " and ".join(where)
        total = self._scalar(f"select count(*) from wb w where {clause}", params)
        rows = self._rows(
            f"""
            select w.name, w.wellbore_id, w.wellbore_type, w.purpose, w.operator, w.field,
                   w.completion_date, w.total_depth_md, w.lat, w.lon,
                   round({distance}, 2) as distance_km,
                   (select count(*) from event e where e.wellbore_id = w.wellbore_id) as events
            from wb w where {clause}
            order by distance_km nulls last, w.name
            limit {limit}
            """,
            params,
        )
        return self._page(rows, limit, total)

    def well_summary(self, wellbore: str) -> dict[str, Any]:
        wb = self.resolve_wellbore(wellbore)
        wid = [wb["wellbore_id"]]
        counts = self._rows(
            "select event_type, count(*) n from event where wellbore_id = ? group by 1", wid
        )
        return {
            "wellbore": wb,
            "formation_tops": self._rows(
                "select formation, level, parent, top_md, base_md from formation_top "
                "where wellbore_id = ? order by top_md, level desc, formation",
                wid,
            ),
            "hole_sections": self._rows(
                "select hole_diameter_in, base_md, casing_type, casing_diameter_in, "
                "casing_depth_md, lot_mud_density_sg from hole_section "
                "where wellbore_id = ? order by base_md",
                wid,
            ),
            "event_counts": {r["event_type"]: r["n"] for r in counts},
            "documents": self._rows(
                "select document_id, title, n_pages from document where wellbore_id = ? "
                "order by title",
                wid,
            ),
            "note": None if self.loaded["event"] else NO_EVENTS_NOTE,
        }

    def _event_filter(
        self, event_type, formation, hole_size, wellbore, origin, radius_km,
        include_reference, min_mud_weight_sg, max_mud_weight_sg, mud_weight_kind=None,
    ) -> tuple[str, list[Any], str]:
        where, params = ["true"], []
        distance = "null"
        if event_type:
            kind = event_type.strip().lower().replace(" ", "_")
            if kind not in EVENT_TYPES:
                raise ValueError(
                    f"Unknown event_type {event_type!r}. Use one of: {', '.join(EVENT_TYPES)}."
                )
            where.append("e.event_type = ?")
            params.append(kind)
        if formation:
            where.append(
                "exists (select 1 from unnest(e.formations) as u(unit) "
                "where upper(u.unit) like ?)"
            )
            params.append(normalise_formation(formation) + "%")
        if hole_size is not None:
            where.append("abs(e.hole_diameter_in - ?) < 0.02")
            params.append(parse_hole_size(hole_size))
        if wellbore:
            where.append("e.wellbore_id = ?")
            params.append(self.resolve_wellbore(wellbore)["wellbore_id"])
        if origin:
            distance = f"haversine_km({origin[0]!r}, {origin[1]!r}, w.lat, w.lon)"
            where.append(f"{distance} <= ?")
            params.append(float(radius_km if radius_km is not None else 20.0))
            if origin[2] is not None and not include_reference:
                where.append("e.wellbore_id <> ?")
                params.append(origin[2])
        if mud_weight_kind:
            kind = mud_weight_kind.strip().lower()
            if kind not in MUD_WEIGHT_KINDS:
                raise ValueError(
                    f"Unknown mud_weight_kind {mud_weight_kind!r}. "
                    f"Use one of: {', '.join(MUD_WEIGHT_KINDS)}."
                )
            where.append("e.mud_weight_kind = ?")
            params.append(kind)
        if min_mud_weight_sg is not None:
            where.append("e.mud_weight_sg >= ?")
            params.append(float(min_mud_weight_sg))
        if max_mud_weight_sg is not None:
            where.append("e.mud_weight_sg <= ?")
            params.append(float(max_mud_weight_sg))
        return " and ".join(where), params, distance

    def query_events(
        self,
        event_type: str | None = None,
        formation: str | None = None,
        hole_size: str | float | None = None,
        wellbore: str | None = None,
        near_wellbore: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_km: float | None = None,
        include_reference: bool = False,
        min_mud_weight_sg: float | None = None,
        max_mud_weight_sg: float | None = None,
        mud_weight_kind: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        limit = _clamp(limit)
        origin = self._origin(near_wellbore, lat, lon)
        clause, params, distance = self._event_filter(
            event_type, formation, hole_size, wellbore, origin, radius_km,
            include_reference, min_mud_weight_sg, max_mud_weight_sg, mud_weight_kind,
        )
        base = f"from event e join wb w using (wellbore_id) where {clause}"
        total = self._scalar(f"select count(*) {base}", params)
        rows = self._rows(
            f"""
            select e.event_id, w.name as wellbore, e.event_type, e.top_md, e.base_md,
                   e.depth_ref, e.mud_weight_sg, e.mud_weight_raw, e.mud_weight_kind,
                   e.hole_diameter_in, e.formations, e.description,
                   e.remedy, e.lesson, round({distance}, 2) as distance_km,
                   e.document_id, e.page_no, e.confidence
            {base}
            order by distance_km nulls last, w.name, e.top_md
            limit {limit}
            """,
            params,
        )
        for r in rows:
            r["evidence"] = {
                "document_id": r.pop("document_id"),
                "page_no": r.pop("page_no"),
                "tool": "get_evidence",
            }
        out = self._page(rows, limit, total)
        out["note"] = None if self.loaded["event"] else NO_EVENTS_NOTE
        return out

    def offset_analysis(
        self,
        near_wellbore: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_km: float | None = None,
        formation: str | None = None,
        hole_size: str | float | None = None,
    ) -> dict[str, Any]:
        origin = self._origin(near_wellbore, lat, lon)
        if origin is None:
            raise ValueError("Give near_wellbore, or both lat and lon, to locate the analysis.")
        radius = float(radius_km if radius_km is not None else 20.0)
        distance = f"haversine_km({origin[0]!r}, {origin[1]!r}, w.lat, w.lon)"
        well_params: list[Any] = [radius]
        exclude = ""
        if origin[2] is not None:
            exclude = "and w.wellbore_id <> ?"
            well_params.append(origin[2])
        wells = self._rows(
            f"select w.name, round({distance}, 2) as distance_km, "
            f"(select count(*) from event e where e.wellbore_id = w.wellbore_id) as events "
            f"from wb w where {distance} <= ? {exclude} order by distance_km, w.name",
            well_params,
        )
        clause, params, _ = self._event_filter(
            None, formation, hole_size, None, origin, radius, False, None, None
        )
        static = "coalesce(e.mud_weight_kind, 'unknown') <> 'ecd'"
        summary = self._rows(
            f"""
            select e.event_type, e.hole_diameter_in,
                   count(*) as events, count(distinct e.wellbore_id) as wells,
                   min(e.mud_weight_sg) filter (where {static}) as min_mud_weight_sg,
                   max(e.mud_weight_sg) filter (where {static}) as max_mud_weight_sg,
                   max(e.mud_weight_sg) filter (where e.mud_weight_kind = 'ecd') as max_ecd_sg,
                   round(min(e.top_md), 1) as shallowest_md,
                   round(max(e.base_md), 1) as deepest_md,
                   list(distinct w.name order by w.name) as wellbores,
                   list(e.event_id order by e.event_id) as event_ids
            from event e join wb w using (wellbore_id) where {clause}
            group by 1, 2 order by events desc, e.event_type, e.hole_diameter_in
            """,
            params,
        )
        return {
            "origin": {"lat": origin[0], "lon": origin[1], "wellbore": near_wellbore},
            "radius_km": radius,
            "offset_wells": len(wells),
            "wells": wells[:MAX_LIMIT],
            "summary": summary,
            "lessons": self._rows(
                f"select w.name as wellbore, e.event_id, e.event_type, e.lesson "
                f"from event e join wb w using (wellbore_id) "
                f"where {clause} and e.lesson is not null order by w.name limit 25",
                params,
            ),
            "note": None if self.loaded["event"] else NO_EVENTS_NOTE,
        }

    def evidence(self, event_id: str, include_page_text: bool = False) -> dict[str, Any]:
        rows = self._rows(
            """
            select e.event_id, w.name as wellbore, e.event_type, e.top_md, e.base_md,
                   e.depth_ref, e.mud_weight_sg, e.mud_weight_raw, e.mud_weight_kind,
                   e.text_span, e.page_no, e.confidence,
                   e.document_id, d.title, d.relative_path, d.n_pages,
                   p.summary as page_summary, p.quality as page_quality, p.text as page_text
            from event e join wb w using (wellbore_id)
            left join document d using (document_id)
            left join page p on p.document_id = e.document_id and p.page_no = e.page_no
            where e.event_id = ?
            """,
            [event_id],
        )
        if not rows:
            raise NotFound(f"No event with id {event_id!r}. Get ids from query_events.")
        r = rows[0]
        page_text = r.pop("page_text")
        if include_page_text:
            r["page_text"] = None if page_text is None else page_text[:MAX_PAGE_TEXT_CHARS]
            r["page_text_truncated"] = bool(page_text and len(page_text) > MAX_PAGE_TEXT_CHARS)
        document = {
            "document_id": r.pop("document_id"),
            "title": r.pop("title"),
            "path_in_archive": r.pop("relative_path"),
            "n_pages": r.pop("n_pages"),
        }
        return {
            **r,
            "pdf_page": None if r["page_no"] is None else r["page_no"] + 1,
            "document": document,
            "source": {"doi": SOURCE_DOI, "url": SOURCE_URL, "attribution": ATTRIBUTION},
        }
