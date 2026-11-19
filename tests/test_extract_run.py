import json

import duckdb
import httpx
import pytest

from wellgraph.extract.models import PageExtraction
from wellgraph.extract.run import (
    OfflineClient,
    OpenAICompatibleClient,
    ResponseCache,
    extract_events,
    merge_duplicates,
)
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
    write(con, "document", [("doc-1", 1, "REPORT.PDF", "99_1-1/WELL/REPORT.PDF", 3)],
          list(TABLES["document"])[:5])
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


# --- runner robustness ---------------------------------------------------------------

def test_a_failing_page_is_skipped_and_retried_on_the_next_run(kg, tmp_path):
    class Flaky(FakeClient):
        def complete(self, messages, schema):
            if "Took a kick" in messages[-1]["content"]:
                raise RuntimeError("Model call failed after 5 attempts: HTTP 429")
            return super().complete(messages, schema)

    stats = run(kg, Flaky({"Lost returns": {"events": [event()]}}), tmp_path)
    assert stats["pages_failed"] == 1 and stats["events_written"] == 1

    retry = FakeClient({})
    stats = run(kg, retry, tmp_path)
    assert len(retry.calls) == 1 and stats["cache_hits"] == 1  # only the failed page


def test_parallel_workers_give_the_same_result(kg, tmp_path):
    replies = {"Lost returns": {"events": [event()]}}
    one = run(kg, FakeClient(replies), tmp_path / "a", workers=1)
    four = run(kg, FakeClient(replies), tmp_path / "b", workers=4)
    assert one["events_written"] == four["events_written"] == 1
    assert one["model_calls"] == four["model_calls"] == 2


# --- HTTP client ---------------------------------------------------------------------

def reply_json(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_client(handler, **kw):
    waits = []
    client = OpenAICompatibleClient(
        "https://llm.test/v1/", "secret", "small-model",
        transport=httpx.MockTransport(handler), sleep=waits.append, **kw,
    )
    return client, waits


def test_client_sends_schema_auth_and_extra_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return reply_json('{"events": []}')

    client, _ = make_client(handler, extra_headers={"X-Integration": "demo"})
    out = client.complete([{"role": "user", "content": "hi"}], {"type": "object"})

    assert out == '{"events": []}'
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert seen["headers"]["x-integration"] == "demo"
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert seen["body"]["model"] == "small-model"


def test_client_honours_retry_after_then_succeeds():
    answers = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(503),
               reply_json("{}")]
    client, waits = make_client(lambda request: answers.pop(0))
    assert client.complete([], {}) == "{}"
    assert waits == [7.0, 2.0]  # server's value first, then exponential back-off


def test_client_gives_up_after_retries_and_does_not_retry_client_errors():
    client, waits = make_client(lambda request: httpx.Response(429), retries=3)
    with pytest.raises(RuntimeError, match="after 3 attempts: HTTP 429"):
        client.complete([], {})
    assert len(waits) == 2

    calls = []
    client, waits = make_client(lambda request: calls.append(1) or httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        client.complete([], {})
    assert len(calls) == 1 and waits == []


def test_client_treats_an_empty_reply_as_an_error():
    client, _ = make_client(lambda request: reply_json(""))
    with pytest.raises(RuntimeError, match="empty reply"):
        client.complete([], {})


def test_client_unfences_and_re_asks_once_when_the_reply_is_invalid():
    ev = event()
    bad = {"events": [{k: v for k, v in ev.items() if k != "text_span"}]}  # no quote
    answers = [f"```json\n{json.dumps(bad)}\n```", f"```json\n{json.dumps({'events': [ev]})}\n```"]
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return reply_json(answers.pop(0))

    client, _ = make_client(handler)
    out = client.complete([{"role": "user", "content": "page"}], {})

    assert PageExtraction.model_validate_json(out).events[0].description == ev["description"]
    assert len(bodies) == 2 and "failed validation" in bodies[1]["messages"][-1]["content"]


# --- duplicates and offline rebuild --------------------------------------------------

def row(event_id, top, conf, wellbore_id=1, kind="stuck_pipe", mw=None, quote="q"):
    return {"event_id": event_id, "wellbore_id": wellbore_id, "event_type": kind,
            "top_md": top, "base_md": top, "confidence": conf, "mud_weight_sg": mw,
            "text_span": quote}


def test_same_event_on_several_pages_is_merged_keeping_the_best_row():
    rows = [row("a", 4340.0, 0.6), row("b", 4343.0, 0.9), row("c", 4347.0, 0.7),
            row("d", 4400.0, 0.5), row("e", 4340.0, 0.9, kind="fishing"),
            row("f", 4340.0, 0.9, wellbore_id=2)]
    assert sorted(r["event_id"] for r in merge_duplicates(rows)) == ["b", "d", "e", "f"]


def test_events_without_depth_merge_only_on_identical_quote():
    rows = [row("a", None, 0.5, quote="Stuck  pipe."), row("b", None, 0.8, quote="Stuck pipe."),
            row("c", None, 0.9, quote="Stuck again.")]
    assert sorted(r["event_id"] for r in merge_duplicates(rows)) == ["b", "c"]


def test_a_chain_of_nearby_depths_does_not_swallow_a_whole_section():
    # each is within 10 m of the previous, but the group is anchored on its first depth
    rows = [row(str(i), 1000.0 + 8 * i, 0.5) for i in range(6)]  # 1000 ... 1040
    assert len(merge_duplicates(rows)) == 3


def test_offline_rebuild_uses_only_cached_replies(kg, tmp_path):
    run(kg, FakeClient({"Lost returns": {"events": [event()]}}), tmp_path)
    (kg / "event.parquet").unlink()
    stats = run(kg, OfflineClient("fake-model"), tmp_path)
    assert stats["model_calls"] == 0 and stats["cache_hits"] == 2
    assert stats["events_written"] == 1 and (kg / "event.parquet").exists()


def test_fenced_reply_already_in_the_cache_is_still_read(kg, tmp_path):
    fenced = "```json\n" + json.dumps({"events": [event()]}) + "\n```"
    stats = run(kg, FakeClient({"Lost returns": fenced}), tmp_path)
    assert stats["events_written"] == 1 and stats["pages_failed"] == 0


# --- context depth in the runner -----------------------------------------------------

CONTEXT_PAGE = (
    "## 12 1/4\" hole section: 2400 - 2900 m\n\n"
    "At 2620 m the string stalled out and the pump pressure rose. The hole packed off and "
    "the string had to be worked free. Drilling continued. Later the hole was tight on the "
    "wiper trip and had to be reamed."
)


@pytest.fixture
def kg_context(kg):
    con = duckdb.connect()
    con.execute(f"create table page as select * from read_parquet('{kg}/page.parquet')")
    con.execute("insert into page values ('doc-1', 3, ?, 'Section summary.', 'Reliable')",
                [CONTEXT_PAGE])
    con.execute(f"copy page to '{kg}/page.parquet' (format parquet)")
    con.close()
    return kg


def context_event(**kw):
    return event(top_md=None, base_md=None, mud_weight_sg=None, mud_weight_raw=None,
                 mud_weight_kind=None, **kw)


def test_context_depths_give_hole_section_and_formations(kg_context, tmp_path):
    client = FakeClient({"string stalled out": {"events": [
        context_event(event_type="pack_off", context_top_md=2620.0, context_base_md=2620.0,
                      text_span="The hole packed off and the string had to be worked free."),
        context_event(event_type="tight_hole", context_top_md=2400.0, context_base_md=2900.0,
                      hole_size_stated='12 1/4"',
                      text_span="Later the hole was tight on the wiper trip and had to be reamed."),
    ]}})
    stats = run(kg_context, client, tmp_path)
    assert stats["events_written"] == 2
    rows = {e["event_type"]: e for e in GraphStore(kg_context).query_events(limit=50)["results"]}
    assert rows["pack_off"]["depth_basis"] == "context_point"
    assert rows["pack_off"]["hole_diameter_in"] == 12.25
    assert rows["pack_off"]["formations"] == ["SHETLAND GP", "TOR FM"]
    assert rows["tight_hole"]["depth_basis"] == "context_interval"
    assert rows["tight_hole"]["hole_diameter_in"] == 12.25
    assert rows["tight_hole"]["top_md"] is None


def test_context_depth_not_on_the_page_is_discarded_but_the_event_kept(kg_context, tmp_path):
    client = FakeClient({"string stalled out": {"events": [
        context_event(event_type="pack_off", context_top_md=3333.0, hole_size_stated='12 1/4"',
                      text_span="The hole packed off and the string had to be worked free."),
    ]}})
    stats = run(kg_context, client, tmp_path)
    assert stats["events_written"] == 1 and stats["context_depths_discarded"] == 1
    (e,) = GraphStore(kg_context).query_events(event_type="pack_off")["results"]
    assert e["depth_basis"] == "none" and e["context_top_md"] is None
    assert e["hole_diameter_in"] == 12.25  # from the page's own section heading


def test_time_breakdown_rows_are_dropped(kg, tmp_path):
    client = FakeClient({"Lost returns": {"events": [
        event(), event(top_md=None, base_md=None, text_span="1 Hour KT - Mud losses"),
    ]}})
    # the quote check would also drop it; the time-breakdown rule fires first and is counted
    stats = run(kg, client, tmp_path)
    assert stats["events_written"] == 1
    assert stats["drop_reasons"] == {"time_breakdown_row": 1}


def test_merge_prefers_the_row_with_a_stated_depth():
    a = {**row("a", None, 0.9), "depth_basis": "context_point", "context_top_md": 2620.0}
    b = {**row("b", 2622.0, 0.5), "depth_basis": "stated", "context_top_md": None}
    c = {**row("c", None, 0.9), "depth_basis": "context_interval", "context_top_md": 2400.0}
    assert sorted(r["event_id"] for r in merge_duplicates([a, b, c])) == ["b", "c"]


# --- mud weight from the SODIR mud table ---------------------------------------------

@pytest.fixture
def kg_mud(kg):
    con = duckdb.connect()
    con.execute("create table mud_check (wellbore_id BIGINT, md DOUBLE, mud_weight_sg DOUBLE, "
                "viscosity_mpas DOUBLE, yield_point_pa DOUBLE, mud_type VARCHAR, date_measured DATE)")
    con.execute("insert into mud_check (wellbore_id, md, mud_weight_sg) values "
                "(1, 2450.0, 1.42), (1, 2300.0, 1.58)")
    con.execute(f"copy mud_check to '{kg}/mud_check.parquet' (format parquet)")
    con.close()
    return kg


def test_missing_mud_weight_is_filled_from_sodir_and_labelled(kg_mud, tmp_path):
    client = FakeClient({"Lost returns": {"events": [
        event(mud_weight_sg=None, mud_weight_raw=None, mud_weight_kind=None)]}})
    stats = run(kg_mud, client, tmp_path)
    assert stats["mud_weights_from_sodir"] == 1
    (e,) = GraphStore(kg_mud).query_events(event_type="lost_circulation")["results"]
    assert e["mud_weight_sg"] == 1.42 and e["mud_weight_source"] == "sodir_mud_table"
    assert e["mud_weight_raw"] is None and e["mud_weight_kind"] == "mud_in_hole"


def test_a_mud_weight_from_the_report_is_never_overwritten(kg_mud, tmp_path):
    stats = run(kg_mud, FakeClient({"Lost returns": {"events": [event()]}}), tmp_path)
    assert stats["mud_weights_from_sodir"] == 0
    (e,) = GraphStore(kg_mud).query_events(event_type="lost_circulation")["results"]
    assert e["mud_weight_sg"] == 1.45 and e["mud_weight_source"] == "report"


def test_tvd_events_and_events_without_depth_are_not_filled(kg_mud, tmp_path):
    client = FakeClient({"Took a kick": {"events": [event(
        event_type="kick", top_md=2300.0, base_md=2300.0, depth_ref="TVD",
        mud_weight_sg=None, mud_weight_raw=None, mud_weight_kind=None,
        text_span="Took a kick at 2300 m TVD while drilling, 2 m3 gain.")]}})
    stats = run(kg_mud, client, tmp_path)
    assert stats["mud_weights_from_sodir"] == 0
    (e,) = GraphStore(kg_mud).query_events(event_type="kick")["results"]
    assert e["mud_weight_sg"] is None and e["mud_weight_source"] is None
