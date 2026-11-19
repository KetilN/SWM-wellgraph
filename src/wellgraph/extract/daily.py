"""Daily drilling report rows from the markdown tables in report pages.

Deterministic parsing, no model calls. Three table layouts are read (docs/daily-reports.md):

- "summary": one row per day, `Date | Days | Depth | Mud weight | Operation`
  (25/7-2, 6507/7-9).
- "report": one row per daily report, each cell is "label value":
  `Rpt # 25 | Start Date 16/05/2011 | End Date ... | Start Depth (m.) 3,262.0 | End Depth ...
  | Last 24hr Op's Summary ...` (6/3-A-1 H).
- "activity": one row per time-coded activity, `Date | ... | From | To | Depth | Activity`,
  with the date written only on the first row of a day (25/7-3, 25/6-3).

A table only counts when it has a date column and a depth column and either an operations
text column or a day-counter column; everything else (mud property, bit, cement, lithology
tables, ...) gives no rows.

    python -m wellgraph.extract.daily --kg data/kg
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

import duckdb

from wellgraph.extract.units import parse_depth_m, parse_mud_weight

# Column name -> DuckDB type of data/kg/daily_report.parquet (to be moved into schema.yaml).
DAILY_REPORT_COLUMNS = {
    "wellbore_id": "BIGINT",
    "document_id": "VARCHAR",
    "page_no": "INTEGER",
    "row_index": "INTEGER",
    "report_date": "DATE",
    "depth_md": "DOUBLE",
    "start_depth_md": "DOUBLE",
    "mud_weight_raw": "VARCHAR",
    "mud_weight_sg": "DOUBLE",
    "operations": "VARCHAR",
}

YEAR_RANGE = (1965, 2020)  # the wells in the collection were drilled in 1970-2015

# ---------------------------------------------------------------------- dates

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_MONTH_NAMES = "|".join(_MONTHS) + "|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec"
_YEAR4 = r"(?:19[6-9]\d|20[012]\d)"

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# "29.01 90": the OCR sometimes reads the second separator as a space.
_NUMERIC = re.compile(r"^(\d{1,4})\s?[./-]\s?(\d{1,2})(?:\s?[./-]\s?|\s)(\d{1,4})$")
# "19872 August": the year of a page heading glued in front of the day.
_NAMED = re.compile(
    rf"^(?:({_YEAR4})\s?)?(\d{{1,2}})(?:st|nd|rd|th)?[\s.-]*({_MONTH_NAMES})\.?"
    r"(?:[\s,.-]+(\d{4}|\d{2}))?$",
    re.IGNORECASE,
)
_TIME_SUFFIX = re.compile(r"\s+\d{1,2}:\d{2}(?::\d{2})?$")


def _full_year(y: int, digits: int) -> int | None:
    if digits <= 2:
        y += 1900 if y >= YEAR_RANGE[0] % 100 else 2000
    return y if YEAR_RANGE[0] <= y <= YEAR_RANGE[1] else None


def _date_parts(text: str, order: str | None = None) -> tuple[int | None, int, int] | None:
    """(year or None, month, day) of a date cell, or None. `order` settles yy.mm.dd/dd.mm.yy."""
    s = _TIME_SUFFIX.sub("", (text or "").strip()).rstrip(".").strip()
    if not s:
        return None
    if m := _ISO.match(s):
        y, mo, d = (int(g) for g in m.groups())
        year = _full_year(y, 4)
    elif m := _NUMERIC.match(s):
        a, b, c = m.groups()
        if len(a) == 4:
            y, d = a, c
        elif len(c) == 4 or len(a) == 3 or len(c) == 3:
            y, d = c, a
        elif order == "ymd" or (order is None and int(a) > 31):
            y, d = a, c
        else:
            y, d = c, a
        if len(y) == 3 or len(d) > 2:
            return None
        mo, d = int(b), int(d)
        year = _full_year(int(y), len(y))
        if year is None:
            return None
    elif m := _NAMED.match(s):
        glued, d, name, y = m.groups()
        mo = next(i for i, full in enumerate(_MONTHS, 1) if full.startswith(name.lower()[:3]))
        d = int(d)
        year = int(glued) if glued else (_full_year(int(y), len(y)) if y else None)
        if y and year is None:
            return None
    else:
        return None
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return year, mo, d


def _make_date(year: int | None, month: int, day: int) -> dt.date | None:
    if year is None:
        return None
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_report_date(text: str, default_year: int | None = None,
                      order: str | None = None) -> dt.date | None:
    """Date written in a table cell: dd.mm.yy, dd/mm/yyyy, yy/mm/dd, yyyy-mm-dd, "5 MAY 1990",
    "26-Apr-11", "3 August" (needs `default_year`). None when it is not a valid date in
    1965-2020. Two-digit years: 65-99 are 19xx, 00-20 are 20xx. A two-digit first number above
    31 is a year (yy.mm.dd); `order="ymd"` forces that reading for ambiguous cells."""
    parts = _date_parts(text, order)
    if parts is None:
        return None
    year, month, day = parts
    return _make_date(year if year is not None else default_year, month, day)


_YEAR_IN_DATE = [
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s.-]*(?:{_MONTH_NAMES})\.?[\s,.-]+({_YEAR4})\b",
               re.IGNORECASE),
    re.compile(rf"\b(?:{_MONTH_NAMES})\.?,?\s+({_YEAR4})\b", re.IGNORECASE),
    re.compile(rf"(?<![\d.,/-])\d{{1,2}}[./-]\d{{1,2}}[./-]({_YEAR4})(?![\d./-])"),
    re.compile(rf"(?<![\d.,/-])({_YEAR4})-\d{{2}}-\d{{2}}(?![\d./-])"),
    re.compile(rf"(?<![\d.,/-])({_YEAR4})\d{{1,2}}\s(?:{_MONTH_NAMES})\b", re.IGNORECASE),
]
_YEAR_WEAK = [
    re.compile(rf"^\W*({_YEAR4})\W*$", re.MULTILINE),  # a line that is only a year
    re.compile(rf"\bdat[eo]:?\s+({_YEAR4})\b", re.IGNORECASE),  # "Date 1990" column header
]
_SHORT_DATE = re.compile(r"(?<![\d.,/-])(\d{1,2})[./](\d{1,2})[./](\d{2})(?![\d./-])")


def infer_year(page_text: str) -> int | None:
    """Year a page's day-and-month dates belong to, from dates written out elsewhere on the
    page ("10 August 1987", "15/08/1995", a "19872 August" OCR join), else from a line that is
    only a year or a "Date 1990" header, else from dd.mm.yy dates. The most frequent year
    wins; None when the page names none. Bare numbers such as "1990m" are never years."""
    text = page_text or ""
    for patterns in (_YEAR_IN_DATE, _YEAR_WEAK):
        years = [int(m.group(1)) for p in patterns for m in p.finditer(text)]
        if years:
            return Counter(years).most_common(1)[0][0]
    years = []
    for m in _SHORT_DATE.finditer(text):
        d = parse_report_date(m.group(0))
        if d:
            years.append(d.year)
    return Counter(years).most_common(1)[0][0] if years else None


# ---------------------------------------------------------------- cell values

_EMPTY = {"", "-", "--", "---", "n/a", "na", "nil", "none"}
_BARE_NUMBER = re.compile(r"^(?:PBTD\s*)?(\d[\d., ]*|[.,]\d+)$", re.IGNORECASE)
_BARE_MUD = re.compile(r"^\d{1,2}(?:[.,]\d{1,2})?$")
_MUD_PAIR = re.compile(r"^(\d{1,2}(?:[.,]\d{1,2})?)\s?/\s?\d{1,2}(?:[.,]\d{1,2})?\s?(ppg|s\.?g\.?)",
                       re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip()


def _cell_depth(cell: str) -> float | None:
    """Depth in metres from a depth cell: "3422m", "PBTD 4812m", "227.00", "171,0",
    "3,262.0". Zero, "N/A" and "-" are no depth."""
    s = _clean(cell)
    if s.lower() in _EMPTY:
        return None
    value = parse_depth_m(s)
    if value is None and (m := _BARE_NUMBER.match(s)):
        value = parse_depth_m(f"{m.group(1).strip()} m")
    return value


def _cell_mud(cell: str, unit: str | None) -> tuple[str | None, float | None]:
    raw = _clean(cell)
    if raw.lower() in _EMPTY:
        return None, None
    sg = parse_mud_weight(raw)
    if sg is None and (m := _MUD_PAIR.match(raw)):  # "11.1/11.3 ppg": weight in / out
        sg = parse_mud_weight(f"{m.group(1)} {m.group(2)}")
    if sg is None and _BARE_MUD.match(raw):
        if unit:
            sg = parse_mud_weight(f"{raw} {unit}")
        elif 0.8 <= float(raw.replace(",", ".")) <= 2.6:
            sg = parse_mud_weight(f"{raw} sg")
    return raw, sg


# --------------------------------------------------------------------- tables

_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    s = s[1:-1] if s.endswith("|") and len(s) > 1 else s[1:]
    return [c.strip() for c in s.split("|")]


def _table_blocks(text: str) -> list[list[list[str]]]:
    """Markdown tables of a page as lists of rows (lists of cells), without separator rows
    and without all-empty rows. A separator row starts a new table at the row before it."""
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []

    def close() -> None:
        nonlocal current
        rows = [r for r in current if any(r)]
        if rows:
            blocks.append(rows)
        current = []

    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith("|"):
            close()
            continue
        cells = _split_row(s)
        if cells and all(_SEPARATOR_CELL.match(c) for c in cells):
            header = current.pop() if current else None
            close()
            if header is not None:
                current = [header]
            continue
        current.append(cells)
    close()
    return blocks


_H_DATE = re.compile(r"^dat[eo]\b", re.IGNORECASE)
_H_DEPTH = re.compile(r"depth", re.IGNORECASE)
_H_OPS = re.compile(r"operation|activity|description|summary|notes|remarks|comments",
                    re.IGNORECASE)
_H_MUD = re.compile(r"^mw\b|mud\s*(?:w|\b)|density", re.IGNORECASE)
_H_FROM = re.compile(r"^from\b", re.IGNORECASE)
_H_TO = re.compile(r"^to\b", re.IGNORECASE)
_H_HOURS = re.compile(r"^(?:hours|hrs|duration)\b", re.IGNORECASE)
# Words that mark a table with dates and depths as something other than a daily report.
_H_OTHER = re.compile(
    r"shoe|slurry|cement|hole\s*size|rheolog|visc|root cause|\blog\b|\bbit\b|\bbha\b|\bpv\b"
    r"|\byp\b|\bwob\b|\brpm\b|temp|plan|budg|water depth|pressure|litholog|formation",
    re.IGNORECASE,
)
_MAX_COLUMNS = 10  # the daily mud and hydraulics tables have 14 to 29 columns
_DAYS_CELL = re.compile(r"^\d{1,3}/\d{1,3}(?:/\d{1,3})?$")  # "85/74/0": rig/drilling/test days
_TIME_CELL = re.compile(r"^(?:\d{2}:\d{2}|\d{4})$")


def _columns(header: list[str]) -> dict | None:
    """Column roles from a header row, or None when it is not a daily report header."""
    if len(header) > _MAX_COLUMNS or any(_H_OTHER.search(c) for c in header):
        return None

    def first(pattern: re.Pattern) -> int | None:
        return next((i for i, c in enumerate(header) if pattern.search(c)), None)

    cols = {"n": len(header), "date": first(_H_DATE), "depth": first(_H_DEPTH),
            "from": first(_H_FROM), "to": first(_H_TO), "hours": first(_H_HOURS)}
    if cols["date"] is None or cols["depth"] is None:
        return None
    ops = [i for i, c in enumerate(header) if _H_OPS.search(c) and i > cols["depth"]]
    cols["ops"] = ops[-1] if ops else None
    mud = [i for i, c in enumerate(header) if _H_MUD.search(c) and i != cols["depth"]]
    cols["mud"] = mud[0] if mud else None
    cols["mud_unit"] = None
    if mud:
        label = header[mud[0]].lower()
        if re.search(r"ppg|p\.p\.g", label) and not re.search(r"g/c|s\.?g", label):
            cols["mud_unit"] = "ppg"
        elif re.search(r"g/c|s\.?g\b", label):
            cols["mud_unit"] = "sg"
    cols["layout"] = (
        "activity" if cols["from"] is not None and cols["to"] == cols["from"] + 1 else "summary")
    return cols


def _is_header(row: list[str]) -> bool:
    return any(_H_DATE.match(c) for c in row) and not any(_date_parts(c) for c in row)


def _date_order(cells: list[str]) -> str | None:
    """"ymd" when a date column is written yy.mm.dd (some first number is above 31)."""
    for c in cells:
        m = _NUMERIC.match(_clean(c))
        if m and len(m.group(1)) == 2 and len(m.group(3)) == 2 and int(m.group(1)) > 31:
            return "ymd"
    return None


def _fit(cells: list[str], n: int) -> list[str]:
    """Pad a short row on the right; fold surplus cells into the last one."""
    if len(cells) > n:
        return cells[: n - 1] + [" ".join(c for c in cells[n - 1:] if c)]
    return cells + [""] * (n - len(cells))


def _new_row(layout: str, **values) -> dict:
    row = {"report_date": None, "depth_md": None, "start_depth_md": None,
           "mud_weight_raw": None, "mud_weight_sg": None, "operations": "", "row_index": -1,
           "layout": layout}
    row.update(values)
    return row


# ------------------------------------------------------------ layout "report"

_KV_ROW = re.compile(r"Rpt\s*#\s*\d+.*Start\s*Date", re.IGNORECASE)
_KV_DATE = r"\s*:?\s*(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
_KV_START_DATE = re.compile(r"Start\s*Date" + _KV_DATE, re.IGNORECASE)
_KV_END_DATE = re.compile(r"End\s*Date" + _KV_DATE, re.IGNORECASE)
_KV_DEPTH = r"\s*Depth[^|\d]*?([\d][\d.,]*)"
_KV_START_DEPTH = re.compile(r"Start" + _KV_DEPTH, re.IGNORECASE)
_KV_END_DEPTH = re.compile(r"End" + _KV_DEPTH, re.IGNORECASE)
_KV_OPS = re.compile(r"Summary\s*:?\s*(.*)$", re.IGNORECASE | re.DOTALL)


def _parse_report_block(block: list[list[str]]) -> list[dict]:
    rows: list[dict] = []
    for cells in block:
        joined = " | ".join(cells)
        if not _KV_ROW.search(joined):
            filled = [c for c in cells if c]
            if rows and len(filled) == 1 and len(filled[0]) > 40:  # text carried to a new row
                rows[-1]["operations"] = _clean(rows[-1]["operations"] + " " + filled[0])
            continue
        start, end = _KV_START_DATE.search(joined), _KV_END_DATE.search(joined)
        d_start, d_end = _KV_START_DEPTH.search(joined), _KV_END_DEPTH.search(joined)
        ops = _KV_OPS.search(joined)
        date = parse_report_date(start.group(1)) if start else None
        if date is None and end and (end_date := parse_report_date(end.group(1))):
            date = end_date - dt.timedelta(days=1)
        rows.append(_new_row(
            "report",
            report_date=date,
            depth_md=_cell_depth(d_end.group(1)) if d_end else None,
            start_depth_md=_cell_depth(d_start.group(1)) if d_start else None,
            operations=_clean(ops.group(1).replace(" | ", " ")) if ops else "",
        ))
    return rows


# ----------------------------------------------------------- layout "summary"


def _parse_summary_block(data: list[list[str]], cols: dict) -> list[dict]:
    n, i_date, i_depth, i_ops = cols["n"], cols["date"], cols["depth"], cols["ops"]
    order = _date_order([r[i_date] for r in data if len(r) > i_date])
    if cols["mud"] is not None:
        mud_cols = [cols["mud"]]
    else:  # unnamed cells between the depth and the operations ("1.03 | (8.6)")
        mud_cols = list(range(i_depth + 1, i_ops if i_ops is not None else n))
    rows: list[dict] = []
    for raw in data:
        if _is_header(raw):
            continue
        filled = [c for c in raw if c]
        parts = _date_parts(raw[i_date], order) if len(raw) > i_date else None
        cells = _fit(raw, n)
        continuation = parts is None and (
            len(raw) < n or (not cells[i_date] and not cells[i_depth]))
        if continuation:
            text = _clean(" ".join(filled))
            if not text or i_ops is None:
                continue
            if rows:
                rows[-1]["operations"] = _clean(rows[-1]["operations"] + " " + text)
            else:  # the row it continues is on the previous page
                rows.append(_new_row("summary", operations=text, _continues=True))
            continue
        if parts is None and _cell_depth(cells[i_depth]) is None:
            continue
        mud_raw, mud_sg = _cell_mud(" ".join(cells[i] for i in mud_cols), cols["mud_unit"])
        row = _new_row(
            "summary",
            depth_md=_cell_depth(cells[i_depth]),
            mud_weight_raw=mud_raw,
            mud_weight_sg=mud_sg,
            operations=_clean(cells[i_ops]) if i_ops is not None else "",
        )
        if parts:
            row["report_date"] = _make_date(*parts)
            if parts[0] is None:
                row["_month_day"] = parts[1:]
        rows.append(row)
    return rows


def _headerless_summary_columns(block: list[list[str]]) -> dict | None:
    """Columns of a `date | days | depth | mud weight | operation` table whose header row is
    on an earlier page: recognised by a date cell followed by a day-counter cell."""
    hits = [r for r in block
            if len(r) == 5 and _date_parts(r[0]) and _DAYS_CELL.match(r[1]) and len(r[4]) > 20]
    if not hits or len(hits) * 2 < len(block):
        return None
    return {"n": 5, "date": 0, "depth": 2, "mud": 3, "ops": 4, "mud_unit": None,
            "from": None, "to": None, "hours": None, "layout": "summary"}


# ---------------------------------------------------------- layout "activity"


def _minutes(cell: str) -> int:
    digits = cell.replace(":", "")
    return int(digits[:2]) * 60 + int(digits[2:])


def _parse_activity_block(data: list[list[str]], cols: dict) -> list[dict]:
    """Time-coded rows. The date is written once per day, so rows are grouped into days by
    the clock (a row starting earlier than the one before, or after one ending at midnight,
    starts a new day) and the written dates anchor the days. A date written on a row in the
    middle of a day that is one day ahead belongs to the next day (OCR row misalignment)."""
    n, i_date, i_from = cols["n"], cols["date"], cols["from"]
    order = _date_order([r[i_date] for r in data if len(r) == n])
    entries: list[dict] = []
    day, day_rows, prev = 0, 0, None
    anchors: dict[int, dt.date] = {}

    def implied(index: int) -> dt.date | None:
        lower = [i for i in anchors if i <= index]
        if lower:
            j = max(lower)
            return anchors[j] + dt.timedelta(days=index - j)
        if anchors:
            j = min(anchors)
            return anchors[j] - dt.timedelta(days=j - index)
        return None

    for raw in data:
        if _is_header(raw):
            continue
        pair = next((i for i in range(len(raw) - 1)
                     if _TIME_CELL.match(raw[i]) and _TIME_CELL.match(raw[i + 1])), None)
        if pair is None:
            continue
        shift = i_from - pair  # a missing date cell moves everything one place left
        cells = _fit([""] * shift + raw if shift >= 0 else raw[-shift:], n)
        t_from, t_to = _minutes(cells[i_from]), _minutes(cells[i_from + 1])
        hours = cells[cols["hours"]].replace(",", ".") if cols["hours"] is not None else ""
        # 00:00-00:00 with zero hours: the look-ahead line ("0000-0600 ...") of the daily
        # report, printed between two days. It starts the next day and is not kept.
        lookahead = t_from == 0 and t_to % 1440 == 0 and re.fullmatch(r"0*\.?0*", hours or "x")
        if prev is not None and (
                prev["closed"] or t_from < prev["from"] or (lookahead and day_rows > 0)):
            day, day_rows = day + 1, 0
        written = parse_report_date(cells[i_date], order=order) if cells[i_date] else None
        if written:
            guess = implied(day)
            if t_from == 0 or day_rows == 0 or guess is None:
                anchors.setdefault(day, written)
            elif written > guess:
                anchors.setdefault(day + 1, written)
        # A row ending at midnight closes the day. 00:00-00:00 does so only as a full day:
        # garbled pages repeat "1.00 | 00:00 | 00:00" rows.
        full_day = not re.fullmatch(r"\d+(?:\.\d+)?", hours) or float(hours) >= 23
        closed = bool(not lookahead and t_to % 1440 == 0 and (t_from > 0 or full_day))
        prev = {"closed": closed, "from": t_from}
        if lookahead:
            continue
        day_rows += 1
        ops = _clean(cells[cols["ops"]]) if cols["ops"] is not None else ""
        depth = _cell_depth(cells[cols["depth"]])
        entries.append({"day": day, "ops": ops, "depth": depth, "closed": closed})

    rows = []
    for e in entries:
        if not e["ops"] and e["depth"] is None:
            continue
        rows.append(_new_row("activity", report_date=implied(e["day"]), depth_md=e["depth"],
                             operations=e["ops"], _day=e["day"]))
    if rows and entries:
        rows[-1]["_closed"] = entries[-1]["closed"]
        rows[-1]["_last_day"] = entries[-1]["day"]
    return rows


# ----------------------------------------------------------------------- page


def _compatible(block: list[list[str]], cols: dict) -> bool:
    """Can a block without a header row be the continuation of a table with `cols`?"""
    n = cols["n"]
    if cols["layout"] == "activity":
        fits = [r for r in block if len(r) in (n, n - 1) and any(
            _TIME_CELL.match(r[i]) and _TIME_CELL.match(r[i + 1]) for i in range(len(r) - 1))]
    else:
        fits = [r for r in block if len(r) == n and _date_parts(r[cols["date"]])]
    return bool(fits) and len(fits) * 2 >= len(block)


def _has_day_counter(data: list[list[str]], cols: dict) -> bool:
    return any(len(r) > cols["date"] + 1 and _DAYS_CELL.match(r[cols["date"] + 1])
               and _date_parts(r[cols["date"]]) for r in data)


def _parse_page(page_text: str, header: list[str] | None = None
                ) -> tuple[list[dict], list[str] | None]:
    """Rows of a page (with private `_` keys, years not yet filled in) and the header row of
    the last daily table on it, to hand to the next page of the same document."""
    carried = _columns(header) if header else None
    rows: list[dict] = []
    for block in _table_blocks(page_text):
        if any(_KV_ROW.search(" | ".join(r)) for r in block):
            rows += _parse_report_block(block)
            header = carried = None
            continue
        own = next((i for i, r in enumerate(block[:3]) if _is_header(r)), None)
        if own is not None:
            cols, data = _columns(block[own]), block[own + 1:]
            if cols and cols["ops"] is None and not (
                    cols["layout"] == "summary" and _has_day_counter(data, cols)):
                cols = None
            if cols:
                header, carried = block[own], cols
        elif carried and _compatible(block, carried):
            cols, data = carried, block
        else:
            cols, data = _headerless_summary_columns(block), block
        if not cols:
            continue
        if cols["layout"] == "activity":
            rows += _parse_activity_block(data, cols)
        else:
            rows += _parse_summary_block(data, cols)
    for i, row in enumerate(rows):
        row["row_index"] = i
    return rows, (header if rows else None)


def _fill_years(rows: list[dict], year: int | None, previous_month: int | None = None) -> None:
    """Give day-and-month rows a year, moving to the next year when the months wrap from
    November/December to January/February."""
    if year is None:
        return
    for row in rows:
        if row["report_date"] is not None or "_month_day" not in row:
            continue
        month, day = row["_month_day"]
        if previous_month is not None and previous_month >= 11 and month <= 2:
            year += 1
        row["report_date"] = _make_date(year, month, day)
        previous_month = month


def _public(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def parse_daily_rows(page_text: str, default_year: int | None = None,
                     header: str | list[str] | None = None) -> list[dict]:
    """Daily drilling report rows of one page, [] when the page has no such table.

    Each dict has report_date (datetime.date or None), depth_md (metres, the depth at report
    time / end of the day; None when not given or zero), start_depth_md (only where the
    layout gives start and end depth), mud_weight_raw (as written), mud_weight_sg,
    operations (text, "" when the table has none), row_index (position on the page) and
    layout ("summary", "report" or "activity").

    Dates without a year take the year found elsewhere on the page (`infer_year`), else
    `default_year`, else stay None. `header` is the header row of the same table on an
    earlier page (markdown line or list of cells), for pages that continue it without one.
    """
    if isinstance(header, str):
        header = _split_row(header)
    rows, _ = _parse_page(page_text, header)
    if any("_month_day" in r for r in rows):
        _fill_years(rows, infer_year(page_text) or default_year)
    return _public(rows)


# ---------------------------------------------------------------------- build


def _clamp_to_next_page(doc_pages: list[tuple[int, list[dict]]]) -> int:
    """Activity rows cannot be later than the first dated row of the following page: on a
    garbled page the clock rule can count too many days. Returns the number of rows changed."""
    changed = 0
    for (page_no, rows), (next_no, next_rows) in zip(
            reversed(doc_pages[:-1]), reversed(doc_pages[1:]), strict=True):
        limit = next((r["report_date"] for r in next_rows if r["report_date"]), None)
        if next_no != page_no + 1 or limit is None:
            continue
        for r in rows:
            if r["layout"] == "activity" and r["report_date"] and r["report_date"] > limit:
                r["report_date"] = limit
                changed += 1
    return changed


def _repair_single_dates(rows: list[dict]) -> int:
    """In one-row-per-day tables, a date that breaks the sequence between two rows that are
    exactly two days apart is a misprint ("04.03.90" between 03.04.90 and 05.04.90): set it
    to the day in between. Returns the number of rows changed."""
    daily = [r for r in rows if r["layout"] in ("summary", "report")]
    changed = 0
    for before, row, after in zip(daily, daily[1:], daily[2:], strict=False):
        a, c = before["report_date"], after["report_date"]
        if a and c and (c - a).days == 2 and row["report_date"] != a + dt.timedelta(days=1):
            row["report_date"] = a + dt.timedelta(days=1)
            changed += 1
    return changed


def build_daily_reports(kg_dir: str | Path) -> dict:
    """Parse every page in `kg_dir`/page.parquet and write `kg_dir`/daily_report.parquet.

    Across the pages of a document: a table continued without its header reuses the header
    of the page before; rows without a year take the year found on their page, else the year
    of the last dated row before them in the document, else the SODIR entry year of the
    wellbore (rolling over to the next year after December); activity rows on a page with no
    date at all continue the days of the page before. Returns counts.
    """
    kg = Path(kg_dir)
    con = duckdb.connect()
    pages = con.execute(
        "select p.document_id, p.page_no, p.text, d.wellbore_id, w.entry_date "
        "from read_parquet(?) p left join read_parquet(?) d using (document_id) "
        "left join read_parquet(?) w using (wellbore_id) order by p.document_id, p.page_no",
        [str(kg / "page.parquet"), str(kg / "document.parquet"), str(kg / "wellbore.parquet")],
    ).fetchall()

    documents: dict[str, list[tuple[int, list[dict]]]] = {}
    keys: dict[str, int | None] = {}
    state: dict = {}
    for document_id, page_no, text, wellbore_id, entry_date in pages:
        if state.get("doc") != document_id:
            state = {"doc": document_id, "header": None, "page": None, "last": None,
                     "closed": False, "last_row": None}
        follows = state["page"] == page_no - 1
        rows, header = _parse_page(text or "", state["header"] if follows else None)
        if not rows:
            continue
        if rows[0].get("_continues") and follows and state["last_row"] is not None:
            # operations text continued from the last row of the previous page
            state["last_row"]["operations"] += " " + rows.pop(0)["operations"]
            if not rows:
                continue
        if any("_month_day" in r for r in rows):
            last = state["last"]
            if year := infer_year(text or ""):
                _fill_years(rows, year)
            elif last:
                _fill_years(rows, last.year, last.month)
            elif entry_date:
                _fill_years(rows, entry_date.year, entry_date.month)
        if follows and state["last"] and all(
                r["report_date"] is None for r in rows if r["layout"] == "activity"):
            base = state["last"] + dt.timedelta(days=1 if state["closed"] else 0)
            for r in rows:
                if r["layout"] == "activity":
                    r["report_date"] = base + dt.timedelta(days=r["_day"])
        for i, r in enumerate(rows):
            r["row_index"] = i
        dated = [r["report_date"] for r in rows if r["report_date"]]
        state.update(header=header, page=page_no, closed=bool(rows[-1].get("_closed")),
                     last=dated[-1] if dated else state["last"], last_row=rows[-1])
        documents.setdefault(document_id, []).append((page_no, rows))
        keys[document_id] = wellbore_id

    out: list[dict] = []
    dates_clamped = dates_repaired = 0
    for document_id, doc_pages in documents.items():
        dates_clamped += _clamp_to_next_page(doc_pages)
        dates_repaired += _repair_single_dates([r for _, rows in doc_pages for r in rows])
        for page_no, rows in doc_pages:
            out += _public([r | {"wellbore_id": keys[document_id], "document_id": document_id,
                                 "page_no": page_no} for r in rows])
    pages_with_rows = sum(len(p) for p in documents.values())

    names = list(DAILY_REPORT_COLUMNS)
    con.execute("create table daily_report ("
                + ", ".join(f"{c} {t}" for c, t in DAILY_REPORT_COLUMNS.items()) + ")")
    if out:
        con.executemany(
            f"insert into daily_report values ({', '.join('?' * len(names))})",
            [[r[c] for c in names] for r in out])
    path = kg / "daily_report.parquet"
    con.execute(f"copy (select * from daily_report order by wellbore_id, document_id, "
                f"page_no, row_index) to '{path}' (format parquet)")
    by_wellbore = Counter(r["wellbore_id"] for r in out)
    return {
        "pages": len(pages),
        "pages_with_rows": pages_with_rows,
        "rows": len(out),
        "rows_with_date": sum(r["report_date"] is not None for r in out),
        "rows_with_depth": sum(r["depth_md"] is not None for r in out),
        "rows_with_mud_weight": sum(r["mud_weight_sg"] is not None for r in out),
        "dates_clamped_to_next_page": dates_clamped,
        "dates_repaired": dates_repaired,
        "rows_by_wellbore": dict(sorted(by_wellbore.items(), key=lambda kv: str(kv[0]))),
        "path": str(path),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kg", default="data/kg", help="directory with page.parquet etc.")
    args = parser.parse_args(argv)
    print(json.dumps(build_daily_reports(args.kg), indent=2))


if __name__ == "__main__":
    main()
