# EOWR Knowledge Graph — project brief

## Goal
Build a knowledge graph over public Norwegian continental shelf end-of-well / completion
reports and expose it to agents (Claude, GPT) through an MCP server.

Pipeline: `Data -> onboard -> KG -> DB -> API/MCP -> Agent`

The point to prove: a KG answers cross-well, multi-hop, aggregating questions that plain
RAG over the reports cannot. Example: "Which wells within 20 km had losses in the Shetland
Group in the 12 1/4 in section, and at what mud weight?"

## Constraints
- Demo: Friday 13:30, 4 minutes. Repo must be public to present.
- Venue wifi is fragile: never download the full dataset. Work on a sample.
- Scope: 30-50 wells in one area, a vertical slice end to end, before scaling.

## Data
- Zenodo record 24320092 (DOI 10.5281/zenodo.24320092): 4462 public wellbore completion
  reports, PDF + JSON (already layout-parsed, with ML descriptions of pages, images,
  tables). Four 30 GB tar parts (120 GB) plus an 8.7 MB Diskos metadata CSV.
- JSON schema and CSV columns are NOT documented. Inspect before designing anything.
- Licence: page lists both CC BY 4.0 and NLOD 2.0. Attribute ConocoPhillips / Diskos.
- Backbone: SODIR FactPages tables (wellbore, field, licence, operator, formation tops,
  casing, lithostratigraphy).

## First steps (do these in order)
1. `curl -s https://zenodo.org/api/records/24320092 | jq -r '.files[] | "\(.size)\t\(.links.self)"'`
2. Download the metadata CSV into `data/raw/`. Print columns and 3 rows.
3. Range-download only the head of part001 and unpack what is whole:
   `curl -L -r 0-2147483647 -o data/raw/part001.head "<part001 link>"`
   `tar -tvf data/raw/part001.head | head -50`
   `tar -xf data/raw/part001.head -C data/sample`  (error at the cut-off end is expected)
4. Open 3 JSONs + 1 PDF. Report: page-level description/type labels? tables as rows or
   flat text? how is the wellbore identified? pages per report?
5. Stop and summarise findings before writing pipeline code. Then pick the area and
   three demo questions with the user.

## Design decisions (already made)
- Backbone first: entities that exist as public tables come from SODIR FactPages and the
  Diskos CSV, loaded deterministically. No LLM for these.
- LLM extraction only for what is unique to the reports: drilling events (losses, kicks,
  stuck pipe, wellbore instability) with depth interval, hole section, formation, mud
  weight, remedy, lesson learned.
- Ontology is small and hand-written in `ontology.yaml` (about 8 entity types, 10
  relationship types). The LLM never invents schema; it fills it.
- OSDU is vocabulary only: borrow entity names and ID conventions (Well, Wellbore,
  HoleSection, TubularAssembly, WellboreMarkerSet). Do not deploy OSDU services.
  Verify names against the OSDU schema repo before using them.
- Every extracted node/edge carries provenance: document id, page, text span.
- Entity resolution: wellbore names -> NPD wellbore ID; formation names -> SODIR
  lithostratigraphy, including old spellings.
- Stack: see "Tech stack" below. Everything is embedded or file-based; nothing to host
  until the public MCP endpoint.

- Integration is one link: a public, read-only remote MCP endpoint (streamable HTTP) that
  is self-describing. Server `instructions`, tool descriptions and a `describe_graph` tool
  (ontology, coverage, example questions) tell the agent what to do; no docs needed.
  Fallbacks: `llms.txt` / `AGENTS.md` at the repo root for agents given the repo URL, and
  a one-command local stdio run that fetches the published DB.
- Canonical published data is Parquet node/edge tables (open format); the graph DB file is
  built from them. PDFs are never re-hosted: evidence links back to the Zenodo record.

## Tech stack
| Stage | Tool | Notes |
|---|---|---|
| Environment | Python 3.12, `uv`, ruff, pytest | One lockfile. System `python3` is 3.14; do not use it. |
| Data access | `httpx` range requests + Python `tarfile` | Pull only chosen wells' JSONs. Zenodo measured ~128 KB/s; tar starts with 4462 dir entries, then 2 CSVs, then `<wellbore>/WELL/*.JSON|PDF`. |
| Onboard / staging | DuckDB + Parquet | Diskos CSV and report JSONs; intermediates are files. |
| Backbone | SODIR FactPages CSV exports -> DuckDB | Deterministic, no LLM. Load the whole shelf. |
| Ontology | `ontology.yaml` + pydantic models | One source of truth for graph schema and extraction schema. |
| Page routing | Keyword rules + DuckDB full-text search (BM25) | Only drilling-event pages go to the model. |
| Extraction | Hackathon-provided model behind a thin `LLMClient` adapter | Pydantic-validated JSON. Provider swappable in one file. SDK chosen once the API is known. |
| Extraction safety | On-disk response cache (JSONL keyed by page hash) + `tenacity` retries | Never re-request a page on fragile wifi. |
| Entity resolution | rapidfuzz + hand-made alias tables | Wellbore -> NPD ID; formation -> SODIR lithostratigraphy. |
| Geo | Haversine in DuckDB SQL on SODIR decimal degrees | No GIS library. |
| Graph | Kuzu (embedded, Cypher); DuckDB-only as fallback | Kuzu is archived upstream, so the DB file is a build product, never the published format. |
| MCP server | FastMCP; stdio locally, streamable HTTP when hosted | Read-only DB, capped result sizes. |
| Demo agent | Claude Desktop / Claude Code connected to the MCP server | No agent code needed. |
| RAG baseline | DuckDB BM25 over page text | Embeddings only if the hackathon offers an embedding endpoint (9 GB free disk, no local torch). |
| Eval | 10 questions in YAML + scoring script | KG vs RAG, one slide. |
| Evidence view | PyMuPDF renders the cited PDF page (optional) | For `get_evidence` in the demo. |
| Publishing | `huggingface_hub` for Parquet upload | Reading needs nothing: DuckDB `hf://`. |

Do not install or change dependencies without the user's agreement.

## Hosting (agreed in outline)
- Hugging Face needs: free account, a write token the user creates (never in the repo),
  a public dataset repo with a dataset card (cc-by-4.0, attribution, source DOI), and a
  public Docker Space on port 7860 for the MCP server. Free Spaces sleep after ~48 h idle.
  Defer upload and Space until milestone 4.
- Code: public GitHub repo. Data: Parquet on Hugging Face Datasets (demo slice may be a
  GitHub Release asset); Zenodo DOI snapshot once stable. MCP server: small container
  with the DB baked in. Demo always has a local stdio fallback (venue wifi).
- Size estimate: demo slice ~50-100 MB; all 4462 reports ~1-3 GB (see docs when written).

## MCP tools (target)
`describe_graph`, `find_wells`, `get_well_summary`, `query_events`, `offset_well_analysis`,
`get_evidence`

## Milestones
1. Schema known, ~30 wells local.
2. Ontology + backbone loaded; graph answers "wells in area by formation / operator".
3. Extraction: page routing -> structured output -> resolve to backbone IDs.
4. MCP server; Claude answers the three demo questions with citations.
5. Eval: 10 questions, KG vs plain RAG. One slide of numbers.
6. README, recorded backup demo, 4-minute story.

## Layout
```
data/raw/  data/sample/  data/factpages/  data/kg/     (git-ignored)
ontology.yaml
src/wellgraph/
  onboard/      tar index, range fetch, SODIR + Diskos loaders
  extract/      page routing, LLM client, event schema, cache
  resolve/      name -> NPD ID, depth -> hole section / formation
  graph/        Parquet -> graph build, queries
  mcp_server/   FastMCP tools
eval/   docs/   tests/
```

## Open questions for the user
- Which area? (If Volve 15/9 wells are in the CSV, Sleipner area links to the
  subsurface camera work.)
- Which three demo questions?
- What goes in the public repo vs stays private?
- Hackathon model: which model, which API (decides the SDK), rate limits / token budget?
- Graph store: confirm Kuzu, or go DuckDB-only?

## Working rules
- Keep `data/` out of git. Commit small and often.
- Prefer a working thin slice over completeness. Ask before any download over 5 GB.
