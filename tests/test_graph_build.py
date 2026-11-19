import json
from pathlib import Path

import duckdb

from wellgraph.graph.build import build_documents
from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore

SAMPLES = Path(__file__).parent.parent / "samples" / "reports"


def write_wellbores(kg_dir, rows):
    """rows: (wellbore_id, name). Other wellbore columns are left NULL."""
    con = duckdb.connect()
    cols = ", ".join(f"{c} {t}" for c, t in TABLES["wellbore"].items())
    con.execute(f"create table wellbore ({cols})")
    con.executemany("insert into wellbore (wellbore_id, name) values (?, ?)", rows)
    con.execute(f"copy wellbore to '{kg_dir}/wellbore.parquet' (format parquet)")
    con.close()


def fake_report(root, folder, wellbore_name, file_id, n_pages=2):
    path = root / folder / "WELL"
    path.mkdir(parents=True)
    doc = {
        f"{folder}/WELL/{file_id}_REPORT.PDF": {
            "original_document_metadata": {
                "wellboreName": wellbore_name, "wellName": wellbore_name,
                "wellborefileId": file_id, "title": "REPORT.PDF",
            },
            "pages": [
                {"page_no": i, "page_metadata": {"quality": "Reliable"},
                 "OCR": {"ocr_markdown": f"page {i} text"},
                 "AI": {"page_summary": f"summary {i}", "tags": []}}
                for i in range(n_pages)
            ],
        }
    }
    (path / f"{folder}_REPORT.JSON").write_text(json.dumps(doc))


def test_documents_and_pages_are_resolved_and_loadable(tmp_path):
    kg = tmp_path / "kg"
    kg.mkdir()
    write_wellbores(kg, [(101, "25/6-1"), (102, "25/6-3"), (103, "25/7-2"), (104, "25/7-3")])

    result = build_documents(SAMPLES, kg, staging_dir=tmp_path / "staging")

    assert result["documents"] == 4 and result["pages"] == 732
    assert result["unresolved"] == []
    store = GraphStore(kg)  # validates the columns against the contract
    counts = store.describe()["counts"]
    assert counts["document"] == 4 and counts["page"] == 732
    docs = store.well_summary("25/6-1")["documents"]
    assert len(docs) == 1 and docs[0]["n_pages"] == 143


def test_sidetrack_reports_attach_to_parent_and_unknown_wells_are_reported(tmp_path):
    kg = tmp_path / "kg"
    kg.mkdir()
    write_wellbores(kg, [(7, "99/1-2 A")])
    reports = tmp_path / "reports"
    fake_report(reports, "99_1-2_AT2", "99/1-2 AT2", 555)
    fake_report(reports, "99_9-9", "99/9-9", 556)

    result = build_documents(reports, kg, staging_dir=tmp_path / "staging")

    assert result["documents"] == 1 and result["pages"] == 2
    assert result["unresolved"] == [{"wellbore_name": "99/9-9", "documents": 1}]
    con = duckdb.connect()
    row = con.execute(
        f"select wellbore_id, document_id from read_parquet('{kg}/document.parquet')"
    ).fetchone()
    assert row == (7, "555")
