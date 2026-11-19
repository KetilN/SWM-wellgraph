"""Build graph tables that link report data to the backbone.

    PYTHONPATH=src python -m wellgraph.graph.build documents <reports_root> <kg_dir>

`documents` reads report JSONs (never PDFs), resolves each report's wellbore name to the
NPD wellbore id in `<kg_dir>/wellbore.parquet`, and writes `document.parquet` and
`page.parquet` into `<kg_dir>` in the shape of `wellgraph.graph.schema.TABLES`.
Reports whose wellbore is not in the backbone are left out and listed in the result.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import duckdb

from wellgraph.graph.schema import TABLES
from wellgraph.onboard.diskos import DISKOS_FIELDS, add_diskos_metadata
from wellgraph.onboard.reports import iter_reports, write_staging

# Same normalisation as GraphStore: upper-case, no spaces or underscores.
_KEY = "regexp_replace(upper({col}), '[\\s_]+', '', 'g')"


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def build_documents(
    reports_root: str | Path,
    kg_dir: str | Path,
    staging_dir: str | Path | None = None,
    diskos_csv: str | Path | None = None,
) -> dict[str, Any]:
    kg_dir = Path(kg_dir)
    wellbore = kg_dir / "wellbore.parquet"
    if not wellbore.is_file():
        raise FileNotFoundError(f"{wellbore} not found: build the backbone first.")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(staging_dir) if staging_dir is not None else Path(tmp)
        staging.mkdir(parents=True, exist_ok=True)
        staged = write_staging(iter_reports(reports_root), staging)

        con = duckdb.connect()
        con.execute(
            f"create view wb as select wellbore_id, {_KEY.format(col='name')} as k "
            f"from read_parquet('{_quote(wellbore)}')"
        )
        con.execute(
            f"create view staged as select *, {_KEY.format(col='wellbore_name')} as k "
            f"from read_parquet('{_quote(staging / 'document_staging.parquet')}')"
        )
        # Exact name first; else the parent of a technical sidetrack (trailing T2, T3 ...).
        con.execute(
            """
            create table resolved as
            select s.document_id, coalesce(a.wellbore_id, b.wellbore_id) as wellbore_id,
                   s.wellbore_name, s.title, s.relative_path, s.n_pages
            from staged s
            left join wb a on a.k = s.k
            left join wb b on b.k = regexp_replace(s.k, 'T[0-9]+$', '') and a.wellbore_id is null
            """
        )
        # Diskos columns start empty; add_diskos_metadata fills them from the metadata CSV.
        doc_cols = ", ".join(
            f"cast({'null' if c in DISKOS_FIELDS else c} as {t}) as {c}"
            for c, t in TABLES["document"].items()
        )
        con.execute(
            f"copy (select {doc_cols} from resolved where wellbore_id is not null "
            f"order by wellbore_id, document_id) "
            f"to '{_quote(kg_dir / 'document.parquet')}' (format parquet)"
        )
        page_cols = ", ".join(f"cast(p.{c} as {t}) as {c}" for c, t in TABLES["page"].items())
        con.execute(
            f"copy (select {page_cols} from read_parquet('{_quote(staging / 'page.parquet')}') p "
            f"join resolved r using (document_id) where r.wellbore_id is not null "
            f"order by p.document_id, p.page_no) "
            f"to '{_quote(kg_dir / 'page.parquet')}' (format parquet, compression zstd)"
        )
        documents = con.execute(
            "select count(*) from resolved where wellbore_id is not null"
        ).fetchone()[0]
        pages = con.execute(
            f"select count(*) from read_parquet('{_quote(kg_dir / 'page.parquet')}')"
        ).fetchone()[0]
        unresolved = [
            {"wellbore_name": name, "documents": n}
            for name, n in con.execute(
                "select wellbore_name, count(*) from resolved where wellbore_id is null "
                "group by 1 order by 1"
            ).fetchall()
        ]
        con.close()

    result = {
        "reports_read": staged["documents"],
        "documents": documents,
        "pages": pages,
        "unresolved": unresolved,
    }
    if diskos_csv is not None and Path(diskos_csv).is_file():
        result["diskos_metadata"] = add_diskos_metadata(kg_dir, diskos_csv)["matched"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.graph.build", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    docs = sub.add_parser("documents", help="Load report documents and pages into the graph.")
    docs.add_argument("reports_root")
    docs.add_argument("kg_dir")
    args = parser.parse_args(argv)
    if args.command == "documents":
        print(json.dumps(build_documents(args.reports_root, args.kg_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
