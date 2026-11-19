"""What the LLM returns per page, how it is checked against the page, and the event row.

The model fills `ExtractedEvent`; it never states hole section or formation. Those arrive in
`to_event_row` from the depth joins in `wellgraph.resolve.depth`.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rapidfuzz import fuzz

from wellgraph.extract.units import (
    DEPTH_RANGE_M,
    MUD_WEIGHT_RANGE_SG,
    find_measurements,
    parse_hole_size_in,
    parse_mud_weight,
)
from wellgraph.graph.schema import DEPTH_REFS, EVENT_TYPES, MUD_WEIGHT_KINDS, TABLES

ASSUMED_MD = "ASSUMED_MD"
UNKNOWN_KIND = "unknown"
assert ASSUMED_MD in DEPTH_REFS and UNKNOWN_KIND in MUD_WEIGHT_KINDS

EventType = Literal[tuple(EVENT_TYPES)]  # type: ignore[valid-type]
# The model may only report a reference the text states; ASSUMED_MD is set in code.
StatedDepthRef = Literal[tuple(r for r in DEPTH_REFS if r != ASSUMED_MD)]  # type: ignore[valid-type]
MudWeightKind = Literal[tuple(MUD_WEIGHT_KINDS)]  # type: ignore[valid-type]

QUOTE_MATCH_THRESHOLD = 90.0  # rapidfuzz partial_ratio, 0-100
DEPTH_TOLERANCE_M = 0.5
MUD_WEIGHT_TOLERANCE_SG = 0.011  # covers ppg -> sg rounded to two decimals
BARE_MUD_WEIGHT_TOLERANCE_SG = 0.005


class ExtractedEvent(BaseModel):
    """One drilling event that happened in this well, as stated on one page."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: EventType = Field(
        description="Kind of event. Use 'other' only for a real drilling problem that fits "
        "none of the specific types."
    )
    top_md: float | None = Field(
        default=None,
        gt=DEPTH_RANGE_M[0],
        le=DEPTH_RANGE_M[1],
        description="Shallowest measured depth of the event in metres, as written on the "
        "page. null if the page gives no depth for it.",
    )
    base_md: float | None = Field(
        default=None,
        gt=DEPTH_RANGE_M[0],
        le=DEPTH_RANGE_M[1],
        description="Deepest measured depth of the event in metres. Equal to top_md for an "
        "event at a single depth. null if the page gives no depth.",
    )
    depth_ref: StatedDepthRef | None = Field(
        default=None,
        description="Depth reference, only if the page states it for this depth: MD "
        "(measured depth, e.g. 'mMD', 'm MD RKB'), TVD or TVDSS. null if no reference is "
        "written (the usual case).",
    )
    context_top_md: float | None = Field(
        default=None,
        gt=DEPTH_RANGE_M[0],
        le=DEPTH_RANGE_M[1],
        description="Only when top_md is null: the depth the page gives as context for the "
        "event, in metres. For a single context depth (the depth in the preceding sentences, "
        "or the depth column of the daily-report row) set context_top_md and context_base_md "
        "to it. For an interval (the section or interval the paragraph or heading says is "
        "being drilled or described) give its shallow end here. null if the page gives none.",
    )
    context_base_md: float | None = Field(
        default=None,
        gt=DEPTH_RANGE_M[0],
        le=DEPTH_RANGE_M[1],
        description="Deep end of the context interval, or the same value as context_top_md "
        "for a single context depth. null if the page gives none.",
    )
    hole_size_stated: str | None = Field(
        default=None,
        description="Hole size of the section the event is in, exactly as the page writes it "
        "in the heading or sentence, e.g. '17 1/2\"' or '12 1/4 in'. null unless the page "
        "states it. Never inferred.",
    )
    mud_weight_raw: str | None = Field(
        default=None,
        description="Mud weight at the event exactly as written on the page, with its unit, "
        "e.g. '10.5 ppg' or '1,35 sg'. null if the page does not state it. Never a leak-off "
        "or FIT value, a pore pressure or a cement slurry density.",
    )
    mud_weight_sg: float | None = Field(
        default=None,
        ge=MUD_WEIGHT_RANGE_SG[0],
        le=MUD_WEIGHT_RANGE_SG[1],
        description="The same mud weight converted to specific gravity (0.8-2.6; "
        "ppg / 8.345, kg/m3 / 1000). null if mud_weight_raw is null.",
    )
    mud_weight_kind: MudWeightKind | None = Field(
        default=None,
        description="What the mud weight is: mud_in_hole (static mud weight in use), ecd "
        "(equivalent circulating density) or unknown. null if mud_weight_raw is null.",
    )
    description: str = Field(
        min_length=1, description="What happened, one or two sentences, facts from the page."
    )
    remedy: str | None = Field(
        default=None, description="What was done about it, if the page says. Otherwise null."
    )
    lesson: str | None = Field(
        default=None,
        description="Lesson or recommendation the report itself draws. Otherwise null.",
    )
    text_span: str = Field(
        min_length=1,
        description="Verbatim quote from the page that states the event, copied exactly.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="0-1: how sure you are this is a real event in this well."
    )

    @model_validator(mode="before")
    @classmethod
    def _tolerate_common_slips(cls, data: Any) -> Any:
        """Repair what models get wrong often, instead of losing the whole page.

        Seen in real replies: `description` left out (the quote says it all), and
        `confidence` given as a word.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not str(data.get("description") or "").strip() and data.get("text_span"):
            data["description"] = data["text_span"]
        words = {"low": 0.3, "medium": 0.6, "moderate": 0.6, "high": 0.9}
        if isinstance(data.get("confidence"), str):
            data["confidence"] = words.get(data["confidence"].strip().lower(), data["confidence"])
        return data

    @field_validator("remedy", "lesson", "mud_weight_raw", "hole_size_stated", mode="after")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _depth_interval(self) -> ExtractedEvent:
        if self.top_md is None and self.base_md is not None:
            self.top_md = self.base_md
        elif self.base_md is None and self.top_md is not None:
            self.base_md = self.top_md
        if self.top_md is not None and self.base_md < self.top_md:
            # Reports give intervals in the direction of travel ("tight at 1121-1110" while
            # pulling out), so a reversed pair is the same interval, not an error.
            self.top_md, self.base_md = self.base_md, self.top_md
        # Context depth: same tidy-up, and it is only kept when there is no stated depth.
        if self.top_md is not None:
            self.context_top_md = self.context_base_md = None
        elif self.context_top_md is None and self.context_base_md is not None:
            self.context_top_md = self.context_base_md
        elif self.context_base_md is None and self.context_top_md is not None:
            self.context_base_md = self.context_top_md
        if self.context_top_md is not None and self.context_base_md < self.context_top_md:
            self.context_top_md, self.context_base_md = self.context_base_md, self.context_top_md
        return self

    # ---- values decided in code, not by the model

    @property
    def resolved_mud_weight_sg(self) -> float | None:
        """Mud weight in sg: parsed from mud_weight_raw when it parses, else the model's."""
        parsed = parse_mud_weight(self.mud_weight_raw) if self.mud_weight_raw else None
        value = parsed if parsed is not None else self.mud_weight_sg
        return None if value is None else round(value, 3)

    @property
    def resolved_depth_basis(self) -> str:
        """stated, context_point, context_interval or none (see schema.yaml depth_bases)."""
        if self.top_md is not None:
            return "stated"
        if self.context_top_md is None:
            return "none"
        return "context_point" if self.context_top_md == self.context_base_md else "context_interval"

    @property
    def join_interval(self) -> tuple[float | None, float | None]:
        """Interval to use for depth joins: the stated depth, else the context depth."""
        if self.top_md is not None:
            return self.top_md, self.base_md
        return self.context_top_md, self.context_base_md

    @property
    def stated_hole_diameter_in(self) -> float | None:
        return parse_hole_size_in(self.hole_size_stated) if self.hole_size_stated else None

    @property
    def resolved_depth_ref(self) -> str | None:
        """Stated reference, ASSUMED_MD when a depth has none, None when there is no depth."""
        if self.top_md is None and self.context_top_md is None:
            return None
        return self.depth_ref or ASSUMED_MD

    @property
    def resolved_mud_weight_kind(self) -> str | None:
        if self.resolved_mud_weight_sg is None:
            return None
        return self.mud_weight_kind or UNKNOWN_KIND


class PageExtraction(BaseModel):
    """All events on one page. An empty list is the normal answer for most pages."""

    model_config = ConfigDict(extra="forbid")

    events: list[ExtractedEvent] = Field(
        default_factory=list, description="Events on this page; empty if there are none."
    )


# ------------------------------------------------------ check against the page

_TABLE_MARKUP = re.compile(r"[|*#]+")
_WHITESPACE = re.compile(r"\s+")
_NUMBER_TOKEN = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", _TABLE_MARKUP.sub(" ", text)).strip().casefold()


def _bare_numbers(text: str) -> set[float]:
    """Every number on the page under both readings of a comma (decimal and thousands)."""
    out: set[float] = set()
    for token in _NUMBER_TOKEN.findall(text):
        readings = [token.replace(",", ".")]
        if _THOUSANDS.match(token):
            readings.append(token.replace(",", ""))
        for reading in readings:
            try:
                out.add(float(reading))
            except ValueError:  # "84.11.30" (a date)
                pass
    return out


def _near(value: float, candidates, tolerance: float) -> bool:
    return any(abs(value - c) <= tolerance for c in candidates)


def validate_against_page(event: ExtractedEvent, page_text: str) -> list[str]:
    """Problems that suggest the event is not supported by the page. Empty list = clean.

    Checks: the quote is on the page (whitespace and table markup ignored, small OCR
    differences allowed); each stated depth and the mud weight appear somewhere on the page,
    either as a recognised measurement (unit conversion and decimal comma handled) or as a
    bare number (table cells and "raise weight from 1.15 to 1.20" carry no unit).
    """
    problems: list[str] = []
    page = page_text or ""

    quote, haystack = _normalise(event.text_span), _normalise(page)
    if quote not in haystack:
        score = fuzz.partial_ratio(quote, haystack)
        if score < QUOTE_MATCH_THRESHOLD:
            problems.append(f"text_span_not_found: best partial match {score:.0f}/100")

    measurements = find_measurements(page)
    bare = _bare_numbers(page)

    depths = [m.value for m in measurements if m.kind == "depth"]
    for name in ("top_md", "base_md", "context_top_md", "context_base_md"):
        value = getattr(event, name)
        if value is None:
            continue
        if not (_near(value, depths, DEPTH_TOLERANCE_M) or _near(value, bare, DEPTH_TOLERANCE_M)):
            problems.append(f"{name}_not_on_page: {value:g} m")

    if event.mud_weight_raw and _normalise(event.mud_weight_raw) not in haystack:
        problems.append(f"mud_weight_raw_not_on_page: {event.mud_weight_raw!r}")
    value = event.resolved_mud_weight_sg
    if (
        value is not None
        and event.mud_weight_sg is not None
        and abs(value - event.mud_weight_sg) > MUD_WEIGHT_TOLERANCE_SG
    ):
        problems.append(
            f"mud_weight_mismatch: {event.mud_weight_raw!r} is {value:.3g} sg, "
            f"model said {event.mud_weight_sg:g} sg (the parsed value is used)"
        )
    if value is not None:
        weights = [m.value for m in measurements if m.kind == "mud_weight"]
        if not (
            _near(value, weights, MUD_WEIGHT_TOLERANCE_SG)
            or _near(value, bare, BARE_MUD_WEIGHT_TOLERANCE_SG)
        ):
            problems.append(f"mud_weight_not_on_page: {value:g} sg")
    return problems


# ------------------------------------------------------------------ event row


def _depth_key(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def event_id(
    document_id: str, page_no: int, event_type: str, top_md: float | None,
    base_md: float | None, text_span: str | None = None,
) -> str:
    """Deterministic id: same document, page, type and depths -> same id on every run.

    An event without a stated depth is told apart by its quote instead; otherwise two such
    events of one type on one page would share an id and one would be lost.
    """
    parts = [str(document_id), str(page_no), event_type, _depth_key(top_md), _depth_key(base_md)]
    if top_md is None and text_span:
        parts.append(" ".join(text_span.split()))
    key = "|".join(parts)
    return "ev-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def to_event_row(
    event: ExtractedEvent,
    wellbore_id: int,
    document_id: str,
    page_no: int,
    hole_diameter_in: float | None,
    formations: list[str] | None,
) -> dict:
    """Row for the `event` table: exactly the columns of TABLES["event"], in order.

    `hole_diameter_in` and `formations` come from the depth joins (see
    `wellgraph.resolve.depth.joins_for_event`). The column list is read from TABLES at call
    time; a column this function does not know raises KeyError instead of drifting silently.
    """
    row = {
        "event_id": event_id(document_id, page_no, event.event_type, event.top_md,
                             event.base_md, event.text_span),
        "wellbore_id": wellbore_id,
        "event_type": event.event_type,
        "top_md": event.top_md,
        "base_md": event.base_md,
        "mud_weight_sg": event.resolved_mud_weight_sg,
        "hole_diameter_in": hole_diameter_in,
        "formations": list(formations or []),
        "description": event.description,
        "remedy": event.remedy,
        "lesson": event.lesson,
        "document_id": document_id,
        "page_no": page_no,
        "text_span": event.text_span,
        "confidence": event.confidence,
        "depth_ref": event.resolved_depth_ref,
        "mud_weight_raw": event.mud_weight_raw,
        "mud_weight_kind": event.resolved_mud_weight_kind,
        "mud_weight_source": "report" if event.resolved_mud_weight_sg is not None else None,
        "depth_basis": event.resolved_depth_basis,
        "context_top_md": event.context_top_md,
        "context_base_md": event.context_base_md,
    }
    return {column: row[column] for column in TABLES["event"]}
