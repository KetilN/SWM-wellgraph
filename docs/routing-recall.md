# Routing recall: what the page router skips

Measured 2026-11-19 on the 7 sample reports in `data/kg`. Script: `eval/routing_recall.py`
(`sample`, `run`, `candidates`, `rescore`). Model replies are cached in `data/cache/recall/`.

**Read this first.** The verdicts below are one reader's judgment on a sample of 150 pages.
The detector is the extraction model: an event it fails to extract on a not-sent page is
invisible here. With one real miss, the intervals are wide and the rule advice is thin.

## 1. Method

1. Every page was scored exactly as the runner does:
   `score_page(text or "", [], summary or "")` (empty tag list). Result: 194 of 1,208 pages
   sent, 1,014 not sent. This matches the runner. (The table in `extraction-design.md`
   says 190; it predates the current rules.)
2. From the 1,014 not-sent pages, 150 were drawn with `random.Random(42)`, stratified
   proportionally by document (minimum 5 per document, largest-remainder rounding).
3. Each sampled page got one extraction call with the runner's own pieces
   (`build_messages`, `json_schema(strict=True)`, `OpenAICompatibleClient.from_env()`,
   `ResponseCache`, text cut at `MAX_PAGE_CHARS`, 4 workers) and the runner's filters
   (time-breakdown rows dropped, events whose quote is not on the page dropped).
4. I read every page that kept at least one event and judged each event REAL (a genuine
   drilling problem in this well that an engineer would want), MARGINAL or NOT.

Model calls: 150 requests, 0 errors, 0 repair calls, 150 valid replies; no event filtered.

## 2. Sample

| Wellbore | Pages | Sent | Not sent | Sampled | With model event | With REAL event |
|---|---|---|---|---|---|---|
| 25/6-1 | 143 | 13 | 130 | 19 | 0 | 0 |
| 25/6-3 | 104 | 14 | 90 | 13 | 1 | 1 |
| 25/7-2 | 265 | 60 | 205 | 30 | 1 | 0 |
| 25/7-3 | 220 | 42 | 178 | 27 | 1 | 0 |
| 6/3-1 | 258 | 27 | 231 | 34 | 0 | 0 |
| 6/3-A-1 H | 159 | 30 | 129 | 19 | 1 | 0 |
| 6507/7-9 | 59 | 8 | 51 | 8 | 0 | 0 |
| **All** | **1,208** | **194** | **1,014** | **150** | **4** | **1** |

By router score:

| Router score | Not-sent pages | Sampled | With model event | With REAL event |
|---|---|---|---|---|
| 0 | 901 | 123 | 1 | 0 |
| above 0, below 1.5 | 43 | 9 | 0 | 0 |
| 1.5 to below 3 | 70 | 18 | 3 | 1 |

## 3. Results

The model returned 5 events on 4 pages. By type: other 2, tight_hole 1, lost_circulation 1,
wellbore_instability 1. My verdicts: 1 REAL, 1 MARGINAL, 3 NOT.

| Measure | Count | Rate | 95% Wilson interval | Scaled to 1,014 not-sent pages |
|---|---|---|---|---|
| Pages with at least one model event | 4 / 150 | 2.7% | 1.0% to 6.7% | 27 (11 to 68) |
| Pages with at least one REAL event (miss rate) | 1 / 150 | 0.67% | 0.12% to 3.7% | 7 (1 to 37) |
| Pages with a REAL event not already in `event.parquet` | 0 / 150 | 0% | 0% to 2.5% | 0 (0 to 25) |

The one REAL page holds one REAL event, so missed REAL events are roughly the same as missed
pages: about 7 (1 to 37).

That one REAL event (bit and stabiliser balling in the 8 1/2" section of 25/6-3, told in a
lessons table on page 12) is the same occurrence the main run already extracted from pages
38 and 39 of the same report. The router missed the page, but the graph did not lose the
event. In this sample no new event was lost.

### Router recall estimate

The main run found 206 events (after cross-page merging) on 108 of the 194 sent pages.

| Assumption about the 206 events | Recall, point | Range from the interval |
|---|---|---|
| All 206 are real | 206 / (206 + 7) = 97% | 85% to 99% |
| Half are real (103) | 103 / (103 + 7) = 94% | 73% to 99% |

Assumptions:
- The 206 are counted as extracted; their precision has not been measured. The second row
  shows how the figure moves if half are false.
- One REAL event per missed page, as observed on the single case.
- Missed events are counted before merging; some will be duplicates of events already found
  (the one observed is), which makes true recall of distinct events higher than shown.
- The model is the detector. Events it does not extract on a not-sent page are not counted.
  On 25/7-2 page 40 I noticed text it did not extract (logging tools could not reach TD after
  several attempts; 31 sidewall-core bullets left in hole), which I would call MARGINAL.
- 7 reports. The rate may differ on other operators, years and report layouts.

## 4. Every candidate page

| Wellbore | Page | Score | Router reasons | Event type | Verdict | Quote |
|---|---|---|---|---|---|---|
| 25/6-3 | 12 | 2.5 | unstable hole x1 (+2), from "bore hole stability" said positively; depth on page (+0.5) | other | REAL: BHA balled up, forced a trip and a BHA change; lessons table. Duplicate of events on p38 and p39 | "The very short near bit stabiliser included in the first 8 1/2" BHA, balled up from behind the bit and up to the near bit stabiliser." |
| 25/6-3 | 12 | 2.5 | same page | tight_hole | MARGINAL: wireline plug stopped at a mud/seawater interface inside casing during P&A. Not a hole problem, but a useful lesson | "When attempting to set the EZSV on wireline at 1100 m, it stopped on the interface between sildril mud and sea water at approximately 400 m. It was not able to" |
| 25/7-2 | 40 | 1.5 | lost in hole x1 (+1); depth on page (+0.5) | other | NOT: jammed core barrels, which the prompt itself excludes | "On five out of six coring runs, the core barrels jammed. Problems with the core barrel jamming prematurely were attributed to formation characteristics." |
| 25/7-3 | 75 | 0 | no event wording | lost_circulation | NOT: one barrel taken during a formation integrity test is the test, not a loss event | "One barrel of mud was lost to the well during the test. Because of this, the formation integrity test did not proceed to leak off." |
| 6/3-A-1 H | 135 | 2.5 | fill x1 (+1.5); depth and mud weight on page (+1) | wellbore_instability | NOT: 1.8 m soft fill after a wiper trip in 36" top hole is routine | "Displaced hole to 1,25sg mud and performed wiper trip to 113m. 1.8m soft fill after wiper trip." |

Three of the four pages scored 1.5 to below 3; the fourth scored 0 and its event is NOT.

## 5. Router rule changes, re-scored on all 1,208 pages

Extra pages are computed by patching the rules in memory and re-scoring every page
(`eval/routing_recall.py rescore --list`). No variant removes a page that is sent today.
"Catches" refers to the 4 candidate pages above.

| Change | Extra pages sent | Catches REAL page (25/6-3 p12) | Also catches |
|---|---|---|---|
| A. New evidence, weight 3: `ball(ed\|ing) up`, `bit balling`, `balling` | +7 (194 to 201) | yes | - |
| B. `SEND_THRESHOLD` 3.0 to 2.5 | +25 (to 219) | yes | 6/3-A-1 H p135 (NOT) |
| C. `SEND_THRESHOLD` 3.0 to 2.0 | +47 (to 241) | yes | 6/3-A-1 H p135 (NOT) |
| D. New evidence, weight 3: `not able / unable / impossible to pass`, `could not pass` | +2 (to 196) | yes | the MARGINAL event on the same page |
| E. New evidence, weight 3: `mud / bbl / m3 lost to the well / hole / formation` | +1 (to 195) | no | 25/7-3 p75 (NOT) |
| F. Weight of `fill` 1.5 to 2.5 | +3 (to 197) | no | 6/3-A-1 H p135 (NOT) |
| G. A plus `gumbo`, weight 3 | +10 (to 204) | yes | - |
| A + B | +31 (to 225) | yes | 6/3-A-1 H p135 (NOT) |

Recommendation, in order:

1. **Add bit balling (A), +7 pages.** The router has no pattern for balling at all, while
   the prompt names bit balling as an `other` event and the main run already holds about 15
   balling events. The 6 extra pages outside the sample were not sent to the model; from the
   matching text, 4 describe balling in the well (25/7-2 p32 and p36, 6/3-A-1 H p39 and
   p150, all summary or lessons pages), one mentions only the remedy (6/3-A-1 H p117) and
   one is a list of figures (6/3-A-1 H p8). Expect mostly duplicates of events already
   found on daily-report pages, with better lesson text.
2. **Add "could not pass" wording (D), +2 pages.** Cheap; both pages are the same 25/6-3
   plug event told twice (p12, p40), which I judged MARGINAL. It matches the prompt's own
   definition of tight hole ("could not pass"), so it is likely to matter more on other
   reports than on these.
3. **Do not lower the threshold (B, C).** B sends 25 more pages. 8 of those 25 were in the
   sample: the model found events on 2, of which 1 REAL (the page A already catches) and 1
   NOT. Scaled, B buys about 3 real pages for 25 calls; A buys the same observed miss for 7.

Not recommended: E and F only catch events I judged NOT. `gumbo` (G) adds 3 pages over A;
the one that was in the sample (6/3-A-1 H p126) produced no event.

Separate observation, not a recall issue: "bore hole stability" stated as a good result
scores +2 as "unstable hole" on 25/6-3 p12. The pattern `(hole|wellbore|borehole)
(in)?stability` cannot tell "very good bore hole stability" from a problem.

## 6. Limits

- One reader, strict reading, no second opinion. Another engineer could move the MARGINAL
  and the "1.8 m soft fill" verdicts either way; counting both as REAL gives 2 pages of 150
  (1.3%, interval 0.4% to 4.7%, about 14 pages of 1,014).
- One real miss is too few to rank rule changes with confidence. The page costs are exact;
  the benefits are not.
- Pages that scored 0 are 89% of the not-sent pages and gave no real event in 123 sampled.
  A follow-up with the same budget would learn more by sampling the 113 not-sent pages that
  score above 0 (27 sampled so far, 1 REAL) and the 7 pages change A adds.
