"""Table contract between the graph build and everything that reads the graph.

PROVISIONAL: the ontology is not agreed yet. This is the minimum the MCP server needs.
The published graph is a folder of Parquet files, one per table, named `<table>.parquet`.
A missing file is allowed (the table is treated as empty), so the server works on the
backbone alone before any events are extracted.

OSDU kinds are noted for reference; column names are kept short and snake_case.
"""

from __future__ import annotations

# table -> {column: DuckDB type}
TABLES: dict[str, dict[str, str]] = {
    # osdu:wks:master-data--Wellbore (source: SODIR FactPages wellbore tables)
    "wellbore": {
        "wellbore_id": "BIGINT",  # NPD wellbore id (wlbNpdidWellbore)
        "name": "VARCHAR",  # e.g. "15/9-19 A"
        "well_name": "VARCHAR",
        "wellbore_type": "VARCHAR",  # EXPLORATION | DEVELOPMENT | OTHER (shallow, soil, scientific)
        "purpose": "VARCHAR",
        "operator": "VARCHAR",
        "field": "VARCHAR",
        "drilling_facility": "VARCHAR",
        "entry_date": "DATE",
        "completion_date": "DATE",
        "total_depth_md": "DOUBLE",  # metres
        "water_depth": "DOUBLE",  # metres
        "lat": "DOUBLE",  # decimal degrees
        "lon": "DOUBLE",
        "factpage_url": "VARCHAR",
    },
    # Wellbore -PENETRATES-> GeologicalFormation (osdu WellboreMarkerSet; SODIR formation tops)
    "formation_top": {
        "wellbore_id": "BIGINT",
        "formation": "VARCHAR",  # SODIR unit name, e.g. "SHETLAND GP"
        "level": "VARCHAR",  # GROUP | SUBGROUP | FORMATION | MEMBER
        "parent": "VARCHAR",
        "top_md": "DOUBLE",
        "base_md": "DOUBLE",
    },
    # osdu:wks:master-data--HoleSection + TubularAssembly (SODIR casing and LOT)
    "hole_section": {
        "wellbore_id": "BIGINT",
        "hole_diameter_in": "DOUBLE",
        "base_md": "DOUBLE",
        "casing_type": "VARCHAR",
        "casing_diameter_in": "DOUBLE",
        "casing_depth_md": "DOUBLE",
        "lot_mud_density_sg": "DOUBLE",
    },
    # osdu:wks:work-product-component--Document (Diskos CSV + report JSON metadata)
    "document": {
        "document_id": "VARCHAR",
        "wellbore_id": "BIGINT",
        "title": "VARCHAR",
        "relative_path": "VARCHAR",  # path inside the Zenodo archive
        "n_pages": "INTEGER",
    },
    # Report page text, so an event's source page can be shown (local type, no OSDU kind)
    "page": {
        "document_id": "VARCHAR",
        "page_no": "INTEGER",  # 0-based, as in the report JSON
        "text": "VARCHAR",  # OCR markdown of the page
        "summary": "VARCHAR",
        "quality": "VARCHAR",  # Reliable | Acceptable | Uncertain | Figure-dominated
    },
    # osdu:wks:master-data--Risk, RiskType "event" (LLM-extracted, depth-joined)
    "event": {
        "event_id": "VARCHAR",
        "wellbore_id": "BIGINT",
        "event_type": "VARCHAR",  # see EVENT_TYPES
        "top_md": "DOUBLE",
        "base_md": "DOUBLE",
        "mud_weight_sg": "DOUBLE",
        "hole_diameter_in": "DOUBLE",  # from depth join against hole_section
        "formations": "VARCHAR[]",  # every unit (any level) containing the depth
        "description": "VARCHAR",
        "remedy": "VARCHAR",
        "lesson": "VARCHAR",
        "document_id": "VARCHAR",
        "page_no": "INTEGER",  # 0-based, as in the report JSON
        "text_span": "VARCHAR",  # verbatim quote from the page
        "confidence": "DOUBLE",
        "depth_ref": "VARCHAR",  # see DEPTH_REFS; hole size / formations only joined for MD
        "mud_weight_raw": "VARCHAR",  # as written in the report, e.g. "10.5 ppg"
        "mud_weight_kind": "VARCHAR",  # see MUD_WEIGHT_KINDS
    },
}

EVENT_TYPES = [
    "lost_circulation",
    "kick",
    "stuck_pipe",
    "tight_hole",
    "wellbore_instability",
    "pack_off",  # hole packing off around the string (not the wellhead pack-off seal)
    "washout",
    "fishing",  # fishing job, twist-off, lost-in-hole
    "sidetrack",  # unplanned sidetrack forced by a hole problem
    "other",
]

# What an event depth is measured along. Most report depths carry no reference at all.
DEPTH_REFS = [
    "MD",  # stated as measured depth (mMD, m MD RT, m RKB along hole)
    "TVD",  # stated as true vertical depth: do not join against MD tables
    "TVDSS",  # true vertical depth subsea: do not join against MD tables
    "ASSUMED_MD",  # no reference written; treated as measured depth
]

# What the mud weight on an event refers to. Leak-off, pore pressure and cement slurry
# densities are not event mud weights and must not be stored here.
MUD_WEIGHT_KINDS = [
    "mud_in_hole",  # static mud weight in use when the event happened
    "ecd",  # equivalent circulating density
    "unknown",
]

SOURCE_DOI = "10.5281/zenodo.24320092"
SOURCE_URL = "https://zenodo.org/records/24320092"
ATTRIBUTION = (
    "Reports: ConocoPhillips / Diskos, Zenodo record 24320092, CC BY 4.0. "
    "Backbone: SODIR FactPages (NLOD 2.0)."
)
