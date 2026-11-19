# Extraction design: everything around the LLM call

Scope: page routing, event model and validation, unit normalisation, prompt builders, depth
joins. No model or API call here. Files: `extract/{routing,units,models,prompt}.py`,
`resolve/depth.py`. Flow per report: `route_report(pages)` -> for each sent page `build_messages` + LLM
with `json_schema(strict=True)` -> `PageExtraction.model_validate` ->
`validate_against_page` -> `joins_for_event` -> `to_event_row`.

## 1. Routing
`score_page(text, tags, summary)` returns `RouteResult(score, send, reasons, matched_types)`.
1. **Mask look-alikes.** Phrases that contain an event word but are not events are blanked
   before matching: kick-off / KOP / kick drill / kick pad / kick tolerance; fluid, filtrate,
   HPHT, thread, make-up, pressure, transmission loss; lost time; kill and choke lines;
   stick-slip, stick-up; fishing neck, fish remains, fishing services; DST and leak-off
   shut-ins ("initial shut-in", "shut-in pressure"); wellhead pack-off fields; "Nearest Well
   Control"; glossary entries ("LCM Lost Circulation Material"); negatives ("no losses", "no
   hole problems", "no drag", "no shallow gas encountered"). Masking is local, so a real
   loss on the same page as "fluid loss" still counts.
2. **Evidence.** Regexes per event type with a weight; a weight-3 phrase (stuck, lost
   returns, LCM, mud losses, kicked, influx, pit gain, tight hole, cavings, twisted off ...)
   sends a page alone. Weak words (loss, tight, drag, reamed, washout, shallow gas) weigh
   0.5-1.5. Each pattern counts at most twice per page. Evidence found only in the page
   summary also counts (capped at 3), so a table page whose summary names the event is sent.
3. **Boosts** (only when there is evidence, and never more than the evidence itself): event
   tags (stuck_pipe, fishing, losses, NPT, downtime, lessons_learned ... +1 each, max 2),
   operations tags (drilling, daily_report, mud_weight ... +0.5 each, max 1.5), a depth on the
   page (+0.5), a mud weight on the page (+0.5).
4. **Penalties.** Non-operations tags (lithology, core_description, well_log, DST, VSP,
   site_survey ...) -1.5 each, max -3, halved when operations tags are also present.
   Table-of-contents and abbreviation pages (by tag, heading, or dotted leader lines) score 0.
5. `send = score >= 3.0`. `matched_types` lists types with at least 2 points; it is a hint
   for logging and evaluation, not passed to the model.

`route_report(pages, max_pages=None)` gives one result per page in order; with a cap only the
highest scores keep `send=True`. All ten schema event types are covered: pack_off and washout
are their own types, fishing covers twist-off / parted string / junk / overshot, and a bare
"sidetrack" (weight 1.5) needs "had to / forced to / due to" wording to send a page alone.

### Measured on the 7 sample reports (no cap)
| Wellbore | Sent / total | Share | Chars sent | ~Tokens (chars/4) | With cap 40 |
|---|---|---|---|---|---|
| 25/6-1 | 14 / 143 | 10% | 28,352 | 7,100 | 14 |
| 25/6-3 | 7 / 104 | 7% | 32,433 | 8,100 | 7 |
| 25/7-2 | 60 / 265 | 23% | 129,884 | 32,500 | 40 |
| 25/7-3 | 41 / 220 | 19% | 120,677 | 30,200 | 40 |
| 6507/7-9 | 7 / 59 | 12% | 12,393 | 3,100 | 7 |
| 6/3-1 | 28 / 258 | 11% | 47,882 | 12,000 | 28 |
| 6/3-A-1 H | 33 / 159 | 21% | 101,859 | 25,500 | 33 |
| **All** | **190 / 1208** | **16%** | **473,480** | **118,400** | |

### Manual spot-check (10 sent, 10 not sent, random, seed 20261119, final router)
- Agree (5): 6507/7-9 p18 (gas bubbles at the wellhead while drilling); 6/3-1 p33 (tight
  spots 1428-1562 m, 10-25 t overpull, fill); 6/3-1 p242 (tight hole, mud weight raised 1.20
  to 1.35); 25/7-2 p85 (stuck in hole, top of fish 4140 m); 6/3-1 p47 (90 t overpull
  retrieving casing).
- Marginal (3): 25/7-2 p103 (mud volume table noting "downhole losses" on two days, no
  depth); 6/3-A-1 H p109 (cement recap, "Total loss during cement job: 51"); 6/3-A-1 H p125
  (NPT table; "one roller stuck" is a bit cone, not pipe).
- Disagree (2): 6/3-A-1 H p118 ("Mud loss" is a solids-control column); 25/6-1 p110
  (rig-time table with a "FISH 3.0 h" row).

Not-sent sample (25/7-3 p24, 25/7-2 p201, 6/3-1 p248, 25/6-1 p58, 25/7-2 p90, 6507/7-9 p12,
25/6-1 p53, 6/3-1 p49, 6/3-1 p19, 25/6-3 p64): agree with all 10 (logs, seismic tie, cost
tables, lithology, schematics). All scored 0, which says little about recall near the
threshold, so I also read every not-sent page scoring 1.5-3 (about 40): sidewall-core
"Over-pull" columns, riserless "no returns", DST shut-ins, RFT "very tight", routine reaming.
None I would send; the closest is 25/7-2 p167 ("hole problems were eliminated when the mud
weight was raised", no depth). Reading: half to two thirds of sent pages hold a real event,
the rest are cheap near-misses. **There is no labelled set, so recall is not measured.**

## 2. Units (`units.py`)
Normalised to metres, sg, inches. Forms handled, all seen in the samples unless noted:
- Mud weight: `1.35 S.G.`, `1,35 sg`, `1.65sg`, `1,28 SG`, `1.20 g/cm3`, `1.33g/cc`, `kg/l`,
  `r.d.` (synthetic), `1350 kg/m3` (synthetic), `10.5 ppg`, `16.0+ ppg`, `p.p.g.`, `lb/gal`,
  ranges `1.46-1.50 SG`, and the table cell `1.03 (8.6)` (sg with ppg in brackets, accepted
  only when the two agree within 0.03 sg). ppg / 8.345. Accepted range 0.8-2.6 sg, which also
  rejects `110 kg/m3` (KCl concentration) and `20 ppb`.
- Depth: `2926 m`, `1215M`, `1178,5 M`, `2,926.0 m`, `3 150 mMD`, `2536mMD`, `4190.3 mRKB`,
  `m TVD`, `9600 ft`, ranges `186 to 260 m`, `2062-2080m`, `f/474m to 659m`, `t/2096.4m`.
  A comma followed by exactly three digits is a thousands separator, any other comma is a
  decimal comma. Not depths: `m3`, `m/hr`, `mm`, `min`, `MT`, `lbs/100ft`, `ft.lbs`.
- Hole size: `12 1/4"`, `12-1/4"`, `12¼`, `8½`, `12.25 in`, `12.25in`, `26"`,
  `17\frac{1}{2}"` (OCR LaTeX), `8 1/2 hole` without a quote mark. Not hole sizes: `ran 7 in
  hole`, coordinate seconds (`58° 21' 30"`). Range 2-48 in.
- `find_measurements(text)` returns all of these with offsets, kind, value, raw text, unit.
  Limits: it cannot tell a mud weight from a leak-off value, pore pressure or cement slurry
  density (all written `1.92 sg`): that is left to the model. A bare number is never a
  measurement (`raise wt. from 1.15 to 1.20`, table cell `| 1220,0 |`); validation covers it.

## 3. Event model and validation (`models.py`)
`ExtractedEvent` (what the model returns): `event_type` (one of `EVENT_TYPES`), `top_md`,
`base_md` (metres, 0-12000), `depth_ref` (MD / TVD / TVDSS or null), `mud_weight_raw` (as
written), `mud_weight_sg` (0.8-2.6), `mud_weight_kind` (`MUD_WEIGHT_KINDS`), `description`,
`remedy`, `lesson`, `text_span`, `confidence` (0-1). Extra fields are rejected, so the model
cannot add a hole section or formation. A single depth fills both ends; `base < top` is an
error; blank optional strings become null. Decided in code, not by the model:
- `depth_ref`: the model may only say MD / TVD / TVDSS when the page states it; a depth with
  no reference becomes `ASSUMED_MD`; an event with no depth gets `depth_ref` NULL.
- `mud_weight_sg` in the row is `parse_mud_weight(mud_weight_raw)` when that parses,
  otherwise the model's own value (needed for unit-less text like "1.20").
- `mud_weight_kind` defaults to `unknown` when there is a mud weight, NULL when there is none.
- `event_id` = `ev-` + sha1(document_id, page_no, type, top, base)[:16]; `to_event_row` reads
  the column list from `TABLES["event"]` at call time (unknown column -> `KeyError`).

`validate_against_page(event, page_text)` returns problem strings (empty = clean):
`text_span_not_found` (exact match after whitespace / table-pipe normalisation, else
rapidfuzz partial ratio >= 90), `top_md_not_on_page` / `base_md_not_on_page` (within 0.5 m of
a parsed depth, feet converted, or of any bare number on the page under either reading of a
comma), `mud_weight_raw_not_on_page`, `mud_weight_mismatch` (model's sg differs from the
parsed raw value by > 0.011), `mud_weight_not_on_page`. It reports; the caller decides
whether to drop the event, lower its confidence, or retry.

## 4. Depth joins (`resolve/depth.py`)
- Joins run only for `depth_ref` MD or ASSUMED_MD. TVD / TVDSS: `hole_diameter_in` NULL,
  `formations` empty (`joins_for_event`).
- Hole section: rows sorted by `base_md`, each covers (previous base, base]. NULL `base_md`
  falls back to `casing_depth_md`; rows with neither are ignored. Deeper than the last base
  -> None. Duplicate rows are harmless. Same base with a NULL and a real diameter -> the real
  one; same base with two diameters (pilot hole opened up) -> the smaller. A covering section
  with NULL diameter -> None, never borrowed from a neighbour. For an interval event the
  section at the deepest point is used (the shallower part is behind casing).
- Formations: every unit with `top_md <= depth <= base_md`, ordered GROUP, SUBGROUP,
  FORMATION, MEMBER, then shallowest first, no duplicates. A depth exactly on a boundary
  returns both neighbours. `formations_in_interval` returns units that overlap the interval;
  merely touching an end does not count; a zero-length or one-ended interval is a point.
- NULL `base_md` on a formation row: the unit ends at the next deeper top of the same level;
  with none deeper it is open-ended. NULL `top_md`: row skipped. Base above top: swapped.

## 5. Cost estimate (rough: tokens = chars / 4; OCR tables with many pipes tokenise worse)
- Per routed page: about 2,500 chars, ~620 tokens of page text (430-1,160 by report). Fixed
  per call: system prompt ~920 + strict schema ~770 tokens, identical on every call, so
  cacheable where the provider supports it.
- Per report (7 samples): 7-60 calls, mean 27; 3k-32k page tokens, mean ~17k; with the fixed
  part ~63k input tokens per report uncached. A cap of 40 pages trims only two samples.
- Whole corpus, if the samples are typical (7 of 5,327 JSONs, so a guess): ~145k calls,
  ~90M page tokens, ~250M fixed tokens before caching.

## 6. Open questions for the project owner
1. Recall is unmeasured. Would you label ~50 pages (event / no event) so the threshold is
   tuned on data instead of on my reading?
2. Should rig-time and NPT tables (hours of fishing, no depth) be sent? They are now, weakly.
3. Negatives ("no losses", "no hole problems") are masked. Wanted later as knowledge?
4. An out-of-range mud weight from the model (ppg given as sg) rejects the whole event.
   Alternative: null the field and keep the event. Which do you prefer?
5. Boundary depths return the unit above and below. Acceptable, or exclusive base?
6. Same-base hole sections with two diameters: I take the smaller. Right default?
7. `event.depth_ref` is NULL when an event has no depth; say so if it should be ASSUMED_MD.
8. The same event told on several pages (daily log, section summary, lessons table) gets
   several `event_id`s. Cross-page de-duplication is not done here.
