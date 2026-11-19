# What is in the report JSONs

Based on **7 reports, 1,208 pages, 2.17 million characters of OCR text** (the 4 files in
`samples/reports`, 2 in `data/sample/ranged`, 1 in `data/sample/head40`). The full dataset
has about 5,300 JSONs, so every number here is indicative, not a statistic of the dataset.
Six of the seven are scanned exploration-well reports from 1984-2000; one is a born-digital
2011 development-well report. Numbers were computed with scratch scripts through
`wellgraph.onboard.reports.load_report`; page numbers are 0-based as in the JSON.

## 1. The reports

| Wellbore | Well year | Pages | Chars | Median chars/page | Reliable / Acceptable / Uncertain / Figure-dominated | has_tables | has_figures |
|---|---|---|---|---|---|---|---|
| 25/6-1 | 1986 | 143 | 177,974 | 1,092 | 123 / 7 / 6 / 7 | 80 | 32 |
| 25/6-3 | 1999-2000 | 104 | 225,431 | 1,936 | 62 / 35 / 2 / 5 | 59 | 29 |
| 25/7-2 | 1990 | 265 | 533,107 | 1,625 | 153 / 91 / 3 / 18 | 150 | 64 |
| 25/7-3 | 1995 | 220 | 489,568 | 1,728 | 104 / 89 / 18 / 9 | 167 | 60 |
| 6507/7-9 | 1987 | 59 | 54,188 | 675 | 43 / 8 / 1 / 7 | 22 | 15 |
| 6/3-1 | 1984-85 | 258 | 321,717 | 1,356 | 188 / 58 / 8 / 4 | 165 | 76 |
| 6/3-A-1 H | 2011 | 159 | 370,272 | 1,947 | 106 / 52 / 1 / 0 | 111 | 76 |
| Total | | 1,208 | 2,172,257 | | 779 / 340 / 39 / 50 | 754 | 352 |

The well year is not a metadata field; it was read from the most frequent dates in the
page text. `createDate` is the scan date (2001 for all scanned reports) and `releaseDate`
the public release date; neither is the drilling date. Take dates from SODIR.

Document metadata is complete in all 7 files: `wellboreName`, `wellName`, `wellborefileId`,
`title`, `document_quality` (one sentence) and `page_quality_summary` (its counts match the
per-page labels exactly). `wellId` / `wellboreId` are Diskos ids, not NPD ids.

File naming: the JSON key is `25_6-1/WELL/3324598_25-06-01_PB-706-0464.PDF` (prefix =
`wellborefileId`), the file on disk is `25_6-1/WELL/25_6-1_25-06-01_PB-706-0464.PDF|JSON`
(prefix = folder name). `load_report` stores the on-disk form as `relative_path`.

## 2. Page-level fields

| Field | Filled | Notes |
|---|---|---|
| `page_no` | 1,208 / 1,208 | 0-based, contiguous in every report |
| `OCR.ocr_markdown` | 1,178 pages have >= 50 chars | 30 near-empty pages (19 of them in 6/3-1: dividers, plots) |
| `page_metadata.quality` | 1,208 | 4 labels only: Reliable 64%, Acceptable 28%, Uncertain 3%, Figure-dominated 4% |
| `page_metadata.image_quality_score` | 1,207 | float about 1.9-9.8; null on one page |
| `page_metadata.size` | 1,208 | string like `1653x2338 px` |
| `AI.page_summary` | 1,207 | 1-3 sentences, usually accurate and quotes key depths |
| `AI.tags` | 1,207 | 4.6-4.9 per page, never more than 5 |
| `AI.has_tables` | 1,208 | agrees with "page contains a markdown table row" on 728 of 754 flagged pages |
| `AI.has_figures` / `figure_details` | 1,208 / 353 non-empty | free-text description per figure, prefixed `[fig_1]` |
| `OCR.layout.elements` | 1,208 | type/label (plain text, title, table, figure, captions), confidence, bounding box; no text |

The quality label is not a text-length or usefulness signal. "Acceptable" pages are on
average 2-3 times longer than "Reliable" ones (e.g. 3,537 vs 1,420 chars in 25/6-3) because
they are the table-dense pages. "Uncertain" and "Figure-dominated" pages can still carry
long text, some of it invented (section 8). The one page with no text, summary or tags is
6/3-A-1 H page 132.

## 3. Tag vocabulary

- 1,420 distinct tags over 1,208 pages; 834 (59%) are used exactly once.
- Only 20 tags occur in all 7 reports; 991 occur in a single report. Per report: 173-451
  distinct tags.
- 141 groups of tags differ only by case, separator or plural, for example
  `core_description` / `core description` / `core_descriptions`, `mud_weight` /
  `mud weight` / `mudweight`, `MWD` / `mwd`, `pressure_test` / `pressure-test` /
  `pressure test` / `pressure_tests`, `North Sea` / `North_Sea` / `north_sea`.
- Many tags are well-specific (`well_6-3-1` and `well_6/3-1`, `BGN_document`).
Top 40 tags (count of pages, number of reports out of 7):

| Tag | Pages | Rep. | Tag | Pages | Rep. | Tag | Pages | Rep. | Tag | Pages | Rep. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| drilling | 325 | 7 | BHA | 62 | 7 | mud | 32 | 6 | claystone | 24 | 4 |
| lithology | 202 | 7 | mud_weight | 55 | 6 | pressure_test | 31 | 6 | rheology | 24 | 5 |
| casing | 180 | 7 | MWD | 50 | 6 | circulation | 30 | 7 | wellhead | 23 | 6 |
| core_description | 139 | 5 | coring | 47 | 5 | DST | 29 | 5 | operations | 23 | 6 |
| cementing | 127 | 7 | wellbore | 44 | 7 | shale | 29 | 4 | ROP | 23 | 4 |
| well_report | 115 | 7 | porosity | 44 | 5 | kaolinite | 26 | 1 | pore_pressure | 22 | 6 |
| well_log | 112 | 7 | mud_properties | 43 | 7 | BOP | 25 | 5 | survey | 22 | 7 |
| sandstone | 78 | 5 | completion | 41 | 7 | formation_pressure | 25 | 5 | lithostratigraphy | 22 | 5 |
| stratigraphy | 63 | 7 | logging | 35 | 6 | wellsite_sample | 25 | 2 | geology | 21 | 7 |
| well_operations | 62 | 7 | wireline | 32 | 7 | formation_tops | 24 | 6 | liner | 21 | 4 |

There are no tags for drilling problems in the top 40: `lost_circulation`, `stuck_pipe`,
`kick` exist but are rare and inconsistent. Tags are usable to route pages by broad topic
after normalisation (lower-case, `_`/`-`/space folded, plural stripped), not as a schema.

## 4. Kinds of pages

Each page was put in one category by keyword rules over its tags (weight 3) and its page
summary (weight 1, capped); the category with the highest score wins, ties go to the earlier
row. This is a rough routing heuristic, not ground truth: a daily-operations page that
mostly describes running casing lands in "casing/cementing".

| Category | 25/6-1 | 25/6-3 | 25/7-2 | 25/7-3 | 6507/7-9 | 6/3-1 | 6/3-A-1 H | Total |
|---|---|---|---|---|---|---|---|---|
| cover / TOC / admin | 13 | 9 | 11 | 11 | 4 | 10 | 5 | 63 |
| daily drilling operations, BHA, bit record | 18 | 12 | 55 | 30 | 17 | 21 | 43 | 196 |
| casing / cementing / LOT / wellhead / P&A | 19 | 15 | 31 | 58 | 10 | 22 | 54 | 209 |
| mud / fluids | 3 | 4 | 41 | 11 | 5 | 13 | 5 | 82 |
| geology / lithology / cores / stratigraphy | 37 | 39 | 49 | 37 | 6 | 132 | 3 | 303 |
| logging / petrophysics | 24 | 7 | 25 | 29 | 7 | 17 | 5 | 114 |
| well testing / formation pressure | 14 | 3 | 23 | 13 | 0 | 17 | 3 | 73 |
| survey / trajectory / location | 6 | 11 | 10 | 15 | 5 | 3 | 15 | 65 |
| completion / equipment | 0 | 0 | 3 | 10 | 0 | 7 | 13 | 33 |
| costs / HSE / logistics | 4 | 3 | 15 | 5 | 4 | 14 | 12 | 57 |
| other / empty | 5 | 1 | 2 | 1 | 1 | 2 | 1 | 13 |

Figures are spread over all categories (352 pages with `has_figures`; most in geology and
casing/cementing, 72 each), so "figure page" is a property, not a category. The exploration
reports are 25-50% geology; the 2011 development report has 3 geology pages out of 159 and
is almost entirely drilling, casing, cementing and equipment.

## 5. Where drilling problems are described

Pages whose OCR text matches each term group (case-insensitive regex; a page can count in
several rows):

| Term group | 25/6-1 | 25/6-3 | 25/7-2 | 25/7-3 | 6507/7-9 | 6/3-1 | 6/3-A-1 H | Total |
|---|---|---|---|---|---|---|---|---|
| lost circulation, losses, lost returns, mud loss, LCM | 1 | 1 | 14 | 15 | 0 | 3 | 15 | 49 |
| kick, influx, well control, SIDPP/SICP, pit gain | 2 | 1 | 11 | 20 | 2 | 5 | 1 | 42 |
| stuck, differentially stuck, jarring | 2 | 2 | 25 | 10 | 0 | 3 | 1 | 43 |
| tight hole/spot, overpull, ream, drag | 23 | 11 | 30 | 23 | 2 | 25 | 22 | 136 |
| wash out | 1 | 0 | 5 | 0 | 1 | 0 | 0 | 7 |
| pack off | 0 | 2 | 23 | 4 | 0 | 1 | 6 | 36 |
| sidetrack | 0 | 0 | 0 | 2 | 0 | 0 | 1 | 3 |
| fish, fishing, overshot, junk basket | 2 | 1 | 27 | 4 | 0 | 5 | 3 | 42 |
| Pages with any match | 25 | 12 | 81 | 55 | 5 | 30 | 42 | 250 |

So about 20% of pages match something, concentrated in three places: the operations
narrative per hole section, the daily/time-coded activity tables, and (in 25/7-3 and
6/3-A-1 H) "experience / lessons / recommendations" sections per section. By the categories
of section 4, most hits fall in "daily drilling operations" and "casing/cementing".

Keyword hits overstate events. "Losses" is the word actually used (61 hits; "lost
circulation" appears 5 times in 7 reports) but it also covers pressure losses and negations
("no losses"). `packoff` (22 of 49 pack-off matches) is mostly the wellhead seal assembly.
"Ream" is often routine; "well flowed" and "shut in well" come from DST descriptions; "junk
basket" is a routine wireline run; objectives ("without any stuck pipe or well control
incidents") match too.

Verbatim examples:

| Wellbore, page | Sentence |
|---|---|
| 25/7-2 p29 | "While drilling at 1938m, the torque increased suddenly and the hole packed off." |
| 25/7-2 p55 | "The pipe got stuck at 2645m, but worked the pipe free with full circulation." |
| 25/7-2 p24 | "The drilling continued to 4263m where the mud weight was raised to 16.2 ppg following a pit gain of 1 bbl/15 min." |
| 25/7-3 p154 | "The initial SIDPP was 16 bar with the 1.20 SG mud in the hole; the SIDPP gradually increased to 43 bar." |
| 25/7-3 p149 | "The Skade formation (porous sandstone), just below the 13 3/8" shoe, was speculated to be the most likely interval for the losses to occur." |
| 6/3-A-1 H p56 | "At 3393 m a tight spot was encountered which was wiped by reaming through it with 160 rpm, 13-16kNm with no further over pull experienced." |
| 6507/7-9 p23 | "Short trip to 370m (tight spot at 380m - 5-10 kdaN overpull)." |
| 6/3-1 p27 | "Drilled to 2926 m and raised mudweight to 1.35 S.G. Had problems with tight hole. Reamed and washed." |
| 25/6-3 p48 | "REAMED TO START DRILLING, STALLING CONDITION AND PACK OFF AT BTM AT 1277M." |
| false positive, 25/7-2 p170 | "Pressures losses in the drill string resulted in low bit hydraulics and reduced penetration rates in the lower part of the 17-1/2" section." |

## 6. How depths, hole sizes, mud weights and formation names appear

**Depths.** Metres throughout; feet are marginal (a handful of `ft` / `'` hits). The unit
is glued or spaced, upper or lower case: `2645m`, `3393 m`, `1277M`, `2575 metres`,
`270 meters`. Most depths in running text carry no reference at all (about 80-90% are bare
`m`). Where a reference is given it varies by report and era: `m RKB` (328 hits), `mRKB`
(95), `MRKB`, `m RT`, `m MD RT`, `mMD`, `m TVD SS`, `m MSL`, `m SS`, `BRT`. The 1980s
reports say RKB almost exclusively; the 2011 report says MD / TVD / RT. Intervals are
written `1630-2926 m`, `from 2200m to`, `4291m - 4330m`, `1562–3560 m`. In tables the depth
is a bare number under a header such as `Depth m` or `DEPTH` (`168.00`).

**Hole and casing sizes.** Inches as mixed fractions with many spellings of the same size:
`12 1/4"`, `12-1/4"`, `12 ¼`, `12.25`; `17 1/2"`, `17-1/2"`, `17½"`, `17.5`; `8 1/2 "`
(space before the quote); `9 5/8"`, `9-5/8"`, `9 5/8in`. OCR damage on fractions occurs:
`9%"` and `13%"` (for 9 5/8 and 13 3/8), `171⁄2"`, `91⁄2`, `9 5/6"`. A size is often not
followed by "hole" or "casing" (`8 1/2"` alone 250 times in 25/7-2), so the hole/casing
distinction must come from the value set (36, 26, 17 1/2, 12 1/4, 8 1/2, 6 are hole sizes;
30, 20, 13 3/8, 9 5/8, 7 are casing/liner) or from context. Section headings such as
`## 12 1/4" hole: 1630-2926 m` are common in the operations narrative and are reliable
anchors.

**Mud weight.** Dominant unit per report (match counts): 25/6-1 `g/cm3` (16, also `SG`,
`s.g.`, `g/cc`, `kg/m3`, `ppg`); 25/6-3 `SG` (33, also `g/cm³`, `g/cc`); 25/7-2 `ppg` /
`PPG` / `p.p.g.` (352) with `sg` (47), often paired as `1.92 sg (16 ppg)`; 25/7-3 `sg` /
`SG` (114, also `kg/m3`, `ppg`, `S.G.`); 6507/7-9 `g/cm3` (table header
`Mud Weight g/cm³ (ppg)`); 6/3-1 `S.G.` / `s.g.` (67, also `g/cc`, `KG/M3`); 6/3-A-1 H
`sg` (80, also `kg/m3`; decimal comma occurs: `1,60 sg`).
`kg/l` was not seen in these 7. s.g., g/cm3, g/cc are numerically the same; ppg needs
conversion (x 0.1198) and kg/m3 division by 1000. The same units are used for LOT/FIT
results, ECD, pore pressure gradients and cement slurry density ("16.6 m3 1.80 sg gas tight
cement slurry"), so a number with `sg` is not necessarily a mud weight. Phrases: "Increased
mud weight to 1.20 S.G.", "raised mudweight to 1.35 S.G.", "mud wt", `Mud Wt p.p.g.` as a
table column with bare values below.

**Formation names.** Mostly the current SODIR names, written `Heimdal Formation`,
`Heimdal Fm`, `Heimdal Fm.`, `HEIMDAL FM.`, `Shetland Group`, `Shetland Gp`, `SHETLAND GP`,
sometimes prefixed (`Top Shetland Gp`, `Upper Heimdal Formation`, `Cretaceous Tor Fm.`).
Variants and old usage seen: `Våle` / `Vale` / `VALE`, `Rødby` / `Rodby` / `RoDBY`,
`Draupne` / `Draupe` / `DRAUPHE`, `Hordland`, `Sale Fm` (for Sele), `CHALK GROUP` and
`Chalk Group` (older name, 6/3-1), `Montrose Group`, `TRIASSIC GROUP`, UK names
(`Smith Bank Formation`, `Burton Formation`), informal units (`Sand Formation`) and glued
tokens from figures (`UTSIRAFORMATION883`). Ages (Paleocene, Jurassic, ...) are used as
often as formation names, especially in the older reports. Formation-top tables exist in
every exploration report (e.g. 6/3-1 p14 with mRKB and mMSL columns) but the 2011
development report names formations only about 26 times.

**Dates.** `18.01.86`, `85.01.10` (yy.mm.dd, 6/3-1), `5 MAY 1990`, `95/08/15`,
`15/08/1995`, `2000-03-14`, `26-Apr-11`; daily entries often give only a time (`0400-0500`).

## 7. Tables in `ocr_markdown`

All tables are GitHub-style markdown pipe tables; there is no HTML and no row objects.
780 pages contain at least one table row; 1,277 table blocks in total.

| Wellbore | Pages with tables | Table blocks | Blocks with uneven column counts | Empty cells |
|---|---|---|---|---|
| 25/6-1 | 81 | 113 | 17 | 28% |
| 25/6-3 | 60 | 87 | 46 | 60% |
| 25/7-2 | 154 | 249 | 51 | 24% |
| 25/7-3 | 193 | 349 | 70 | 48% |
| 6507/7-9 | 21 | 28 | 5 | 63% |
| 6/3-1 | 166 | 203 | 130 | 49% |
| 6/3-A-1 H | 105 | 248 | 48 | 29% |

Typical problems:
- The header row is frequently empty (`|  |  |  |`) and the real header is the first data
  row, or the header is split over two rows.
- Row cells shift: in 25/7-3 p91 the activity table has 9 columns but several rows have 8,
  so `HOURS` lands under `OP`. 29% of table blocks have uneven column counts.
- Wide tables (25-27 columns in the 25/7-2 mud property tables) keep their values, but
  columns under merged headers lose their names.
- Several logical records are merged into one cell without spaces
  ("0000-0400Continue to drill to TD at 227m.Drilled gravel bed f/ 215m to 227m.0400-0500Pumped...").
- A table can continue across pages with no repeated header.

Short key/value and formation-top tables come through well. Numeric columns in simple
tables are usually correct, but a transposed digit is possible ("Changed bit and drilled to
1915 m" between 2641 m and 2926 m in 6/3-1 p27).

## 8. Machine-written text mixed into the OCR field

On figure pages `ocr_markdown` is sometimes not a transcription but a description of the
figure ("X-axis label: Equivalent Mud Density (SG). X-axis tick range shown approximately
from 0.9 to 2.0", 25/6-3 p28), with hedges such as "(partly legible)" or "(maybe ...)".
Some pages start with wrapper headings (`# Page Text`, `# OCR Text`, `[NO TEXT]`). A few
pages are runaway repetitions: 25/7-3 p10 is 26,667 chars, mostly one table row repeating
`TD 2575m`; 25/7-3 p192 is 32,868 chars; 25/6-3 has one of 23,955 chars. 14 pages have a
single cell value making up more than 30% of all cells (not all of them are errors). The
quality label does not reliably flag these pages: p192 is "Acceptable".

## 9. Old scanned exploration reports vs the 2011 development report

| Aspect | 1984-2000 scanned exploration reports (6) | 2011 development report (6/3-A-1 H) |
|---|---|---|
| Source | typewritten / printed scans, some Norwegian forms (`FORMASJONSSTYRKETEST`, `Brønn nr.`, `Dybde`) in 6/3-1 and 25/6-1 | born-digital PDF, English |
| Content mix | 25-50% geology, cores, logs, well tests | 97% drilling, casing, cementing, equipment; 3 geology pages |
| Operations text | short narrative per hole section plus daily diary lines | long narrative per section with parameters (lpm, bar, rpm, kNm), plus "lessons" and "recommendations" |
| Problem wording | "tight hole", "stuck", "reamed and washed" | "tight spot", "over pull", "tendency to pack off", ECD and FIT discussion |
| Depth reference | `m RKB` | `mMD`, `m MD RT`, `m TVD RT`, `m TVD SS` |
| Mud weight | `S.G.`, `g/cm3`, `ppg` (operator dependent) | `sg`, decimal comma in places |
| OCR problems | fraction damage, empty/hallucinated figure pages, mixed-case noise (`VELL NAME`) | few; 0 Figure-dominated pages |

Structure of the JSON is identical for both kinds.

## 10. Implications for extraction

- Select candidate pages with a problem-term regex on `text` (about 20% of pages), not with
  tags; then let the model decide, because half the keyword hits are routine operations,
  negations ("no losses"), objectives or equipment names (`packoff`). Include "losses",
  "tight spot", "over pull", "packed off", "SIDPP", "pit gain", which are the words used.
- Send the model the page plus its neighbours or the whole hole-section narrative: events
  run across page breaks, and the hole size is usually stated only in the section heading
  (`## 12 1/4" hole: 1630-2926 m`), not in the sentence with the event.
- Extract the depth as written with its unit and reference (`m`, `m RKB`, `mMD`, `m TVD`)
  as separate fields, and assume MD RKB only as a flagged default. Most depths have no
  reference; TVD and SS values exist and must not be joined against MD casing/formation tables.
- Extract mud weight as value + original unit and convert in code (ppg x 0.1198,
  kg/m3 / 1000; s.g. = g/cm3 = g/cc). Require the model to say what the density refers to:
  mud, LOT/FIT, ECD, pore pressure and cement slurry all use the same units. Accept decimal commas.
- Normalise hole sizes in code with a tolerant parser (`12 1/4"`, `12-1/4"`, `12¼`,
  `12.25`, OCR forms `9%"`, `171⁄2"`) and snap to the standard size list; prefer the SODIR
  depth join for the section, as planned, and use the text size only as a cross-check.
- Do not take formation names from the model as final: map them to SODIR units with an
  alias list (Fm/Fm./Formation, Gp/Group, Våle/Vale, Rødby/Rodby, Draupne/Draupe, Chalk
  Group, ages used as units). The depth join remains the primary source of formation.
- Skip or down-weight pages that are Figure-dominated, longer than about 8,000 chars, or
  dominated by one repeated cell; their text can be a description or a repetition, not a
  transcription. Verify every quoted `text_span` is a literal substring of the page text.
- Treat tables as text for the model (ragged columns, empty headers make programmatic
  parsing unreliable), and strip repeated page headers before chunking the 2011-style reports.
