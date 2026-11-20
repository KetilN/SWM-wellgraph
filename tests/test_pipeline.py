import json

import duckdb
import pytest

from wellgraph.extract.run import OfflineClient
from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore
from wellgraph.pipeline import run_pipeline

DAILY = (
    "## 12 1/4\" hole section: 2400 - 2900 m\n\n"
    "| Date | Days | Depth Meter | Mud Weight | Operation |\n| --- | --- | --- | --- | --- |\n"
    "| 21.04.90 | 84 | 2500m | 1.45 sg | Drilled to 2500m. Lost returns at 2500 m, losses 12 m3/hr. Pumped LCM pill. |\n"
    "| 22.04.90 | 85 | 2620m | 1.45 sg | Drilled to 2620m. |\n"
)
FOLLOW = ("The hole packed off and the pipe became stuck. Jarred and worked the string free. "
          "Continued drilling ahead.")


@pytest.fixture
def workspace(tmp_path):
    kg = tmp_path / "kg"
    kg.mkdir()
    con = duckdb.connect()
    rows = {
        "wellbore": ([(1, "99/1-1", "1990-01-01")], ["wellbore_id", "name", "entry_date"]),
        "formation_top": ([(1, "SHETLAND GP", "GROUP", None, 2400.0, 2900.0)],
                          list(TABLES["formation_top"])),
        "hole_section": ([(1, 12.25, 2950.0, "INTERM.", 9.625, 2945.0, 1.78)],
                         list(TABLES["hole_section"])),
    }
    for table, (data, columns) in rows.items():
        cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
        con.execute(f"create table {table} ({cols})")
        marks = ", ".join("?" for _ in columns)
        con.executemany(f"insert into {table} ({', '.join(columns)}) values ({marks})", data)
        con.execute(f"copy {table} to '{kg}/{table}.parquet' (format parquet)")
    con.close()

    reports = tmp_path / "reports" / "99_1-1" / "WELL"
    reports.mkdir(parents=True)
    doc = {"99_1-1/WELL/555_REPORT.PDF": {
        "original_document_metadata": {"wellboreName": "99/1-1", "wellName": "99/1-1",
                                       "wellborefileId": 555, "title": "REPORT.PDF"},
        "pages": [
            {"page_no": i, "page_metadata": {"quality": "Reliable"},
             "OCR": {"ocr_markdown": text}, "AI": {"page_summary": "Daily drilling report.", "tags": []}}
            for i, text in enumerate([DAILY, FOLLOW])
        ]}}
    (reports / "99_1-1_REPORT.JSON").write_text(json.dumps(doc))
    return tmp_path


class FakeClient:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, schema):
        self.calls += 1
        user = messages[-1]["content"]
        if "<event>" in user:  # second pass: where was the well?
            return json.dumps({"found": True, "context_top_md": 2620.0, "context_base_md": 2620.0,
                               "hole_size_stated": None, "depth_evidence": "Drilled to 2620m."})
        base = {"depth_ref": None, "mud_weight_sg": None, "mud_weight_raw": None,
                "mud_weight_kind": None, "remedy": None, "lesson": None, "confidence": 0.8,
                "context_top_md": None, "context_base_md": None, "hole_size_stated": None}
        if "Lost returns" in user:
            return json.dumps({"events": [{**base, "event_type": "lost_circulation",
                "top_md": 2500.0, "base_md": 2500.0, "description": "Lost returns.",
                "text_span": "Lost returns at 2500 m, losses 12 m3/hr."}]})
        if "packed off" in user:
            return json.dumps({"events": [{**base, "event_type": "pack_off", "top_md": None,
                "base_md": None, "description": "Hole packed off.",
                "text_span": "The hole packed off and the pipe became stuck."}]})
        return json.dumps({"events": []})


def test_one_call_takes_reports_to_a_queryable_graph(workspace):
    client = FakeClient()
    stats = run_pipeline(workspace / "reports", workspace / "kg", client,
                         cache_dir=workspace / "cache")

    assert list(stats) == ["backbone", "documents", "events", "depth_pass", "cleanup",
                           "daily_reports"]
    assert stats["backbone"] == "already built"
    assert stats["documents"]["documents"] == 1 and stats["documents"]["pages"] == 2
    assert stats["events"]["events_written"] == 2
    assert stats["depth_pass"]["resolved"] == 1
    assert stats["daily_reports"]["rows"] == 2

    store = GraphStore(workspace / "kg")
    events = {e["event_type"]: e for e in store.query_events(limit=10)["results"]}
    assert events["lost_circulation"]["depth_basis"] == "stated"
    assert events["pack_off"]["depth_basis"] == "context_point"
    assert events["pack_off"]["hole_diameter_in"] == 12.25
    assert events["pack_off"]["formations"] == ["SHETLAND GP"]
    assert (workspace / "kg" / "daily_report.parquet").is_file()


def test_second_run_costs_nothing(workspace):
    first = FakeClient()
    run_pipeline(workspace / "reports", workspace / "kg", first, cache_dir=workspace / "cache")
    assert first.calls == 3  # two pages, one depth question
    stats = run_pipeline(workspace / "reports", workspace / "kg", OfflineClient("fake"),
                         cache_dir=workspace / "cache")
    assert stats["events"]["model_calls"] == 0 and stats["depth_pass"]["model_calls"] == 0
    assert stats["events"]["events_written"] == 2 and stats["depth_pass"]["resolved"] == 1


def test_missing_backbone_is_a_clear_error_when_not_asked_to_build_it(tmp_path):
    (tmp_path / "kg").mkdir()
    (tmp_path / "reports").mkdir()
    with pytest.raises(FileNotFoundError, match="backbone"):
        run_pipeline(tmp_path / "reports", tmp_path / "kg", FakeClient(),
                     cache_dir=tmp_path / "cache", raw_dir=tmp_path / "none")
