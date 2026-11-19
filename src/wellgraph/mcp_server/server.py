"""MCP server exposing the wellgraph knowledge graph. All tools are read-only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from wellgraph.graph.schema import ATTRIBUTION, EVENT_TYPES
from wellgraph.graph.store import DEFAULT_LIMIT, MAX_LIMIT, GraphStore, NotFound

INSTRUCTIONS = f"""\
wellgraph is a knowledge graph of Norwegian continental shelf wellbores. It combines the
public SODIR backbone (wellbores, coordinates, operators, formation tops, hole sections)
with drilling events (lost circulation, kicks, stuck pipe, tight hole, instability)
extracted from public end-of-well reports. Every event links to the report page it came
from.

How to use it:
1. Call describe_graph first. It returns what is loaded, the area covered, units, the
   event types and example questions. Coverage is a slice, not the whole shelf.
2. Use find_wells to locate wellbores by block, field, operator, formation or distance.
3. Use query_events for cross-well questions (event type + formation + hole size +
   distance + mud weight). Use offset_well_analysis for an aggregated view around a
   wellbore or a planned location.
4. Use get_well_summary for one wellbore's formations, hole sections and events.
5. Before stating an event as fact, call get_evidence with its event_id and cite the
   report title and page. Quote the text span; do not paraphrase numbers.

Conventions: depths are metres measured depth, mud weight is specific gravity (sg), hole
sizes are inches ('12 1/4' or 12.25 both work), distances are km. Wellbore names follow
SODIR, e.g. '15/9-19 A'; '15_9-19_A' and '15/9-19A' are accepted. Formation names follow
SODIR lithostratigraphy; 'Shetland Group' and 'SHETLAND GP' both work.

If a result is empty, say so: absence of an event in the graph does not prove it did not
happen, only that it was not extracted from the available reports.

{ATTRIBUTION}
"""

EXAMPLE_QUESTIONS = [
    (
        "Which wells within 20 km of <wellbore> had losses in the Shetland Group in the "
        "12 1/4 in section, and at what mud weight?"
    ),
    "What drilling problems did offset wells have in the 8 1/2 in section near <wellbore>?",
    "Which wellbores operated by <operator> penetrate the <formation>?",
    "Summarise <wellbore>: formations, casing points and reported events, with citations.",
    "What lessons learned were recorded for kicks within 30 km of <lat, lon>?",
]

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

Wellbore = Annotated[str, Field(description="Wellbore name, e.g. '15/9-19 A'.")]
NearWellbore = Annotated[
    str | None, Field(description="Reference wellbore to measure distance from.")
]
Lat = Annotated[float | None, Field(description="Latitude, decimal degrees (with lon).", ge=-90, le=90)]
Lon = Annotated[float | None, Field(description="Longitude, decimal degrees (with lat).", ge=-180, le=180)]
Radius = Annotated[
    float | None, Field(description="Search radius in km. Default 20.", gt=0, le=500)
]
Formation = Annotated[
    str | None,
    Field(description="Lithostratigraphic unit, e.g. 'Shetland Group', 'TOR FM'. Prefix match."),
]
HoleSize = Annotated[
    str | float | None, Field(description="Hole size in inches, e.g. '12 1/4' or 12.25.")
]
Limit = Annotated[
    int | None, Field(description=f"Max rows. Default {DEFAULT_LIMIT}, capped at {MAX_LIMIT}.")
]
EventType = Annotated[
    str | None, Field(description="One of: " + ", ".join(EVENT_TYPES) + ".")
]


def default_kg_dir() -> Path:
    return Path(os.environ.get("WELLGRAPH_KG_DIR", "data/kg"))


def build_server(kg_dir: str | Path | None = None) -> FastMCP:
    store = GraphStore(kg_dir if kg_dir is not None else default_kg_dir())
    mcp = FastMCP(name="wellgraph", instructions=INSTRUCTIONS)

    def guarded(fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Turn bad input into a message the agent can act on, never a stack trace."""
        try:
            return fn(*args, **kwargs)
        except (NotFound, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    def describe_graph() -> dict[str, Any]:
        """Start here. Returns what the graph contains: row counts, which tables are
        loaded, the area and blocks covered, event types, units, the entity/relationship
        schema, example questions and data attribution."""
        out = guarded(store.describe)
        out["example_questions"] = EXAMPLE_QUESTIONS
        return out

    @mcp.tool(annotations=READ_ONLY)
    def find_wells(
        block: Annotated[
            str | None, Field(description="Block or name prefix, e.g. '15/9' or '15/9-F'.")
        ] = None,
        field: Annotated[str | None, Field(description="Field name, partial match.")] = None,
        operator: Annotated[
            str | None, Field(description="Drilling operator, partial match.")
        ] = None,
        formation: Formation = None,
        wellbore_type: Annotated[
            str | None, Field(description="EXPLORATION or DEVELOPMENT.")
        ] = None,
        near_wellbore: NearWellbore = None,
        lat: Lat = None,
        lon: Lon = None,
        radius_km: Radius = None,
        limit: Limit = None,
    ) -> dict[str, Any]:
        """Find wellbores by block, field, operator, penetrated formation, type, and/or
        distance from a reference wellbore or coordinate. Filters combine with AND.
        Returns name, operator, field, total depth, coordinates, distance_km (when a
        location is given) and how many events each wellbore has. The reference wellbore
        itself is excluded from distance searches."""
        return guarded(
            store.find_wells, block=block, field=field, operator=operator,
            formation=formation, wellbore_type=wellbore_type, near_wellbore=near_wellbore,
            lat=lat, lon=lon, radius_km=radius_km, limit=limit,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_well_summary(wellbore: Wellbore) -> dict[str, Any]:
        """Everything known about one wellbore: header data (operator, field, dates,
        depth, coordinates), formation tops with depths, hole sections with casing and
        leak-off values, event counts by type, and the source reports. Use query_events
        with wellbore=... to list the events themselves."""
        return guarded(store.well_summary, wellbore)

    @mcp.tool(annotations=READ_ONLY)
    def query_events(
        event_type: EventType = None,
        formation: Formation = None,
        hole_size: HoleSize = None,
        wellbore: Annotated[
            str | None, Field(description="Only events in this wellbore.")
        ] = None,
        near_wellbore: NearWellbore = None,
        lat: Lat = None,
        lon: Lon = None,
        radius_km: Radius = None,
        include_reference: Annotated[
            bool, Field(description="Also return events in near_wellbore itself.")
        ] = False,
        min_mud_weight_sg: Annotated[float | None, Field(description="Minimum mud weight, sg.")] = None,
        max_mud_weight_sg: Annotated[float | None, Field(description="Maximum mud weight, sg.")] = None,
        limit: Limit = None,
    ) -> dict[str, Any]:
        """List drilling events across wells, filtered by any combination of event type,
        formation (matches the unit or any parent/child unit at that depth), hole size,
        wellbore, distance, and mud weight range. This is the tool for cross-well
        questions such as 'losses in the Shetland Group in the 12 1/4 in section within
        20 km, and at what mud weight'. Each row has depth interval, mud weight, hole
        size, formations, description, remedy, lesson and an event_id for get_evidence."""
        return guarded(
            store.query_events, event_type=event_type, formation=formation,
            hole_size=hole_size, wellbore=wellbore, near_wellbore=near_wellbore,
            lat=lat, lon=lon, radius_km=radius_km, include_reference=include_reference,
            min_mud_weight_sg=min_mud_weight_sg, max_mud_weight_sg=max_mud_weight_sg,
            limit=limit,
        )

    @mcp.tool(annotations=READ_ONLY)
    def offset_well_analysis(
        near_wellbore: NearWellbore = None,
        lat: Lat = None,
        lon: Lon = None,
        radius_km: Radius = None,
        formation: Formation = None,
        hole_size: HoleSize = None,
    ) -> dict[str, Any]:
        """Aggregate what happened in offset wells around a wellbore or a planned
        location (lat/lon). Returns the offset wells with distances, then events grouped
        by event type and hole size with counts, number of wells affected, mud weight
        range, depth range, the wellbores involved and event_ids, plus recorded lessons
        learned. Optionally restrict to one formation or hole size."""
        return guarded(
            store.offset_analysis, near_wellbore=near_wellbore, lat=lat, lon=lon,
            radius_km=radius_km, formation=formation, hole_size=hole_size,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_evidence(
        event_id: Annotated[str, Field(description="event_id from query_events.")],
    ) -> dict[str, Any]:
        """Return the source of one event: the verbatim text span, report title, page
        (page_no is 0-based; pdf_page is the page to open), path of the report inside the
        Zenodo archive, extraction confidence, and the DOI to cite."""
        return guarded(store.evidence, event_id)

    return mcp
