"""Daily drilling report table parsing (deterministic, no model calls).

Snippets are copied from the OCR text of real report pages (operations text shortened); the
wellbore and page are named above each one. The few synthetic cases say so.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from wellgraph.extract.daily import (
    DAILY_REPORT_COLUMNS,
    build_daily_reports,
    infer_year,
    parse_daily_rows,
    parse_report_date,
)

ROW_KEYS = {
    "report_date", "depth_md", "start_depth_md", "mud_weight_raw", "mud_weight_sg",
    "operations", "row_index",
}

# 25/7-2 p48, p49, p61: "DAILY OPERATIONAL SUMMARY", dd.mm.yy, ppg.
SUMMARY_25_7_2 = """3.4 DAILY OPERATIONAL SUMMARY

|  |  |  |  |  |
| --- | --- | --- | --- | --- |
| DATE | DAYS | DEPTH | MW | OPERATION |
| 29.01 90 | 2/0/0 | N/A | N/A | Rig under tow to location 25/7-2. |
| 07.02.90 | 11/0/0 | N/A | N/A | Set piggy back anchor in place. Picked up 30" conductor casing. |
| 08.02.90 | 12/1/0 | 165m | 8.6 ppg/8.9 ppg SW + hi-vis sweeps | Made up 17 1/2" pilot hole assembly. Spudded the well at 0430 hrs. Drilled and surveyed 17 1/2" pilot hole from 149m to 265m. |
| 22.04.90 | 85/74/0 | 3422m | 12.0 ppg high temp. polymer | Drilled to 3360m. Flowchecked. |

- 39 -
"""

# 25/7-2 p72: last page of the same table, no header row.
SUMMARY_25_7_2_NO_HEADER = """|  |  |  |  |  |
| --- | --- | --- | --- | --- |
| 16.07.90 | 170/159/19 | PBTD 212m | 16.0 ppg high temp. polymer | RIH with perforation gun to 171m. Perforated the 9 5/8" casing. |
| 17.07.90 | 171/160/19 | Om | N/A | Finished pulling BOP. Cut 20"/30" casing at 155m RKB. POOH. |
"""

# 6507/7-9 p23: day and month only; the year is glued to the first date by the OCR.
SUMMARY_6507_7_9 = """6507/7-9 DAILY OPERATIONAL SUMMARY

|  |  |  |  |  |
| --- | --- | --- | --- | --- |
| Date | Days | Depth Meter | Mud Weight g/cm³ (ppg) | Operation |
| 19872 August | 1/0 | - | - | Rig released f/6507/7-8 to 6507/7-9 at 1700 hours on 2 August. |
| 3 August | 2/0 | - | - | Finish laying anchors. Ballast rig. RIH and land TGB on seafloor at 370m RKB (3/4°). |
| 4 August | 3/1 | 474m | 1.03 (8.6) | Stab 17 1/2" bit in TGB at 370m RKB. Drill f/370m to 474m (average ROP 8 m/hr). |
"""

# 6507/7-9 p24: the OCR invented the header and put the operations outside the table.
SUMMARY_6507_7_9_NO_OPS = """**Log / Daily entries**

| Date | Code | Depth | Value | Parenthesis |
| --- | --- | ---: | ---: | --- |
| 6 August | 5/3 | 850m | 1.03 | (8.6) |
| 7 August | 6/4 | 418m PBTD | 1.03 | (8.6) |

Drill and survey w/Teleco MWD 12 1/4" hole for shallow gas detection f/659m to 850m.
"""

# 6/3-A-1 H p135 and p137: one report per row, each cell is "label value".
REPORT_6_3_A_1_H = """Gaupe South 6/3-A-1 H End of Well Report

|  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- |
| Daily Operations Summary | | | | | |
| IH1 | | | | | |
| Rpt #12 | Start Date03/05/2011 | End Date04/05/2011 | Start Depth (m..)650.0 | End Depth (m..)1,059.0 | Last 24hr Op's SummaryCompleted drilling out wiper plug and float collar at 629m. Displaced to 1.40sg KCL mud. |
| PH2 | | | | | |
| Rpt # 25 | Start Date 16/05/2011 | End Date 17/05/2011 | Start Depth (m.) 3,262.0 | End Depth (m.) 3,262.0 | Last 24hr Op's Summary Ran 10 3/4" csg to 3150 m. |
| Rpt # 27 | Start Date 18/05/2011 | End Date 19/05/2011 | Start Depth (m.) 3,262.0 | End Depth (m.) 3,410.0 | Last 24hr Op's Summary Completed TBRA for continued operation with mud pumps. |

Page: 138 of 159
"""

# 25/7-3 p90: time-coded activity rows; the date cell is missing on most rows (so the cells
# shift left) and one date sits one row too early.
ACTIVITY_25_7_3 = """Detailed Activity Report

DATE: 15/08/1995 TIME: 00:00

|  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DATE | PH | CL | OP | HOURS | FROM | TO | DEPTH | ACTIVITY |
| 25/07/1995 | 1 | P14 | 8B | 24.00 | 00:00 | 00:00 | 0.00 | Continued thruster changeout. |
| 26/07/1995 | 1 |  |  | 0.00 | 00:00 | 00:00 | 0.00 |
| 1 | P14 | 8B | 6.50 | 00:00 | 06:30 | 0.00 | Replaced cooling water pipework. |
| 1 | P1B | 1B | 6.50 | 06:30 | 13:00 | 0.00 |
| 27/07/1995 | 1 | P1B | 1A | 11.00 | 13:00 | 00:00 | 0.00 | Move Deepsea Bergen to 25/7-3. |
| 1 |  |  | 0.00 | 00:00 | 00:00 | 0.00 |
| 1 | P1B | 1A | 5.50 | 00:00 | 05:30 | 0.00 |
| 1 | P1B | 1B | 6.50 | 05:30 | 12:00 | 0.00 | Continued running anchors. |
| 2 | P2 | 2A | 0.50 | 22:30 | 23:00 | 159.00 |
"""

# 25/7-3 p91: the page starts in the middle of a day, before the first date cell.
ACTIVITY_25_7_3_PAGE_START = """|  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DATE | PH | CL | OP | HOURS | FROM | TO | DEPTH | ACTIVITY |
|  | 2 | P2 | 2A | 0.50 | 23:00 | 23:30 | 168.00 | Drill to 168m. Survey at 168m - 0 deg. |
| 2 | P2 | 2A | 0.50 | 23:30 | 00:00 | 172.00 | Drill to 172m. Pumping Hi-Vis sweeps on every connection. |
| 2 |  |  | 0.00 | 00:00 | 00:00 | 172.00 | 0000-0400Continue to drill to TD at 227m. |
| 28/07/1995 | 2 | P2 | 2A | 4.00 | 00:00 | 04:00 | 227.00 | Drilled from 172m to 227m (36" hole to 223). |
| 2 | P2 | 6D | 1.00 | 04:00 | 05:00 | 227.00 | Pumped Hi-Vis sweep and spotted 1.22 sg mud in the hole. |
"""

# 25/6-3 p47 (header) and p48 (rows): yy/mm/dd, HHMM, decimal commas.
ACTIVITY_25_6_3_HEADER = (
    "| Date | From hrs | To hrs | Duration | Depth mMD | Act. code | Main mode | Sub mode "
    "| Description |"
)
ACTIVITY_25_6_3_ROWS = """|  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 99/10/25 | 0000 | 0030 | ,50 | 171,0 | CAWW | FAIL | E FAIL | CONTINUED WIPER TRIP TO INSIDE 30" CSG TO 171 M. |
| 0630 | 0800 | 1,50 | 1215,0 | CAWW | FAIL | OK | WASHED AND ROTATED ON FILL FROM 1131 M TO TD AT 1215 M. |
| 1030 | 1500 | 4,50 |  | DTCU | OK | OK | POOH. LAID DOWN 17 1/2" ASSY. |
| 1730 | 2400 | 6,50 | 322,0 | CARU | OK | OK | RAN 13 3/8" CSG. JAM EACH CONNECTION WITH JAM UNIT. |
| 99/10/26 | 0000 | 0600 | 6,00 | 1066,0 | CARU | OK | OK | CONTINUED RUN 13 3/8" CSG. |
"""


def test_summary_layout_ddmmyy_ppg():
    rows = parse_daily_rows(SUMMARY_25_7_2)
    assert len(rows) == 4
    assert all(ROW_KEYS <= set(r) for r in rows)
    assert [r["row_index"] for r in rows] == [0, 1, 2, 3]
    assert [r["report_date"] for r in rows] == [
        date(1990, 1, 29), date(1990, 2, 7), date(1990, 2, 8), date(1990, 4, 22)]
    assert [r["depth_md"] for r in rows] == [None, None, 165.0, 3422.0]
    assert rows[0]["mud_weight_raw"] is None and rows[0]["mud_weight_sg"] is None
    assert rows[2]["mud_weight_raw"] == "8.6 ppg/8.9 ppg SW + hi-vis sweeps"
    assert rows[2]["mud_weight_sg"] == pytest.approx(8.6 / 8.345, abs=1e-3)
    assert rows[3]["mud_weight_sg"] == pytest.approx(12.0 / 8.345, abs=1e-3)
    assert rows[3]["operations"] == "Drilled to 3360m. Flowchecked."
    assert rows[2]["start_depth_md"] is None


def test_summary_layout_without_header_row():
    rows = parse_daily_rows(SUMMARY_25_7_2_NO_HEADER)
    assert [r["report_date"] for r in rows] == [date(1990, 7, 16), date(1990, 7, 17)]
    assert [r["depth_md"] for r in rows] == [212.0, None]  # "Om" is an OCR zero: no depth
    assert rows[0]["mud_weight_sg"] == pytest.approx(16.0 / 8.345, abs=1e-3)
    assert rows[1]["mud_weight_raw"] is None
    assert rows[1]["operations"].startswith("Finished pulling BOP.")


def test_day_month_dates_take_year_from_page():
    rows = parse_daily_rows(SUMMARY_6507_7_9)
    assert [r["report_date"] for r in rows] == [
        date(1987, 8, 2), date(1987, 8, 3), date(1987, 8, 4)]
    assert [r["depth_md"] for r in rows] == [None, None, 474.0]
    assert rows[2]["mud_weight_raw"] == "1.03 (8.6)"
    assert rows[2]["mud_weight_sg"] == pytest.approx(1.03)
    assert rows[0]["mud_weight_raw"] is None


def test_day_month_dates_use_default_year_or_stay_empty():
    rows = parse_daily_rows(SUMMARY_6507_7_9_NO_OPS, default_year=1987)
    assert [r["report_date"] for r in rows] == [date(1987, 8, 6), date(1987, 8, 7)]
    assert [r["depth_md"] for r in rows] == [850.0, 418.0]
    assert [r["mud_weight_raw"] for r in rows] == ["1.03 (8.6)", "1.03 (8.6)"]
    assert [r["mud_weight_sg"] for r in rows] == [pytest.approx(1.03)] * 2
    assert [r["operations"] for r in rows] == ["", ""]
    no_year = parse_daily_rows(SUMMARY_6507_7_9_NO_OPS)
    assert len(no_year) == 2 and all(r["report_date"] is None for r in no_year)


def test_report_per_row_layout_with_start_and_end_depth():
    rows = parse_daily_rows(REPORT_6_3_A_1_H)
    assert [r["report_date"] for r in rows] == [
        date(2011, 5, 3), date(2011, 5, 16), date(2011, 5, 18)]
    assert [r["start_depth_md"] for r in rows] == [650.0, 3262.0, 3262.0]
    assert [r["depth_md"] for r in rows] == [1059.0, 3262.0, 3410.0]
    assert rows[0]["operations"].startswith("Completed drilling out wiper plug")
    assert rows[2]["operations"] == "Completed TBRA for continued operation with mud pumps."
    assert all(r["mud_weight_raw"] is None for r in rows)


def test_activity_layout_shifted_rows_and_misplaced_date():
    rows = parse_daily_rows(ACTIVITY_25_7_3)
    got = [(r["report_date"], r["depth_md"], r["operations"]) for r in rows]
    assert got == [
        (date(1995, 7, 25), None, "Continued thruster changeout."),
        (date(1995, 7, 26), None, "Replaced cooling water pipework."),
        (date(1995, 7, 26), None, "Move Deepsea Bergen to 25/7-3."),
        (date(1995, 7, 27), None, "Continued running anchors."),
        (date(1995, 7, 27), 159.0, ""),
    ]


def test_activity_layout_rows_before_first_date_are_backfilled():
    rows = parse_daily_rows(ACTIVITY_25_7_3_PAGE_START)
    assert [(r["report_date"], r["depth_md"]) for r in rows] == [
        (date(1995, 7, 27), 168.0), (date(1995, 7, 27), 172.0),
        (date(1995, 7, 28), 227.0), (date(1995, 7, 28), 227.0),
    ]  # the 00:00-00:00 zero-hour look-ahead row is dropped


def test_activity_layout_yymmdd_and_decimal_commas():
    text = ACTIVITY_25_6_3_ROWS.replace(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        + ACTIVITY_25_6_3_HEADER + "\n")
    rows = parse_daily_rows(text)
    assert [r["report_date"] for r in rows] == [date(1999, 10, 25)] * 4 + [date(1999, 10, 26)]
    assert [r["depth_md"] for r in rows] == [171.0, 1215.0, None, 322.0, 1066.0]
    assert rows[2]["operations"] == 'POOH. LAID DOWN 17 1/2" ASSY.'


def test_activity_rows_without_header_need_the_header_of_an_earlier_page():
    assert parse_daily_rows(ACTIVITY_25_6_3_ROWS) == []
    rows = parse_daily_rows(ACTIVITY_25_6_3_ROWS, header=ACTIVITY_25_6_3_HEADER)
    assert len(rows) == 5 and rows[1]["depth_md"] == 1215.0


def test_continuation_row_is_merged_into_previous_row():
    # Synthetic: a 25/7-2 row split the way the OCR splits long cells elsewhere.
    text = """| DATE | DAYS | DEPTH | MW | OPERATION |
| --- | --- | --- | --- | --- |
| 11.02.90 | 15/4/0 | 1030m | 8.9 ppg SW + hi vis sweeps | Continued to drill 17 1/2" pilot hole from 342m to 1030m RKB. |
|  |  |  |  | The MWD failed. Swept the hole with visc. pills. |
| 12.02.90 | 16/5/0 | 1030m | 9.0 ppg SW + hi-vis sweeps | Made a wiper trip to the 30" shoe. |
| Logged the hole with DIS-GR-LDL-/CAL-SLS-SP-AMS. |
"""
    rows = parse_daily_rows(text)
    assert len(rows) == 2
    assert rows[0]["operations"] == (
        'Continued to drill 17 1/2" pilot hole from 342m to 1030m RKB. '
        "The MWD failed. Swept the hole with visc. pills.")
    assert rows[1]["operations"].endswith("Logged the hole with DIS-GR-LDL-/CAL-SLS-SP-AMS.")
    assert [r["row_index"] for r in rows] == [0, 1]


@pytest.mark.parametrize("text", [
    "",
    "3.3 Extract of daily activities.\n\n84.11.05 Drilled 12 1/4\" hole from 175 m to 352 m.",
    # 25/7-2 p29, bit record in the operations narrative
    ("| Bit no. | Bit type | Depth | ROP | Location |\n| --- | --- | --- | --- | --- |\n"
    "| Bit no. 4, | Smith SDGH, | 728m, | 15.8 m/hr | (Hordaland) |\n"
    "| Bit no. 5, | Smith ECL 15JS, | 2m, | 0.8 m/hr | (Hordaland) |"),
    # 25/7-2 p257, lithology
    ("| DEPTH m MD | LITHOLOGY DESCRIPTION |\n| --- | --- |\n"
    "| 1225 | CLAYSTONE: m gy, v hd, v calc, mmic |\n"
    "| 1265 | SILTSTONE: m bn, frm-hd, mod calc, mmic, carb |"),
    # 25/6-1 p103, cement jobs: a date, a depth and remarks, but not a daily report
    ("| DATE | JOB DESCRIPTION | SHOE DEPTH m-RKB | CEMENT TYPE | SLURRY WEIGHT SG | REMARKS |\n"
    "| 20.12.85 | 30\" csg. | 255 | G | Lead: 1.56Tail | To cement 30\" casing, 80 m of dp was "
    "used as a stinger. |\n"
    "| 27.12.85 | 20\" csg. | 1013 | G | Lead: 1.56Tail: 1.92 | Full returns during complete job. |"),
    # 25/6-1 p98, mud properties per day: date, depth and mud weight, no operations
    ("| Date | Hole size | Hole depth | Mud weight | PV | YP | Gel strength | pH | Mudtype |\n"
    "| 851218 | 36 | 185.0 | 1.04 |  |  |  |  | SPUD MUD |\n"
    "| 851221 | 17-1/2 | 627.0 | 1.16 | 7 | 43 | 25/28 | 9.6 | GEL MUD |"),
    # 25/7-3 p138, drilling parameters per interval
    ("| Date | Depth From(m) | Depth To(m) | Drill Mode O/R | TFO | Drill Hrs. | Bit No. |\n"
    "| 02-AUG-95 | 1186 | 1189 | R |  | 0.08 | 2 |\n| 02-AUG-95 | 1189 | 1211 | R |  | 1.08 | 2 |"),
    # 6/3-A-1 H p125, non-productive time: start/end date and depth per problem
    ("| RC | Root Cause | Des | Start Date | End Date | Dur (hr) | | Start Depth (mKB) | "
    "End Depth (mKB) | Problem Cost (Cost) |\n"
    "| DHM | Downhole Motor/Turbine/RS Failure | Checked rollers on bit and found one roller "
    "stuck. | 27/04/2011 08:30 | 27/04/2011 08:45 | 0.25 | | 650.0 |  |  |"),
    # 25/6-1 p117, daily operations with a date but no depth column
    ("| Date | Stop | Operational Description |\n| --- | --- | --- |\n"
    "| 16.12.85 | 2400 | On way to location. ETA 22:00 hrs 17/12. |\n"
    "| 18.12.85 | 1000 | Anchorhandling. Started ballasting rig at 0140 hrs |\n"
    "| 2400 | Drilled 36\" hole from 147 to 185 m. Reamed and pumped hi-vis pills. |"),
])
def test_pages_without_a_daily_report_table_give_nothing(text):
    assert parse_daily_rows(text) == []


@pytest.mark.parametrize("raw, expected", [
    ("22.04.90", date(1990, 4, 22)),
    ("29.01 90", date(1990, 1, 29)),
    ("7.1.86", date(1986, 1, 7)),
    ("16/05/2011", date(2011, 5, 16)),
    ("99/10/18", date(1999, 10, 18)),
    ("84.11.26", date(1984, 11, 26)),
    ("2000-03-14", date(2000, 3, 14)),
    ("5 MAY 1990", date(1990, 5, 5)),
    ("26-Apr-11", date(2011, 4, 26)),
    ("02-AUG-95", date(1995, 8, 2)),
    ("19872 August", date(1987, 8, 2)),
    ("N/A", None),
    ("31.02.90", None),
    ("12.05.45", None),  # 1945 and 2045 are both outside 1965-2020
    ("3 August", None),
])
def test_parse_report_date(raw, expected):
    assert parse_report_date(raw) == expected


def test_parse_report_date_default_year():
    assert parse_report_date("3 August", default_year=1987) == date(1987, 8, 3)
    assert parse_report_date("1 Nov.", default_year=1984) == date(1984, 11, 1)
    assert parse_report_date("22.04.90", default_year=1987) == date(1990, 4, 22)


def test_infer_year():
    assert infer_year(SUMMARY_6507_7_9) == 1987
    assert infer_year("Rig released at 1716 hours, 10 August 1987.") == 1987
    assert infer_year("DAILY OPERATIONS LOG\n\n1984\n\n1 Nov.  Rig move to 6/3-1.") == 1984
    assert infer_year("| 6 August | 5/3 | 850m | 1.03 | (8.6) |\nDrill f/1990m to 2010m.") is None
    assert infer_year("") is None


def _write_kg(tmp_path, pages, wellbores):
    """Minimal page / document / wellbore parquet files with the columns the build reads."""
    con = duckdb.connect()
    con.register("page", pd.DataFrame(pages, columns=["document_id", "page_no", "text"]))
    con.register("wb", pd.DataFrame(
        wellbores, columns=["wellbore_id", "document_id", "name", "entry", "completion"]))
    con.execute(f"copy (select document_id, page_no::integer page_no, text, "
                f"null::varchar summary, 'Reliable' quality from page) "
                f"to '{tmp_path}/page.parquet'")
    con.execute(f"copy (select document_id, wellbore_id::bigint wellbore_id from wb) "
                f"to '{tmp_path}/document.parquet'")
    con.execute(f"copy (select wellbore_id::bigint wellbore_id, name, entry::date entry_date, "
                f"completion::date completion_date from wb) to '{tmp_path}/wellbore.parquet'")


def test_build_daily_reports(tmp_path):
    # Synthetic rows in the 6507/7-9 layout: no year anywhere, the well runs over new year.
    over_new_year = """| Date | Days | Depth Meter | Mud Weight g/cm³ (ppg) | Operation |
| --- | --- | --- | --- | --- |
| 30 December | 12/10 | 1028m | 1.16 (9.7) | Drilled 17 1/2" pilot hole. |
| 31 December | 13/11 | 1028m | 1.16 (9.7) | Ran logs. |
| 1 January | 14/12 | 1100m | 1.16 (9.7) | Drilled ahead. |
"""
    _write_kg(
        tmp_path,
        pages=[
            ("A", 22, "2.3 Daily Operational Summary\n\nno table here"),
            ("A", 23, SUMMARY_6507_7_9),
            ("A", 24, SUMMARY_6507_7_9_NO_OPS),
            ("B", 5, over_new_year),
            ("C", 47, ACTIVITY_25_6_3_ROWS.replace(
                "| 99/10/25", ACTIVITY_25_6_3_HEADER + "\n| 99/10/25")),
            ("C", 48, ACTIVITY_25_6_3_ROWS.replace("99/10/25", "99/10/27")
             .replace("99/10/26", "99/10/28")),
        ],
        wellbores=[
            (1078, "A", "6507/7-9", "1987-08-04", "1987-08-08"),
            (524, "B", "25/6-1", "1985-12-18", "1986-02-03"),
            (3885, "C", "25/6-3", "1999-10-21", "1999-11-11"),
        ],
    )
    counts = build_daily_reports(tmp_path)
    assert counts["rows"] == 5 + 3 + 10
    assert counts["pages_with_rows"] == 5
    assert counts["rows_by_wellbore"] == {1078: 5, 524: 3, 3885: 10}

    con = duckdb.connect()
    out = f"'{tmp_path}/daily_report.parquet'"
    schema = {r[0]: r[1] for r in con.sql(f"describe select * from {out}").fetchall()}
    assert schema == DAILY_REPORT_COLUMNS
    assert list(schema) == [
        "wellbore_id", "document_id", "page_no", "row_index", "report_date", "depth_md",
        "start_depth_md", "mud_weight_raw", "mud_weight_sg", "operations"]

    def dates(doc):
        return [r[0] for r in con.sql(
            f"select report_date from {out} where document_id = '{doc}' "
            "order by page_no, row_index").fetchall()]

    # page 24 has no year: it comes from page 23
    assert dates("A") == [date(1987, 8, d) for d in (2, 3, 4, 6, 7)]
    # no year in the document: SODIR entry year, rolling over after December
    assert dates("B") == [date(1985, 12, 30), date(1985, 12, 31), date(1986, 1, 1)]
    # page 48 has no header: the header of page 47 is reused
    assert dates("C")[5:] == [date(1999, 10, 27)] * 4 + [date(1999, 10, 28)]
    row = con.sql(f"select wellbore_id, page_no, row_index, depth_md, mud_weight_sg "
                  f"from {out} where document_id = 'A' and report_date = '1987-08-04'").fetchall()
    assert row == [(1078, 23, 2, 474.0, pytest.approx(1.03))]


def test_build_repairs_a_misprinted_date_and_clamps_a_garbled_page(tmp_path):
    # 25/7-2 p57: "04.03.90" stands between 03.04.90 and 05.04.90.
    misprint = """| DATE | DAYS | DEPTH | MW | OPERATION |
| --- | --- | --- | --- | --- |
| 03.04.90 | 66/55/0 | 2843m | 11.7 ppg Polymer | Pumped a slug and continued to POOH. |
| 04.03.90 | 67/56/0 | 2843m | 11.7 ppg Polymer | Inspected and tested new kelly. |
| 05.04.90 | 68/57/0 | 2864m | 11.8 ppg Polymer | Continued to wash and ream down to 2827m. |
"""
    # 25/7-3 p95 (rows without text, clock times damaged) and the start of p96.
    garbled = """| DATE | PH | CL | OP | HOURS | FROM | TO | DEPTH | ACTIVITY |
| 06/08/1995 | 7 | T4 | 5 | 3.50 | 20:00 | 23:30 | 1966.00 | Monitor well with a SIDPP - 32 bar. |
| 7 | T4 | 5 | 0.50 | 23:30 | 00:00 | 1966.00 |
| 7 | T4 | 5 | 6.00 | 00:00 | 06:00 | 1966.00 |
| 7 | T4 | 5 | 6.50 | 06:00 | 12:30 | 1966.00 |
| 7 | T4 | 5 | 6.50 | 00:00 | 12:30 | 1966.00 |
| 7 | T4 | 5 | 1.00 | 23:00 | 00:00 | 1966.00 |
| 7 | T4 | 5 | 1.00 | 00:00 | 00:00 | 1966.00 |
| 7 | T4 | 6F | 2.00 | 01:00 | 03:00 | 1966.00 |
"""
    next_page = """| DATE | PH | CL | OP | HOURS | FROM | TO | DEPTH | ACTIVITY |
|  | 7 | T4 | 6C | 1.00 | 13:00 | 14:00 | 1966.00 | P/U 8.5" BHA. Test MWD. |
| 7 | T4 | 5 | 1.00 | 23:00 | 00:00 | 1966.00 | CBU to perform FIT after squeezing. |
| 08/08/1995 | 7 | T4 | 2A | 2.50 | 00:00 | 02:30 | 1969.00 | Drill 3 m new formation. |
"""
    _write_kg(
        tmp_path,
        pages=[("A", 57, misprint), ("B", 95, garbled), ("B", 96, next_page)],
        wellbores=[(1494, "A", "25/7-2", "1990-02-08", "1990-07-18"),
                   (2623, "B", "25/7-3", "1995-07-27", "1995-08-28")],
    )
    counts = build_daily_reports(tmp_path)
    assert counts["dates_repaired"] == 1 and counts["dates_clamped_to_next_page"] > 0
    con = duckdb.connect()
    got = con.sql(f"select document_id, page_no, min(report_date), max(report_date) "
                  f"from '{tmp_path}/daily_report.parquet' group by all order by 1, 2").fetchall()
    assert got == [
        ("A", 57, date(1990, 4, 3), date(1990, 4, 5)),
        ("B", 95, date(1995, 8, 6), date(1995, 8, 7)),
        ("B", 96, date(1995, 8, 7), date(1995, 8, 8)),
    ]


def test_mud_weight_written_as_a_pair_before_the_unit():
    # 25/7-2 p52
    text = """| DATE | DAYS | DEPTH | MW | OPERATION |
| 03.03.90 | 35/24/0 | 1938 | 11.1/11.3 ppg Polymer | Drilled from 1684m to 1938m. |
"""
    (row,) = parse_daily_rows(text)
    assert row["mud_weight_raw"] == "11.1/11.3 ppg Polymer"
    assert row["mud_weight_sg"] == pytest.approx(11.1 / 8.345, abs=1e-3)
