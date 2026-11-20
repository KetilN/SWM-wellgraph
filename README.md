# wellgraph

A knowledge graph over public Norwegian continental shelf end-of-well reports, served to
AI agents through an MCP server.

Plain search over the reports can find a paragraph. It cannot answer a question that
spans wells, such as:

> Which wells within 20 km had losses in the Shetland Group in the 12 1/4 in section, and
> at what mud weight?

wellgraph answers that by building one graph from three sources and serving it through
one link (`Data -> onboard -> knowledge graph -> MCP server -> agent`):

- **A backbone from public tables** (SODIR FactPages, no model involved): wellbores,
  coordinates, operators, formation tops, hole sections and casing for the whole shelf
  (9,883 wellbores).
- **Drilling events from the reports**: lost circulation, kicks, stuck pipe, tight hole
  and instability, each with depth, mud weight, remedy and the quoted text. A language
  model reads only the pages that describe drilling problems.
- **Core data**: porosity and permeability measurements on the same wellbores, with a
  fitted relationship per formation.

Two rules keep it trustworthy:

- **Depth joins attach the context.** An event's hole section and formation come from its
  depth against the SODIR casing and formation tables, not from the model.
- **Evidence on everything.** Every event links to its report and page, so an agent can
  quote the source.

## What is in it

| Content | Size |
|---|---|
| Reports | 5,326, over 4,032 wellbores (648,520 pages) |
| Drilling events | 79,413, over 3,513 wellbores |
| Core samples | 372,863, over 1,237 wellbores |

An example of what an agent finds: in Gullfaks block 34/10, a kick at 1.65 sg at 1,725 m
in one well and losses at 1.70 sg at 1,704 m in a neighbour, both in the Shetland Group,
with both report pages cited. More cases are in
[docs/mcp-live-test.md](docs/mcp-live-test.md).

Limits: formation is known for 40% of events and hole section for 63%, because most
development wells have no SODIR tops or casing to join against. Event types are sometimes
wrong: in a check of 23 events against their pages, about 15 were right.

Built at the FORCE hackathon, November 2026, with Python, DuckDB over Parquet, FastMCP and
claude-haiku-5.5 for extraction. Plan and history: [docs/PLAN.md](docs/PLAN.md).

## Use it from an agent

The server speaks MCP over stdio or streamable HTTP. It is read-only and needs no login.

### Hosted endpoint (temporary, hackathon environment)

```
https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp
```

It holds the whole SODIR backbone, all 5,326 reports (79,413 drilling events) and the
core data. An agent cannot attach an MCP server from a link pasted in a chat: register
the address once in the client, as below. See [docs/deploy-azure.md](docs/deploy-azure.md)
for how it is hosted.

**Claude chat (claude.ai or the desktop app)**

1. Open Settings, then Connectors.
2. Choose "Add custom connector".
3. Name it `wellgraph` and paste the address. Leave the OAuth fields empty.
4. In a new chat, switch the connector on in the tools menu.

Custom connectors depend on the plan; on a Team or Enterprise plan an owner may have to
add it.

**Claude Code**

```bash
claude mcp add --transport http wellgraph https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp
```

Start a new session afterwards; `/mcp` shows whether it connected.

**GitHub Copilot in VS Code**

Run "MCP: Add Server" from the command palette, choose HTTP and paste the address. Or
create `.vscode/mcp.json` in the open folder:

```json
{
  "servers": {
    "wellgraph": {
      "type": "http",
      "url": "https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io/mcp"
    }
  }
}
```

Click "Start" above the server entry, then open Copilot Chat in Agent mode and check that
the wellgraph tools are ticked under the tools icon. If they do not appear, MCP servers
may be switched off in the Copilot policy of the organisation that gave the licence.

**A first question to try**

> Use wellgraph. In block 34/10, at what mud weights did kicks and losses occur in the
> Shetland Group? Cite the report pages.

The agent needs no other documentation: the server's instructions and the
`describe_graph` tool tell it what the graph holds and how to query it.

### Local server

Use this when there is no network. It reads the graph from `data/kg`.

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
