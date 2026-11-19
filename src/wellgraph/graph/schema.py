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
        "wellbore_type": "VARCHAR",  # EXPLORATION | DEVELOPMENT | OTHER
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
        "level": "VARCHAR",  # GROUP | FORMATION | MEMBER
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
    },
}

EVENT_TYPES = [
    "lost_circulation",
    "kick",
    "stuck_pipe",
    "tight_hole",
    "wellbore_instability",
    "other",
]

SOURCE_DOI = "10.5281/zenodo.24320092"
SOURCE_URL = "https://zenodo.org/records/24320092"
ATTRIBUTION = (
    "Reports: ConocoPhillips / Diskos, Zenodo record 24320092, CC BY 4.0. "
    "Backbone: SODIR FactPages (NLOD 2.0)."
)
