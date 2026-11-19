"""Synthetic fixture graph.

All wells are invented (block 99/x does not exist) so no test asserts a fake fact about
a real well. Geometry: W1 is the reference; W2 is ~10 km north, W3 ~30 km north, W4 is
far away in another block.
"""

from __future__ import annotations

import duckdb
import pytest

from wellgraph.graph.schema import TABLES

WELLBORES = [
    # id, name, well, type, purpose, operator, field, rig, entry, completion, td, wd, lat, lon, url
    (1, "99/1-1", "99/1-1", "EXPLORATION", "WILDCAT", "Alpha Oil", "ALFA", "Rig One",
     "1990-01-01", "1990-03-01", 3200.0, 110.0, 60.00, 2.00, "https://example.test/1"),
    (2, "99/1-2 A", "99/1-2", "DEVELOPMENT", "PRODUCTION", "Alpha Oil", "ALFA", "Rig One",
     "1995-01-01", "1995-02-15", 3400.0, 112.0, 60.09, 2.00, "https://example.test/2"),
    (3, "99/1-3", "99/1-3", "EXPLORATION", "APPRAISAL", "Beta Energy", None, "Rig Two",
     "2001-05-01", "2001-07-01", 3600.0, 115.0, 60.27, 2.00, "https://example.test/3"),
    (4, "99/2-1", "99/2-1", "EXPLORATION", "WILDCAT", "Beta Energy", "BRAVO", "Rig Two",
     "2005-05-01", "2005-06-20", 2900.0, 300.0, 61.50, 3.00, "https://example.test/4"),
]

FORMATION_TOPS = [
    # wellbore_id, formation, level, parent, top, base
    (1, "NORDLAND GP", "GROUP", None, 130.0, 900.0),
    (1, "SHETLAND GP", "GROUP", None, 2400.0, 2900.0),
    (1, "TOR FM", "FORMATION", "SHETLAND GP", 2400.0, 2650.0),
    (2, "SHETLAND GP", "GROUP", None, 2450.0, 2950.0),
    (2, "TOR FM", "FORMATION", "SHETLAND GP", 2450.0, 2700.0),
    (3, "SHETLAND GP", "GROUP", None, 2500.0, 3000.0),
    (4, "NORDLAND GP", "GROUP", None, 320.0, 1100.0),
]

HOLE_SECTIONS = [
    # wellbore_id, hole_in, base_md, casing_type, casing_in, casing_depth, lot_sg
    (1, 17.5, 1200.0, "INTERM.", 13.375, 1195.0, 1.55),
    (1, 12.25, 2950.0, "INTERM.", 9.625, 2945.0, 1.78),
    (2, 12.25, 3000.0, "INTERM.", 9.625, 2995.0, 1.80),
    (3, 12.25, 3050.0, "INTERM.", 9.625, 3045.0, 1.82),
]

DOCUMENTS = [
    ("doc-1", 1, "COMPLETION_REPORT.PDF", "99_1-1/WELL/99_1-1_COMPLETION_REPORT.PDF", 120),
    ("doc-2", 2, "COMPLETION_REPORT_1.PDF", "99_1-2_A/WELL/99_1-2_A_COMPLETION_REPORT_1.PDF", 80),
    ("doc-3", 3, "COMPLETION_REPORT.PDF", "99_1-3/WELL/99_1-3_COMPLETION_REPORT.PDF", 95),
]

EVENTS = [
    # id, wb, type, top, base, mw, hole, formations, description, remedy, lesson, doc, page, span, conf
    ("ev-1", 1, "lost_circulation", 2500.0, 2520.0, 1.45, 12.25, ["SHETLAND GP", "TOR FM"],
     "Lost 12 m3/hr while drilling.", "Pumped LCM pill.", "Keep ECD below 1.50 sg.",
     "doc-1", 41, "Lost returns at 2500 m, 12 m3/hr, mud weight 1.45 sg.", 0.9),
    ("ev-2", 2, "lost_circulation", 2600.0, 2610.0, 1.50, 12.25, ["SHETLAND GP", "TOR FM"],
     "Total losses.", "Set cement plug.", None,
     "doc-2", 17, "Total losses at 2600 m with 1.50 sg mud.", 0.8),
    ("ev-3", 2, "tight_hole", 1000.0, 1050.0, 1.20, 17.5, [],
     "Tight hole while tripping.", "Reamed.", None,
     "doc-2", 9, "Tight hole 1000-1050 m, reamed and washed.", 0.7),
    ("ev-4", 3, "lost_circulation", 2700.0, 2705.0, 1.52, 12.25, ["SHETLAND GP"],
     "Partial losses.", "Reduced pump rate.", None,
     "doc-3", 55, "Partial losses at 2700 m, 1.52 sg.", 0.85),
    ("ev-5", 3, "kick", 3300.0, 3300.0, 1.60, 8.5, [],
     "Took 2 m3 influx.", "Shut in, circulated out.", "Raise mud weight earlier.",
     "doc-3", 70, "Kick taken at 3300 m, 2 m3 gain.", 0.95),
]

ROWS = {
    "wellbore": WELLBORES,
    "formation_top": FORMATION_TOPS,
    "hole_section": HOLE_SECTIONS,
    "document": DOCUMENTS,
    "event": EVENTS,
}


def write_tables(directory, tables):
    con = duckdb.connect()
    for table in tables:
        cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
        con.execute(f"create table {table} ({cols})")
        marks = ", ".join("?" for _ in TABLES[table])
        con.executemany(f"insert into {table} values ({marks})", ROWS[table])
        con.execute(f"copy {table} to '{directory}/{table}.parquet' (format parquet)")
    con.close()


@pytest.fixture
def kg_dir(tmp_path):
    write_tables(tmp_path, list(ROWS))
    return tmp_path


@pytest.fixture
def backbone_only_dir(tmp_path):
    """Graph before any extraction has run: no document or event tables."""
    write_tables(tmp_path, ["wellbore", "formation_top", "hole_section"])
    return tmp_path
