"""Deterministic graph backbone from SODIR FactPages CSV exports (no LLM).

    python -m wellgraph.onboard.sodir --raw data/factpages --out data/kg [--download]

Raw CSVs are cached in `--raw` (one file per FactPages table, never re-downloaded unless
`--force`). The transforms are pure: a directory of CSVs in, DataFrames / Parquet out.
Output tables `wellbore`, `formation_top` and `hole_section` follow
`wellgraph.graph.schema.TABLES` exactly; `mud_check` and `formation` are the
`extra_tables` of schema.yaml (`wellgraph.graph.schema.EXTRA_TABLES`).

What the real exports look like (checked 2026-11-19), and what is done about it:
- UTF-8 with BOM, comma separated, quoted multi-line text. Dates are `DD.MM.YYYY`;
  blank means unknown.
- Depths are metres MD, mud densities are specific gravity (g/cm3), diameters inches.
- String columns are sometimes space padded (`"CONDUCTOR "`, `"30    "`): stripped, and
  empty strings become NULL.
- 0 means "not set" in water depth, hole depth, LOT mud density, mud weight and mud
  viscosity: stored as NULL. Yield point 0 is kept when a viscosity was measured.
- Some "other" wellbores have latitude and longitude both 0: stored as NULL.
- Hole and casing diameters are free text: `12 1/4`, `8  1/2`, `15.95`, and a few typos
  (`12 174`, `42x36`, `9 5/9`). Whole numbers, decimals and fractions over 2/4/8/16 are
  parsed; anything else becomes NULL rather than a guessed size.
- Shallow / soil / scientific wellbores (`wlbWellType` OTHER) live in a third table,
  `wellbore_other_all`, which is optional here. Formation tops, casing and mud rows whose
  wellbore id is in no wellbore table are dropped by `build_backbone` and counted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd

from wellgraph.graph.schema import EXTRA_TABLES, TABLES

log = logging.getLogger(__name__)

URL = (
    "https://factpages.sodir.no/public?/Factpages/external/tableview/{table}"
    "&rs:Command=Render&rc:Toolbar=false&rc:Parameters=f&IpAddress=not_used"
    "&CultureCode=en&rs:Format=CSV&Top100=false"
)

# source table -> wellbore_type
WELLBORE_SOURCES = {
    "wellbore_exploration_all": "EXPLORATION",
    "wellbore_development_all": "DEVELOPMENT",
    "wellbore_other_all": "OTHER",
}
OPTIONAL_TABLES = {"wellbore_other_all"}
DEFAULT_TABLES = (
    "wellbore_exploration_all",
    "wellbore_development_all",
    "wellbore_other_all",
    "wellbore_formation_top",
    "wellbore_casing_and_lot",
    "wellbore_mud",
    "strat_litho_overview",
)
MAX_TABLE_BYTES = 50 * 1024 * 1024

SCHEMA_TABLES = ("wellbore", "formation_top", "hole_section")

# mud_check and formation are defined under extra_tables in schema.yaml (imported above).

MUD_TYPE_PLACEHOLDERS = {"DUMMY"}


# --- download ------------------------------------------------------------------------


def download_tables(
    dest_dir: str | Path,
    tables: Iterable[str] = DEFAULT_TABLES,
    force: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Path]:
    """Fetch each table once into `dest_dir/<table>.csv`; existing non-empty files are kept.

    Returns {table: path} for every table now on disk. A table in OPTIONAL_TABLES that
    cannot be fetched is skipped with a warning; any other failure raises.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True)
    paths: dict[str, Path] = {}
    try:
        for table in tables:
            path = dest / f"{table}.csv"
            if not force and path.is_file() and path.stat().st_size > 0:
                paths[table] = path
                continue
            try:
                _fetch(client, table, path)
            except (httpx.HTTPError, RuntimeError) as exc:
                if table in OPTIONAL_TABLES:
                    log.warning("optional table %s not downloaded: %s", table, exc)
                    continue
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"Download of SODIR table {table} failed: {exc}") from exc
            paths[table] = path
    finally:
        if own_client:
            client.close()
    return paths


def _fetch(client: httpx.Client, table: str, path: Path) -> None:
    part = path.with_suffix(".part")
    size = 0
    head = b""
    try:
        with client.stream("GET", URL.format(table=table)) as response:
            response.raise_for_status()
            with open(part, "wb") as handle:
                for chunk in response.iter_bytes(1 << 16):
                    if len(head) < 64:
                        head += chunk[:64]
                    size += len(chunk)
                    if size > MAX_TABLE_BYTES:
                        raise RuntimeError(
                            f"SODIR table {table} is larger than "
                            f"{MAX_TABLE_BYTES // (1024 * 1024)} MB; download stopped."
                        )
                    handle.write(chunk)
        start = head.removeprefix(b"\xef\xbb\xbf").lstrip().lower()
        if size == 0 or start.startswith(b"<"):
            raise RuntimeError(f"SODIR table {table} did not return CSV (empty or HTML page).")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


# --- parsing -------------------------------------------------------------------------

_FRACTION = re.compile(r"^(?:(\d+) )?(\d+)/(\d+)$")
_DENOMINATORS = {2, 4, 8, 16}


def parse_diameter(value: Any) -> float | None:
    """'12 1/4' -> 12.25, '8  1/2' -> 8.5, '36' -> 36.0, '15.95' -> 15.95.

    Blank, zero and unreadable values ('12 174', '42x36', '9 5/9') give None.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        m = _FRACTION.match(text)
        if not m:
            return None
        num, den = int(m.group(2)), int(m.group(3))
        if den not in _DENOMINATORS or num >= den:
            return None
        number = int(m.group(1) or 0) + num / den
    return number if number > 0 else None


def _read(raw_dir: str | Path, table: str) -> pd.DataFrame:
    """Raw CSV as stripped strings; missing values are ''."""
    path = Path(raw_dir) / f"{table}.csv"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run `python -m wellgraph.onboard.sodir --download` "
            "or copy the FactPages export there."
        )
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False,
                     encoding="utf-8-sig")
    return df.apply(lambda column: column.str.strip())


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Column as strings, or all-blank when this export does not have it."""
    if name in df.columns:
        return df[name]
    return pd.Series([""] * len(df), index=df.index, dtype=object)


def _text(series: pd.Series) -> pd.Series:
    return pd.Series([v if v else None for v in series], index=series.index, dtype=object)


def _number(series: pd.Series, zero_is_null: bool = False) -> pd.Series:
    out = pd.to_numeric(series.replace("", None), errors="coerce").astype("float64")
    if zero_is_null:
        out = out.mask(out == 0)
    return out


def _integer(series: pd.Series) -> pd.Series:
    """Optional integer ids as an object column of int | None."""
    return pd.Series([int(v) if v else None for v in series], index=series.index, dtype=object)


def _date(series: pd.Series) -> pd.Series:
    """'20.03.1989' -> date(1989, 3, 20); blank -> None. Any other shape is an error."""

    def one(value: str) -> dt.date | None:
        if not value:
            return None
        return dt.datetime.strptime(value, "%d.%m.%Y").date()

    return pd.Series([one(v) for v in series], index=series.index, dtype=object)


def _wellbore_id(df: pd.DataFrame, table: str) -> pd.Series:
    ids = df["wlbNpdidWellbore"]
    if (ids == "").any():
        raise ValueError(f"{table}: {int((ids == '').sum())} rows have no wlbNpdidWellbore")
    return ids.astype("int64")


def _finish(df: pd.DataFrame, columns: Iterable[str], sort: list[str]) -> pd.DataFrame:
    df = df[list(columns)]
    return df.sort_values(sort, kind="stable", na_position="last").reset_index(drop=True)


# --- transforms ----------------------------------------------------------------------


def build_wellbore(raw_dir: str | Path) -> pd.DataFrame:
    """Union of the exploration, development and (if present) other wellbore tables."""
    parts = []
    for table, wellbore_type in WELLBORE_SOURCES.items():
        if table in OPTIONAL_TABLES and not (Path(raw_dir) / f"{table}.csv").is_file():
            continue
        src = _read(raw_dir, table)
        parts.append(
            pd.DataFrame(
                {
                    "wellbore_id": _wellbore_id(src, table),
                    "name": _text(src["wlbWellboreName"]),
                    "well_name": _text(_col(src, "wlbWell")),
                    "wellbore_type": wellbore_type,
                    "purpose": _text(_col(src, "wlbPurpose")),
                    "operator": _text(_col(src, "wlbDrillingOperator")),
                    "field": _text(_col(src, "wlbField")),
                    "drilling_facility": _text(_col(src, "wlbDrillingFacility")),
                    "entry_date": _date(_col(src, "wlbEntryDate")),
                    "completion_date": _date(_col(src, "wlbCompletionDate")),
                    "total_depth_md": _number(_col(src, "wlbTotalDepth"), zero_is_null=True),
                    "water_depth": _number(_col(src, "wlbWaterDepth"), zero_is_null=True),
                    "lat": _number(_col(src, "wlbNsDecDeg")),
                    "lon": _number(_col(src, "wlbEwDecDeg")),
                    "factpage_url": _text(_col(src, "wlbFactPageUrl")),
                    "source_table": table,
                }
            )
        )
    df = pd.concat(parts, ignore_index=True)
    no_position = (df["lat"] == 0) & (df["lon"] == 0)  # 64 shallow wellbores sit at 0, 0
    df.loc[no_position, ["lat", "lon"]] = None
    duplicated = df[df["wellbore_id"].duplicated(keep=False)]
    if len(duplicated):
        detail = "; ".join(
            f"{r.wellbore_id} {r.name!r} in {r.source_table}"
            for r in duplicated.sort_values("wellbore_id").head(10).itertuples(index=False)
        )
        raise ValueError(f"wlbNpdidWellbore is not unique across wellbore tables: {detail}")
    if df["name"].isna().any():
        raise ValueError("wellbore rows without wlbWellboreName")
    return _finish(df, TABLES["wellbore"], ["wellbore_id"])


def build_formation_top(raw_dir: str | Path) -> pd.DataFrame:
    src = _read(raw_dir, "wellbore_formation_top")
    df = pd.DataFrame(
        {
            "wellbore_id": _wellbore_id(src, "wellbore_formation_top"),
            "formation": _text(src["lsuName"]),
            "level": _text(src["lsuLevel"]),
            "parent": _text(src["lsuNameParent"]),
            "top_md": _number(src["lsuTopDepth"]),
            "base_md": _number(src["lsuBottomDepth"]),
        }
    )
    return _finish(df, TABLES["formation_top"], ["wellbore_id", "top_md", "level", "formation"])


def build_hole_section(raw_dir: str | Path) -> pd.DataFrame:
    """One row per row of the casing and leak-off table."""
    src = _read(raw_dir, "wellbore_casing_and_lot")
    df = pd.DataFrame(
        {
            "wellbore_id": _wellbore_id(src, "wellbore_casing_and_lot"),
            "hole_diameter_in": src["wlbHoleDiameter"].map(parse_diameter).astype("float64"),
            "base_md": _number(src["wlbHoleDepth"], zero_is_null=True),
            "casing_type": _text(src["wlbCasingType"]),
            "casing_diameter_in": src["wlbCasingDiameter"].map(parse_diameter).astype("float64"),
            "casing_depth_md": _number(src["wlbCasingDepth"], zero_is_null=True),
            "lot_mud_density_sg": _number(src["wlbLotMudDencity"], zero_is_null=True),
        }
    )
    return _finish(
        df, TABLES["hole_section"],
        ["wellbore_id", "casing_depth_md", "base_md", "hole_diameter_in"],
    )


def build_mud_check(raw_dir: str | Path) -> pd.DataFrame:
    src = _read(raw_dir, "wellbore_mud")
    viscosity = _number(src["wlbMudViscosityAtMD"], zero_is_null=True)
    yield_point = _number(src["wlbYieldPointAtMD"])
    mud_type = src["wlbMudType"].mask(src["wlbMudType"].str.upper().isin(MUD_TYPE_PLACEHOLDERS), "")
    df = pd.DataFrame(
        {
            "wellbore_id": _wellbore_id(src, "wellbore_mud"),
            "md": _number(src["wlbMD"]),
            "mud_weight_sg": _number(src["wlbMudWeightAtMD"], zero_is_null=True),
            "viscosity_mpas": viscosity,
            # 0 with no viscosity is an unfilled pair; 0 next to a viscosity is a reading
            "yield_point_pa": yield_point.mask((yield_point == 0) & viscosity.isna()),
            "mud_type": _text(mud_type),
            "date_measured": _date(src["wlbMudDateMeasured"]),
        }
    )
    return _finish(df, EXTRA_TABLES["mud_check"], ["wellbore_id", "md", "date_measured"])


def build_formation(raw_dir: str | Path) -> pd.DataFrame:
    src = _read(raw_dir, "strat_litho_overview")
    df = pd.DataFrame(
        {
            "formation_id": src["lsuNpdidLithoStrat"].astype("int64"),
            "name": _text(src["lsuName"]),
            "level": _text(src["lsuLevel"]),
            "parent": _text(src["lsuNameParent"]),
            "parent_id": _integer(_col(src, "lsuNpdidLithoStratParent")),
            # the SODIR header really is "IsuFactPageUrl" (capital i)
            "factpage_url": _text(_col(src, "IsuFactPageUrl")),
        }
    )
    if df["name"].duplicated().any():
        names = sorted(df.loc[df["name"].duplicated(), "name"])
        raise ValueError(f"strat_litho_overview: duplicate unit names {names[:10]}")
    return _finish(df, EXTRA_TABLES["formation"], ["name"])


# --- output --------------------------------------------------------------------------


def write_parquet(df: pd.DataFrame, path: str | Path, columns: dict[str, str]) -> Path:
    """Write `df` with exactly `columns` (name -> DuckDB type), in that order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = df.astype(object).where(df.notna(), None)  # one NULL spelling for DuckDB
    con = duckdb.connect()
    try:
        con.execute(f"create table t ({', '.join(f'{c} {t}' for c, t in columns.items())})")
        marks = ", ".join("?" for _ in columns)
        rows = list(frame[list(columns)].itertuples(index=False, name=None))
        if rows:
            con.executemany(f"insert into t values ({marks})", rows)
        escaped = str(path).replace("'", "''")
        con.execute(f"copy t to '{escaped}' (format parquet, compression zstd)")
    finally:
        con.close()
    return path


def build_backbone(raw_dir: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Build every table from `raw_dir` and write `<out_dir>/<table>.parquet`."""
    out = Path(out_dir)
    wellbore = build_wellbore(raw_dir)
    known = set(wellbore["wellbore_id"])
    frames: dict[str, tuple[pd.DataFrame, dict[str, str]]] = {
        "wellbore": (wellbore, TABLES["wellbore"]),
    }
    dropped: dict[str, int] = {}
    for table, builder, columns in (
        ("formation_top", build_formation_top, TABLES["formation_top"]),
        ("hole_section", build_hole_section, TABLES["hole_section"]),
        ("mud_check", build_mud_check, EXTRA_TABLES["mud_check"]),
    ):
        df = builder(raw_dir)
        keep = df["wellbore_id"].isin(known)
        dropped[table] = int((~keep).sum())
        frames[table] = (df[keep].reset_index(drop=True), columns)
    frames["formation"] = (build_formation(raw_dir), EXTRA_TABLES["formation"])

    files = {}
    for table, (df, columns) in frames.items():
        files[table] = str(write_parquet(df, out / f"{table}.parquet", columns))
    return {
        "rows": {table: len(df) for table, (df, _) in frames.items()},
        "dropped_unknown_wellbore": dropped,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--raw", default="data/factpages", help="folder of raw SODIR CSVs")
    parser.add_argument("--out", default="data/kg", help="folder for the Parquet tables")
    parser.add_argument("--download", action="store_true",
                        help="fetch tables that are not in --raw yet")
    parser.add_argument("--force", action="store_true",
                        help="with --download: fetch again even if the file exists")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.download:
        paths = download_tables(args.raw, force=args.force)
        for table, path in paths.items():
            log.info("%s: %.1f MB", table, path.stat().st_size / 1e6)
    report = build_backbone(args.raw, args.out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
