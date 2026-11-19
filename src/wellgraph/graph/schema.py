"""Table contract between the graph build and everything that reads the graph.

The contract itself lives in `schema.yaml` at the repository root; this module only loads
it and exposes it to Python. Do not add column lists here.

`schema.yaml` is found at the repository root (three levels above this package). Set
`WELLGRAPH_SCHEMA` to use a file elsewhere, for example inside a container image.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

SCHEMA_PATH = Path(
    os.environ.get("WELLGRAPH_SCHEMA") or Path(__file__).resolve().parents[3] / "schema.yaml"
)


def load_schema(path: str | Path = SCHEMA_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Schema file {path} not found. Set WELLGRAPH_SCHEMA to the path of schema.yaml."
        )
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    for section in ("tables", "extra_tables", "enums", "source"):
        if section not in spec:
            raise ValueError(f"{path} has no '{section}' section.")
    for group in ("tables", "extra_tables"):
        for table, body in spec[group].items():
            if not body.get("columns"):
                raise ValueError(f"{path}: table {table} has no columns.")
            for column, col in body["columns"].items():
                if not isinstance(col, dict) or "type" not in col:
                    raise ValueError(f"{path}: {table}.{column} needs a 'type'.")
    return spec


def _types(tables: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {t: {c: v["type"] for c, v in body["columns"].items()} for t, body in tables.items()}


SPEC = load_schema()

# table -> {column: DuckDB type}. Read by the MCP server; a missing file is an empty table.
TABLES: dict[str, dict[str, str]] = _types(SPEC["tables"])
# Built and published, not yet read by the server.
EXTRA_TABLES: dict[str, dict[str, str]] = _types(SPEC["extra_tables"])

# table -> {column: description}, for all tables.
COLUMN_DOCS: dict[str, dict[str, str]] = {
    t: {c: v.get("description", "") for c, v in body["columns"].items()}
    for group in ("tables", "extra_tables")
    for t, body in SPEC[group].items()
}

# name -> description
EVENT_TYPE_DOCS: dict[str, str] = dict(SPEC["enums"]["event_types"])
DEPTH_REF_DOCS: dict[str, str] = dict(SPEC["enums"]["depth_refs"])
MUD_WEIGHT_KIND_DOCS: dict[str, str] = dict(SPEC["enums"]["mud_weight_kinds"])
DEPTH_BASIS_DOCS: dict[str, str] = dict(SPEC["enums"]["depth_bases"])

EVENT_TYPES: list[str] = list(EVENT_TYPE_DOCS)
DEPTH_REFS: list[str] = list(DEPTH_REF_DOCS)
MUD_WEIGHT_KINDS: list[str] = list(MUD_WEIGHT_KIND_DOCS)
DEPTH_BASES: list[str] = list(DEPTH_BASIS_DOCS)

SOURCE_DOI: str = SPEC["source"]["doi"]
SOURCE_URL: str = SPEC["source"]["url"]
ATTRIBUTION: str = SPEC["source"]["attribution"]
