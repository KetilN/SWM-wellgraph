"""Diskos metadata CSV -> report fields on the document table.

    PYTHONPATH=src python -m wellgraph.onboard.diskos --kg data/kg [--csv data/raw/diskos_metadata.csv]

The CSV is the small table published with the Zenodo record: one row per file, a PDF row
and a JSON row for each report. A document's id is the `Native Uid` of its PDF row, so
that row gives the report type, delivering company, dates and file size. The quality
statement sits in the `Remark` of the JSON row with the same file name.

Only `document.parquet` in the graph folder is rewritten; nothing else is read or changed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_CSV = Path("data/raw/diskos_metadata.csv")

# document column -> SQL over one PDF row `p` and its JSON twin `j`
DISKOS_FIELDS: dict[str, str] = {
    "report_type": "nullif(trim(p.\"Info Item Type\"), '')",
    "description": "nullif(trim(p.\"Description\"), '')",
    "delivery_company": "nullif(trim(p.\"Delivery Company\"), '')",
    "release_date": "try_cast(left(p.\"Release Date\", 10) as date)",
    "created_date": "try_cast(left(p.\"Create Date\", 10) as date)",
    "data_collection": "nullif(trim(p.\"Data Collection\"), '')",
    "pdf_size_mb": "try_cast(p.\"File Size (MB)\" as double)",
    "diskos_item_id": (
        "case when regexp_full_match(replace(p.\"DISKOS Info Item Id\", ',', ''), '[0-9]+') "
        "then replace(p.\"DISKOS Info Item Id\", ',', '') end"
    ),
    "quality_remark": "j.remark",
}

_STEM = "upper(regexp_replace({col}, '\\.[A-Za-z]+$', ''))"


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def add_diskos_metadata(kg_dir: str | Path, csv_path: str | Path = DEFAULT_CSV) -> dict[str, Any]:
    """Fill the Diskos columns of `<kg_dir>/document.parquet` from the metadata CSV."""
    from wellgraph.graph.schema import TABLES

    kg_dir, csv_path = Path(kg_dir), Path(csv_path)
    target = kg_dir / "document.parquet"
    if not target.is_file():
        raise FileNotFoundError(f"{target} not found: build the documents first.")
    if not csv_path.is_file():
        raise FileNotFoundError(f"Diskos metadata CSV {csv_path} not found.")

    con = duckdb.connect()
    try:
        con.execute(
            f"create table csv as select * from read_csv('{_quote(csv_path)}', all_varchar=true)"
        )
        con.execute(
            """
            create table pdf as
            select * from csv where upper("Format") = 'PDF'
            qualify row_number() over (partition by "Native Uid" order by "Update Date" desc) = 1
            """
        )
        con.execute(
            f"""
            create table quality as
            select {_STEM.format(col='"FolderFileName"')} as stem,
                   any_value(nullif(trim("Remark"), '')) as remark
            from csv where upper("Format") = 'JSON' group by 1
            """
        )
        kept = [c for c in TABLES["document"] if c not in DISKOS_FIELDS]
        select = ", ".join(
            [f"d.{c}" for c in kept]
            + [f"cast({sql} as {TABLES['document'][c]}) as {c}" for c, sql in DISKOS_FIELDS.items()]
        )
        con.execute(
            f"""
            create table out as
            select {select}, p."Native Uid" is not null as matched
            from read_parquet('{_quote(target)}') d
            left join pdf p on p."Native Uid" = d.document_id
            left join quality j on j.stem = {_STEM.format(col='p."FolderFileName"')}
            """
        )
        columns = ", ".join(TABLES["document"])
        con.execute(
            f"copy (select {columns} from out order by wellbore_id, document_id) "
            f"to '{_quote(target)}' (format parquet)"
        )
        documents, matched = con.execute("select count(*), count(*) filter (matched) from out").fetchone()
    finally:
        con.close()
    return {"documents": documents, "matched": matched}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kg", default="data/kg")
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    args = parser.parse_args(argv)
    print(json.dumps(add_diskos_metadata(args.kg, args.csv), indent=2))


if __name__ == "__main__":
    main()
