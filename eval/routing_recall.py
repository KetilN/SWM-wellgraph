"""Measure what the page router skips (its recall) on the sample reports.

    PYTHONPATH=src uv run --no-sync python eval/routing_recall.py sample
    set -a && . ./.env && set +a && PYTHONPATH=src uv run --no-sync python eval/routing_recall.py run
    PYTHONPATH=src uv run --no-sync python eval/routing_recall.py candidates --out <dir>
    PYTHONPATH=src uv run --no-sync python eval/routing_recall.py rescore

sample      route every page like the runner, draw 150 not-sent pages (seed 42, stratified by
            document), write data/cache/recall/sample.json
run         one extraction call per sampled page (cached in data/cache/recall), same prompt,
            schema, client and filters as wellgraph.extract.run; writes results.json
candidates  dump every sampled page that kept at least one valid event, for a human to read
rescore     re-score all pages under candidate router rule changes and count extra pages

Reads data/kg only; writes only under data/cache/recall (and --out). The verdicts in
docs/routing-recall.md come from reading the `candidates` output; they are not in this file.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from wellgraph.extract import routing, run
from wellgraph.extract.models import PageExtraction, validate_against_page
from wellgraph.extract.prompt import build_messages, json_schema

KG = Path("data/kg")
CACHE = Path("data/cache/recall")
SEED = 42
SAMPLE_SIZE = 150
MIN_PER_DOC = 5
MAX_HTTP_CALLS = 170  # hard budget over all runs, kept in CACHE/calls.json
WORKERS = 4


def load_pages() -> list[dict]:
    con = duckdb.connect()
    cur = con.execute(
        f"select p.document_id, p.page_no, p.text, p.summary, w.name as wellbore "
        f"from read_parquet('{KG}/page.parquet') p "
        f"join read_parquet('{KG}/document.parquet') d using (document_id) "
        f"join read_parquet('{KG}/wellbore.parquet') w using (wellbore_id) "
        f"order by w.name, p.document_id, p.page_no"
    )
    names = [d[0] for d in cur.description]
    pages = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    for p in pages:
        r = routing.score_page(p["text"] or "", [], p["summary"] or "")  # as the runner does
        p["score"], p["send"], p["reasons"] = r.score, r.send, r.reasons
    return pages


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def draw_sample(pages: list[dict]) -> list[dict]:
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        if not p["send"]:
            by_doc[p["document_id"]].append(p)
    total = sum(len(v) for v in by_doc.values())
    docs = sorted(by_doc)
    quota = {d: SAMPLE_SIZE * len(by_doc[d]) / total for d in docs}
    alloc = {d: max(MIN_PER_DOC, math.floor(quota[d])) for d in docs}
    # largest remainder up, then largest allocation down, until the total is exact
    for d in sorted(docs, key=lambda d: quota[d] - math.floor(quota[d]), reverse=True):
        if sum(alloc.values()) >= SAMPLE_SIZE:
            break
        alloc[d] += 1
    while sum(alloc.values()) > SAMPLE_SIZE:
        alloc[max(docs, key=lambda d: alloc[d] - quota[d])] -= 1
    rng = random.Random(SEED)
    sample = []
    for d in docs:
        chosen = rng.sample(sorted(by_doc[d], key=lambda p: p["page_no"]), alloc[d])
        sample.extend(sorted(chosen, key=lambda p: p["page_no"]))
    return sample


def cmd_sample(_args) -> None:
    pages = load_pages()
    sent = Counter(p["wellbore"] for p in pages if p["send"])
    total = Counter(p["wellbore"] for p in pages)
    sample = draw_sample(pages)
    drawn = Counter(p["wellbore"] for p in sample)
    print(f"pages {len(pages)}  sent {sum(sent.values())}  not sent {len(pages) - sum(sent.values())}")
    for w in sorted(total):
        print(f"  {w:12s} total {total[w]:4d}  sent {sent[w]:3d}  not sent {total[w] - sent[w]:4d}"
              f"  sampled {drawn[w]:3d}")
    scores = Counter("0" if p["score"] == 0 else "0<s<1.5" if p["score"] < 1.5 else "1.5<=s<3"
                     for p in sample)
    print("sample score bands:", dict(scores))
    allscores = Counter("0" if p["score"] == 0 else "0<s<1.5" if p["score"] < 1.5 else "1.5<=s<3"
                        for p in pages if not p["send"])
    print("all not-sent score bands:", dict(allscores))
    CACHE.mkdir(parents=True, exist_ok=True)
    keep = ("document_id", "page_no", "wellbore", "score", "reasons")
    (CACHE / "sample.json").write_text(
        json.dumps([{k: p[k] for k in keep} for p in sample], indent=1), encoding="utf-8")


class Budget:
    """Counts every HTTP request to the model across runs and refuses to pass the cap."""

    def __init__(self, path: Path, limit: int):
        self.path, self.limit, self.lock = path, limit, threading.Lock()
        self.state = {"http_requests": 0, "http_errors": 0}
        if path.is_file():
            self.state.update(json.loads(path.read_text()))

    def wrap(self, post):
        def counted(*args, **kwargs):
            with self.lock:
                if self.state["http_requests"] >= self.limit:
                    raise RuntimeError(f"call budget of {self.limit} requests is used up")
                self.state["http_requests"] += 1
                self.path.write_text(json.dumps(self.state))
            try:
                response = post(*args, **kwargs)
            except Exception:
                self._error()
                raise
            if response.status_code >= 400:
                self._error()
            return response
        return counted

    def _error(self):
        with self.lock:
            self.state["http_errors"] += 1
            self.path.write_text(json.dumps(self.state))


def evaluate(sample_pages: list[dict], cache: run.ResponseCache, model: str) -> list[dict]:
    """Apply the runner's filters to the cached replies. One record per sampled page."""
    out = []
    for p in sample_pages:
        text = (p["text"] or "")[: run.MAX_PAGE_CHARS]
        rec = {k: p[k] for k in ("document_id", "page_no", "wellbore", "score", "reasons")}
        rec.update(status="no_reply", events=[], dropped=[])
        reply = cache.get(model, text)
        if reply is not None:
            try:
                extraction = PageExtraction.model_validate_json(run._strip_fences(reply))
            except Exception as exc:  # pydantic ValidationError
                rec["status"] = "invalid: " + str(exc).splitlines()[0]
            else:
                rec["status"] = "ok"
                for event in extraction.events:
                    if run._TIME_BREAKDOWN_ROW.match(event.text_span):
                        rec["dropped"].append("time_breakdown_row")
                        continue
                    problems = validate_against_page(event, text)
                    if any(x.startswith(run.FATAL_PROBLEMS) for x in problems):
                        rec["dropped"].append("text_span_not_found")
                        continue
                    rec["events"].append({**event.model_dump(), "problems": problems})
        out.append(rec)
    return out


def cmd_run(_args) -> None:
    logging.basicConfig(level=logging.WARNING)  # httpx logs request lines at INFO
    pages = {(p["document_id"], p["page_no"]): p for p in load_pages()}
    sample = [pages[(s["document_id"], s["page_no"])]
              for s in json.loads((CACHE / "sample.json").read_text())]
    client = run.OpenAICompatibleClient.from_env()
    budget = Budget(CACHE / "calls.json", MAX_HTTP_CALLS)
    client._http.post = budget.wrap(client._http.post)
    client.retries = 2  # the budget is small: one retry per call, not five
    cache = run.ResponseCache(CACHE)
    schema = json_schema(strict=True)

    def text_of(p):
        return (p["text"] or "")[: run.MAX_PAGE_CHARS]

    todo = [p for p in sample if cache.get(client.model, text_of(p)) is None]
    print(f"sample {len(sample)}  cached {len(sample) - len(todo)}  to call {len(todo)}")

    def call(p):
        return client.complete(build_messages(text_of(p), p["wellbore"], p["page_no"]), schema)

    failures = []

    def collect(p, getter):
        try:
            reply = getter()
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the others
            failures.append((p["wellbore"], p["page_no"], type(exc).__name__, str(exc)[:200]))
            return False
        cache.put(client.model, text_of(p), reply)
        return True

    probe, rest = todo[:5], todo[5:]
    ok = [collect(p, lambda p=p: call(p)) for p in probe]
    if probe and not any(ok):
        print("first calls all failed; stopping:", failures)
        return
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [(p, pool.submit(call, p)) for p in rest]
        for p, future in futures:
            collect(p, future.result)

    results = evaluate(sample, cache, client.model)
    (CACHE / "results.json").write_text(json.dumps(
        {"model": client.model, "pages": results}, indent=1), encoding="utf-8")
    status = Counter(r["status"].split(":")[0] for r in results)
    print("http:", budget.state, " failures this run:", len(failures))
    for f in failures:
        print("  failed", f)
    print("page status:", dict(status))
    print("pages with >=1 valid event:", sum(1 for r in results if r["events"]),
          " events:", sum(len(r["events"]) for r in results),
          " dropped:", dict(Counter(d for r in results for d in r["dropped"])))


def cmd_candidates(args) -> None:
    pages = {(p["document_id"], p["page_no"]): p for p in load_pages()}
    results = json.loads((CACHE / "results.json").read_text())["pages"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for r in results:
        if not r["events"]:
            continue
        p = pages[(r["document_id"], r["page_no"])]
        name = f"{r['wellbore'].replace('/', '_').replace(' ', '_')}_p{r['page_no']}.txt"
        lines = [f"{r['wellbore']} page {r['page_no']} score {r['score']} reasons {r['reasons']}",
                 f"summary: {p['summary']}", ""]
        for i, e in enumerate(r["events"]):
            lines.append(f"EVENT {i}: {e['event_type']} top={e['top_md']} ctx={e['context_top_md']}"
                         f" conf={e['confidence']}\n  desc: {e['description']}\n  quote: {e['text_span']}")
        lines += ["", "=" * 30 + " PAGE TEXT", p["text"] or ""]
        (out / name).write_text("\n".join(lines), encoding="utf-8")
        print(name, len(r["events"]))


# ---------------------------------------------------------------- rule changes
# Each variant patches the routing module in memory, re-scores all pages, and is undone.

def _with_evidence(extra: list[tuple[str, str, str, float]]):
    def apply():
        old = list(routing._EVIDENCE)
        routing._EVIDENCE.extend((t, label, re.compile(p), w) for t, label, p, w in extra)
        return lambda: routing._EVIDENCE.__setitem__(slice(None), old)
    return apply


def _with_threshold(value: float):
    def apply():
        old = routing.SEND_THRESHOLD
        routing.SEND_THRESHOLD = value
        return lambda: setattr(routing, "SEND_THRESHOLD", old)
    return apply


def _with_weight(label: str, weight: float):
    def apply():
        old = list(routing._EVIDENCE)
        routing._EVIDENCE[:] = [(t, lab, pat, weight if lab == label else w)
                                for t, lab, pat, w in old]
        return lambda: routing._EVIDENCE.__setitem__(slice(None), old)
    return apply


_BALLING = ("other", "balling", r"\bball(?:ed|ing)[- ]?up\b|\bbit[- ]balling\b|\bballing\b", 3.0)
_NO_PASS = ("tight_hole", "could not pass",
            r"\b(?:not able|unable|not possible|failed|impossible) to (?:pass|get (?:past|below|down))\b"
            r"|\b(?:could|would|did) not (?:pass|go (?:past|below|deeper|down))\b"
            , 3.0)
_LOST_TO_WELL = ("lost_circulation", "lost to well",
                 r"\b(?:mud|bbls?|m3) (?:was |were )?lost to (?:the )?(?:well|hole|formation)\b", 3.0)
_GUMBO = ("other", "gumbo", r"\bgumbo\b", 3.0)
VARIANTS: dict[str, list] = {
    "A  add 'balled up / balling' (weight 3)": [_with_evidence([_BALLING])],
    "B  threshold 3.0 -> 2.5": [_with_threshold(2.5)],
    "C  threshold 3.0 -> 2.0": [_with_threshold(2.0)],
    "D  add 'could not pass / not able to pass / impossible to pass' (weight 3)":
        [_with_evidence([_NO_PASS])],
    "E  add '<mud|bbl|m3> lost to the well/hole/formation' (weight 3)":
        [_with_evidence([_LOST_TO_WELL])],
    "F  'fill' weight 1.5 -> 2.5": [_with_weight("fill", 2.5)],
    "G  A plus 'gumbo' (weight 3)": [_with_evidence([_BALLING, _GUMBO])],
    "A+D+E": [_with_evidence([_BALLING, _NO_PASS, _LOST_TO_WELL])],
    "A+B": [_with_evidence([_BALLING]), _with_threshold(2.5)],
}


def rescore(pages: list[dict], patches: list) -> set[tuple[str, int]]:
    undo = [patch() for patch in patches]
    try:
        return {(p["document_id"], p["page_no"]) for p in pages
                if routing.score_page(p["text"] or "", [], p["summary"] or "").send}
    finally:
        for u in reversed(undo):
            u()


def cmd_rescore(args) -> None:
    pages = load_pages()
    base = {(p["document_id"], p["page_no"]) for p in pages if p["send"]}
    name_of = {(p["document_id"], p["page_no"]): f"{p['wellbore']} p{p['page_no']}" for p in pages}
    misses = set()
    if args.misses:
        wanted = set(args.misses)
        misses = {k for k, v in name_of.items() if v in wanted}
    sample = {(s["document_id"], s["page_no"])
              for s in json.loads((CACHE / "sample.json").read_text())}
    print(f"baseline sent {len(base)} of {len(pages)}")
    for name, patches in VARIANTS.items():
        sent = rescore(pages, patches)
        extra, lost = sent - base, base - sent
        caught = sorted(name_of[k] for k in extra & misses)
        print(f"{name}: +{len(extra)} pages, -{len(lost)} pages, in sample {len(extra & sample)}, "
              f"catches {len(caught)}/{len(misses)} misses {caught}")
        if args.list:
            for k in sorted(extra, key=lambda k: name_of[k]):
                print("    +", name_of[k], "(sampled)" if k in sample else "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample").set_defaults(fn=cmd_sample)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    c = sub.add_parser("candidates")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_candidates)
    r = sub.add_parser("rescore")
    r.add_argument("--misses", nargs="*", help='pages judged REAL, as "25/7-2 p167"')
    r.add_argument("--list", action="store_true", help="list the extra pages of each variant")
    r.set_defaults(fn=cmd_rescore)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
