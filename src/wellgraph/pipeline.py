"""Reports in, queryable graph out: every build step in the right order.

    PYTHONPATH=src python -m wellgraph.pipeline --reports <folder of report JSONs> --kg data/kg

Steps:
  1. backbone       SODIR tables into the graph (skipped when already built)
  2. documents      report JSONs -> document and page tables, linked to wellbores
  3. events         route pages, ask the model, validate, depth-join, write events
  4. depth_pass     find a depth context for events whose page gave none
  5. daily_reports  parse daily drilling report tables (no model)
  6. core           porosity, permeability and rock type from the RealPore Excel file, when
                    it is present (--core-xlsx); --core-all loads every well, not only
                    those with a report

Model replies are cached under --cache, so a rerun only pays for pages it has not seen.
`--offline MODEL` rebuilds everything from the cache with no model calls. The model
endpoint comes from WELLGRAPH_LLM_* environment variables (see .env.example).

`--reports` defaults to WELLGRAPH_DATA_DIR. It is only read, never written.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from wellgraph.extract.daily import build_daily_reports
from wellgraph.extract.depth_pass import resolve_depths
from wellgraph.extract.run import LLMClient, OfflineClient, OpenAICompatibleClient, extract_events
from wellgraph.graph.build import build_documents
from wellgraph.onboard.diskos import DEFAULT_CSV
from wellgraph.onboard.realpore import DEFAULT_XLSX, build_core_samples, build_poro_perm_fits
from wellgraph.onboard.sodir import build_backbone, download_tables


def run_pipeline(
    reports_root: str | Path,
    kg_dir: str | Path,
    client: LLMClient,
    cache_dir: str | Path = "data/cache",
    raw_dir: str | Path = "data/factpages",
    download_backbone: bool = False,
    max_pages_per_report: int | None = None,
    wellbores: list[str] | None = None,
    workers: int = 4,
    core_xlsx: str | Path | None = None,
    core_all_wellbores: bool = False,
    diskos_csv: str | Path | None = None,
) -> dict[str, Any]:
    kg_dir, cache_dir, raw_dir = Path(kg_dir), Path(cache_dir), Path(raw_dir)
    kg_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {}

    if (kg_dir / "wellbore.parquet").is_file():
        stats["backbone"] = "already built"
    else:
        if download_backbone:
            download_tables(raw_dir)
        if not raw_dir.is_dir() or not any(raw_dir.glob("*.csv")):
            raise FileNotFoundError(
                f"No backbone in {kg_dir} and no SODIR tables in {raw_dir}. "
                "Run again with --download-backbone to fetch them (about 14 MB)."
            )
        stats["backbone"] = build_backbone(raw_dir, kg_dir)

    stats["documents"] = build_documents(reports_root, kg_dir, diskos_csv=diskos_csv)
    stats["events"] = extract_events(
        kg_dir, client, cache_dir / "extract", max_pages_per_report, wellbores, False, workers
    )
    stats["depth_pass"] = resolve_depths(kg_dir, client, cache_dir / "depth", workers)
    stats["daily_reports"] = build_daily_reports(kg_dir)
    if core_xlsx is not None and Path(core_xlsx).is_file():
        stats["core"] = {
            "samples": build_core_samples(core_xlsx, kg_dir, all_wellbores=core_all_wellbores),
            "fits": build_poro_perm_fits(kg_dir),
        }
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reports", default=os.environ.get("WELLGRAPH_DATA_DIR"),
                        help="Folder of report JSONs (<wellbore>/WELL/*.JSON). Read-only.")
    parser.add_argument("--kg", default=os.environ.get("WELLGRAPH_KG_DIR", "data/kg"))
    parser.add_argument("--cache", default="data/cache")
    parser.add_argument("--raw", default="data/factpages", help="Folder of SODIR CSV downloads.")
    parser.add_argument("--download-backbone", action="store_true",
                        help="Download the SODIR tables if the backbone is not built yet.")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap of pages per report.")
    parser.add_argument("--wellbore", action="append", help="Extract only this wellbore (repeatable).")
    parser.add_argument("--workers", type=int, default=4, help="Parallel model calls.")
    parser.add_argument("--core-xlsx", default=str(DEFAULT_XLSX),
                        help="RealPore Excel file; the core step is skipped if it is absent.")
    parser.add_argument("--core-all", action="store_true",
                        help="Load core data for every well, not only wells with a report.")
    parser.add_argument("--diskos-csv", default=str(DEFAULT_CSV),
                        help="Diskos metadata CSV; fills report type, company and dates.")
    parser.add_argument("--offline", metavar="MODEL",
                        help="Rebuild from cached replies of MODEL; no model calls.")
    args = parser.parse_args(argv)
    if not args.reports:
        parser.error("give --reports or set WELLGRAPH_DATA_DIR")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    client = OfflineClient(args.offline) if args.offline else OpenAICompatibleClient.from_env()
    stats = run_pipeline(args.reports, args.kg, client, args.cache, args.raw,
                         args.download_backbone, args.max_pages, args.wellbore, args.workers,
                         args.core_xlsx, args.core_all, args.diskos_csv)
    print(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
