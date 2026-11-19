"""Extract drilling events from the report pages in a graph folder.

    PYTHONPATH=src python -m wellgraph.extract.run --kg data/kg --dry-run
    PYTHONPATH=src python -m wellgraph.extract.run --kg data/kg --wellbore "25/7-2"

Reads `page`, `document`, `hole_section` and `formation_top` from the graph folder, routes
pages, asks a language model for the events on each routed page, checks every event
against the page text, attaches hole section and formations by depth, and writes
`event.parquet`.

The model is reached through an OpenAI-compatible chat-completions endpoint, configured by
environment variables (put them in .env, never in the repository):

    WELLGRAPH_LLM_BASE_URL   e.g. https://<resource>.openai.azure.com/openai/v1
    WELLGRAPH_LLM_API_KEY
    WELLGRAPH_LLM_MODEL      model or deployment name

Every model reply is cached on disk, keyed by model, prompt and page text, so a rerun
after a network failure costs nothing for pages already done.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

import duckdb
import httpx
from pydantic import ValidationError

from wellgraph.extract.models import PageExtraction, to_event_row, validate_against_page
from wellgraph.extract.prompt import SYSTEM_PROMPT, build_messages, json_schema
from wellgraph.extract.routing import score_page
from wellgraph.graph.schema import TABLES
from wellgraph.resolve.depth import joins_for_event

log = logging.getLogger(__name__)

# A quote that is not on the page means the event cannot be cited: drop it. Other
# validation findings (a number not found verbatim) are logged and the event is kept.
FATAL_PROBLEMS = ("text_span_not_found",)
MAX_PAGE_CHARS = 20_000  # runaway OCR pages are cut before they are sent


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        """Return the model's reply as a JSON string matching `schema`."""


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint (Azure Foundry included)."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0,
                 retries: int = 4):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retries = retries
        self._http = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "api-key": api_key},
        )

    @classmethod
    def from_env(cls) -> OpenAICompatibleClient:
        missing = [v for v in ("WELLGRAPH_LLM_BASE_URL", "WELLGRAPH_LLM_API_KEY",
                               "WELLGRAPH_LLM_MODEL") if not os.environ.get(v)]
        if missing:
            raise RuntimeError(f"Set {', '.join(missing)} (see .env.example).")
        return cls(os.environ["WELLGRAPH_LLM_BASE_URL"], os.environ["WELLGRAPH_LLM_API_KEY"],
                   os.environ["WELLGRAPH_LLM_MODEL"])

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "page_extraction", "schema": schema, "strict": True},
            },
        }
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self._http.post(f"{self.base_url}/chat/completions", json=body)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=response.request,
                                                response=response)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"Model call failed after {self.retries} attempts: {last}")


class ResponseCache:
    """Append-only JSONL cache of raw model replies."""

    def __init__(self, directory: str | Path):
        self.path = Path(directory) / "responses.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, str] = {}
        if self.path.is_file():
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        self._items[item["key"]] = item["reply"]
                    except (json.JSONDecodeError, KeyError):
                        continue  # a line cut off by a crash is ignored

    @staticmethod
    def key(model: str, page_text: str) -> str:
        digest = hashlib.sha256()
        for part in (model, SYSTEM_PROMPT, page_text):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def get(self, model: str, page_text: str) -> str | None:
        return self._items.get(self.key(model, page_text))

    def put(self, model: str, page_text: str, reply: str) -> None:
        key = self.key(model, page_text)
        self._items[key] = reply
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "model": model, "reply": reply}) + "\n")


def _read(con: duckdb.DuckDBPyConnection, kg_dir: Path, table: str) -> None:
    path = kg_dir / f"{table}.parquet"
    if path.is_file():
        escaped = str(path).replace("'", "''")
        con.execute(f"create view {table} as select * from read_parquet('{escaped}')")
    else:
        cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
        con.execute(f"create table {table} ({cols})")


def _dicts(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def extract_events(
    kg_dir: str | Path,
    client: LLMClient | None,
    cache_dir: str | Path,
    max_pages_per_report: int | None = None,
    wellbores: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    kg_dir = Path(kg_dir)
    con = duckdb.connect()
    for table in ("wellbore", "document", "page", "hole_section", "formation_top"):
        _read(con, kg_dir, table)

    where, params = "", []
    if wellbores:
        keys = [w.upper().replace(" ", "").replace("_", "") for w in wellbores]
        where = "where regexp_replace(upper(w.name), '[\\s_]+', '', 'g') in (select unnest(?))"
        params = [keys]
    documents = _dicts(con.execute(
        f"select d.document_id, d.wellbore_id, w.name as wellbore from document d "
        f"join wellbore w using (wellbore_id) {where} order by w.name, d.document_id",
        params,
    ))

    schema = json_schema(strict=True)
    cache = ResponseCache(cache_dir)
    stats: dict[str, Any] = {
        "documents": len(documents), "pages_total": 0, "pages_sent": 0, "pages_failed": 0,
        "cache_hits": 0, "model_calls": 0, "events_written": 0, "events_dropped": 0,
        "drop_reasons": Counter(), "estimated_input_tokens": 0, "warnings": 0,
    }
    rows: dict[str, dict[str, Any]] = {}
    fixed_tokens = (len(SYSTEM_PROMPT) + len(json.dumps(schema))) // 4

    for doc in documents:
        pages = _dicts(con.execute(
            "select page_no, text, summary from page where document_id = ? order by page_no",
            [doc["document_id"]],
        ))
        stats["pages_total"] += len(pages)
        scored = [(score_page(p["text"] or "", [], p["summary"] or ""), p) for p in pages]
        sent = [(r, p) for r, p in scored if r.send]
        if max_pages_per_report is not None:
            sent = sorted(sent, key=lambda rp: (-rp[0].score, rp[1]["page_no"]))
            sent = sorted(sent[:max_pages_per_report], key=lambda rp: rp[1]["page_no"])
        stats["pages_sent"] += len(sent)
        stats["estimated_input_tokens"] += sum(
            fixed_tokens + min(len(p["text"] or ""), MAX_PAGE_CHARS) // 4 for _, p in sent
        )
        if dry_run or not sent:
            continue

        wid = [doc["wellbore_id"]]
        sections = _dicts(con.execute("select * from hole_section where wellbore_id = ?", wid))
        tops = _dicts(con.execute("select * from formation_top where wellbore_id = ?", wid))

        for _, page in sent:
            text = (page["text"] or "")[:MAX_PAGE_CHARS]
            reply = cache.get(client.model, text)
            if reply is None:
                messages = build_messages(text, doc["wellbore"], page["page_no"])
                reply = client.complete(messages, schema)
                stats["model_calls"] += 1
                cache.put(client.model, text, reply)
            else:
                stats["cache_hits"] += 1
            try:
                extraction = PageExtraction.model_validate_json(reply)
            except ValidationError as exc:
                stats["pages_failed"] += 1
                log.warning("%s page %s: invalid model output (%s)", doc["wellbore"],
                            page["page_no"], str(exc).splitlines()[0])
                continue
            for event in extraction.events:
                problems = validate_against_page(event, text)
                fatal = [p for p in problems if p.startswith(FATAL_PROBLEMS)]
                if fatal:
                    stats["events_dropped"] += 1
                    stats["drop_reasons"][fatal[0].split(":")[0]] += 1
                    continue
                stats["warnings"] += len(problems)
                hole, formations = joins_for_event(
                    sections, tops, event.top_md, event.base_md, event.resolved_depth_ref
                )
                row = to_event_row(event, doc["wellbore_id"], doc["document_id"],
                                   page["page_no"], hole, formations)
                rows[row["event_id"]] = row

    stats["drop_reasons"] = dict(stats["drop_reasons"])
    if dry_run:
        con.close()
        return stats

    cols = ", ".join(f"{c} {t}" for c, t in TABLES["event"].items())
    con.execute(f"create table event_out ({cols})")
    if rows:
        marks = ", ".join("?" for _ in TABLES["event"])
        con.executemany(
            f"insert into event_out values ({marks})",
            [[row[c] for c in TABLES["event"]] for row in rows.values()],
        )
    escaped = str(kg_dir / "event.parquet").replace("'", "''")
    con.execute(
        f"copy (select * from event_out order by wellbore_id, top_md, event_id) "
        f"to '{escaped}' (format parquet)"
    )
    con.close()
    stats["events_written"] = len(rows)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.extract.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kg", default=os.environ.get("WELLGRAPH_KG_DIR", "data/kg"))
    parser.add_argument("--cache", default="data/cache/extract")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap of pages per report.")
    parser.add_argument("--wellbore", action="append", help="Only this wellbore (repeatable).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Route pages and estimate cost; no model calls, no output.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    client = None if args.dry_run else OpenAICompatibleClient.from_env()
    stats = extract_events(args.kg, client, args.cache, args.max_pages, args.wellbore,
                           args.dry_run)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
