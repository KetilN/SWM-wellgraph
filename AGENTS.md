# For AI agents

This repository contains **wellgraph**, a read-only MCP server over a knowledge graph of
Norwegian continental shelf wellbores and the drilling events reported in their public
end-of-well reports.

## If you want to query the graph

Connect to the MCP server; do not parse the repository files.

- Local, stdio: `uv run --directory <repo> python -m wellgraph.mcp_server`
  with environment `PYTHONPATH=src` and `WELLGRAPH_KG_DIR=<folder of Parquet files>`.
- Local, HTTP: add `--http --port 7860`, then use `http://localhost:7860/mcp`.
- Hosted endpoint: planned, not yet available.

Once connected:

1. Call `describe_graph`. It returns coverage, units, event types and example questions.
   The graph covers a slice of the shelf, not all of it.
2. Use `find_wells`, `query_events` and `offset_well_analysis` to answer the question.
3. Before stating an event as fact, call `get_evidence` and cite the report and page.
4. An empty result means "not found in the extracted reports", not "did not happen".

Conventions: depths in metres measured depth, mud weight in specific gravity (sg), hole
size in inches (`"12 1/4"` or `12.25`), distance in km. Wellbore names follow SODIR, for
example `15/9-19 A`.

## If you want to work on the code

- Read `docs/PLAN.md` for goals, decisions and open questions.
- Python 3.12 with `uv`. Run `uv run pytest` before and after a change.
- Tests come first for deterministic code.
- `schema.yaml` is the contract between the graph build and the server (loaded by
  `src/wellgraph/graph/schema.py`); `ontology.yaml` is the conceptual model. Change
  them deliberately, with their tests.
- Never commit anything under `data/`. Never commit secrets; configuration goes in `.env`
  (see `.env.example`).
