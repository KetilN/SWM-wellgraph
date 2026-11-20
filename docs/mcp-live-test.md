# Live MCP test, 2026-11-20

Endpoint: `https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp`
(image v12, all 5,326 reports: 4,032 wellbores with reports, 100,405 drilling events).

How it was tested: three independent agent sessions, each given only an MCP client for the
live endpoint (no repo access), answered 9 user-style questions and ran a robustness
check, about 150 tool calls in total. For 23 events they read the cited page with
`get_evidence` and compared it with what the tool reported. Server `instructions` were not
seen by the test client, so they are untested. Claude chat itself was not used: adding the
connector is a step in the user's account.

## Cases that hold up

### 1. Mud-weight window at Gullfaks top reservoir (best 30-second demo)
"In block 34/10, at what mud weights did kicks and losses occur in the same formations?"
- Shetland Gp, 12 1/4 in, about 1,700 m: 34/10-13 flowed at 1.65 sg at 1,725 m
  (`ev-d2a9eecc9b53b468`, pdf p154); 34/10-4 lost returns raising to 1.70 sg at 1,704 m
  (`ev-29c30b6ca8633628`, pdf p84).
- Brent Gp, 34/10-4: kick at 1.75 sg at 1,821 m (`ev-4b509f5374dd371b`), loss at 1.75 sg
  two metres deeper (`ev-a49da6dc08be8987`).
- Both evidence pages support type, depth, mud weight and formation. The 34/10-4 page also
  states pore pressure 1.65 and leak-off 1.74.
- Tools: `query_events` by radius around 34/10-1, then `get_evidence`. It took 14 calls
  because events cannot be filtered by block (see gaps).

### 2. One well across three sources: 6507/7-2 (Heidrun discovery area)
- `get_well_summary`: Conoco 1985, TD 3,262 m, tops, casing with leak-off values.
- `query_events`: 14 events. 120 bbl lost at 2,611 m in the Åre Fm at 12.5 ppg, cured by
  cutting mud to 11.6 ppg (`ev-4ba59e396699f35e`, supported verbatim); tight hole reamed
  2,435-2,448.5 m in the Tilje Fm at 13.2 ppg (`ev-09891bb99c396cd6`, supported).
- `reservoir_quality` for the well: Tilje core, 371 samples, median porosity 22.2 %,
  median horizontal permeability 32.6 mD (Klinkenberg). Core data © GeoProvider, CC BY 4.0.
- Do not show `ev-9caa8a962a31fb84` from this well: its depth is feet stored as metres.

### 3. Permeability overview (Ketil's question)
One `reservoir_quality` call per formation answers it completely.
- Hugin Fm: 8,956 samples, 52 wellbores; Klinkenberg 7,937 samples in 48 wells, air 36,
  gas 4, none 979. Without Klinkenberg horizontal permeability: 25/2-7, 25/6-1, 15/3-4,
  25/2-6.
- Garn Fm: 9,072 samples, 66 wellbores; Klinkenberg 7,246 in 61 wells, air 701, nitrogen 4.
  Flagged: 6407/1-3, 6506/12-1, 6506/12-6, 6507/12-3, 6305/12-1.

### 4. Brent Group reservoir quality
| Formation | Samples / wells | Median porosity % | Median kh mD | Fit R2 |
|---|---|---|---|---|
| Tarbert | 8,134 / 58 | 18.7 | 21.0 | 0.59 |
| Ness | 11,669 / 75 | 15.7 | 3.8 | 0.62 |
| Etive | 4,711 / 46 | 21.9 | 181 | 0.62 |
| Rannoch | 4,320 / 40 | 18.4 | 4.7 | 0.72 |

Etive is clearly best. The fits scatter by 0.84-1.0 log10 units, so a predicted
permeability is good to about a factor of 7-10.

### 5. Offset lessons before a well near 2/4-X-8 (Ekofisk)
568 wellbores within 10 km, 238 with reports, 175 with events. 12 1/4 in: losses in 54
wells (129 events, 1.56-1.88 sg), tight hole 94, pack-off 50, kicks 30. Recorded lessons
with citations, e.g. stuck at 6,821 ft after 53 h without a wiper trip (2/4-A-22,
`ev-076615ea39611d09`, supported verbatim). Ekofisk development wells have no SODIR tops or
casing, so formation is empty and hole size often missing there.

### 6. The headline question, with a caveat
"Lost circulation in the Shetland Gp, 12 1/4 in, within 20 km of 15/9-19 A": one call,
11 events in 7 wellbores (15/9-11, -9, -18, -12, -5, -3, -15). But all 11 mud weights come
from the SODIR mud table, not the reports, and 8 of 11 are losses while cementing the
9 5/8 in casing. The tool labels both; an agent has to say so. Case 1 is the safer demo.

## Evidence check (23 events read against the page)
- Event type: about 15 right, 4 partly, 4 wrong. Typical errors: a stuck wireline tool or a
  gauge ring that would not pass classed as stuck pipe / tight hole; a negative flow check
  or a post-test kill circulation classed as a kick.
- Depth: about 15 right, 4 partly, 4 wrong (feet stored as metres; depth taken from a
  neighbouring daily-report row).
- Mud weight: values and unit conversions were right where the report states them. Values
  filled from the SODIR table can disagree with the report (one kick: 1.48 sg from SODIR
  against 1.26 sg in the text).
- Counts in `describe_graph`, `find_wells`, `get_well_summary`, `query_events` and
  `offset_well_analysis` agree with each other. All calls answered in under 3 s.

## Fixed during the test (live in v12)
- `offset_well_analysis` returned up to 1.17 MB; it now returns about 30,000 characters
  (nearest 50 wells, 40 largest groups, 5 event ids per group; counts stay complete).
- `get_evidence` with an unknown id ran the server out of memory; it now answers
  "No event with id ..." at once. The container has 2 GB instead of 1 GB.
- Row limit capped at 100 (200 rows was 135,000 characters).
- A formation that matches nothing now returns a note; "Draupne Fm." matches.
- `describe_graph` says that its block list is only the 15 largest blocks.

## Open weak spots, by effect on a demo
1. **Feet stored as metres.** 6,252 events (6 %) have a depth more than 100 m below the
   wellbore's total depth, 2,240 of them marked as stated depths. Their hole section and
   formation are wrong too. Mostly reports written in feet (Ekofisk).
2. **Duplicates.** A report that covers several wellbores is loaded once per wellbore and
   its events are repeated on each (2/4-14 R and R2: 150 events each; 34/10-A-48, A and B:
   59 each; 1/3-1 and 1/3-2). The same incident is also extracted from the narrative, the
   daily log and the cement sheet of one report (the 34/4-3 kick appears three times with
   three mud weights). Counts are inflated; the size has not been measured.
3. **Operations inside casing or pipe** get an open-hole section and a formation from the
   depth join.
4. **No aggregation.** "Which formations are most associated with stuck pipe" took about
   100 calls. A `group_by` on `query_events` or a statistics tool would answer it in one.
   (Result, exact depths only: Nordland 295, Shetland 292, Hordaland 268 events by count;
   by share of a formation's events Statfjord, Hegre, Dunlin 22-23 % against 7-10 % for
   Nordland / Hordaland / Shetland.)
5. **Missing filters.** `query_events` has no block, field, operator or wellbore-type
   filter and no paging; `reservoir_quality` does not accept group names ("Brent" returns
   nothing) and has no depth window.
6. **Nothing outside drilling events.** "Oil rate on the drill stem test of 25/11-15" gets
   an honest but silent miss: the well and its report are found, no tool reads the pages.
   A page search tool would reach the 648,520 loaded pages.
7. Smaller: `page_no` is 0-based next to a 1-based `pdf_page`; the offset summary text says
   "report-derived mud only" while also counting SODIR-filled values; `query_events` and
   `get_well_summary` carry no attribution line; validation errors are raw pydantic text.
