"""Small web app on the same server as the MCP endpoint.

`/` serves a single page; `/api/*` returns JSON from the same read-only GraphStore the MCP
tools use, so the page and an agent always see the same data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from wellgraph.graph.store import GraphStore, NotFound

INDEX = Path(__file__).parent / "static" / "index.html"
TRUE = {"1", "true", "yes", "on"}


def _json(payload: Any, status: int = 200) -> Response:
    return Response(json.dumps(payload, default=str), status_code=status,
                    media_type="application/json")


def _float(request: Request, name: str) -> float | None:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}.") from exc


def _text(request: Request, name: str) -> str | None:
    return request.query_params.get(name) or None


def _flag(request: Request, name: str) -> bool:
    return (request.query_params.get(name) or "").lower() in TRUE


def _limit(request: Request) -> int | None:
    value = _float(request, "limit")
    return None if value is None else int(value)


def register(mcp: FastMCP, store: GraphStore) -> None:
    def guarded(handler):
        async def wrapped(request: Request) -> Response:
            try:
                return _json(handler(request))
            except NotFound as exc:
                return _json({"error": str(exc)}, 404)
            except (ValueError, KeyError) as exc:
                return _json({"error": str(exc)}, 400)
        return wrapped

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def index(request: Request) -> Response:
        return HTMLResponse(INDEX.read_text(encoding="utf-8"))

    def describe(request: Request):
        return store.describe()

    def report_wells(request: Request):
        return {"results": store.report_wells()}

    def wells(request: Request):
        near = _text(request, "near")
        out = store.find_wells(
            block=_text(request, "block"), field=_text(request, "field"),
            operator=_text(request, "operator"), formation=_text(request, "formation"),
            wellbore_type=_text(request, "wellbore_type"), near_wellbore=near,
            radius_km=_float(request, "radius_km"), limit=_limit(request),
        )
        if near:
            ref = store.resolve_wellbore(near)
            out["origin"] = {"name": ref["name"], "lat": ref["lat"], "lon": ref["lon"]}
        return out

    def well(request: Request):
        name = _text(request, "name")
        if not name:
            raise ValueError("Give a wellbore name: /api/well?name=15/9-19 A")
        summary = store.well_summary(name)
        resolved = summary["wellbore"]["name"]
        summary["mud_checks"] = store.mud_profile(resolved)
        summary["events"] = store.query_events(wellbore=resolved, limit=200)["results"]
        return summary

    def events(request: Request):
        return store.query_events(
            event_type=_text(request, "event_type"), formation=_text(request, "formation"),
            hole_size=_text(request, "hole_size"), wellbore=_text(request, "wellbore"),
            near_wellbore=_text(request, "near"), radius_km=_float(request, "radius_km"),
            include_reference=_flag(request, "include_reference"),
            min_mud_weight_sg=_float(request, "min_mud_weight_sg"),
            max_mud_weight_sg=_float(request, "max_mud_weight_sg"),
            mud_weight_kind=_text(request, "mud_weight_kind"),
            exact_depth_only=_flag(request, "exact_depth_only"), limit=_limit(request),
        )

    def offset(request: Request):
        return store.offset_analysis(
            near_wellbore=_text(request, "near"), lat=_float(request, "lat"),
            lon=_float(request, "lon"), radius_km=_float(request, "radius_km"),
            formation=_text(request, "formation"), hole_size=_text(request, "hole_size"),
        )

    def evidence(request: Request):
        event_id = _text(request, "id")
        if not event_id:
            raise ValueError("Give an event id: /api/evidence?id=...")
        return store.evidence(event_id, include_page_text=_flag(request, "page_text"))

    for path, handler in {
        "/api/describe": describe, "/api/report_wells": report_wells, "/api/wells": wells,
        "/api/well": well, "/api/events": events, "/api/offset": offset,
        "/api/evidence": evidence,
    }.items():
        mcp.custom_route(path, methods=["GET"], include_in_schema=False)(guarded(handler))
