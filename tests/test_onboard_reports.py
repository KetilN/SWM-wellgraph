"""Tests for wellgraph.onboard.reports against the four tracked sample reports."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import pytest

from wellgraph.onboard.reports import (
    DOCUMENT_STAGING_COLUMNS,
    PAGE_COLUMNS,
    Page,
    Report,
    iter_reports,
    load_report,
    main,
    write_staging,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "reports"
R_25_6_1 = SAMPLES / "25_6-1" / "WELL" / "25_6-1_25-06-01_PB-706-0464.JSON"
R_25_7_2 = SAMPLES / "25_7-2" / "WELL" / "25_7-2_25-07-02_PB-706-0941.JSON"

EXPECTED_PAGES = {"25/6-1": 143, "25/6-3": 104, "25/7-2": 265, "25/7-3": 220}


@pytest.fixture(scope="module")
def reports() -> list[Report]:
    return list(iter_reports(SAMPLES))


def minimal_doc(**meta) -> dict:
    return {
        "99_1-1/WELL/123_REPORT.PDF": {
            "original_document_metadata": {"wellboreName": "99/1-1", **meta},
            "pages": [
                {"page_no": 0, "OCR": {"ocr_markdown": "Lost returns at 2500 m."}},
                {"page_no": 1},
                {"page_no": 2, "AI": {"page_summary": "A summary", "tags": None}},
            ],
        }
    }


def test_load_report_25_6_1():
    r = load_report(R_25_6_1)
    assert isinstance(r, Report)
    assert r.document_id == "3324598"
    assert r.wellbore_name == "25/6-1"
    assert r.well_name == "25/6-1"
    assert r.title == "25-06-01_PB-706-0464.PDF"
    assert r.n_pages == 143
    assert len(r.pages) == 143
    assert "excellent quality" in r.quality_note


def test_relative_path_matches_file_name_on_disk():
    # JSON key is "25_6-1/WELL/3324598_25-06-01_PB-706-0464.PDF"; the archive file is
    # "25_6-1/WELL/25_6-1_25-06-01_PB-706-0464.PDF" (id prefix replaced by the folder name).
    r = load_report(R_25_6_1)
    assert r.relative_path == "25_6-1/WELL/25_6-1_25-06-01_PB-706-0464.PDF"
    assert Path(r.relative_path).with_suffix(".JSON").name == R_25_6_1.name


def test_pages_are_typed_and_ordered():
    r = load_report(R_25_7_2)
    assert r.n_pages == 265
    assert [p.page_no for p in r.pages] == list(range(265))
    p = r.pages[10]
    assert isinstance(p, Page)
    assert isinstance(p.text, str) and isinstance(p.summary, str)
    assert isinstance(p.tags, list) and all(isinstance(t, str) for t in p.tags)
    assert isinstance(p.has_tables, bool) and isinstance(p.has_figures, bool)
    assert p.n_chars == len(p.text)
    assert {p.quality for p in r.pages} <= {
        "Reliable", "Acceptable", "Uncertain", "Figure-dominated", "",
    }


def test_iter_reports_finds_the_four_samples(reports):
    assert {r.wellbore_name: r.n_pages for r in reports} == EXPECTED_PAGES
    assert [r.wellbore_name for r in reports] == sorted(EXPECTED_PAGES)
    assert len({r.document_id for r in reports}) == 4


def test_most_pages_have_text_summary_and_tags(reports):
    for r in reports:
        with_text = sum(1 for p in r.pages if p.n_chars > 50)
        assert with_text / r.n_pages > 0.9, r.wellbore_name
        assert sum(1 for p in r.pages if p.summary) / r.n_pages > 0.9
        assert sum(1 for p in r.pages if p.tags) / r.n_pages > 0.9


def test_drilling_problem_text_is_present(reports):
    text = " ".join(p.text.lower() for r in reports for p in r.pages)
    assert "lost circulation" in text
    assert "stuck" in text


def test_iter_reports_is_lazy_case_insensitive_and_skips_bad_files(tmp_path, caplog):
    (tmp_path / "a" / "WELL").mkdir(parents=True)
    (tmp_path / "b" / "WELL").mkdir(parents=True)
    (tmp_path / "a" / "WELL" / "x.json").write_text(json.dumps(minimal_doc()))
    (tmp_path / "b" / "WELL" / "y.JSON").write_text(json.dumps(minimal_doc(wellboreName="99/1-2")))
    (tmp_path / "b" / "WELL" / "broken.Json").write_text("{not json")
    (tmp_path / "b" / "WELL" / "other.json").write_text('{"a": 1}')
    (tmp_path / "b" / "WELL" / "y.PDF").write_bytes(b"%PDF-1.4 not parsed")
    it = iter_reports(tmp_path)
    assert iter(it) is it  # a generator, nothing loaded yet
    with caplog.at_level(logging.WARNING, logger="wellgraph.onboard.reports"):
        found = list(it)
    assert [r.wellbore_name for r in found] == ["99/1-1", "99/1-2"]
    assert "broken.Json" in caplog.text and "other.json" in caplog.text


def test_tolerates_missing_optional_fields(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps(minimal_doc()))
    r = load_report(f)
    assert r.document_id and r.document_id != "None"  # hash fallback, no wellborefileId
    assert r.document_id == load_report(f).document_id
    assert r.wellbore_name == "99/1-1"
    assert r.well_name == "" and r.title == "REPORT.PDF" and r.quality_note == ""
    assert r.relative_path == "99_1-1/WELL/99_1-1_REPORT.PDF"
    assert r.n_pages == 3
    p0, p1, p2 = r.pages
    assert (p0.text, p0.summary, p0.tags, p0.quality) == ("Lost returns at 2500 m.", "", [], "")
    assert (p0.has_tables, p0.has_figures, p0.n_chars) == (False, False, 23)
    assert (p1.text, p1.n_chars, p1.tags) == ("", 0, [])
    assert (p2.summary, p2.tags) == ("A summary", [])


def test_wellbore_name_falls_back_to_well_name_not_folder(tmp_path):
    doc = minimal_doc()
    meta = next(iter(doc.values()))["original_document_metadata"]
    del meta["wellboreName"]
    f = tmp_path / "m.json"
    f.write_text(json.dumps(doc))
    assert load_report(f).wellbore_name == ""  # not guessed from the folder: see module doc
    meta["wellName"] = "99/1-1"
    f.write_text(json.dumps(doc))
    assert load_report(f).wellbore_name == "99/1-1"


@pytest.mark.parametrize(
    "content",
    ['{"a": 1}', "[1, 2]", '{"k": {"pages": "x", "original_document_metadata": {}}}', "{nope"],
)
def test_non_report_json_raises_value_error(tmp_path, content):
    f = tmp_path / "bad.json"
    f.write_text(content)
    with pytest.raises(ValueError, match="bad.json"):
        load_report(f)


def test_write_staging_round_trip(tmp_path, reports):
    counts = write_staging(iter(reports), tmp_path / "out")
    assert counts == {"documents": 4, "pages": 732}
    con = duckdb.connect()
    doc = f"'{tmp_path}/out/document_staging.parquet'"
    page = f"'{tmp_path}/out/page.parquet'"
    assert [c[0] for c in con.execute(f"describe select * from {doc}").fetchall()] == list(
        DOCUMENT_STAGING_COLUMNS
    )
    described = con.execute(f"describe select * from {page}").fetchall()
    assert {c[0]: c[1] for c in described} == PAGE_COLUMNS
    assert list(PAGE_COLUMNS) == [
        "document_id", "page_no", "text", "summary", "tags", "quality",
        "has_tables", "has_figures", "n_chars",
    ]
    assert con.execute(f"select count(*) from {doc}").fetchone()[0] == 4
    assert con.execute(f"select count(*) from {page}").fetchone()[0] == 732
    rows = con.execute(
        f"select d.wellbore_name, d.n_pages, count(*) from {doc} d join {page} p "
        "using (document_id) group by all order by 1"
    ).fetchall()
    assert rows == [(k, v, v) for k, v in sorted(EXPECTED_PAGES.items())]
    r = reports[0]
    text, tags, n_chars = con.execute(
        f"select text, tags, n_chars from {page} where document_id = ? and page_no = 20",
        [r.document_id],
    ).fetchone()
    assert text == r.pages[20].text and list(tags) == r.pages[20].tags and n_chars == len(text)
    assert not list((tmp_path / "out").glob("*.duckdb*"))


def test_write_staging_empty_and_all_empty_tags(tmp_path):
    assert write_staging([], tmp_path / "e") == {"documents": 0, "pages": 0}
    f = tmp_path / "m.json"
    f.write_text(json.dumps(minimal_doc()))
    assert write_staging([load_report(f)], tmp_path / "o") == {"documents": 1, "pages": 3}
    con = duckdb.connect()
    assert con.execute(f"select tags from '{tmp_path}/o/page.parquet'").fetchall() == [([],)] * 3
    assert con.execute(f"select count(*) from '{tmp_path}/e/page.parquet'").fetchone()[0] == 0


def test_cli_prints_counts(tmp_path, capsys):
    assert main([str(SAMPLES), str(tmp_path / "cli")]) == 0
    out = capsys.readouterr().out
    assert "4" in out and "732" in out
    assert (tmp_path / "cli" / "page.parquet").exists()
