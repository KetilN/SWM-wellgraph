"""Decide which report pages are worth an LLM extraction call. No model involved.

A page is scored from keyword evidence for each event type, after look-alike phrases
("fluid loss", "kick-off point", "kill line", "stick-slip" ...) have been blanked out. Tags,
the page summary and the presence of depths and mud weights can raise the score but never
send a page on their own; tags that mark a page as not operations lower it. The router is
recall-oriented: one clear event phrase is enough to send a page.

Numbers measured on the sample reports are in docs/extraction-design.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from wellgraph.extract.units import find_measurements
from wellgraph.graph.schema import EVENT_TYPES

SEND_THRESHOLD = 3.0
TYPE_THRESHOLD = 2.0  # evidence needed before an event type is listed in matched_types
MAX_COUNT_PER_PATTERN = 2  # ten "reamed" on a page are not five times the evidence of two
MAX_SUMMARY_EVIDENCE = 3.0


@dataclass
class RouteResult:
    score: float
    send: bool
    reasons: list[str] = field(default_factory=list)
    matched_types: list[str] = field(default_factory=list)  # subset of EVENT_TYPES
    page_no: int | None = None
    n_chars: int = 0


# --------------------------------------------------------------- look-alikes
# Blanked out (replaced by spaces) before any evidence pattern runs. Text is lower-cased.

_MASKS = [
    # kick: directional drilling, drills, equipment
    r"kick(?:ed|ing|s)?[- ]?offs?\b",
    r"\bkop\b",
    r"kick[- ]?(?:drills?|pads?|lines?|joints?|subs?|tolerances?|margins?)",
    # loss: mud filtrate property, casing tallies, seismic, hydraulics, HSE statistics
    (r"(?:fluid|filtrate|filter|water|api|hthp|hpht|spurt|thread|make[- ]?up|transm\w*\.?|"
    r"pressure|friction|heat|weight|power|signal|circulation)[- ]?loss(?:es)?"),
    r"loss(?:es)?[- ]?(?:cake|on ignition|of signal|of power|of time)",
    r"lost[- ]time",
    r"\blcm\W{0,4}lost circulation materials?",  # glossary entry
    r"lost circulation materials?\W{0,4}\(?lcm\b",
    # kill: BOP plumbing
    r"kill[- /&,]*(?:and[- ]+|or[- ]+)?(?:choke|booster)",
    r"choke[- /&,]*(?:and[- ]+|or[- ]+)?kill",
    r"kill[- ]?(?:lines?|valves?|wing|hoses?|manifold|side)",
    # stuck / fish / jar
    r"stick[- /]?(?:and[- ])?slip|stick[- ]?up",
    r"fish(?:ing)?[- ]?(?:neck|vessels?|boats?|gear|activit\w+|trawl\w*)",
    r"fish[- ](?:teeth|remains|debris|scales?|fragments?|bones?)",
    # well tests and leak-off tests: planned shut-ins
    r"(?:initial|final|second|first|main)[- ]shut[- ]?in",
    (r"shut[- ]?in[- ](?:downhole|down hole|pressures?|times?|periods?|tools?|valves?|"
    r"for (?:the )?(?:initial|main|final|first|second)?\s?build)"),
    (r"well control (?:equipment|drills?|systems?|manuals?|procedures?|school|course|"
    r"certificates?|training)"),
    # wellhead / tools
    (r"pack[- ]?off[- ](?:type|length|seal|assembly|test|bushing|tool|element|spiral|rubber|"
    r"running)"),
    r"(?:spear|seal assembly|wear bushing) and pack[- ]?off",
    r"drag[- ]bits?",
    r"side[- ]?track no\b",  # BHA sheet form field
    # form fields, headings and negatives seen on the samples
    r"nearest well control",
    r"rkb\W{1,4}pack[- ]?off|pack[- ]?off stinger",
    r"jarring services?",
    r"shows, cavings",  # column heading of wellsite sample descriptions
    r"\bno (?:hole|downhole|wellbore stability|hole cleaning) problems?",
    r"\bno (?:mud |fluid |down ?hole )?loss(?:es)?\b",
    (r"\bno (?:indications? of )?(?:overpressured )?shallow gas(?: (?:was|were))?"
    r"(?: (?:encountered|observed|reported|found|seen))?"),
    r"\bno (?:excess\w* |abnormal )?drag\b",
    r"fishing (?:& well abandonment |and well abandonment )?services?|fishing old code",
    (r"gas[- ]tight|very tight[.,]|tow line tight"
    r"|tight (?:test|formation|reservoir|sandstone|sand|clearance|tow|tolerances?)\b"),
]
_MASK_RE = re.compile("|".join(f"(?:{m})" for m in _MASKS))


def _mask(text: str) -> str:
    return _MASK_RE.sub(lambda m: " " * len(m.group(0)), text.lower())


# ------------------------------------------------------------------ evidence
# (event type, label, pattern, weight). Weight 3 = one hit sends the page.

_VOL = r"\d+(?:[.,]\d+)?\s?(?:bbls?|m3|m³|cu\.? ?m\w*|l/min|lpm|bph)"
_EVIDENCE_SPEC: list[tuple[str, str, str, float]] = [
    # lost circulation
    ("lost_circulation", "lost circulation", r"\b(?:lost|loss of|losing) circulation", 3.0),
    ("lost_circulation", "lost returns",
     (r"\b(?:lost|losing|loss of|partial|no|without) (?:full |total |all |mud )?returns?\b"
     r"|\d\s?% returns"), 3.0),
    ("lost_circulation", "LCM", r"\blcm\b", 3.0),
    ("lost_circulation", "mud losses", r"\bmud[- ]?loss(?:es)?\b|\b(?:lost|losing) mud\b", 3.0),
    ("lost_circulation", "losses to formation",
     (r"\bloss(?:es)? (?:in)?to (?:the )?(?:formation|hole|well)\b"
     r"|\b(?:down[- ]?hole|formation|total|partial|severe|seepage|static|dynamic|massive|"
     r"heavy|complete|minor|slight) (?:mud )?loss(?:es)?\b"
     r"|\bloss(?:es)? (?:were|was) (?:observed|experienced|encountered|recorded|seen|noted)"
     r"|\bloss(?:es)? (?:occurred|observed|experienced|encountered)"), 3.0),
    ("lost_circulation", "lost volume",
     (rf"\b(?:lost|losing) (?:a total of |approx\w*\.? |about |some )?{_VOL}"
     rf"|{_VOL} (?:mud )?loss(?:es)?\b|\bloss(?:es)? of {_VOL}"), 3.0),
    ("lost_circulation", "loss", r"\bloss(?:es)?\b", 1.0),
    # kick / well control
    ("kick", "kick", r"\bkick(?:ed|s)?\b", 3.0),
    ("kick", "influx", r"\binflux\b", 3.0),
    ("kick", "pit gain",
     (rf"\bpit gain|\btrip tank gain|\bgain(?:ed)? (?:of )?{_VOL}|{_VOL} (?:pit )?gain\b"
     r"|\bhole gained\b"), 3.0),
    ("kick", "shut-in pressures",
     r"\bsidpp\b|\bsicp\b|\bshut[- ]?in (?:drill ?pipe|casing) pressure", 3.0),
    ("kick", "well control", r"\bwell control\b", 2.0),
    ("kick", "kill",
     (r"\bkill(?:ing)? (?:mud|weight|sheet|fluid)|\bkill(?:ed|ing)? (?:the )?well\b"
     r"|\bwell (?:was |is )?killed\b"), 2.0),
    ("kick", "well flowing",
     r"\bwell (?:was |is )?flowing\b|\bwell flowed\b|\bpositive flow ?check", 2.0),
    ("kick", "shut in well",
     (r"\bshut[- ]?in (?:the )?well\b|\bwell (?:was |is )?shut[- ]?in\b"
     r"|\b(?:closed|shut) (?:the )?(?:upper |lower )?(?:annular|bop|pipe rams?)"), 1.5),
    ("kick", "gas at surface",
     (r"\bgas bubbles\b|\bshallow gas (?:was |were )?(?:encountered|observed|flow\w*|kick)"
     r"|\bgas (?:was |were )?observed (?:at|from) (?:the )?(?:wellhead|seabed|sea ?bed)"), 3.0),
    ("kick", "shallow gas", r"\bshallow gas\b", 0.5),
    # stuck pipe
    ("stuck_pipe", "stuck", r"\bstuck\b", 3.0),
    ("stuck_pipe", "differential sticking", r"\bdifferential(?:ly)? (?:stick|stuck)\w*", 3.0),
    ("stuck_pipe", "jarring",
     r"\bjarr(?:ed|ing)\b|\bjar(?:s)? (?:on|up|down|free|fired)|\b(?:fired|worked) (?:the )?jars?",
     3.0),
    ("stuck_pipe", "back-off",
     r"\bback(?:ed|ing)?[- ]?off\b|\bfree[- ]?point|\bfpit\b|\bstring shot", 2.0),
    ("stuck_pipe", "sticking", r"\bsticking\b|\bpipe (?:got |became )?free\b", 2.0),
    # fishing job, twist-off, lost in hole
    ("fishing", "twist-off",
     r"\btwist(?:ed)?[- ]?off|\b(?:string|pipe|bha) parted\b|\bjunk in (?:the )?hole\b", 3.0),
    ("fishing", "fishing",
     r"\bfishing\b|\bfish\b|\bovershot\b|\bwash(?:ed)?[- ]?over\b|\bjunk basket", 2.5),
    ("fishing", "lost in hole", r"\b(?:lost|left) in (?:the )?hole\b", 1.0),
    # unplanned sidetrack (planned ones are common in development wells: not enough alone)
    ("sidetrack", "forced sidetrack",
     (r"\b(?:technical|unplanned|forced to|had to|decided to|necessary to) side[- ]?track"
     r"|\bside[- ]?track(?:ed|ing)? (?:around|past) (?:the )?(?:fish|junk|obstruction)"
     r"|\bside[- ]?track\w* (?:due to|because of)"), 3.0),
    ("sidetrack", "sidetrack", r"\bside[- ]?track(?:ed|ing|s)?\b", 1.5),
    # tight hole
    ("tight_hole", "tight hole", r"\btight[- ]?(?:hole|spots?)\b", 3.0),
    ("tight_hole", "tight at depth",
     (r"\btight (?:at|from|between|in|on|section|interval|zone)\b"
     r"|\bhole (?:was |is |very |still |became )*tight\b"), 2.5),
    ("tight_hole", "overpull", r"\bover[- ]?pull\w*|\bo'pull", 2.5),
    ("tight_hole", "took weight",
     r"\btook weight\b|\bheld up at\b|\bhung up\b|\bstood up at\b|\bledg(?:e|es|y)\b", 1.5),
    ("tight_hole", "drag", r"\bdrag\b", 1.5),
    ("tight_hole", "sticky hole",
     r"\bhole (?:was |is |very |ledgy |and )*sticky\b|\bsticky (?:hole|spots?|parts?)\b", 1.5),
    ("tight_hole", "tight", r"\btight\b", 1.0),
    ("tight_hole", "reaming", r"\b(?:back)?ream(?:ed|ing)?\b", 0.5),
    ("tight_hole", "could not pass",
     (r"\b(?:not able|unable|impossible) to pass\b"
     r"|\bcould not (?:pass|get past|be worked past)\b"), 3.0),
    # bit / BHA balling (a recall check on not-sent pages found this was missing)
    ("other", "bit balling", r"\bball(?:ed|ing)[- ]?up\b|\bbit[- ]?balling\b|\bballing\b", 3.0),
    # wellbore instability
    ("wellbore_instability", "cavings", r"\bcaving\w*", 3.0),
    ("wellbore_instability", "sloughing", r"\bslough\w*", 3.0),
    ("pack_off", "pack-off", r"\bpack(?:ed|ing|s)?[- ]?off\b", 2.5),
    ("wellbore_instability", "hole collapse",
     (r"\b(?:hole|formation|wellbore|borehole|shale) (?:had |has |was |is )?collaps\w*"
     r"|\bcollaps\w* (?:of the |of )?(?:hole|formation|wellbore|shale)"), 2.5),
    ("washout", "washed-out hole",
     (r"\bhole (?:was |is |had |badly |severely )*wash(?:ed)?[- ]?out"
     r"|\bwash(?:ed)?[- ]?outs? (?:hole|zones?|sections?|intervals?|to \d)"
     r"|\bhole enlarge\w*|\benlarged hole|\bover[- ]?gauge"), 2.5),
    ("wellbore_instability", "unstable hole",
     (r"\bunstable (?:hole|shale|formation|wellbore|clay)"
     r"|\b(?:hole|wellbore|borehole) (?:in)?stability\b"), 2.0),
    ("wellbore_instability", "fill", r"\d\s?m (?:of )?(?:hard |soft )?fill\b", 1.5),
    ("washout", "washout", r"\bwash(?:ed)?[- ]?outs?\b", 1.0),
    # other real trouble
    ("other", "hole problems", r"\bhole (?:problems?|trouble)\b|\bdownhole problems?\b", 2.0),
]
assert {t for t, *_ in _EVIDENCE_SPEC} <= set(EVENT_TYPES)
_EVIDENCE = [(t, label, re.compile(p), w) for t, label, p, w in _EVIDENCE_SPEC]

# --------------------------------------------------------------- tag signals
# Tags are free-form; these are the ones seen on the samples that carry signal.

OPS_TAGS = {
    "drilling", "daily_report", "daily_operations", "daily_log", "daily_activities",
    "operations", "well_operations", "drilling_operations", "rig_operations",
    "operational_summary", "operations_log", "activity_summary", "mud_weight", "mud_density",
    "drilling_progress", "hole_section", "wiper_trip", "time_log", "drilling_summary",
}
EVENT_TAGS = {
    "stuck_pipe", "fishing", "lost_circulation", "losses", "well_control", "kick",
    "tight_hole", "pack_off", "washout", "sidetrack", "twist_off", "npt", "downtime", "lessons_learned", "lessons", "incident",
    "incident_report", "hole_problems", "borehole_stability", "wellbore_stability",
    "hole_cleaning", "experience", "problems", "non_productive_time",
}
NON_OPS_TAGS = {
    "cover_page", "title_page", "document_header", "document_control", "report_index",
    "lithology", "core_description", "core_log", "core", "cores", "core_analysis",
    "core_data", "well_core", "sidewall_core", "sidewall_cores", "conventional_core",
    "conventional_cores", "wellsite_sample", "wellsite_samples", "biostratigraphy",
    "palynology", "micropaleontology", "sedimentology", "petrophysics", "well_log",
    "well_logs", "composite_log", "wireline_log", "wireline_logs", "log_analysis",
    "depth_log", "sonic_log", "density_log", "pvt", "dst", "drill_stem_testing",
    "well_test", "production_test", "vsp", "geophysics", "seismic", "casing_tally",
    "deck_tally", "site_survey", "regional_geology",
}
BLOCK_TAGS = {"table_of_contents", "abbreviations", "glossary", "contents", "nomenclature"}

_BLOCK_HEADING = re.compile(
    r"table of contents|\bcontents\b|list of (?:figures|tables|enclosures|contents)"
    r"|\babbreviations\b|\bglossary\b|\bnomenclature\b"
)
_LEADER_LINE = re.compile(r"\.{4,}\s*\d+\s*$", re.MULTILINE)
_HEADING_WINDOW = 200


def _norm_tag(tag: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(tag).strip().lower())


def _evidence(masked: str, cap: int) -> list[tuple[str, str, int, float]]:
    """(type, label, count, points) for every pattern that hits."""
    hits = []
    for event_type, label, pattern, weight in _EVIDENCE:
        count = len(pattern.findall(masked))
        if count:
            hits.append((event_type, label, count, weight * min(count, cap)))
    return hits


def score_page(text: str, tags: list[str], summary: str) -> RouteResult:
    """Score one page. `send` is True when the score reaches SEND_THRESHOLD."""
    text = text or ""
    summary = summary or ""
    tag_set = {_norm_tag(t) for t in (tags or [])}
    reasons: list[str] = []
    result = RouteResult(score=0.0, send=False, reasons=reasons, n_chars=len(text))

    masked = _mask(text)
    head = masked[:_HEADING_WINDOW]
    if tag_set & BLOCK_TAGS or _BLOCK_HEADING.search(head) or len(_LEADER_LINE.findall(text)) >= 5:
        reasons.append("blocked: table of contents or abbreviations list")
        return result

    type_scores = dict.fromkeys(EVENT_TYPES, 0.0)
    evidence = 0.0
    text_labels = set()
    for event_type, label, count, points in _evidence(masked, MAX_COUNT_PER_PATTERN):
        text_labels.add(label)
        evidence += points
        type_scores[event_type] += points
        reasons.append(f"{event_type}: {label} x{count} (+{points:g})")

    summary_points = 0.0
    for event_type, label, _, points in _evidence(_mask(summary), 1):
        if label in text_labels:  # the summary repeats the page: not extra evidence
            continue
        gain = min(points, MAX_SUMMARY_EVIDENCE - summary_points)
        if gain <= 0:
            break
        summary_points += gain
        type_scores[event_type] += gain
        reasons.append(f"{event_type}: summary mentions {label} (+{gain:g})")
    evidence += summary_points

    if evidence == 0:
        reasons.append("no event wording")
        return result

    boost = 0.0
    event_tags = sorted(tag_set & EVENT_TAGS)
    if event_tags:
        points = min(1.0 * len(event_tags), 2.0)
        boost += points
        reasons.append(f"event tags: {', '.join(event_tags)} (+{points:g})")
    ops_tags = sorted(tag_set & OPS_TAGS)
    if ops_tags:
        points = min(0.5 * len(ops_tags), 1.5)
        boost += points
        reasons.append(f"operations tags: {', '.join(ops_tags)} (+{points:g})")
    kinds = {m.kind for m in find_measurements(text)}
    measured = [k for k in ("depth", "mud_weight") if k in kinds]
    if measured:
        points = 0.5 * len(measured)
        boost += points
        reasons.append(f"{' and '.join(measured).replace('_', ' ')} on page (+{points:g})")
    if boost > evidence:  # hints can at most double the evidence, never carry a page
        reasons.append(f"boost limited to evidence ({boost:g} -> {evidence:g})")
        boost = evidence

    penalty = 0.0
    non_ops = sorted(tag_set & NON_OPS_TAGS)
    if non_ops:
        penalty = min(1.5 * len(non_ops), 3.0)
        if ops_tags or event_tags:
            penalty /= 2
        reasons.append(f"non-operations tags: {', '.join(non_ops)} (-{penalty:g})")

    result.score = round(max(0.0, evidence + boost - penalty), 2)
    result.send = result.score >= SEND_THRESHOLD
    result.matched_types = [t for t in EVENT_TYPES if type_scores[t] >= TYPE_THRESHOLD]
    return result


def page_fields(page: Mapping[str, Any]) -> tuple[str, list[str], str]:
    """(text, tags, summary) of a report-JSON page, tolerating missing parts."""
    ocr = page.get("OCR") or {}
    ai = page.get("AI") or {}
    return (ocr.get("ocr_markdown") or "", list(ai.get("tags") or []),
            ai.get("page_summary") or "")


def route_report(
    pages: Iterable[Mapping[str, Any]], max_pages: int | None = None
) -> list[RouteResult]:
    """One RouteResult per page, in input order.

    With `max_pages`, only the highest-scoring pages keep send=True (ties go to the earlier
    page); the others keep their score and get a reason saying the cap dropped them.
    """
    results = []
    for index, page in enumerate(pages):
        result = score_page(*page_fields(page))
        result.page_no = page.get("page_no", index)
        results.append(result)
    if max_pages is not None:
        sent = [i for i, r in enumerate(results) if r.send]
        keep = set(sorted(sent, key=lambda i: (-results[i].score, i))[: max(0, max_pages)])
        for i in sent:
            if i not in keep:
                r = results[i]
                results[i] = replace(
                    r, send=False, reasons=[*r.reasons, f"dropped by cap of {max_pages} pages"]
                )
    return results
