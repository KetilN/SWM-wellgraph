"""Unit normalisation for depths, mud weights and hole sizes as written in the reports.

Pure functions, no model calls. Everything is normalised to: depth in metres, mud weight in
specific gravity (sg), hole size in inches. `find_measurements` returns every mention with
character offsets so the same code serves routing (does this page talk about depths and mud
weights?) and validation (is the number the LLM returned really on the page?).

Conventions decided here (see docs/extraction-design.md):
- A comma followed by exactly three digits is a thousands separator in a depth ("2,926 m")
  and in kg/m3; any other comma is a decimal comma ("1178,5 M", "1,35 sg").
- A bare number is never a measurement, except the table form "1.03 (8.6)" where the bracket
  is the ppg equivalent of the sg value.
- Values outside a plausible range are dropped, not clamped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PPG_PER_SG = 8.345  # pounds per US gallon for 1.00 sg
M_PER_FT = 0.3048

MUD_WEIGHT_RANGE_SG = (0.8, 2.6)
DEPTH_RANGE_M = (0.0, 12000.0)  # exclusive lower bound; deepest NCS wellbores are < 11 km MD
HOLE_SIZE_RANGE_IN = (2.0, 48.0)

_NOT_IN_NUMBER = r"(?<![\d.,/])"
# "f/474m" and "t/2096.4m" mean from/to; any other slash ("lbs/100 ft") is a ratio.
_NOT_IN_DEPTH_NUMBER = r"(?<![\d.,])(?:(?<!/)|(?<=(?<![A-Za-z])[ftFT]/))"


@dataclass(frozen=True)
class Measurement:
    kind: str  # "depth" | "mud_weight" | "hole_size"
    value: float  # metres | sg | inches
    start: int
    end: int
    raw: str  # text[start:end]
    unit: str  # unit as recognised: m, ft, sg, kg/m3, ppg, sg(ppg), in


def _decimal(s: str) -> float:
    """Number where a comma can only be a decimal comma (mud weights)."""
    return float(s.replace(" ", "").replace(",", "."))


_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _depth_number(s: str) -> float:
    s = s.replace(" ", "")
    if _THOUSANDS.match(s):
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


# ---------------------------------------------------------------- mud weight

_RANGE_SEP = r"\s*(?:-|–|to)\s*"
_SG_NUM = r"\d(?:[.,]\d{1,3})?"
_SG_UNIT = r"(?:s\.?\s?g\.?|g/cm\s?[3³]|gm?/cc|kg/l|r\.d\.)"
_SG = re.compile(
    rf"{_NOT_IN_NUMBER}(?:(?P<a>{_SG_NUM}){_RANGE_SEP})?(?P<b>{_SG_NUM})\s*{_SG_UNIT}(?![A-Za-z])",
    re.IGNORECASE,
)
_KGM3 = re.compile(
    rf"{_NOT_IN_NUMBER}(?P<b>\d{{4}}|\d[ ,]\d{{3}})\s*kg/m\s?[3³](?![A-Za-z0-9])", re.IGNORECASE
)
_PPG_NUM = r"\d{1,2}(?:[.,]\d{1,2})?"
_PPG = re.compile(
    rf"{_NOT_IN_NUMBER}(?:(?P<a>{_PPG_NUM}){_RANGE_SEP})?(?P<b>{_PPG_NUM})\+?\s*"
    r"(?:ppg|p\.p\.g\.?|lbs?/gal|#/gal)(?![A-Za-z])",
    re.IGNORECASE,
)
# "1.03 (8.6)": cell of a "Mud Weight g/cm3 (ppg)" column.
_SG_PPG_PAIR = re.compile(
    rf"{_NOT_IN_NUMBER}(?P<sg>\d[.,]\d{{1,2}})\s*\(\s*(?P<ppg>\d{{1,2}}[.,]\d{{1,2}})\s*\)"
)
_PAIR_TOLERANCE_SG = 0.03


def _plausible(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def _mud_weights(text: str) -> list[Measurement]:
    out: list[Measurement] = []

    def add(value: float, start: int, end: int, unit: str) -> None:
        if _plausible(value, MUD_WEIGHT_RANGE_SG):
            out.append(Measurement("mud_weight", value, start, end, text[start:end], unit))

    for m in _SG.finditer(text):
        if m.group("a"):
            add(_decimal(m.group("a")), m.start("a"), m.end("a"), "sg")
        add(_decimal(m.group("b")), m.start("b"), m.end(), "sg")
    for m in _KGM3.finditer(text):
        add(float(m.group("b").replace(" ", "").replace(",", "")) / 1000.0,
            m.start("b"), m.end(), "kg/m3")
    for m in _PPG.finditer(text):
        if m.group("a"):
            add(_decimal(m.group("a")) / PPG_PER_SG, m.start("a"), m.end("a"), "ppg")
        add(_decimal(m.group("b")) / PPG_PER_SG, m.start("b"), m.end(), "ppg")
    for m in _SG_PPG_PAIR.finditer(text):
        sg = _decimal(m.group("sg"))
        if abs(_decimal(m.group("ppg")) / PPG_PER_SG - sg) <= _PAIR_TOLERANCE_SG:
            add(sg, m.start(), m.end(), "sg(ppg)")
    return out


# --------------------------------------------------------------------- depth

_DEPTH_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2} \d{3}(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_DEPTH_REF = r"(?:\s?(?:MD\s?RKB|TVDSS|TVD|MD|RKB|BRT|MSL|BSF|RT|KB|SS))?"
_DEPTH_UNIT = rf"(?P<unit>meters?|metres?|feet|ft|m){_DEPTH_REF}(?![A-Za-z0-9/²³])(?![.\- ]?lb)"
_DEPTH = re.compile(
    rf"{_NOT_IN_DEPTH_NUMBER}(?:(?P<a>{_DEPTH_NUM})\s?(?:m\s?)?{_RANGE_SEP})?"
    rf"(?P<b>{_DEPTH_NUM})\s?{_DEPTH_UNIT}",
    re.IGNORECASE,
)


def _depths(text: str) -> list[Measurement]:
    out: list[Measurement] = []
    for m in _DEPTH.finditer(text):
        feet = m.group("unit").lower() in ("ft", "feet")
        factor, unit = (M_PER_FT, "ft") if feet else (1.0, "m")
        for group, end in (("a", m.end("a")), ("b", m.end())):
            if not m.group(group):
                continue
            value = _depth_number(m.group(group)) * factor
            if DEPTH_RANGE_M[0] < value <= DEPTH_RANGE_M[1]:
                start = m.start(group)
                out.append(Measurement("depth", value, start, end, text[start:end], unit))
    return out


# ----------------------------------------------------------------- hole size

_UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
_INCH_MARK = r"(?:\"|”|″|''|in\b|inch(?:es)?\b)"
_HOLE_WORD = r"(?=\s*(?:hole|bit|section|casing|csg|liner)\b)"
_HOLE_FRACTION = re.compile(
    rf"{_NOT_IN_NUMBER}(?P<w>\d{{1,2}})(?:"
    rf"\s?(?P<uf>[{''.join(_UNICODE_FRACTIONS)}])\s?{_INCH_MARK}?"
    rf"|(?:[ -]?(?P<n>\d{{1,2}})/(?P<d>2|4|8|16)|\\frac\{{(?P<n2>\d{{1,2}})\}}\{{(?P<d2>2|4|8|16)\}})"
    rf"(?:\s?{_INCH_MARK}|{_HOLE_WORD})"
    r")",
    re.IGNORECASE,
)
_HOLE_DECIMAL = re.compile(
    rf"{_NOT_IN_NUMBER}(?P<v>\d{{1,2}}(?:\.\d{{1,3}})?)(?:\s?(?:\"|”|″|'')|\s?inch(?:es)?\b)"
    rf"|{_NOT_IN_NUMBER}(?P<v2>\d{{1,2}}\.\d{{1,3}})\s?in\b",
    re.IGNORECASE,
)
_ARC_MINUTES_BEFORE = re.compile(r"['′’]\s*$")  # 58° 21' 30" is a coordinate, not a 30" hole


def _hole_sizes(text: str) -> list[Measurement]:
    out: list[Measurement] = []

    def add(value: float, start: int, end: int) -> None:
        if _plausible(value, HOLE_SIZE_RANGE_IN):
            out.append(Measurement("hole_size", value, start, end, text[start:end], "in"))

    for m in _HOLE_FRACTION.finditer(text):
        whole = float(m.group("w"))
        if m.group("uf"):
            frac = _UNICODE_FRACTIONS[m.group("uf")]
        else:
            num = float(m.group("n") or m.group("n2"))
            den = float(m.group("d") or m.group("d2"))
            if num >= den:
                continue
            frac = num / den
        add(whole + frac, m.start(), m.end())
    for m in _HOLE_DECIMAL.finditer(text):
        if _ARC_MINUTES_BEFORE.search(text, max(0, m.start() - 3), m.start()):
            continue
        add(float(m.group("v") or m.group("v2")), m.start(), m.end())
    return out


# ------------------------------------------------------------------- public


def find_measurements(text: str) -> list[Measurement]:
    """Every depth, mud-weight and hole-size mention in `text`, ordered by offset.

    Overlapping matches are resolved in favour of hole size, then mud weight, then depth.
    """
    if not text:
        return []
    kept: list[Measurement] = []
    for candidates in (_hole_sizes(text), _mud_weights(text), _depths(text)):
        for c in candidates:
            if not any(c.start < k.end and k.start < c.end for k in kept):
                kept.append(c)
    return sorted(kept, key=lambda m: (m.start, m.end))


def _first(text: str, kind: str) -> float | None:
    for m in find_measurements(text or ""):
        if m.kind == kind:
            return m.value
    return None


def parse_mud_weight(text: str) -> float | None:
    """First mud weight in `text` as specific gravity, or None."""
    return _first(text, "mud_weight")


def parse_depth_m(text: str) -> float | None:
    """First depth in `text` in metres (feet converted), or None."""
    return _first(text, "depth")


def parse_hole_size_in(text: str) -> float | None:
    """First hole or casing size in `text` in inches, or None."""
    return _first(text, "hole_size")
