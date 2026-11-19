# Daily drilling report rows (`data/kg/daily_report.parquet`)

Rows of the daily drilling report tables in the report pages, parsed without any model call
by `src/wellgraph/extract/daily.py` (`python -m wellgraph.extract.daily --kg data/kg`).
Figures are from the 7 loaded reports (1,208 pages), 2026-11-19. Columns: `wellbore_id`,
`document_id`, `page_no`, `row_index` (position on the page), `report_date`, `depth_md` (m,
at report time / end of day), `start_depth_md` (m, only where the layout gives it),
`mud_weight_raw` (as written), `mud_weight_sg`, `operations`.

## What counts as a daily report table

A markdown table with a date column and a depth column, at most 10 columns, and either an
operations text column or a day-counter column ("85/74/0"). Tables whose header names
something else (shoe, slurry, cement, bit, BHA, log, hole size, rheology, root cause,
pressure, lithology, formation, plan) are rejected. 52 of 1,208 pages gave rows, all of
them in the daily report sections.

## Layouts found

| Layout | Wellbores (pages) | Form | Grain |
|---|---|---|---|
| summary | 25/7-2 (p48-72), 6507/7-9 (p23-25) | `Date \| Days \| Depth \| Mud weight \| Operation` | one row per day |
| report | 6/3-A-1 H (p135-138) | each cell is "label value": `Rpt # 25 \| Start Date 16/05/2011 \| End Date ... \| Start Depth (m.) 3,262.0 \| End Depth ... \| Last 24hr Op's Summary ...` | one row per day |
| activity | 25/7-3 (p90-105), 25/6-3 (p47-50) | `Date \| ... \| From \| To \| Depth \| Activity`, date only on the first row of a day | one row per activity (0.5 to 24 h) |

- summary: dd.mm.yy with OCR damage ("29.01 90"); day and month only ("3 August",
  "19872 August"); depth as "3422m", "PBTD 4812m", "N/A", "-", "Om" (OCR zero); mud weight
  as "12.0 ppg high temp. polymer", "11.1/11.3 ppg Polymer", "1.03 (8.6)", also split over
  two cells; a table continued on the next page without its header row.
- report: `report_date` is the Start Date (the day of the operations; the end date is the
  next morning); `start_depth_md` / `depth_md` are Start Depth / End Depth.
- activity: rows without a date cell have one cell fewer, so cells are aligned on the
  From/To pair. Rows are grouped into days by the clock (a row starting earlier than the row
  before, or following a row that ends at midnight, starts a new day); the written dates
  anchor the days. In 25/7-3 a date printed one row too early is moved to its day, and the
  00:00-00:00 zero-hour "look-ahead" rows (next morning's summary) are dropped. 25/6-3
  writes yy/mm/dd, HHMM and decimal commas ("1215,0"); p48-50 reuse the header of p47.

Years: rows without a year take the year found on their page, else that of the last dated
row before them in the document, else the SODIR entry year (rolling over after December).
Only 6507/7-9 p24 needed this (year from p23). Two repairs are made when the table is built:
17 activity rows on 25/7-3 p95 dated later than the first row of p96 were set to that date;
one summary date in 25/7-2 p57 ("04.03.90" between 03.04.90 and 05.04.90) was set to the day
in between.

## Rows and coverage

| Wellbore | Layout | Pages | Rows | Days | With date | With depth | With mud weight | With operations text |
|---|---|---|---|---|---|---|---|---|
| 25/6-3 | activity | 4 | 227 | 24 | 100% | 49.8% | 0% | 99.6% |
| 25/7-2 | summary | 25 | 172 | 172 | 100% | 92.4% | 92.4% | 100% |
| 25/7-3 | activity | 16 | 290 | 35 | 100% | 96.2% | 0% | 89.0% |
| 6/3-A-1 H | report | 4 | 39 | 39 | 100% | 87.2% | 0% | 100% |
| 6507/7-9 | summary | 3 | 9 | 9 | 100% | 55.6% | 55.6% | 77.8% |
| Total | | 52 | 737 | | 100% | 80.1% | 22.3% | 95.3% |

25/6-1 and 6/3-1 have no rows (see below). Rows without a depth are rig moves, anchoring and
(25/6-3) empty depth cells; zero is stored as no depth. Only the summary layout has a mud
weight column. The largest `depth_md` equals the SODIR total depth for all five wellbores.

## Dates compared with SODIR

| Wellbore | First row | First row with a depth | SODIR entry | Last row | SODIR completion | Agreement |
|---|---|---|---|---|---|---|
| 25/6-3 | 1999-10-18 | 1999-10-21 | 1999-10-21 | 1999-11-10 | 1999-11-11 | entry yes; listing ends one day before completion |
| 25/7-2 | 1990-01-28 | 1990-02-08 | 1990-02-08 | 1990-07-18 | 1990-07-18 | yes |
| 25/7-3 | 1995-07-25 | 1995-07-27 | 1995-07-27 | 1995-08-28 | 1995-08-28 | yes |
| 6/3-A-1 H | 2011-04-22 | 2011-04-27 | 2011-04-26 | 2011-05-30 | 2011-05-23 | **differs**, see below |
| 6507/7-9 | 1987-08-02 | 1987-08-04 | 1987-08-04 | 1987-08-10 | 1987-08-08 | entry yes; two rows after completion are the tow to port |

Rows before the entry date are rig move and anchoring. 6/3-A-1 H: the report of 26 April
describes drilling the pilot hole (matches SODIR entry) but its depth cells are 0.0, so the
first depth is on 27 April. The reports run to 30 May (anchor handling), 7 days after the
SODIR completion date of 23 May, the day the 7" liner hanger was set.

## Is depth non-decreasing over time?

| Wellbore | Rows with depth | Rows lower than the row before | Rows below the running maximum |
|---|---|---|---|
| 25/6-3 | 113 | 24 | 58 |
| 25/7-2 | 159 | 3 | 18 |
| 25/7-3 | 279 | 0 | 0 |
| 6/3-A-1 H | 34 | 0 | 0 |
| 6507/7-9 | 5 | 1 | 2 |

28 rows are lower than the row before them; none is a parsing error:
- 25/7-2 (3): p69 row 0, 1990-06-29, 4850 -> "PBTD 4812m"; p71 rows 2 and 3, 1990-07-14/15,
  "PBTD 3713m", "PBTD 212m". All 18 rows below the maximum are plug-back depths.
- 6507/7-9 (1): p24 row 1, 1987-08-07, 850 -> "418m PBTD" (cement plugs).
- 25/6-3 (24, on p47-50): `Depth mMD` is the bit or string depth of each activity, not the
  hole depth (1999-10-25: 171, 894, 1131, 1215, then 322 while running casing). The daily
  maximum falls on 5 of 20 days; for hole depth use the running maximum of `depth_md`.

## Pages that look like daily reports but were not read

| Wellbore, pages | What is there | Why no rows |
|---|---|---|
| 25/6-1 p117 | table `Date \| Stop \| Operational Description` (6.7.3 Daily operational description) | no depth column; depths only inside the text |
| 25/6-1 p118-128 | the same listing as fixed-width text | not a table |
| 6/3-1 p30-48 | "3.3 Extract of daily activities", paragraphs starting "84.11.05" | not a table, no depth field |
| 6/3-1 p232-238 | mud engineer's "Daily operations log", paragraphs starting "5 Nov." | not a table, no depth field |
| 25/6-1 p98-99, 25/7-2 p108-145, 25/7-3 p161, 6/3-1 p227-231, 6507/7-9 p35 | daily mud property tables: date, depth, mud weight, 14 to 29 numeric columns | no operations column; rejected by design (they would duplicate the days of the summary table) |
| 25/7-3 p95 | activity table, OCR lost the text of 23 of 24 rows and damaged the clock times | rows are kept (depth only) but their dates are approximate (6-7 Aug 1995) |
| 6507/7-9 p24 | the OCR put the operations text of 6 and 7 August outside the table | rows kept with date, depth and mud weight, `operations` empty |

So 25/6-1 and 6/3-1 have no rows: their daily narratives need a text parser that takes the
depth from phrases such as "Drilled to 2641 m".

## Known limits

- Activity rows are per activity, summary and report rows per day: count days with
  `count(distinct report_date)`, not rows.
- `depth_md` is what the cell says: hole depth (plug-back depth after plugging), but bit
  depth in 25/6-3.
- Activity layout: a wrong or missing time cell can shift rows by a day; text the OCR merged
  into a neighbouring row stays there (33 rows have a depth but no text).
- A table continued without its header is recognised only on the page directly after one
  that gave rows (or, for the summary layout, by its date and day-counter cells).
- Header word lists (`_H_OPS`, `_H_OTHER`, `_MAX_COLUMNS`) are tuned on these 7 reports.
