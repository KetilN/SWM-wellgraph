"""Event repairs after extraction: feet stored as metres, reports shared by wellbores."""

from datetime import date

import duckdb
import pytest

from wellgraph.extract.cleanup import _name_pattern, _says_feet, clean_events
from wellgraph.graph.schema import TABLES


def _write(kg, table, rows, columns=None):
    columns = columns or TABLES[table]
    con = duckdb.connect()
    con.execute(f"create table t ({', '.join(f'{c} {t}' for c, t in columns.items())})")
    if rows:
        marks = ", ".join("?" for _ in columns)
        con.executemany(f"insert into t values ({marks})",
                        [[r.get(c) for c in columns] for r in rows])
    con.execute(f"copy t to '{kg / (table + '.parquet')}' (format parquet)")
    con.close()


def _event(event_id, wellbore_id, document_id, page_no, text, **extra):
    row = {c: None for c in TABLES["event"]}
    row.update(event_id=event_id, wellbore_id=wellbore_id, event_type="lost_circulation",
               document_id=document_id, page_no=page_no, text_span=text, confidence=0.8,
               formations=[], depth_ref="ASSUMED_MD", depth_basis="stated")
    row.update(extra)
    return row


@pytest.fixture
def kg(tmp_path):
    _write(tmp_path, "wellbore", [
        {"wellbore_id": 1, "name": "99/1-1", "total_depth_md": 3000.0,
         "entry_date": date(1980, 1, 1)},
        {"wellbore_id": 2, "name": "99/1-1 A", "total_depth_md": 3500.0,
         "entry_date": date(1981, 1, 1)},
        {"wellbore_id": 3, "name": "99/2-1", "total_depth_md": 2500.0,
         "entry_date": date(1975, 1, 1)},
    ])
    _write(tmp_path, "document", [
        {"document_id": "d1", "wellbore_id": 1, "title": "shared"},
        {"document_id": "d2", "wellbore_id": 2, "title": "shared"},
        {"document_id": "d3", "wellbore_id": 3, "title": "feet report"},
    ])
    shared = ["Well 99/1-1 drilling summary. Lost returns at 1500 m.",
              "Lost 20 m3 while reaming at 2000 m.",
              "Sidetrack 99/1-1A. Lost returns at 2200 m.",
              "Lost circulation at 3400 m."]
    pages = [{"document_id": d, "page_no": i, "text": t}
             for d in ("d1", "d2") for i, t in enumerate(shared)]
    pages.append({"document_id": "d3", "page_no": 0,
                  "text": "Drilled to 6,890 ft. Lost returns at 6890'. POOH to 5000 ft."})
    pages.append({"document_id": "d3", "page_no": 1,
                  "text": "Drilled to 2100 m. Lost returns. Depth 6890 on the log."})
    _write(tmp_path, "page", pages)
    _write(tmp_path, "hole_section", [
        {"wellbore_id": 3, "hole_diameter_in": 17.5, "base_md": 1000.0},
        {"wellbore_id": 3, "hole_diameter_in": 12.25, "base_md": 2500.0},
    ])
    _write(tmp_path, "formation_top", [
        {"wellbore_id": 3, "formation": "SHETLAND GP", "level": "GROUP",
         "top_md": 2000.0, "base_md": 2400.0},
    ])
    _write(tmp_path, "mud_check", [{"wellbore_id": 3, "md": 2050.0, "mud_weight_sg": 1.4}],
           {"wellbore_id": "BIGINT", "md": "DOUBLE", "mud_weight_sg": "DOUBLE"})
    events = []
    for wid, doc in ((1, "d1"), (2, "d2")):
        events += [
            _event(f"s0-{wid}", wid, doc, 0, "Lost returns at 1500 m.", top_md=1500.0),
            _event(f"s1-{wid}", wid, doc, 1, "Lost 20 m3 while reaming at 2000 m.", top_md=2000.0),
            _event(f"s2-{wid}", wid, doc, 2, "Lost returns at 2200 m.", top_md=2200.0),
            _event(f"s3-{wid}", wid, doc, 3, "Lost circulation at 3400 m.", top_md=3400.0),
        ]
    events += [
        _event("feet", 3, "d3", 0, "Lost returns at 6890'.", top_md=6890.0,
               mud_weight_sg=1.9, mud_weight_kind="mud_in_hole",
               mud_weight_source="sodir_mud_table"),
        _event("metres", 3, "d3", 1, "Lost returns.", context_top_md=6890.0,
               context_base_md=6890.0, depth_basis="context_point", event_type="kick"),
    ]
    _write(tmp_path, "event", events)
    return tmp_path


def _events(kg):
    con = duckdb.connect()
    cur = con.execute(f"select * from '{kg / 'event.parquet'}'")
    names = [d[0] for d in cur.description]
    return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}


def test_feet_depth_is_converted_and_joined_again(kg):
    stats = clean_events(kg)
    row = _events(kg)["feet"]
    assert row["top_md"] == pytest.approx(2100.1, abs=0.1)
    assert row["hole_diameter_in"] == 12.25
    assert row["formations"] == ["SHETLAND GP"]
    assert row["mud_weight_sg"] == 1.4 and row["mud_weight_source"] == "sodir_mud_table"
    assert stats["depths_converted_from_feet"] == 1


def test_depth_beyond_total_depth_on_a_metric_page_is_left_alone(kg):
    clean_events(kg)
    assert _events(kg)["metres"]["context_top_md"] == 6890.0


def test_shared_report_events_are_kept_once_on_the_wellbore_the_page_names(kg):
    stats = clean_events(kg)
    events = _events(kg)
    shared = {k: v["wellbore_id"] for k, v in events.items() if k.startswith("s")}
    assert sorted(shared.values()) == [1, 1, 2, 2]
    assert {k[:2]: w for k, w in shared.items()} == {"s0": 1, "s1": 1, "s2": 2, "s3": 2}
    assert stats["shared_report_copies_dropped"] == 4
    assert stats["events_out"] == 6


def test_cleanup_is_stable_when_run_twice(kg):
    clean_events(kg)
    first = _events(kg)
    stats = clean_events(kg)
    assert _events(kg) == first
    assert stats["depths_converted_from_feet"] == 0


def test_name_pattern_does_not_match_a_longer_name():
    assert _name_pattern("99/1-1").search("well 99/1-1 was")
    assert not _name_pattern("99/1-1").search("well 99/1-11 was")
    assert _name_pattern("99/1-1 A").search("sidetrack 99/1-1A.")


def test_says_feet_needs_feet_marks_or_no_metre_depths():
    assert _says_feet("at 6890'", "6,890 ft and 5000 ft")
    assert _says_feet("Drilling - pipe stuck", "8-29 9642 stuck")
    assert not _says_feet("Lost returns.", "Drilled to 2100 m.")
