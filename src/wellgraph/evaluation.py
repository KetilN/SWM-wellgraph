"""Evaluate the knowledge graph against plain search over the report text.

    PYTHONPATH=src python -m wellgraph.evaluation --kg data/kg [--questions eval/questions.yaml]

For each question in eval/questions.yaml this:

1. runs the question's tool call against the graph and compares the answer with the
   reference answer, when there is one;
2. runs a BM25 keyword search over the report pages (the retrieval step of a plain
   search-and-read pipeline) and reports how many of the wellbores the answer involves
   have any page in the top k.

What this does not do yet: generate and grade free-text answers with a language model on
either side. Step 2 measures a ceiling instead: a reader can only use what retrieval
hands it, so a question whose answer spans more wellbores than appear in the top k cannot
be answered completely by search, whatever model reads the pages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from wellgraph.graph.store import GraphStore
from wellgraph.resolve.depth import formations_at, hole_section_at

_TOKEN = re.compile(r"[a-z0-9]+(?:[/.-][a-z0-9]+)*")


def load_questions(path: str | Path) -> list[dict[str, Any]]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]


# --- graph side ----------------------------------------------------------------------

def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


def answer_from_graph(store: GraphStore, question: dict[str, Any]) -> dict[str, Any]:
    kg = question["kg"]
    tool, args, kind = kg["tool"], dict(kg.get("args") or {}), kg["answer"]
    call = {
        "find_wells": store.find_wells,
        "get_well_summary": lambda **a: store.well_summary(a["wellbore"]),
        "query_events": store.query_events,
        "offset_well_analysis": store.offset_analysis,
    }[tool]
    result = call(**args)

    if kind == "result_names":
        return {"names": sorted(r["name"] for r in result["results"])}
    if kind == "total":
        return {"total": result["total"]}
    if kind == "wellbore_fields":
        return {f: _plain(result["wellbore"][f]) for f in kg["fields"]}
    if kind == "formation_and_section_at_depth":
        depth = float(kg["depth_md"])
        return {
            "formations": formations_at(result["formation_tops"], depth),
            "hole_diameter_in": hole_section_at(result["hole_sections"], depth),
        }
    if kind == "event_rows":
        return {"events": [
            {"wellbore": e["wellbore"], "event_type": e["event_type"], "top_md": e["top_md"],
             "mud_weight_sg": e["mud_weight_sg"]}
            for e in result["results"]
        ]}
    if kind == "summary_rows":
        return {"summary": [
            {"event_type": r["event_type"], "hole_diameter_in": r["hole_diameter_in"],
             "events": r["events"]}
            for r in result["summary"]
        ]}
    raise ValueError(f"Unknown answer kind {kind!r} in question {question.get('id')}.")


def compare(answer: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    if expect is None:
        return {"match": None, "reason": "no reference answer yet"}
    if "names" in expect:
        got, want = set(answer.get("names", [])), set(expect["names"])
        return {"match": got == want, "missing": sorted(want - got),
                "unexpected": sorted(got - want)}
    differing = {k: {"got": answer.get(k), "want": v} for k, v in expect.items()
                 if answer.get(k) != v}
    return {"match": not differing, "differing": differing}


def _wellbores_in(answer: dict[str, Any], question: dict[str, Any]) -> set[str]:
    names = set(answer.get("names", []))
    names |= {e["wellbore"] for e in answer.get("events", [])}
    for key in ("wellbore", "near_wellbore"):
        if (question["kg"].get("args") or {}).get(key):
            names.add(question["kg"]["args"][key])
    return names


# --- search side ---------------------------------------------------------------------

class BM25Index:
    """Okapi BM25 over (id, text) pairs. Small and dependency-free."""

    def __init__(self, documents: list[tuple[Any, str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = [doc_id for doc_id, _ in documents]
        self.counts = [Counter(_TOKEN.findall(text.lower())) for _, text in documents]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.document_frequency: Counter[str] = Counter()
        for counts in self.counts:
            self.document_frequency.update(counts.keys())

    def search(self, query: str, k: int = 8) -> list[tuple[Any, float]]:
        terms = set(_TOKEN.findall(query.lower()))
        n = len(self.ids)
        scores = []
        for i, counts in enumerate(self.counts):
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                df = self.document_frequency[term]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = 1 - self.b + self.b * self.lengths[i] / (self.average or 1.0)
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * norm)
            if score > 0:
                scores.append((self.ids[i], score))
        return sorted(scores, key=lambda item: -item[1])[:k]


def _page_index(store: GraphStore) -> tuple[BM25Index, dict[Any, str]]:
    rows = store._rows(
        "select p.document_id, p.page_no, p.text, w.name as wellbore "
        "from page p join document d using (document_id) join wellbore w using (wellbore_id)"
    )
    owner = {(r["document_id"], r["page_no"]): r["wellbore"] for r in rows}
    index = BM25Index([((r["document_id"], r["page_no"]), r["text"] or "") for r in rows])
    return index, owner


# --- run -----------------------------------------------------------------------------

def run(kg_dir: str | Path, questions_path: str | Path, k: int = 8) -> dict[str, Any]:
    store = GraphStore(kg_dir)
    index, owner = _page_index(store)
    wells_with_reports = set(owner.values())
    events_loaded = store.loaded["event"]
    out = []
    for question in load_questions(questions_path):
        answer = answer_from_graph(store, question)
        graph = compare(answer, question.get("expect"))
        if question["needs"] == "events" and not events_loaded:
            graph["reason"] = "events are not loaded in this graph"
        needed = _wellbores_in(answer, question)
        hits = index.search(question["question"], k=k)
        found = {owner[page_id] for page_id, _ in hits}
        out.append({
            "id": question["id"],
            "category": question["category"],
            "needs": question["needs"],
            "question": question["question"],
            "graph": {"answer": answer, **graph},
            "search": {
                "k": k,
                "pages_returned": len(hits),
                # a count question involves every wellbore counted, not just the named one
                "wellbores_needed": max(len(needed), int(answer.get("total", 0))),
                "wellbores_with_any_report_loaded": len(needed & wells_with_reports),
                "wellbores_with_a_page_in_top_k": len(needed & found),
                "why_search_struggles": question["search"],
            },
        })
    checked = [q for q in out if q["graph"]["match"] is not None]
    return {
        "graph": {"counts": store.describe()["counts"]},
        "summary": {
            "questions": len(out),
            "graph_checked": len(checked),
            "graph_matched": sum(1 for q in checked if q["graph"]["match"]),
            "awaiting_reference": len(out) - len(checked),
            "search_full_coverage": sum(
                1 for q in out
                if q["search"]["wellbores_needed"]
                and q["search"]["wellbores_with_a_page_in_top_k"] == q["search"]["wellbores_needed"]
            ),
        },
        "questions": out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.evaluation", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kg", default="data/kg")
    parser.add_argument("--questions", default="eval/questions.yaml")
    parser.add_argument("-k", type=int, default=8, help="Pages retrieved per question.")
    parser.add_argument("--out", help="Write the full report as JSON to this file.")
    args = parser.parse_args(argv)
    report = run(args.kg, args.questions, args.k)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"{'id':30s} {'needs':9s} {'graph':10s} search: wells in top-{args.k} / needed")
    for q in report["questions"]:
        match = {True: "match", False: "MISMATCH", None: "no ref"}[q["graph"]["match"]]
        s = q["search"]
        print(f"{q['id']:30s} {q['needs']:9s} {match:10s} "
              f"{s['wellbores_with_a_page_in_top_k']} / {s['wellbores_needed']}")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
