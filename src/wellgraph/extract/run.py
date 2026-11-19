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
    WELLGRAPH_LLM_EXTRA_HEADERS   optional JSON object of extra request headers

Every model reply is cached on disk, keyed by model, prompt and page text, so a rerun
after a network failure costs nothing for pages already done.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

import duckdb
import httpx
from pydantic import ValidationError

from wellgraph.extract.models import PageExtraction, to_event_row, validate_against_page
from wellgraph.extract.prompt import SYSTEM_PROMPT, build_messages, json_schema
from wellgraph.extract.routing import score_page
from wellgraph.graph.schema import EXTRA_TABLES, TABLES
from wellgraph.resolve.depth import MD_DEPTH_REFS, joins_for_event
from wellgraph.resolve.mud import mud_weight_at

log = logging.getLogger(__name__)

# A quote that is not on the page means the event cannot be cited: drop it. Other
# validation findings (a number not found verbatim) are logged and the event is kept.
FATAL_PROBLEMS = ("text_span_not_found",)
# Rows of a time-breakdown table ("1 Hour KT - Mud losses") are bookkeeping, not events.
_TIME_BREAKDOWN_ROW = re.compile(r"^\W*\d+(?:[.,]\d+)?\s*(?:hours?|hrs?)\s+[A-Z]{1,3}\s*-", re.IGNORECASE)
MAX_PAGE_CHARS = 20_000  # runaway OCR pages are cut before they are sent


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        """Return the model's reply as a JSON string matching `schema`."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1) if match else text


class OfflineClient:
    """Answers only from the cache; used to rebuild events without calling a model."""

    def __init__(self, model: str):
        self.model = model

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        raise RuntimeError("offline: this page has no cached reply")


def merge_duplicates(rows: list[dict[str, Any]], window_m: float = 10.0) -> list[dict[str, Any]]:
    """Collapse the same event told on several pages (daily report, summary, appendix).

    Two rows are the same event when wellbore and type match and their depths lie within
    `window_m` of each other (a stated depth or a single context depth; a context interval
    is too wide to tell occurrences apart); rows without such a depth only merge on an
    identical quote. A row with a stated depth is preferred over one without. The
    row kept is the most confident one, then the one with a mud weight, then the longest
    quote. Its `description` is unchanged; nothing is blended across pages.
    """
    def rank(row):
        return (row["top_md"] is not None, row["confidence"] or 0.0,
                row["mud_weight_sg"] is not None, len(row["text_span"] or ""))

    kept: list[dict[str, Any]] = []
    def point(row):  # a depth precise enough to tell two occurrences apart
        if row["top_md"] is not None:
            return row["top_md"]
        if row.get("depth_basis") == "context_point":
            return row["context_top_md"]
        return None

    rows = [{**r, "_point": point(r)} for r in rows]
    with_depth = sorted((r for r in rows if r["_point"] is not None),
                        key=lambda r: (r["wellbore_id"], r["event_type"], r["_point"]))
    for row in with_depth:
        last = kept[-1] if kept else None
        if (last and last["wellbore_id"] == row["wellbore_id"]
                and last["event_type"] == row["event_type"]
                and row["_point"] - last["_anchor"] <= window_m):
            if rank(row) > rank(last):
                kept[-1] = {**row, "_anchor": last["_anchor"]}
        else:
            kept.append({**row, "_anchor": row["_point"]})
    by_quote: dict[tuple, dict[str, Any]] = {}
    for row in (r for r in rows if r["_point"] is None):
        key = (row["wellbore_id"], row["event_type"], " ".join((row["text_span"] or "").split()))
        if key not in by_quote or rank(row) > rank(by_quote[key]):
            by_quote[key] = row
    return [{k: v for k, v in r.items() if k not in ("_anchor", "_point")}
            for r in [*kept, *by_quote.values()]]


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint (Azure Foundry included)."""

    RETRYABLE = (429, 500, 502, 503, 504)

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0,
                 retries: int = 5, extra_headers: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retries = retries
        self._sleep = sleep
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "api-key": api_key,
                     **(extra_headers or {})},
        )

    @classmethod
    def from_env(cls) -> OpenAICompatibleClient:
        missing = [v for v in ("WELLGRAPH_LLM_BASE_URL", "WELLGRAPH_LLM_API_KEY",
                               "WELLGRAPH_LLM_MODEL") if not os.environ.get(v)]
        if missing:
            raise RuntimeError(f"Set {', '.join(missing)} (see .env.example).")
        extra = os.environ.get("WELLGRAPH_LLM_EXTRA_HEADERS")
        try:
            headers = json.loads(extra) if extra else None
        except json.JSONDecodeError as exc:
            raise RuntimeError("WELLGRAPH_LLM_EXTRA_HEADERS must be a JSON object.") from exc
        return cls(os.environ["WELLGRAPH_LLM_BASE_URL"], os.environ["WELLGRAPH_LLM_API_KEY"],
                   os.environ["WELLGRAPH_LLM_MODEL"], extra_headers=headers)

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any],
                 validate=PageExtraction.model_validate_json) -> str:
        """Call the model, unwrap markdown fences, and re-ask once if the reply is invalid.

        `validate` checks a reply against the caller's own model (default: the per-page
        extraction).

        Some models behind OpenAI-compatible endpoints (Claude via Copilot) ignore strict
        json_schema: they fence the JSON or omit required fields.
        """
        reply = _strip_fences(self._post(messages, schema))
        try:
            validate(reply)
        except ValidationError as exc:
            repair = [*messages, {"role": "assistant", "content": reply},
                      {"role": "user", "content": (
                          f"Your reply failed validation:\n{exc}\nReturn only the corrected "
                          "JSON object, with every required field present and no markdown.")}]
            reply = _strip_fences(self._post(repair, schema))
        return reply

    def _post(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "page_extraction", "schema": schema, "strict": True},
            },
        }
        last: str = ""
        for attempt in range(self.retries):
            wait = float(min(2 ** attempt, 30))
            try:
                response = self._http.post(f"{self.base_url}/chat/completions", json=body)
            except httpx.TransportError as exc:  # dropped connection, timeout
                last = repr(exc)
            else:
                if response.status_code not in self.RETRYABLE:
                    response.raise_for_status()  # other 4xx: a retry will not help
                    content = response.json()["choices"][0]["message"]["content"]
                    if not content:
                        raise RuntimeError(f"Model {self.model} returned an empty reply.")
                    return content
                last = f"HTTP {response.status_code}"
                try:  # honour the server's own back-off when it gives one
                    wait = min(float(response.headers.get("Retry-After", wait)), 60.0)
                except ValueError:
                    pass
            if attempt < self.retries - 1:
                self._sleep(wait)
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
        columns = TABLES.get(table) or EXTRA_TABLES[table]
        cols = ", ".join(f"{c} {t}" for c, t in columns.items())
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
    workers: int = 1,
) -> dict[str, Any]:
    kg_dir = Path(kg_dir)
    con = duckdb.connect()
    for table in ("wellbore", "document", "page", "hole_section", "formation_top", "mud_check"):
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
        "context_depths_discarded": 0, "mud_weights_from_sodir": 0,
    }
    rows: dict[str, dict[str, Any]] = {}
    fixed_tokens = (len(SYSTEM_PROMPT) + len(json.dumps(schema))) // 4

    # 1. Route: which pages go to the model.
    jobs: list[tuple[dict[str, Any], int, str]] = []  # (document, page_no, text)
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
        for _, page in sent:
            jobs.append((doc, page["page_no"], (page["text"] or "")[:MAX_PAGE_CHARS]))
    stats["pages_sent"] = len(jobs)
    stats["estimated_input_tokens"] = sum(fixed_tokens + len(t) // 4 for _, _, t in jobs)

    if not dry_run:
        # 2. Call the model for pages not answered before. A page whose call fails after
        #    retries is counted and skipped; the next run picks it up from where this left.
        todo = [job for job in jobs if cache.get(client.model, job[2]) is None]
        stats["cache_hits"] = len(jobs) - len(todo)

        def call(job):
            doc, page_no, text = job
            return client.complete(build_messages(text, doc["wellbore"], page_no), schema)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [(job, pool.submit(call, job)) for job in todo]
            for job, future in futures:
                try:
                    reply = future.result()
                except (RuntimeError, httpx.HTTPError, KeyError, ValueError) as exc:
                    # one bad page must not lose the others
                    stats["pages_failed"] += 1
                    log.warning("%s page %s: model call failed (%s)", job[0]["wellbore"],
                                job[1], exc)
                    continue
                stats["model_calls"] += 1
                cache.put(client.model, job[2], reply)

        # 3. Validate, depth-join and collect rows.
        joins: dict[int, tuple[list, list, list]] = {}
        for doc, page_no, text in jobs:
            reply = cache.get(client.model, text)
            if reply is None:
                continue
            try:
                extraction = PageExtraction.model_validate_json(_strip_fences(reply))
            except ValidationError as exc:
                stats["pages_failed"] += 1
                log.warning("%s page %s: invalid model output (%s)", doc["wellbore"],
                            page_no, str(exc).splitlines()[0])
                continue
            wid = doc["wellbore_id"]
            if wid not in joins:
                joins[wid] = (
                    _dicts(con.execute("select * from hole_section where wellbore_id = ?", [wid])),
                    _dicts(con.execute("select * from formation_top where wellbore_id = ?", [wid])),
                    _dicts(con.execute(
                        "select md, mud_weight_sg from mud_check where wellbore_id = ?", [wid])),
                )
            sections, tops, mud_checks = joins[wid]
            for event in extraction.events:
                if _TIME_BREAKDOWN_ROW.match(event.text_span):
                    stats["events_dropped"] += 1
                    stats["drop_reasons"]["time_breakdown_row"] += 1
                    continue
                problems = validate_against_page(event, text)
                fatal = [p for p in problems if p.startswith(FATAL_PROBLEMS)]
                if fatal:
                    stats["events_dropped"] += 1
                    stats["drop_reasons"][fatal[0].split(":")[0]] += 1
                    continue
                if any(p.startswith("context_") for p in problems):
                    # a context depth that is not on the page is not evidence: forget it
                    event = event.model_copy(
                        update={"context_top_md": None, "context_base_md": None})
                    stats["context_depths_discarded"] += 1
                stats["warnings"] += len(problems)
                top, base = event.join_interval
                hole, formations = joins_for_event(
                    sections, tops, top, base, event.resolved_depth_ref
                )
                if hole is None and event.resolved_depth_ref in (None, "MD", "ASSUMED_MD"):
                    hole = event.stated_hole_diameter_in  # the page's own section heading
                row = to_event_row(event, wid, doc["document_id"], page_no, hole, formations)
                if row["mud_weight_sg"] is None and event.resolved_depth_ref in MD_DEPTH_REFS:
                    # the report gives none: use SODIR's nearest measurement, and say so
                    found = mud_weight_at(mud_checks, base if base is not None else top)
                    if found:
                        row.update(mud_weight_sg=found[0], mud_weight_kind="mud_in_hole",
                                   mud_weight_source="sodir_mud_table")
                        stats["mud_weights_from_sodir"] += 1
                rows[row["event_id"]] = row

    stats["drop_reasons"] = dict(stats["drop_reasons"])
    if dry_run:
        con.close()
        return stats

    extracted = len(rows)
    rows = {r["event_id"]: r for r in merge_duplicates(list(rows.values()))}
    stats["events_merged"] = extracted - len(rows)

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
    parser.add_argument("--workers", type=int, default=4, help="Parallel model calls.")
    parser.add_argument("--offline", metavar="MODEL",
                        help="Rebuild events from cached replies of MODEL; no model calls.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Route pages and estimate cost; no model calls, no output.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.dry_run:
        client = None
    elif args.offline:
        client = OfflineClient(args.offline)
    else:
        client = OpenAICompatibleClient.from_env()
    stats = extract_events(args.kg, client, args.cache, args.max_pages, args.wellbore,
                           args.dry_run, args.workers)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
