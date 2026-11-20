"""Repairs on the event table after extraction and the depth pass. No model calls.

    PYTHONPATH=src python -m wellgraph.extract.cleanup --kg data/kg

Three steps, in this order:

1. Feet stored as metres. Old reports give depths in feet; where the unit was not written
   next to the number the model returned the figure unconverted. A depth more than
   `TD_MARGIN_M` below the wellbore's total depth that fits inside the well once read as
   feet, on a page that writes depths in feet (or none in metres), is converted. Hole section, formations and
   a SODIR-filled mud weight are then joined again at the corrected depth.
2. One report registered under several wellbores (a well and its re-entry or sidetracks).
   Its events were extracted once per copy. Each event is kept once, on the first-drilled
   of those wellbores that is deep enough to hold it.
3. The same incident told on several pages: `merge_duplicates` again, because corrected
   depths now bring copies within its window.

Only `event.parquet` is rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb

from wellgraph.extract.run import merge_duplicates
from wellgraph.resolve.depth import joins_for_event
from wellgraph.resolve.mud import mud_weight_at

FT = 0.3048
TD_MARGIN_M = 100.0
MD_REFS = ("MD", "ASSUMED_MD")
# a number followed by a feet mark: 6,821 ft / 6821' / 6821 FEET
_FEET_MARK = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:ft\b|feet\b|['’′])", re.IGNORECASE)
# a depth written in metres: 2611 m / 3852 mMD / 1,704 m RKB / 2100 meter
_METRE_MARK = re.compile(r"\d{2,}(?:\.\d+)?\s*(?:m\b|m\s?(?:MD|RKB|TVD)\b|met(?:er|re)s?\b)",
                         re.IGNORECASE)
MIN_FEET_MARKS = 3


def _q(path: Path) -> str:
    return str(path).replace("'", "''")


def _dicts(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _deepest(row: dict[str, Any], *fields: str) -> float | None:
    values = [row[f] for f in fields if row[f] is not None]
    return max(values) if values else None


def _join_depths(row: dict[str, Any]) -> tuple[float | None, float | None]:
    """The interval the joins use: the stated depth, else the context depth."""
    if row["top_md"] is not None or row["base_md"] is not None:
        return row["top_md"], row["base_md"] if row["base_md"] is not None else row["top_md"]
    return row["context_top_md"], row["context_base_md"]


def _name_pattern(name: str) -> re.Pattern[str]:
    """'15/9-19 A' as written in a report: spacing varies, and it must not run on."""
    body = r"\s*".join(re.escape(part) for part in name.split())
    return re.compile(rf"(?<![0-9A-Za-z/]){body}(?![0-9A-Za-z])", re.IGNORECASE)


def _says_feet(*texts: str | None) -> bool:
    """The page writes depths in feet, or at least never writes one in metres."""
    feet = sum(len(_FEET_MARK.findall(t)) for t in texts if t)
    metres = sum(len(_METRE_MARK.findall(t)) for t in texts if t)
    return feet >= MIN_FEET_MARKS or metres == 0


def clean_events(kg_dir: str | Path) -> dict[str, Any]:
    from wellgraph.graph.schema import TABLES

    kg_dir = Path(kg_dir)
    target = kg_dir / "event.parquet"
    if not target.is_file():
        raise FileNotFoundError(f"{target} not found: run the extraction first.")
    con = duckdb.connect()
    for table in ("event", "wellbore", "document", "page", "hole_section", "formation_top",
                  "mud_check"):
        path = kg_dir / f"{table}.parquet"
        if path.is_file():
            con.execute(f"create view {table} as select * from read_parquet('{_q(path)}')")
        else:
            cols = ", ".join(f"{c} {t}" for c, t in TABLES.get(table, {
                "wellbore_id": "BIGINT", "md": "DOUBLE", "mud_weight_sg": "DOUBLE"}).items())
            con.execute(f"create table {table} ({cols})")

    columns = list(TABLES["event"])
    events = _dicts(con.execute(f"select {', '.join(columns)} from event"))
    wells = {w["wellbore_id"]: w for w in _dicts(con.execute(
        "select wellbore_id, name, total_depth_md, entry_date from wellbore"))}
    stats: dict[str, Any] = {"events_in": len(events)}

    # Documents with identical page text are copies of one report.
    copies = _dicts(con.execute(
        """
        with sig as (
            select document_id, md5(string_agg(md5(coalesce(text, '')), '' order by page_no)) h
            from page group by 1
        )
        select s.h as report, d.document_id, d.wellbore_id
        from sig s join document d using (document_id)
        where s.h in (select h from sig join document using (document_id)
                      group by 1 having count(distinct wellbore_id) > 1)
        """
    ))
    report_of = {c["document_id"]: c["report"] for c in copies}
    sharing: dict[str, set[int]] = defaultdict(set)
    for c in copies:
        sharing[c["report"]].add(c["wellbore_id"])
    stats["shared_reports"] = len(sharing)
    stats["shared_report_wellbores"] = len({w for ws in sharing.values() for w in ws})

    def depth_limit(row: dict[str, Any]) -> float | None:
        """Deepest total depth among the wellbores the event's report covers."""
        group = sharing.get(report_of.get(row["document_id"]), {row["wellbore_id"]})
        depths = [wells[w]["total_depth_md"] for w in group
                  if w in wells and wells[w]["total_depth_md"] is not None]
        return max(depths) + TD_MARGIN_M if depths else None

    # --- 1. feet stored as metres ---------------------------------------------------------
    suspects: list[tuple[dict[str, Any], list[tuple[str, str]]]] = []
    for row in events:
        if row["depth_ref"] not in MD_REFS:
            continue
        limit = depth_limit(row)
        if limit is None:
            continue
        pairs = []
        for pair in (("top_md", "base_md"), ("context_top_md", "context_base_md")):
            deepest = _deepest(row, *pair)
            if deepest is not None and deepest > limit and deepest * FT <= limit:
                pairs.append(pair)
        if pairs:
            suspects.append((row, pairs))
    stats["depths_beyond_total_depth_fitting_feet"] = len(suspects)

    page_text: dict[tuple[str, int], str] = {}
    if suspects:
        con.execute("create temp table want (document_id VARCHAR, page_no INTEGER)")
        con.executemany("insert into want values (?, ?)", list(
            {(r["document_id"], r["page_no"]) for r, _ in suspects}))
        for document_id, page_no, text in con.execute(
            "select p.document_id, p.page_no, p.text from page p "
            "join want using (document_id, page_no)"
        ).fetchall():
            page_text[(document_id, page_no)] = text or ""

    joins: dict[int, tuple[list, list, list]] = {}

    def tables_for(wid: int) -> tuple[list, list, list]:
        if wid not in joins:
            joins[wid] = (
                _dicts(con.execute("select * from hole_section where wellbore_id = ?", [wid])),
                _dicts(con.execute("select * from formation_top where wellbore_id = ?", [wid])),
                _dicts(con.execute(
                    "select md, mud_weight_sg from mud_check where wellbore_id = ?", [wid])),
            )
        return joins[wid]

    def rejoin(row: dict[str, Any]) -> None:
        sections, tops, checks = tables_for(row["wellbore_id"])
        top, base = _join_depths(row)
        hole, formations = joins_for_event(sections, tops, top, base, row["depth_ref"])
        if sections or hole is not None:
            row["hole_diameter_in"] = hole  # else keep the size the page itself stated
        row["formations"] = formations
        if row["mud_weight_source"] == "sodir_mud_table":
            found = mud_weight_at(checks, base if base is not None else top)
            row["mud_weight_sg"] = found[0] if found else None
            if not found:
                row["mud_weight_kind"] = row["mud_weight_source"] = None

    converted = 0
    for row, pairs in suspects:
        if not _says_feet(row["text_span"], page_text.get((row["document_id"], row["page_no"]))):
            continue
        for pair in pairs:
            for field in pair:
                if row[field] is not None:
                    row[field] = round(row[field] * FT, 1)
        rejoin(row)
        converted += 1
    stats["depths_converted_from_feet"] = converted

    # --- 2. one report under several wellbores -----------------------------------------------
    def order(wid: int) -> tuple:
        w = wells.get(wid, {})
        return (w.get("entry_date") is None, str(w.get("entry_date")), w.get("name") or "")

    def home(group: set[int], depth: float | None) -> int:
        ranked = sorted(group, key=order)
        if depth is None:
            return ranked[0]
        for wid in ranked:
            td = wells.get(wid, {}).get("total_depth_md")
            if td is None or td + TD_MARGIN_M >= depth:
                return wid
        return max(ranked, key=lambda w: wells.get(w, {}).get("total_depth_md") or 0.0)

    # Which of the sharing wellbores a page is about: the names written on it, else the
    # last page before it that named exactly one of them.
    named: dict[tuple[str, int], set[int]] = {}
    running: dict[tuple[str, int], int] = {}
    if sharing:
        first_copy = {}
        for c in copies:
            first_copy.setdefault(c["report"], c["document_id"])
        con.execute("create temp table one_copy (report VARCHAR, document_id VARCHAR)")
        con.executemany("insert into one_copy values (?, ?)", list(first_copy.items()))
        patterns = {
            report: [(wid, _name_pattern(wells[wid]["name"]))
                     for wid in sorted(group, key=lambda w: -len(wells[w]["name"] or ""))
                     if wid in wells and wells[wid]["name"]]
            for report, group in sharing.items()
        }
        last: dict[str, int] = {}
        for report, page_no, text in con.execute(
            "select o.report, p.page_no, p.text from page p join one_copy o using (document_id) "
            "order by o.report, p.page_no"
        ).fetchall():
            found, rest = set(), text or ""
            for wid, pattern in patterns[report]:  # longest name first: '2/4-14 R2' before 'R'
                rest, hits = pattern.subn(" ", rest)
                if hits:
                    found.add(wid)
            named[(report, page_no)] = found
            if len(found) == 1:
                last[report] = next(iter(found))
            if report in last:
                running[(report, page_no)] = last[report]

    def fits(wid: int, depth: float | None) -> bool:
        td = wells.get(wid, {}).get("total_depth_md")
        return depth is None or td is None or td + TD_MARGIN_M >= depth

    by_page = by_depth = 0

    def assign(report: str, page_no: int, depth: float | None) -> int:
        nonlocal by_page, by_depth
        on_page = named.get((report, page_no)) or set()
        if len(on_page) != 1 and (report, page_no) in running:
            on_page = {running[(report, page_no)]}
        candidates = {w for w in on_page if fits(w, depth)}
        if len(candidates) == 1:
            by_page += 1
            return next(iter(candidates))
        by_depth += 1
        return home(candidates or sharing[report], depth)

    kept: list[dict[str, Any]] = []
    seen: dict[tuple, dict[str, Any]] = {}
    dropped = moved = 0
    for row in events:
        report = report_of.get(row["document_id"])
        if report is None:
            kept.append(row)
            continue
        depth = _deepest(row, "top_md", "base_md", "context_top_md", "context_base_md")
        if row["depth_ref"] not in MD_REFS:
            depth = None
        target_wb = assign(report, row["page_no"], depth)
        key = (report, row["page_no"], row["event_type"],
               " ".join((row["text_span"] or "").split()))
        held = seen.get(key)
        if held is None:
            seen[key] = {**row, "_home": target_wb}
        else:
            dropped += 1
            if row["wellbore_id"] == target_wb and held["wellbore_id"] != held["_home"]:
                seen[key] = {**row, "_home": target_wb}
    for row in seen.values():
        target_wb = row.pop("_home")
        if row["wellbore_id"] != target_wb:
            # the home wellbore's own copy is gone (merged earlier): move this one there
            row["wellbore_id"] = target_wb
            rejoin(row)
            moved += 1
        kept.append(row)
    stats["assigned_by_name_on_page"] = by_page
    stats["assigned_by_drilling_order_and_depth"] = by_depth
    stats["shared_report_copies_dropped"] = dropped
    stats["shared_report_events_moved"] = moved

    # --- 3. the same incident on several pages -----------------------------------------------
    merged = merge_duplicates(kept)
    stats["merged_after_repair"] = len(kept) - len(merged)
    stats["events_out"] = len(merged)

    cols = ", ".join(f"{c} {t}" for c, t in TABLES["event"].items())
    con.execute(f"create table event_out ({cols})")
    marks = ", ".join("?" for _ in columns)
    if merged:
        con.executemany(f"insert into event_out values ({marks})",
                        [[row[c] for c in columns] for row in merged])
    tmp = target.with_suffix(".parquet.tmp")
    con.execute(
        f"copy (select * from event_out order by wellbore_id, top_md, event_id) "
        f"to '{_q(tmp)}' (format parquet)"
    )
    con.close()
    tmp.replace(target)
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kg", default="data/kg")
    args = parser.parse_args(argv)
    print(json.dumps(clean_events(args.kg), indent=2))


if __name__ == "__main__":
    main()
