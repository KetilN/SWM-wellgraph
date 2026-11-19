from pathlib import Path

import pytest
import yaml

from wellgraph.evaluation import (
    BM25Index,
    answer_from_graph,
    compare,
    load_questions,
    run,
)
from wellgraph.graph.store import GraphStore

QUESTIONS_FILE = Path(__file__).parent.parent / "eval" / "questions.yaml"


def test_question_file_has_ten_well_formed_questions():
    questions = load_questions(QUESTIONS_FILE)
    assert len(questions) == 10
    assert len({q["id"] for q in questions}) == 10
    for q in questions:
        assert q["needs"] in {"backbone", "events"}, q["id"]
        assert q["question"].endswith("?") and q["search"], q["id"]
        assert q["kg"]["tool"] in {
            "find_wells", "get_well_summary", "query_events", "offset_well_analysis",
        }, q["id"]
        # event answers must be labelled by a person, never copied from the graph
        assert (q["expect"] is None) == (q["needs"] == "events"), q["id"]


def test_backbone_and_event_questions_are_both_represented():
    needs = [q["needs"] for q in load_questions(QUESTIONS_FILE)]
    assert needs.count("backbone") == 5 and needs.count("events") == 5


# --- answering from the graph (synthetic fixture graph from conftest) ------------------

def question(tool, args, answer, **extra):
    return {"id": "t", "needs": "backbone", "kg": {"tool": tool, "args": args,
                                                   "answer": answer, **extra}}


def test_answer_extractors(kg_dir):
    store = GraphStore(kg_dir)
    names = answer_from_graph(store, question("find_wells", {"block": "99/1"}, "result_names"))
    assert names == {"names": ["99/1-1", "99/1-2 A", "99/1-3"]}
    total = answer_from_graph(store, question("find_wells", {"block": "99", "limit": 1}, "total"))
    assert total == {"total": 4}
    fields = answer_from_graph(store, question(
        "get_well_summary", {"wellbore": "99/1-1"}, "wellbore_fields",
        fields=["operator", "entry_date"]))
    assert fields == {"operator": "Alpha Oil", "entry_date": "1990-01-01"}
    at = answer_from_graph(store, question(
        "get_well_summary", {"wellbore": "99/1-1"}, "formation_and_section_at_depth",
        depth_md=2500.0))
    assert at == {"formations": ["SHETLAND GP", "TOR FM"], "hole_diameter_in": 12.25}
    events = answer_from_graph(store, question(
        "query_events", {"event_type": "kick"}, "event_rows"))
    assert events["events"] == [
        {"wellbore": "99/1-3", "event_type": "kick", "top_md": 3300.0, "mud_weight_sg": 1.6}
    ]
    summary = answer_from_graph(store, question(
        "offset_well_analysis", {"near_wellbore": "99/1-1", "radius_km": 35}, "summary_rows"))
    assert {"event_type": "kick", "hole_diameter_in": 8.5, "events": 1} in summary["summary"]


def test_compare_is_exact_and_order_insensitive_for_name_lists():
    assert compare({"names": ["b", "a"]}, {"names": ["a", "b"]})["match"] is True
    out = compare({"names": ["a"]}, {"names": ["a", "b"]})
    assert out["match"] is False and out["missing"] == ["b"] and out["unexpected"] == []
    assert compare({"total": 78}, {"total": 78})["match"] is True
    assert compare({"total": 77}, {"total": 78})["match"] is False
    assert compare({"x": 1}, None) == {"match": None, "reason": "no reference answer yet"}


# --- search baseline -----------------------------------------------------------------

def test_bm25_ranks_the_relevant_page_first():
    index = BM25Index([
        ("a", "cover page table of contents"),
        ("b", "lost circulation at 2500 m, pumped LCM pill, lost returns"),
        ("c", "ran casing and cemented, no losses"),
    ])
    assert index.search("lost circulation losses", k=2)[0][0] == "b"
    assert index.search("zebra", k=3) == []


def test_run_reports_graph_results_and_search_coverage(kg_dir, tmp_path):
    questions = tmp_path / "q.yaml"
    questions.write_text(yaml.safe_dump({"questions": [
        {"id": "ok", "category": "spatial", "needs": "backbone", "question": "Wells in 99/1?",
         "search": "x", "kg": {"tool": "find_wells", "args": {"block": "99/1"},
                               "answer": "result_names"},
         "expect": {"names": ["99/1-1", "99/1-2 A", "99/1-3"]}},
        {"id": "pending", "category": "events", "needs": "events",
         "question": "Losses at 2500 m in 99/1-1?", "search": "x",
         "kg": {"tool": "query_events", "args": {"wellbore": "99/1-1"}, "answer": "event_rows"},
         "expect": None},
    ]}))
    report = run(kg_dir, questions, k=5)
    by_id = {r["id"]: r for r in report["questions"]}
    assert by_id["ok"]["graph"]["match"] is True
    assert by_id["pending"]["graph"]["match"] is None
    # the graph answer for 'ok' names three wellbores; only reports for two exist as pages
    assert by_id["ok"]["search"]["wellbores_needed"] == 3
    assert by_id["ok"]["search"]["wellbores_with_a_page_in_top_k"] <= 2
    assert report["summary"]["graph_checked"] == 1 and report["summary"]["graph_matched"] == 1
    assert report["summary"]["awaiting_reference"] == 1


def test_unknown_answer_kind_is_rejected(kg_dir):
    with pytest.raises(ValueError, match="answer kind"):
        answer_from_graph(GraphStore(kg_dir), question("find_wells", {}, "nonsense"))
