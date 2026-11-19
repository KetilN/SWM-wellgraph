# wellgraph

A knowledge graph over public Norwegian continental shelf end-of-well reports, served to
AI agents through an MCP server.

Plain search over the reports can find a paragraph. It cannot answer a question that
spans wells, such as:

> Which wells within 20 km had losses in the Shetland Group in the 12 1/4 in section, and
> at what mud weight?

wellgraph answers that by combining two things:

- **A backbone from public tables** (SODIR FactPages): wellbores, coordinates, operators,
  formation tops, hole sections and casing.
- **Drilling events from the reports**: lost circulation, kicks, stuck pipe, tight hole
  and instability, each with depth, mud weight and a citation to the report page.

Built at the FORCE hackathon, November 2026. Status: work in progress. See
[docs/PLAN.md](docs/PLAN.md).

## Use it from an agent

The server speaks MCP over stdio or streamable HTTP. It is read-only.

**Claude Code**

```bash
claude mcp add wellgraph -e PYTHONPATH=src -e WELLGRAPH_KG_DIR=data/kg -- uv run --directory /path/to/SWM-wellgraph python -m wellgraph.mcp_server
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "wellgraph": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/SWM-wellgraph", "python", "-m", "wellgraph.mcp_server"],
      "env": { "PYTHONPATH": "src", "WELLGRAPH_KG_DIR": "data/kg" }
    }
  }
}
```

**Any MCP client over HTTP**

```bash
PYTHONPATH=src uv run python -m wellgraph.mcp_server --http --port 7860
```

Then connect to `http://localhost:7860/mcp`.

**Hosted (temporary, hackathon environment)**

- MCP endpoint: `https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp`
- Web page: <https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/>

```bash
claude mcp add --transport http wellgraph https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp
```

It holds a small slice: the whole SODIR backbone and 10 reports. See
[docs/deploy-azure.md](docs/deploy-azure.md).

The agent needs no other documentation: the server's instructions and the
`describe_graph` tool tell it what the graph holds and how to query it.

## Look at it in a browser

The same server has a small page for people: an offset map, a depth strip for one
wellbore (formations, hole sections, mud weight, events) and the report text behind each
event.

```bash
PYTHONPATH=src uv run python -m wellgraph.mcp_server --http --port 7860
```

Open `http://localhost:7860/`. The page reads the same graph as the MCP tools, through
JSON endpoints under `/api/`.

## Tools

| Tool | What it does |
|---|---|
| `describe_graph` | What is loaded, area covered, units, schema, example questions. Start here. |
| `find_wells` | Wellbores by block, field, operator, formation, type, or distance. |
| `get_well_summary` | One wellbore: header data, formation tops, hole sections, event counts, reports. |
| `query_events` | Events across wells by type, formation, hole size, distance, mud weight. |
| `offset_well_analysis` | Aggregated events and lessons around a wellbore or planned location. |
| `get_evidence` | The verbatim quote, report and page behind one event. |
| `reservoir_quality` | Porosity and permeability from core per formation and rock type, with fitted relationships. |

Depths are metres measured depth, mud weight is specific gravity, hole sizes are inches,
distances are km.

## Run it locally

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

The server reads a folder of Parquet files (default `data/kg`, or set
`WELLGRAPH_KG_DIR`). The tests build a small synthetic graph, so they run without any
data.

## Build the graph from reports

One command takes a folder of report JSONs to a queryable graph:

```bash
PYTHONPATH=src uv run python -m wellgraph.pipeline --reports /path/to/reports --kg data/kg --download-backbone
```

It loads the SODIR backbone (once), links reports to wellbores, extracts events with a
language model, finds a depth context for events that lack one, and parses daily report
tables. Model replies are cached, so a rerun only pays for new pages; `--offline <model>`
rebuilds from the cache with no model calls. The model endpoint is set in `.env` (see
`.env.example`).

## Layout

```
src/wellgraph/
  onboard/      read report JSONs, load SODIR and Diskos tables
  extract/      page routing, event schema, model client
  resolve/      wellbore names to NPD ids, depth to hole section and formation
  graph/        graph build and read-only store
  mcp_server/   the MCP tools
  app/          small web page and JSON API on the same server
samples/        four report JSONs to try the pipeline on
ontology.yaml   what exists: entity and relationship types, OSDU kinds
schema.yaml     how it is stored: tables, columns, types, units
docs/           plan and design notes
```

## Data and attribution

- Reports: "4462 public wellbore completion reports in pdf and json format from the
  Norwegian continental shelf", Zenodo record 24320092,
  DOI [10.5281/zenodo.24320092](https://doi.org/10.5281/zenodo.24320092), CC BY 4.0.
  Attribution: ConocoPhillips / Diskos.
- Backbone: SODIR FactPages, Norwegian Licence for Open Government Data (NLOD) 2.0.
- Core data (porosity, permeability, rock type): RealPore, Zenodo record 4723018,
  DOI [10.5281/zenodo.4723018](https://doi.org/10.5281/zenodo.4723018).
  Data are © GeoProvider https://geoprovider.no/ and licensed CC-BY 4.0.
- Entity names follow the [OSDU](https://osduforum.org/) data definitions where one
  exists.

The full dataset is not in this repository. `samples/` holds four report JSONs.
