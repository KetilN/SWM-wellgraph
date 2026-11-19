import csv

import duckdb
import pytest

from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore
from wellgraph.onboard.diskos import DISKOS_FIELDS, add_diskos_metadata

HEADER = [
    "Wellbore Name", "Info Item Type", "Description", "Delivery Company", "Create Date",
    "Release Date", "Format", "File Size (MB)", "Data Collection", "Remark", "Native Uid",
    "DISKOS Info Item Id", "Update Date", "FolderFileName",
]
ROWS = [
    ["99/1-1", "COMPLETION_REPORT", "<99/1-1> completion report", "Statoil",
     "2001-07-17T06:24:45Z", "2006-02-03T00:00:00Z", "PDF", "13.24",
     "DISKOS SCANNED COMPLETION REPORTS", "", "doc-1", "340,698,384",
     "2026-08-24T09:09:03Z", "99_1-1_COMPLETION_REPORT.PDF"],
    ["99/1-1", "COMPLETION_REPORT", "<99/1-1> completion report", "Statoil",
     "2026-09-07T10:46:29Z", "2007-05-16T00:00:00Z", "JSON", "0.74", "DISKOS INSIGHT PROJECT",
     "This document is of excellent quality: 86.0% pages are reliable.", "9001", "2.00E+10",
     "2026-09-07T10:46:29Z", "99_1-1_COMPLETION_REPORT.JSON"],
    ["99/1-2 A", "WELLBORE_DOCUMENTS", ".", " ", "not a date", "", "PDF", "", "NPD TABLE A1",
     "", "doc-2", "2.00E+10", "2026-08-24T09:09:03Z", "99_1-2_A_COMPLETION_REPORT_1.PDF"],
]


@pytest.fixture
def diskos_csv(tmp_path):
    path = tmp_path / "diskos.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(HEADER)
        writer.writerows(ROWS)
    return path


def read_documents(kg_dir):
    con = duckdb.connect()
    cur = con.execute(f"select * from read_parquet('{kg_dir}/document.parquet') order by document_id")
    names = [d[0] for d in cur.description]
    return {row[0]: dict(zip(names, row, strict=True)) for row in cur.fetchall()}


def test_schema_lists_every_diskos_field():
    assert set(DISKOS_FIELDS) <= set(TABLES["document"])


def test_fields_come_from_the_pdf_row_and_the_quality_from_its_json_twin(kg_dir, diskos_csv):
    before = read_documents(kg_dir)

    result = add_diskos_metadata(kg_dir, diskos_csv)

    assert result == {"documents": 3, "matched": 2}
    docs = read_documents(kg_dir)
    assert list(next(iter(docs.values()))) == list(TABLES["document"])
    d1 = docs["doc-1"]
    assert d1["report_type"] == "COMPLETION_REPORT" and d1["delivery_company"] == "Statoil"
    assert str(d1["release_date"]) == "2006-02-03" and str(d1["created_date"]) == "2001-07-17"
    assert d1["pdf_size_mb"] == 13.24 and d1["diskos_item_id"] == "340698384"
    assert d1["data_collection"] == "DISKOS SCANNED COMPLETION REPORTS"
    assert d1["quality_remark"].startswith("This document is of excellent quality")
    for column in ("document_id", "wellbore_id", "title", "relative_path", "n_pages"):
        assert {k: v[column] for k, v in docs.items()} == {k: v[column] for k, v in before.items()}


def test_blank_and_broken_values_become_empty_and_unlisted_reports_stay(kg_dir, diskos_csv):
    add_diskos_metadata(kg_dir, diskos_csv)
    docs = read_documents(kg_dir)
    d2 = docs["doc-2"]
    assert d2["report_type"] == "WELLBORE_DOCUMENTS" and d2["data_collection"] == "NPD TABLE A1"
    for column in ("delivery_company", "release_date", "created_date", "pdf_size_mb",
                   "diskos_item_id", "quality_remark"):
        assert d2[column] is None, column
    assert all(docs["doc-3"][c] is None for c in DISKOS_FIELDS)
    GraphStore(kg_dir)  # still matches the contract


def test_missing_files_are_reported(tmp_path, kg_dir):
    with pytest.raises(FileNotFoundError, match="Diskos metadata CSV"):
        add_diskos_metadata(kg_dir, tmp_path / "nope.csv")
    with pytest.raises(FileNotFoundError, match="build the documents first"):
        add_diskos_metadata(tmp_path / "empty", tmp_path / "nope.csv")
