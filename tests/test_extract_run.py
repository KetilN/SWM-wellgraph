import json

import duckdb
import pytest

from wellgraph.extract.run import ResponseCache, extract_events
from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore

LOSS_PAGE = (
    "## 12 1/4\" hole section\n\n"
    "Drilled ahead to 2500 m with 1.45 sg mud. Lost returns at 2500 m, losses 12 m3/hr. "
    "Pumped LCM pill and regained full returns. Stuck pipe was not experienced."
)
QUIET_PAGE = "Table of contents\n\n1. Introduction\n2. Geology\n3. Appendix"
TVD_PAGE = (
    "Daily operations. Took a kick at 2300 m TVD while drilling, 2 m3 gain. Shut in well "
    "and circulated out the influx with 1.60 sg kill mud."
)


def write(con, table, rows, columns=None):
    cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
    con.execute(f"create table {table} ({cols})")
    names = columns or list(TABLES[table])
    marks = ", ".join("?" for _ in names)
    con.executemany(f"insert into {table} ({', '.join(names)}) values ({marks})", rows)


@pytest.fixture
def kg(tmp_path):
    con = duckdb.connect()
    write(con, "wellbore", [(1, "99/1-1", 60.0, 2.0)], ["wellbore_id", "name", "lat", "lon"])
    write(con, "formation_top", [
        (1, "SHETLAND GP", "GROUP", None, 2400.0, 2900.0),
        (1, "TOR FM", "FORMATION", "SHETLAND GP", 2400.0, 2650.0),
    ])
    write(con, "hole_section", [
        (1, 17.5, 1200.0, "INTERM.", 13.375, 1195.0, 1.55),
        (1, 12.25, 2950.0, "INTERM.", 9.625, 2945.0, 1.78),
    ])
    write(con, "document", [("doc-1", 1, "REPORT.PDF", "99_1-1/WELL/REPORT.PDF", 3)])
    write(con, "page", [
        ("doc-1", 0, QUIET_PAGE, "Table of contents.", "Reliable"),
        ("doc-1", 1, LOSS_PAGE, "Daily drilling report with losses.", "Reliable"),
        ("doc-1", 2, TVD_PAGE, "Daily report: kick.", "Reliable"),
    ])
    for table in ("wellbore", "formation_top", "hole_section", "document", "page"):
        con.execute(f"copy {table} to '{tmp_path}/{table}.parquet' (format parquet)")
    con.close()
    return tmp_path


def event(**kw):
    base = {
        "event_type": "lost_circulation", "top_md": 2500.0, "base_md": 2500.0,
        "depth_ref": None, "mud_weight_sg": 1.45, "mud_weight_raw": "1.45 sg",
        "mud_weight_kind": "mud_in_hole", "description": "Lost returns while drilling.",
        "remedy": "Pumped LCM pill.", "lesson": None,
        "text_span": "Lost returns at 2500 m, losses 12 m3/hr.", "confidence": 0.9,
    }
    return {**base, **kw}


class FakeClient:
    model = "fake-model"

    def __init__(self, replies):
        self.replies = replies  # substring of the page text -> reply (dict or raw string)
        self.calls = []

    def complete(self, messages, schema):
        user = messages[-1]["content"]
        self.calls.append(user)
        for needle, reply in self.replies.items():
            if needle in user:
                return reply if isinstance(reply, str) else json.dumps(reply)
        return json.dumps({"events": []})


def run(kg, client, tmp_path, **kw):
    return extract_events(kg, client, cache_dir=tmp_path / "cache", **kw)


def test_events_are_extracted_joined_and_served(kg, tmp_path):
    client = FakeClient({
        "Lost returns": {"events": [event()]},
        "Took a kick": {"events": [event(
            event_type="kick", top_md=2300.0, base_md=2300.0, depth_ref="TVD",
            mud_weight_sg=1.60, mud_weight_raw="1.60 sg", mud_weight_kind="mud_in_hole",
            description="Kick while drilling.", remedy="Shut in and circulated out.",
            text_span="Took a kick at 2300 m TVD while drilling, 2 m3 gain.",
        )]},
    })
    stats = run(kg, client, tmp_path)

    assert stats["pages_total"] == 3 and stats["pages_sent"] == 2  # contents page not sent
    assert stats["events_written"] == 2 and stats["events_dropped"] == 0

    store = GraphStore(kg)
    losses = store.query_events(event_type="lost_circulation", formation="Shetland Group",
                                hole_size="12 1/4")["results"]
    assert len(losses) == 1
    assert losses[0]["mud_weight_sg"] == 1.45 and losses[0]["depth_ref"] == "ASSUMED_MD"
    assert losses[0]["formations"] == ["SHETLAND GP", "TOR FM"]
    kick = store.query_events(event_type="kick")["results"][0]
    assert kick["depth_ref"] == "TVD" and kick["hole_diameter_in"] is None
    assert kick["formations"] == []
    ev = store.evidence(losses[0]["event_id"], include_page_text=True)
    assert ev["page_no"] == 1 and "Lost returns at 2500 m" in ev["page_text"]


def test_event_whose_quote_is_not_on_the_page_is_dropped(kg, tmp_path):
    client = FakeClient({"Lost returns": {"events": [
        event(),
        event(event_type="stuck_pipe", text_span="The string became stuck at 2510 m and was jarred free."),
    ]}})
    stats = run(kg, client, tmp_path)
    assert stats["events_written"] == 1 and stats["events_dropped"] == 1
    assert stats["drop_reasons"] == {"text_span_not_found": 1}


def test_malformed_model_output_is_counted_not_fatal(kg, tmp_path):
    client = FakeClient({"Lost returns": "this is not json", "Took a kick": {"events": [
        {"event_type": "explosion", "text_span": "x"}]}})
    stats = run(kg, client, tmp_path)
    assert stats["pages_failed"] == 2 and stats["events_written"] == 0
    assert GraphStore(kg).describe()["counts"]["event"] == 0  # empty table is still valid


def test_second_run_uses_the_cache_and_makes_no_calls(kg, tmp_path):
    first = FakeClient({"Lost returns": {"events": [event()]}})
    run(kg, first, tmp_path)
    assert len(first.calls) == 2
    second = FakeClient({})
    stats = run(kg, second, tmp_path)
    assert second.calls == [] and stats["cache_hits"] == 2 and stats["events_written"] == 1


def test_dry_run_calls_nothing_and_estimates_cost(kg, tmp_path):
    client = FakeClient({})
    stats = run(kg, client, tmp_path, dry_run=True)
    assert client.calls == [] and stats["pages_sent"] == 2
    assert stats["estimated_input_tokens"] > 0 and not (kg / "event.parquet").exists()


def test_page_cap_and_wellbore_filter(kg, tmp_path):
    client = FakeClient({})
    assert run(kg, client, tmp_path, dry_run=True, max_pages_per_report=1)["pages_sent"] == 1
    assert run(kg, client, tmp_path, dry_run=True, wellbores=["99/9-9"])["pages_total"] == 0


def test_cache_key_depends_on_model_and_text(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("m1", "page text", "reply")
    assert cache.get("m1", "page text") == "reply"
    assert cache.get("m2", "page text") is None and cache.get("m1", "other") is None
    assert ResponseCache(tmp_path).get("m1", "page text") == "reply"  # persisted
