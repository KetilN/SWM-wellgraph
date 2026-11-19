"""Page routing: which pages are worth an LLM call. Keyword evidence, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wellgraph.extract.routing import (
    SEND_THRESHOLD,
    RouteResult,
    page_fields,
    route_report,
    score_page,
)
from wellgraph.graph.schema import EVENT_TYPES

SAMPLES = sorted((Path(__file__).parents[1] / "samples" / "reports").glob("*/WELL/*.JSON"))

OPS_TAGS = ["drilling", "daily_report", "mud_weight"]


def route(text, tags=(), summary=""):
    result = score_page(text, list(tags), summary)
    assert isinstance(result, RouteResult)
    assert set(result.matched_types) <= set(EVENT_TYPES)
    assert result.send == (result.score >= SEND_THRESHOLD)
    return result


# ------------------------------------------------------------ true positives


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        (("Drilled to 2926 m and raised mudweight to 1.35 S.G. Had problems with tight hole. "
         "Reamed and washed."), "tight_hole"),
        ("POOH. Tight at 1121-1110, 10 MT max. o'pull. Topped up hole at 160 m.", "tight_hole"),
        ("POOH to shoe, max overpull 68 tons. RIH to 1275 m. Reamed from 1275 to 1290 m.",
         "tight_hole"),
        ("At 2322m, an 84 bbl loss to the hole was recorded. A 60 bbl LCM pill was pumped.",
         "lost_circulation"),
        ("Noticed mudlosses (60 bbls to formation) at 2950 m.", "lost_circulation"),
        ("Lost returns at 2500 m, 12 m3/hr, mud weight 1.45 sg.", "lost_circulation"),
        ("Pumped casing volume with mud losses (70% returns).", "lost_circulation"),
        ("Lost circulation while drilling at 1938 m.", "lost_circulation"),
        ("Upon drilling out the shoe with 1.20 sg mud the well kicked.", "kick"),
        (("Flowchecked the well due to pit gain (2 bbls in 40 mins.). Shut in well. Increased "
         "mud weight to 11.4 ppg."), "kick"),
        ("Well was flowing. Shut in well with 1.5 m3 gain at 1965 m.", "kick"),
        ("Took a 3 m3 influx at 3300 m. Circulated kill mud of 1.60 sg.", "kick"),
        ("At 4306m the pipe got stuck with the bit at 4304m.", "stuck_pipe"),
        ("Continued to jar on stuck pipe, no progress. Ran free point and backed off.",
         "stuck_pipe"),
        ("Fishing assembly. Top of fish at 1022m. Washed over fish from 4140m to 4277m.",
         "fishing"),
        ("At 2747m the drill string twisted off in the lower service break threads.", "fishing"),
        ("Unable to recover the fish. Plugged back and had to sidetrack the well from 2600 m.",
         "sidetrack"),
        ("The string became differentially stuck at 2810 m.", "stuck_pipe"),
        ("Large amounts of cavings over the shakers while drilling 8 1/2\" hole at 2300 m.",
         "wellbore_instability"),
        ("The hole packed off at 1930m and was backreamed to the 20\" shoe.", "pack_off"),
        ("Sloughing shale caused 8m of fill on bottom at 2040 m.", "wellbore_instability"),
        (("Caliper showed the hole washed out to 22\" from 1100 m to 1180 m; hole collapse "
         "suspected."), "washout"),
        ("Unstable shale: hole collapsed around the BHA at 2210 m.", "wellbore_instability"),
    ],
)
def test_event_text_is_sent_with_the_right_type(text, expected_type):
    result = route(text, OPS_TAGS)
    assert result.send, result
    assert expected_type in result.matched_types
    assert result.reasons


def test_event_text_is_sent_even_without_tags_or_summary():
    assert route("At 4306m the pipe got stuck with the bit at 4304m.").send
    assert route("Lost circulation at 1938 m. Pumped LCM.").send


def test_event_named_only_in_the_summary_is_sent():
    result = route(
        "| 12 | 4345 | 16.0 | see remarks |",
        ["drilling"],
        "Daily log: stuck pipe at 4306 m, jarring and fishing for eight days.",
    )
    assert result.send and "stuck_pipe" in result.matched_types


def test_several_types_on_one_page():
    result = route("Lost returns at 2500 m. Pipe stuck at 2510 m, jarred free. Cavings seen.")
    assert {"lost_circulation", "stuck_pipe", "wellbore_instability"} <= set(result.matched_types)


# ----------------------------------------------------------- false positives


@pytest.mark.parametrize(
    "text",
    [
        "| Fluid loss ml | cake | HTHP 500 psi | pH |\n| 4.2 | 1 | 12 | 9.5 |",
        "Reduce HPHT fluid loss with addition of CC-16. API filtrate loss 5 ml.",
        "The kick-off point was at 1761 m. Kicked off the well and built angle to 24 deg.",
        "KOP at 650 m. Kick off and directional drilling started with low RPM.",
        "0130 Held kick drill. 0600 Drilled 12 1/4\" hole from 2742 m to 2816 m.",
        "Tested choke and kill lines to 20/690 bar. Installed kill/choke goosenecks.",
        "13 3/8\" shoe LOT required for kick tolerance in the 12 1/4\" section.",
        "| ANGLE | OFFSET | KICK PAD | ECCENTRIC BLADE STABILIZER |",
        "Very high stick and slip shocks were experienced. Stick-slip in the limestone.",
        "| Thread loss ...... | 0.0000 m | Vam Top |\n| Make up loss | 0.0000 m |",
        "SY. SEIS. Prim. without Transm. Loss 35 Hz. Min. Ph.",
        ("LTI Lost Time Incident  MD Measured depth  LOT Leak Off Test  LCM Lost Circulation "
        "Material  POOH Pull out of hole  RIH Run in hole  WOW Waiting on weather"),
        "| 10 | 2423 | Lost |\n| 9 | 2425 | Misfire |\n| 8 | 2431 | Lost |\n| 7 | 2441 | Lost |",
        ("Initial shut-in 67 minutes. The well was shut in downhole for the main build up. "
        "SHUT-IN PRESSURE 86.2 bar. SHUT-IN TIME 5 min."),
        "Claystone: grey, soft, sticky, silty, calcareous. Sandstone: tight, well cemented.",
        "Packoff Type: SS-15  Packoff Length: 0.4 M  Hanger Type: DRIL-QUIP",
        "Washpipe no. 9 failed after four hours and showed washout on the packing.",
        "Drilled 17 1/2\" hole from 653m to 1059m. Reamed each stand before connection.",
        "| Left in hole | m3 | 0 | 0 |\n| Back-loaded | m3 | 132 | 132 |",
        "BHA No. 3  Sidetrack No.  Job No. 0940-00010  Company CONOCO",
        "BHA: 8 1/2\" bit, NB stab, 6 x 6 1/2\" DC, JAR, 2 x 6 1/2\" DC, 9 x 5\" HWDP.",
        "Benefits were gained by using an ongoing crew change schedule.",
        "",
    ],
)
def test_lookalike_text_is_not_sent(text):
    result = route(text, OPS_TAGS)
    assert not result.send, result
    assert result.matched_types == []


def test_fluid_loss_does_not_hide_real_losses_on_the_same_page():
    result = route("Fluid loss 4.2 ml. At 2322m, an 84 bbl loss to the hole was recorded.")
    assert result.send and "lost_circulation" in result.matched_types


def test_kick_off_does_not_hide_a_real_kick_on_the_same_page():
    result = route("Kick-off point 1761 m. At 3300 m the well kicked; took 2 m3 influx.")
    assert result.send and "kick" in result.matched_types


def test_non_operations_pages_are_penalised():
    text = "Core no. 3: sandstone, tight, lost 0.5 m of core. Overpull on core barrel."
    plain = route(text, ["drilling"])
    core = route(text, ["core_description", "lithology"], "Core description for core 3.")
    assert core.score < plain.score
    assert any("non-operations" in r for r in core.reasons)


def test_table_of_contents_is_not_sent():
    text = "TABLE OF CONTENTS\n4.3 Stuck pipe ..... 31\n4.4 Lost circulation ..... 35\n"
    assert not route(text, ["table_of_contents"], "Table of contents of the report.").send


def test_hints_and_measurements_raise_the_score_but_never_send_alone():
    text = "Drilled 12 1/4\" hole from 2742 m to 2816 m. Mud weight 1.33 SG."
    assert not route(text, ["drilling", "daily_report", "mud_weight", "lessons_learned", "NPT"],
                     "Daily drilling report.").send
    weak = "Had some drag on the wiper trip."
    bare = route(weak)
    hinted = route(weak + " Drilled to 2816 m with 1.33 SG mud.", OPS_TAGS)
    assert hinted.score > bare.score


def test_tags_are_matched_case_insensitively():
    weak = "Had some drag on the wiper trip at 2816 m."
    assert route(weak, ["Daily_Report", "NPT"]).score > route(weak).score


# ------------------------------------------------------------- route_report


def _page(page_no, text, tags=(), summary=""):
    return {
        "page_no": page_no,
        "OCR": {"ocr_markdown": text},
        "AI": {"tags": list(tags), "page_summary": summary},
    }


PAGES = [
    _page(0, "FINAL WELL REPORT", ["cover_page"]),
    _page(1, "At 4306m the pipe got stuck. Jarred for 6 hrs. Fishing. Lost returns.", OPS_TAGS),
    _page(2, "Sandstone: grey, fine, friable.", ["lithology"]),
    _page(3, "Tight hole at 1950 m, max overpull 40 tons.", OPS_TAGS),
    _page(4, "Lost circulation at 2500 m, pumped LCM pill.", OPS_TAGS),
]


def test_page_fields_tolerates_missing_parts():
    assert page_fields({"page_no": 3}) == ("", [], "")
    assert page_fields({"OCR": {"ocr_markdown": None}, "AI": {"tags": None}}) == ("", [], "")
    assert page_fields(PAGES[3]) == ("Tight hole at 1950 m, max overpull 40 tons.", OPS_TAGS, "")


def test_route_report_one_result_per_page_in_order():
    results = route_report(PAGES)
    assert [r.page_no for r in results] == [0, 1, 2, 3, 4]
    assert [r.send for r in results] == [False, True, False, True, True]


def test_route_report_cap_keeps_the_highest_scores():
    results = route_report(PAGES, max_pages=1)
    assert [r.page_no for r in results] == [0, 1, 2, 3, 4]
    sent = [r for r in results if r.send]
    assert [r.page_no for r in sent] == [1]
    dropped = [r for r in results if r.page_no in (3, 4)]
    assert all(not r.send and any("cap" in reason for reason in r.reasons) for r in dropped)
    assert all(r.score >= SEND_THRESHOLD for r in dropped)  # score is kept, only send changes


def test_route_report_cap_larger_than_candidates_changes_nothing():
    assert [r.send for r in route_report(PAGES, max_pages=50)] == [
        r.send for r in route_report(PAGES)]
    assert route_report([]) == []


# ------------------------------------------------------- real sample reports


def _load(path):
    doc = json.loads(path.read_text())
    return next(iter(doc.values()))["pages"]


@pytest.mark.skipif(len(SAMPLES) < 4, reason="sample reports not present")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.parts[-3])
def test_samples_send_a_minority_of_pages(path):
    results = route_report(_load(path))
    sent = sum(r.send for r in results)
    assert 0 < sent < 0.4 * len(results)


@pytest.mark.skipif(len(SAMPLES) < 4, reason="sample reports not present")
def test_known_event_pages_in_samples_are_sent():
    # pages read by hand: 25/7-2 p63 stuck pipe and fishing at 4306 m, p54 mud losses and LCM;
    # 25/7-3 p94 well flowing, shut in with 1.5 m3 gain; 25/6-3 p49 tight hole and overpull.
    known = {"25_7-2": [54, 63], "25_7-3": [94], "25_6-3": [49]}
    for path in SAMPLES:
        results = {r.page_no: r for r in route_report(_load(path))}
        for page_no in known.get(path.parts[-3], []):
            assert results[page_no].send, (path.parts[-3], page_no, results[page_no])


@pytest.mark.parametrize(
    "text",
    [
        "Nearest Well Control: 6507/7-2 1.6 km NNW (distance/direction from 6507/7-9)",
        "RKB - Wellhead 146.53 M  RKB - Hanger 147.97 M  RKB - Packoff 147.82 M",
        "| Jarring Services: | Eastman Whipstock |\n| Stabilizers: | Chr. Diamond |",
        "No hole problems were encountered within this 36\" section to 260 m.",
        "Top plug bumped. Floats holding. No losses during cementing and displacement.",
        "| 13 | 4501 | 4493.5 | 12213 | Very tight. 250 psi after 3 min. |\n| 14 | Very tight. |",
        "| Depth | Rec | Lithology | Shows, cavings, mudadditives, etc. |\n| 1520 | 95 | Clst |",
        "The well programme indicates shallow gas at 282 m RKB. No shallow gas was reported.",
    ],
)
def test_form_fields_headings_and_negatives_are_not_sent(text):
    assert not route(text, OPS_TAGS).send


def test_gas_seen_at_the_wellhead_is_sent_as_a_kick():
    result = route("Gas bubbles were observed from the wellhead when drilling at 609m RKB.")
    assert result.send and "kick" in result.matched_types


def test_negated_shallow_gas_and_drag_are_not_evidence():
    assert not route("Site survey report. No shallow gas encountered in the 36\" hole to 227 m.",
                     ["drilling"], "No shallow gas encountered.").send
    assert route("Pull out of hole, no drag. Rig up and run logs.", OPS_TAGS).score == 0
