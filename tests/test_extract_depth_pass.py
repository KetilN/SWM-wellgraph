import json

import duckdb
import pytest

from wellgraph.extract.depth_pass import DepthContext, build_depth_messages, resolve_depths
from wellgraph.extract.run import OfflineClient
from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore

PREVIOUS = (
    "## 12 1/4\" hole section: 2400 - 2900 m\n\n"
    "Drilled ahead from 2400 m with 1.45 sg mud. At 2620 m the string stalled out and the "
    "pump pressure rose sharply."
)
PAGE = "The hole packed off and the string had to be worked free. Drilling continued."


def write(con, table, rows, columns):
    cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
    con.execute(f"create table {table} ({cols})")
    marks = ", ".join("?" for _ in columns)
    con.executemany(f"insert into {table} ({', '.join(columns)}) values ({marks})", rows)
    return table


@pytest.fixture
def kg(tmp_path):
    con = duckdb.connect()
    write(con, "wellbore", [(1, "99/1-1")], ["wellbore_id", "name"])
    write(con, "formation_top", [(1, "SHETLAND GP", "GROUP", None, 2400.0, 2900.0)],
          list(TABLES["formation_top"]))
    write(con, "hole_section", [(1, 12.25, 2950.0, "INTERM.", 9.625, 2945.0, 1.78)],
          list(TABLES["hole_section"]))
    write(con, "document", [("doc-1", 1, "REPORT.PDF", "x.PDF", 2)], list(TABLES["document"]))
    write(con, "page", [("doc-1", 4, PREVIOUS, "s", "Reliable"), ("doc-1", 5, PAGE, "s", "Reliable")],
          list(TABLES["page"]))
    write(con, "event", [
        ("ev-none", 1, "pack_off", "The hole packed off and the string had to be worked free.",
         "doc-1", 5, "The hole packed off and the string had to be worked free.", 0.6, "none"),
        ("ev-stated", 1, "kick", "Kick.", "doc-1", 5, "Drilling continued.", 0.6, "stated"),
    ], ["event_id", "wellbore_id", "event_type", "description", "document_id", "page_no",
        "text_span", "confidence", "depth_basis"])
    con.execute("update event set top_md = 2500, base_md = 2500, formations = [] where event_id = 'ev-stated'")
    con.execute("update event set formations = [] where formations is null")
    for table in ("wellbore", "formation_top", "hole_section", "document", "page", "event"):
        con.execute(f"copy {table} to '{tmp_path}/{table}.parquet' (format parquet)")
    con.close()
    return tmp_path


class FakeClient:
    model = "fake"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, messages, schema):
        self.calls.append(messages[-1]["content"])
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def answer(**kw):
    return {"found": True, "context_top_md": 2620.0, "context_base_md": 2620.0,
            "hole_size_stated": '12 1/4"',
            "depth_evidence": "At 2620 m the string stalled out", **kw}


def event_row(kg, event_id):
    return {e["event_id"]: e for e in GraphStore(kg).query_events(limit=50)["results"]}[event_id]


def test_messages_carry_previous_pages_and_the_event(kg):
    messages = build_depth_messages("pack_off", "The hole packed off.", PAGE, [PREVIOUS], "99/1-1")
    user = messages[-1]["content"]
    assert user.index("2400 - 2900 m") < user.index("The hole packed off and the string")
    assert "<event>" in user and "pack off" in user.replace("_", " ")


def test_depth_found_on_the_previous_page_is_applied_and_joined(kg, tmp_path):
    client = FakeClient(answer())
    stats = resolve_depths(kg, client, tmp_path / "cache")
    assert stats == {"candidates": 1, "model_calls": 1, "cache_hits": 0, "resolved": 1,
                     "rejected": 0, "not_found": 0, "failed": 0}
    e = event_row(kg, "ev-none")
    assert e["depth_basis"] == "context_point" and e["context_top_md"] == 2620.0
    assert e["depth_ref"] == "ASSUMED_MD" and e["top_md"] is None
    assert e["hole_diameter_in"] == 12.25 and e["formations"] == ["SHETLAND GP"]
    assert len(client.calls) == 1  # the event that already has a depth is not asked about
    assert event_row(kg, "ev-stated")["depth_basis"] == "stated"


def test_interval_answer_becomes_context_interval(kg, tmp_path):
    resolve_depths(kg, FakeClient(answer(context_top_md=2400.0, context_base_md=2900.0,
                   depth_evidence='12 1/4" hole section: 2400 - 2900 m')), tmp_path / "c")
    e = event_row(kg, "ev-none")
    assert e["depth_basis"] == "context_interval"
    assert (e["context_top_md"], e["context_base_md"]) == (2400.0, 2900.0)


def test_depth_not_in_the_supplied_text_is_rejected(kg, tmp_path):
    stats = resolve_depths(kg, FakeClient(answer(context_top_md=3333.0, context_base_md=3333.0)),
                           tmp_path / "c")
    assert stats["rejected"] == 1 and stats["resolved"] == 0
    assert event_row(kg, "ev-none")["depth_basis"] == "none"


def test_evidence_quote_must_be_in_the_supplied_text(kg, tmp_path):
    stats = resolve_depths(kg, FakeClient(answer(depth_evidence="Somewhere at 2620 m maybe")),
                           tmp_path / "c")
    assert stats["rejected"] == 1


def test_not_found_and_bad_output_leave_the_event_alone(kg, tmp_path):
    none = {"found": False, "context_top_md": None, "context_base_md": None,
            "hole_size_stated": None, "depth_evidence": None}
    assert resolve_depths(kg, FakeClient(none), tmp_path / "a")["not_found"] == 1
    assert resolve_depths(kg, FakeClient("not json"), tmp_path / "b")["failed"] == 1
    assert event_row(kg, "ev-none")["depth_basis"] == "none"


def test_hole_size_alone_is_kept_when_no_depth_is_found(kg, tmp_path):
    reply = {"found": True, "context_top_md": None, "context_base_md": None,
             "hole_size_stated": '12 1/4"', "depth_evidence": '12 1/4" hole section'}
    stats = resolve_depths(kg, FakeClient(reply), tmp_path / "c")
    e = event_row(kg, "ev-none")
    assert stats["resolved"] == 1 and e["depth_basis"] == "none" and e["hole_diameter_in"] == 12.25


def test_rerun_uses_the_cache_and_offline_makes_no_calls(kg, tmp_path):
    resolve_depths(kg, FakeClient(answer()), tmp_path / "c")
    # put the event back to 'none' and resolve again from the cache only
    con = duckdb.connect()
    con.execute(f"create table e as select * from read_parquet('{kg}/event.parquet')")
    con.execute("update e set depth_basis = 'none', context_top_md = null, context_base_md = null, "
                "hole_diameter_in = null where event_id = 'ev-none'")
    con.execute(f"copy e to '{kg}/event.parquet' (format parquet)")
    con.close()
    stats = resolve_depths(kg, OfflineClient("fake"), tmp_path / "c")
    assert stats["cache_hits"] == 1 and stats["model_calls"] == 0 and stats["resolved"] == 1


def test_model_schema_is_closed():
    schema = DepthContext.model_json_schema()
    assert set(schema["properties"]) == {
        "found", "context_top_md", "context_base_md", "hole_size_stated", "depth_evidence"}
