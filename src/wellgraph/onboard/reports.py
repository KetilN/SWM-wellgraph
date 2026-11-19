"""Read Diskos end-of-well report JSONs and stage them as Parquet.

One report JSON has a single top-level key (the document path) whose value holds
`original_document_metadata` and `pages[]`. Only the JSON is read; PDFs are never opened.

Choices made here (checked against 7 real files and the Diskos inventory CSV):

* `document_id` is the Diskos `wellborefileId` as a string (e.g. "3324598"). It is the
  "Native Uid" of the PDF in the Diskos CSV and is unique per document. If it is missing,
  the id is "p" + the first 16 hex chars of sha1(relative_path).
* `relative_path` is the path of the PDF as it is named on disk in the Zenodo archive.
  The JSON key looks like `25_6-1/WELL/3324598_25-06-01_PB-706-0464.PDF` (numeric
  wellborefileId prefix), but the files in the archive are named
  `25_6-1/WELL/25_6-1_25-06-01_PB-706-0464.PDF` (wellbore folder name as prefix, the
  "FolderFileName" column of the Diskos CSV). So the numeric prefix is replaced by the
  folder name. The JSON sits next to it with the extension `.JSON`.
* `wellbore_name` is `wellboreName` (matches SODIR `wlbWellboreName`), else `wellName`,
  else "". It is not guessed from the folder name, because `_` stands for both `/` and
  a space there (`6_3-A-1_H` is `6/3-A-1 H`).
* `page_no` is kept as in the JSON (0-based).
* The wellbore NPD id is unknown at this stage, so the document table is written as
  `document_staging.parquet` with `wellbore_name`; the resolve step adds `wellbore_id`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from wellgraph import progress

log = logging.getLogger(__name__)

FLUSH_EVERY_REPORTS = 200

DOCUMENT_STAGING_COLUMNS: dict[str, str] = {
    "document_id": "VARCHAR",
    "wellbore_name": "VARCHAR",
    "title": "VARCHAR",
    "relative_path": "VARCHAR",
    "n_pages": "INTEGER",
}
PAGE_COLUMNS: dict[str, str] = {
    "document_id": "VARCHAR",
    "page_no": "INTEGER",
    "text": "VARCHAR",
    "summary": "VARCHAR",
    "tags": "VARCHAR[]",
    "quality": "VARCHAR",
    "has_tables": "BOOLEAN",
    "has_figures": "BOOLEAN",
    "n_chars": "INTEGER",
}

_ID_PREFIX = re.compile(r"^\d+_")


@dataclass
class Page:
    page_no: int
    text: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    quality: str = ""
    has_tables: bool = False
    has_figures: bool = False
    n_chars: int = 0


@dataclass
class Report:
    document_id: str
    wellbore_name: str
    well_name: str
    title: str
    relative_path: str
    n_pages: int
    quality_note: str
    pages: list[Page] = field(default_factory=list)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _relative_path(key: str) -> tuple[str, str]:
    """Return (archive path of the PDF, file name without the numeric id prefix)."""
    parts = key.replace("\\", "/").strip("/").split("/")
    name = _ID_PREFIX.sub("", parts[-1])
    if len(parts) < 2:
        return name, name
    folder = parts[0]
    if not name.startswith(folder + "_"):
        parts[-1] = f"{folder}_{name}"
    else:
        parts[-1] = name
    return "/".join(parts), name


def _page(raw: dict, index: int) -> Page:
    ai = _dict(raw.get("AI"))
    text = _str(_dict(raw.get("OCR")).get("ocr_markdown"))
    tags = ai.get("tags")
    page_no = raw.get("page_no")
    return Page(
        page_no=page_no if isinstance(page_no, int) else index,
        text=text,
        summary=_str(ai.get("page_summary")),
        tags=[str(t) for t in tags if t is not None] if isinstance(tags, list) else [],
        quality=_str(_dict(raw.get("page_metadata")).get("quality")),
        has_tables=bool(ai.get("has_tables")),
        has_figures=bool(ai.get("has_figures")),
        n_chars=len(text),
    )


def load_report(path: str | os.PathLike) -> Report:
    """Load one report JSON. Raises ValueError if the file is not a report JSON."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError(f"{path}: not a report JSON (expected one top-level document key)")
    key, body = next(iter(data.items()))
    if not isinstance(body, dict) or not isinstance(body.get("pages"), list):
        raise ValueError(f"{path}: not a report JSON (no 'pages' list under {key!r})")  # noqa: TRY004

    meta = _dict(body.get("original_document_metadata"))
    relative_path, bare_name = _relative_path(key)
    file_id = meta.get("wellborefileId")
    if file_id not in (None, ""):
        document_id = str(file_id)
    else:
        document_id = "p" + hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]
    pages = [_page(_dict(p), i) for i, p in enumerate(body["pages"])]
    return Report(
        document_id=document_id,
        wellbore_name=_str(meta.get("wellboreName")) or _str(meta.get("wellName")),
        well_name=_str(meta.get("wellName")),
        title=_str(meta.get("title")) or bare_name,
        relative_path=relative_path,
        n_pages=len(pages),
        quality_note=_str(meta.get("document_quality")),
        pages=pages,
    )


def iter_report_paths(root: str | os.PathLike) -> Iterator[Path]:
    """Yield *.json files (any case) under root in sorted order, one folder at a time."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.lower().endswith(".json") and not name.startswith("."):
                yield Path(dirpath) / name


def iter_reports(root: str | os.PathLike) -> Iterator[Report]:
    """Lazily yield every report under root. Unreadable or non-report files are skipped."""
    paths = list(iter_report_paths(root))
    for done, path in enumerate(paths):
        progress.report("documents", done, len(paths))
        try:
            yield load_report(path)
        except (ValueError, OSError) as exc:
            log.warning("skipping %s: %s", path, exc)
    progress.report("documents", len(paths), len(paths), force=True)


def write_staging(reports: Iterable[Report], out_dir: str | os.PathLike) -> dict[str, int]:
    """Write document_staging.parquet and page.parquet to out_dir. Returns row counts.

    Reports are consumed one at a time. Pages are flushed every few hundred reports to
    compressed Parquet part files in a temporary folder, so neither memory nor temporary
    disk space grows with the uncompressed text of the full dataset.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page_types = ", ".join(f"cast({c} as {t}) as {c}" for c, t in PAGE_COLUMNS.items())
    documents: list[tuple] = []
    buffer: dict[str, list] = {c: [] for c in PAGE_COLUMNS}
    with tempfile.TemporaryDirectory(prefix="wellgraph_staging_") as tmp:
        con = duckdb.connect()
        parts = 0

        def flush() -> None:
            nonlocal parts
            if not buffer["document_id"]:
                return
            df = pd.DataFrame(buffer)  # noqa: F841 (read by duckdb through replacement scan)
            part = str(Path(tmp) / f"page_{parts:05d}.parquet").replace("'", "''")
            con.execute(
                f"copy (select {page_types} from (select * replace ("
                "case when tags = '' then []::varchar[] else string_split(tags, chr(31)) end as tags"
                f") from df)) to '{part}' (format parquet, compression zstd)"
            )
            parts += 1
            for values in buffer.values():
                values.clear()

        try:
            for done, r in enumerate(reports, 1):
                documents.append((r.document_id, r.wellbore_name, r.title, r.relative_path, r.n_pages))
                for p in r.pages:
                    buffer["document_id"].append(r.document_id)
                    buffer["page_no"].append(p.page_no)
                    buffer["text"].append(p.text)
                    buffer["summary"].append(p.summary)
                    buffer["tags"].append("\x1f".join(p.tags))
                    buffer["quality"].append(p.quality)
                    buffer["has_tables"].append(p.has_tables)
                    buffer["has_figures"].append(p.has_figures)
                    buffer["n_chars"].append(p.n_chars)
                if done % FLUSH_EVERY_REPORTS == 0:
                    flush()
            flush()

            doc_cols = ", ".join(f"{c} {t}" for c, t in DOCUMENT_STAGING_COLUMNS.items())
            con.execute(f"create table document ({doc_cols})")
            if documents:
                con.executemany("insert into document values (?, ?, ?, ?, ?)", documents)
            target = str(out / "document_staging.parquet").replace("'", "''")
            con.execute(f"copy (select * from document order by document_id) to '{target}' "
                        "(format parquet)")
            target = str(out / "page.parquet").replace("'", "''")
            if parts:
                glob = str(Path(tmp) / "page_*.parquet").replace("'", "''")
                source = f"read_parquet('{glob}')"
            else:
                page_cols = ", ".join(f"{c} {t}" for c, t in PAGE_COLUMNS.items())
                con.execute(f"create table page ({page_cols})")
                source = "page"
            con.execute(f"copy (select * from {source} order by document_id, page_no) "
                        f"to '{target}' (format parquet, compression zstd)")
            counts = {
                "documents": len(documents),
                "pages": con.execute(f"select count(*) from read_parquet('{target}')").fetchone()[0],
            }
        finally:
            con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m wellgraph.onboard.reports <root> <out_dir>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    root, out_dir = args
    if not Path(root).is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    counts = write_staging(iter_reports(root), out_dir)
    print(f"documents: {counts['documents']}")
    print(f"pages: {counts['pages']}")
    print(f"written: {Path(out_dir) / 'document_staging.parquet'}, {Path(out_dir) / 'page.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
