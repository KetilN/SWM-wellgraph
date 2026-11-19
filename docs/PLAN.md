# wellgraph — EOWR Knowledge Graph (project brief and plan)

Repo: `hackathon-purple-fish-15/SWM-wellgraph` (public). That org is deleted on
2026-11-21: copy the repo to a personal account before then.

## Goal
Build a knowledge graph over public Norwegian continental shelf end-of-well / completion
reports and expose it to agents (Claude, GPT) through an MCP server.

Pipeline: `Data -> onboard -> KG -> DB -> API/MCP -> Agent`

The point to prove: a KG answers cross-well, multi-hop, aggregating questions that plain
RAG over the reports cannot. Example: "Which wells within 20 km had losses in the Shetland
Group in the 12 1/4 in section, and at what mud weight?"

## Constraints
- Demo: Friday 2026-11-20 13:30, 4 minutes. Repo must be public to present.
- Venue wifi is fragile (Zenodo measured ~128 KB/s). Source data is read locally; only
  SODIR tables (a few MB) and model calls need the network.
- Laptop internal disk has ~8 GB free: read reports from the external disk, write only
  small derived files (Parquet, graph) locally.
- Scope: 30-50 wells in one area, a vertical slice end to end, before scaling.

## Data
- Source: Zenodo record 24320092 (DOI 10.5281/zenodo.24320092), 4462 public wellbore
  completion reports, PDF + JSON. Licence CC BY 4.0. Attribute ConocoPhillips / Diskos.
- **Location: the user's external disk, not downloaded.** Path comes from
  `WELLGRAPH_DATA_DIR` in `.env`. `data/` in the repo holds derived files only.
  Volume name and whether it is extracted folders or tar parts: to be confirmed.
- Backbone: SODIR FactPages CSV exports.

### Verified findings (3 reports + CSVs inspected, 2026-11-19)
- Diskos CSV: 10,780 rows, 4,441 wellbores (2,975 development, 1,442 exploration),
  5,327 JSON + 5,453 PDF. All JSONs total 3.6 GB (median 0.58 MB); PDFs 105 GB.
- Folder layout: `<wellbore with / and space as _>/WELL/<name>.JSON|PDF`,
  e.g. `6_3-A-1_H/WELL/6_3-A-1_H_COMPLETION_REPORT_1.JSON`. A wellbore can have several
  reports.
- Report JSON: one top-level key (document path) ->
  `original_document_metadata` (wellName, wellboreName, Diskos wellId/wellboreId, title,
  dates, document_quality, page_quality_summary) and `pages[]`.
- Page: `page_no`, `page_metadata.quality` (Reliable / Acceptable / Uncertain /
  Figure-dominated), `OCR.ocr_markdown` (tables are markdown tables, not row objects),
  `OCR.layout.elements` (type, confidence, bounding box, no text), `AI.tags` (free-form,
  317 distinct in one report: use for routing, not as schema), `AI.page_summary`,
  `AI.has_tables`, `AI.has_figures`, `AI.figure_details`.
- 59-258 pages per report, ~2,000 characters per page. Same structure for a 2011
  development well and two 1980s exploration wells.
- Wellbore identity: JSON `wellboreName` (e.g. `6/3-A-1 H`) matches SODIR
  `wlbWellboreName`. JSON numeric IDs are Diskos IDs, NOT NPD IDs: resolve by name.
- Drilling events are present in daily-operations text with depths and mud weights.
- SODIR tables confirmed reachable (~1 s each): `wellbore_exploration_all`,
  `wellbore_development_all` (coordinates, operator, field, rig, dates,
  `wlbNpdidWellbore`), `wellbore_formation_top` (top/bottom depth per unit),
  `wellbore_casing_and_lot` (casing and hole diameter, depths, LOT mud density),
  `wellbore_mud` (mud weight at MD), `strat_litho_overview` (hierarchy).
  `field_overview` returned an error: find the correct table name.
- Candidate areas: 15/9 Sleipner/Volve (96 wellbores, 136 JSONs, 73 MB; 21 are 15/9-F
  Volve) or 6507/7 Heidrun area (100 wellbores, 146 JSONs).

## Design decisions
- Backbone first: entities that exist as public tables come from SODIR FactPages and the
  Diskos CSV, loaded deterministically. No LLM for these.
- LLM extraction only for what is unique to the reports: drilling events (losses, kicks,
  stuck pipe, tight hole, wellbore instability) with depth interval, mud weight, remedy,
  lesson learned, and the quoted text.
- **Depth joins, not LLM, attach context:** an event's hole section and formation are
  derived from its depth against SODIR casing and formation-top tables. If the model also
  states them, use that only as a cross-check.
- Ontology is small and hand-written in `ontology.yaml`. The LLM never invents schema; it
  fills it. `ontology.yaml` is a draft with two rings: core (what the graph holds,
  checked against the table contract by `tests/test_ontology.py`) and planned (the wider
  SWM proposal: well data, seismic, geology; no data source connected).
- **OSDU official schemas are the main reference for the data schema** (data-definitions
  repo, community.opengroup.org). The Accenture OSDU Ontology is secondary: it covers
  only Well / Wellbore / WellLog / Trajectory, useful for relation naming.
  Do not deploy OSDU services.
- Every extracted node/edge carries provenance: document id, page, text span.
- Entity resolution: wellbore names -> NPD wellbore ID; formation names -> SODIR
  lithostratigraphy, including old spellings.
- "Within N km" is computed at query time from coordinates, not stored as an edge.
- Integration is one link: a public, read-only remote MCP endpoint (streamable HTTP) that
  is self-describing. Server `instructions`, tool descriptions and a `describe_graph` tool
  (ontology, coverage, example questions) tell the agent what to do; no docs needed.
  Fallbacks: `llms.txt` / `AGENTS.md` at the repo root for agents given the repo URL, and
  a one-command local stdio run that fetches the published DB.
- Canonical published data is Parquet node/edge tables (open format); the graph DB file is
  built from them. PDFs are never re-hosted: evidence links back to the Zenodo record.

### Proposed ontology (not yet agreed; OSDU names verified to exist at 1.0.0)
| Node | OSDU kind | Source |
|---|---|---|
| Well | `master-data--Well` | SODIR |
| Wellbore | `master-data--Wellbore` | SODIR, key `wlbNpdidWellbore` |
| Field | `master-data--Field` | SODIR |
| Organisation | `master-data--Organisation` | SODIR drilling operator |
| Rig | `master-data--Rig` | SODIR drilling facility |
| GeologicalFormation | `reference-data--GeologicalFormation` | SODIR lithostratigraphy |
| HoleSection | `master-data--HoleSection` | SODIR casing table |
| TubularAssembly | `work-product-component--TubularAssembly` | SODIR casing table |
| FormationIntegrityTest | `work-product-component--FormationIntegrityTest` | SODIR LOT + reports |
| Document | `work-product-component--Document` | Diskos CSV + JSON metadata |
| Page | local type (no OSDU equivalent) | JSON pages |
| DrillingEvent | `master-data--Risk` (WellboreID, RiskStartDepth, RiskEndDepth, Cause, Consequence, Mitigations, RiskType "event" / "lessons learned") | LLM extraction |

Relationships: Wellbore BELONGS_TO Well, IN_FIELD Field, DRILLED_BY Organisation,
DRILLED_WITH Rig, PENETRATES GeologicalFormation (top/base depth, from
`WellboreMarkerSet`); GeologicalFormation PART_OF GeologicalFormation; HoleSection
SECTION_OF Wellbore; TubularAssembly SET_IN HoleSection; DrillingEvent OCCURRED_IN
Wellbore, IN_SECTION HoleSection, IN_FORMATION GeologicalFormation, EVIDENCED_BY Page;
Page PAGE_OF Document; Document DESCRIBES Wellbore.

Not in OSDU (do not use): `WellboreEvent`, `WellProblemType`, `LostCirculationType`.
The team's wider SWM entity list (well logs, production, seismic, geology model,
petrophysics, saturations) has no data in this dataset. It is recorded as the planned
ring in `ontology.yaml`, mapped to OSDU kinds, with the wellbore as the join point.

## Tech stack
| Stage | Tool | Notes |
|---|---|---|
| Environment | Python 3.12, `uv`, ruff, pytest | One lockfile. System `python3` is 3.14; do not use it. |
| Data access | Plain file reads from `WELLGRAPH_DATA_DIR` (external disk); Python `tarfile` only if the disk holds tar parts | No download tooling. A Zenodo range-fetch fallback was proven to work (one tar header per request, ~0.33 s, limit 133 requests/min) but is not planned. |
| Onboard / staging | DuckDB + Parquet | Diskos CSV and report JSONs; intermediates are files. |
| Backbone | SODIR FactPages CSV exports -> DuckDB | Deterministic, no LLM. Load the whole shelf. |
| Ontology | `ontology.yaml` + pydantic models | One source of truth for graph schema and extraction schema. |
| Page routing | Keyword rules + `AI.tags` + DuckDB full-text search (BM25) | Only drilling-event pages go to the model. |
| Extraction | Model from Microsoft Foundry (hackathon Azure subscription) behind a thin `LLMClient` adapter | Pydantic-validated JSON. Provider swappable in one file. SDK chosen once the available models are known. |
| Extraction safety | On-disk response cache (JSONL keyed by page hash) + `tenacity` retries | Never re-request a page on fragile wifi. |
| Entity resolution | rapidfuzz + hand-made alias tables | Wellbore -> NPD ID; formation -> SODIR lithostratigraphy. |
| Geo | Haversine in DuckDB SQL on SODIR decimal degrees | No GIS library. |
| Graph | Kuzu (embedded, Cypher); DuckDB-only as fallback | Kuzu is archived upstream, so the DB file is a build product, never the published format. |
| MCP server | FastMCP; stdio locally, streamable HTTP when hosted | Read-only DB, capped result sizes. |
| Demo agent | Claude Desktop / Claude Code connected to the MCP server | No agent code needed. |
| RAG baseline | DuckDB BM25 over page text | Embeddings only if Foundry offers an embedding model (no local torch). |
| Eval | 10 questions in YAML + scoring script | KG vs RAG, one slide. |
| Evidence view | PyMuPDF renders the cited PDF page (optional) | PDFs are on the external disk. |
| Publishing | `huggingface_hub` for Parquet upload | Reading needs nothing: DuckDB `hf://`. |

Do not install or change dependencies without the user's agreement. `pyproject.toml` and
`uv.lock` exist locally but are uncommitted and not yet agreed.

### Model access (from the organisers' emails)
- Microsoft Foundry on a shared Azure subscription ("Contoso" directory); $2,000 of
  tokens for the whole event. Tenant invite lands in Gmail spam. Models not yet known.
- GitHub Copilot: $100 credit per participant via the hackathon org. A coding-assistant
  licence, not a pipeline API: use it for coding help.
- Anthropic tokens mentioned by organisers; distribution unknown.

## Hosting (agreed in outline)
- Hugging Face needs: free account, a write token the user creates (never in the repo),
  a public dataset repo with a dataset card (cc-by-4.0, attribution, source DOI), and a
  public Docker Space on port 7860 for the MCP server. Free Spaces sleep after ~48 h idle.
  Defer upload and Space until milestone 5.
- Code: public GitHub repo. Data: Parquet on Hugging Face Datasets (demo slice may be a
  GitHub Release asset); Zenodo DOI snapshot once stable. MCP server: small container
  with the DB baked in. Demo always has a local stdio fallback (venue wifi).
- Size estimate: demo slice ~50-100 MB; all 4462 reports ~1-3 GB.

## MCP tools (target)
`describe_graph`, `find_wells`, `get_well_summary`, `query_events`, `offset_well_analysis`,
`get_evidence`

## Plan
Done:
- [x] Brief, tech stack and hosting outline agreed.
- [x] Data schema inspected (see "Verified findings").
- [x] Repo created and pushed: structure, `.gitignore`, this file.
- [x] MCP server: six read-only tools over a Parquet graph, stdio and HTTP.
- [x] Backbone: whole-shelf SODIR tables in the graph (9,883 wellbores, 41,537 formation
      tops, 8,758 hole sections).
- [x] Report reader and document / page tables; 7 sample reports loaded (1,208 pages).
- [x] `ontology.yaml` (core + planned rings) and `schema.yaml` (table contract).
- [x] Extraction pipeline up to the model call: routing, prompt, event model, validation
      against the page, depth joins, response cache, runner. Tested with a fake model.
      Dry run on the 7 samples: 194 of 1,208 pages routed, ~450k input tokens (rough).
- [x] Evaluation: 10 questions in `eval/questions.yaml` (5 backbone with reference
      answers, 5 event questions awaiting hand-labelled answers) and a runner
      (`wellgraph.evaluation`) with a BM25 search baseline.
- [x] `Dockerfile` for the hosted MCP endpoint (port 7860, graph baked in from a tarball).
- [x] First real extraction on the 7 samples (claude-haiku-5.5, 194 pages, no failed
      pages): 211 events after merging duplicates, 68% with a depth, 29% with a mud
      weight. Spot-check of 20 random events after prompt tuning: about 17 are real hole
      problems (a reading of a small sample, not a measured precision).
      Known gaps: a third of events have no depth; same event without depth can repeat.

Next, in order:
1. **Disk:** user connects the external disk; check its layout read-only; set
   `WELLGRAPH_DATA_DIR`.
2. **Decisions:** area, three demo questions, Foundry model, Kuzu vs DuckDB-only,
   dependency list, licence, ontology go-ahead.
3. **Backbone:** load SODIR tables + Diskos CSV; graph answers "wells in area by
   formation / operator" with no LLM.
4. **Extraction:** page routing -> structured events -> depth joins to hole section and
   formation -> resolve to backbone IDs. 30-50 wells in the chosen area.
5. **MCP server:** six tools; Claude answers the three demo questions with citations.
   Then Hugging Face upload and hosted endpoint.
6. **Eval:** 10 questions, KG vs plain RAG. One slide of numbers.
7. **Demo:** README, `AGENTS.md` / `llms.txt`, recorded backup demo, 4-minute story.
   Copy the repo out of the hackathon org.

## Layout
```
data/                     derived files only, git-ignored (factpages/, staging/, kg/)
ontology.yaml             entity / relationship types: core ring + planned ring (draft)
schema.yaml               the table contract: tables, columns, types, units, enums
                          (loaded by src/wellgraph/graph/schema.py)
samples/reports/          four report JSONs (25/6, 25/7) for trying the pipeline
src/wellgraph/
  onboard/      report reader, SODIR + Diskos loaders
  extract/      page routing, LLM client, event schema, cache
  resolve/      name -> NPD ID, depth -> hole section / formation
  graph/        Parquet -> graph build, queries
  mcp_server/   FastMCP tools
eval/   docs/   tests/
```

## Open questions for the user
- External disk: volume name; extracted folders or tar parts?
- Which area: 15/9 Sleipner/Volve (links to the subsurface camera work) or 6507/7?
- Which three demo questions?
- Foundry: which models are available, rate limits / token budget?
- Graph store: confirm Kuzu, or go DuckDB-only?
- Test-first as a working rule? (Proposed: strict for deterministic parts, recorded
  fixtures for extraction, none for one-off scripts.)
- Code licence (MIT / Apache-2.0)?
- How does Ketil's entity list fit: outer ring, or a second scope?

## Working rules
- Keep `data/` out of git. Commit small and often. No AI attribution lines in commits
  or PRs.
- Prefer a working thin slice over completeness.
- Propose before acting on stack, dependencies, scaffolding and schema; wait for a yes.
- Never write to the external source disk.
