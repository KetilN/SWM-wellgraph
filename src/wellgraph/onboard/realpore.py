"""Core plug data (porosity, permeability, rock type) from the RealPore compilation.

    PYTHONPATH=src python -m wellgraph.onboard.realpore --kg data/kg            # wells with a report
    PYTHONPATH=src python -m wellgraph.onboard.realpore --kg data/kg --all      # every well

Source: "Porosity, Permeability, Rock Type data from 1240 Norwegian exploration, appraisal
and development wells", Zenodo record 4723018, one Excel sheet of about 373,000 samples.
Data are © GeoProvider https://geoprovider.no/ and licensed CC-BY 4.0.

What this does, with no model involved:
  1. reads the sheet with the standard library (no spreadsheet package needed) and caches
     it as Parquet next to the Excel file;
  2. normalises each sample: decimal commas, one horizontal and one vertical permeability
     chosen by method (Klinkenberg, then air, nitrogen, gas, unspecified) with the method
     kept, one porosity with its method, out-of-range values dropped;
  3. matches samples to wellbores by NPD id and attaches formation and group by depth;
  4. writes `core_sample.parquet` and `poro_perm_fit.parquet` into the graph folder.

Sample depths are measured depth, as stated by the data owner.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import duckdb
import httpx

from wellgraph.graph.schema import EXTRA_TABLES

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ZENODO_RECORD = "https://zenodo.org/api/records/4723018"
ATTRIBUTION = "Data are © GeoProvider https://geoprovider.no/ and licensed CC-BY 4.0"
DEFAULT_XLSX = Path("data/raw/realpore/realpore_v2.xlsx")

POROSITY_RANGE = (0.0, 60.0)        # percent
GRAIN_DENSITY_RANGE = (1.5, 4.0)    # g/cm3
SATURATION_RANGE = (0.0, 100.0)     # percent
MAX_PERMEABILITY_MD = 1.0e6

# (method label, source column) in order of preference
KH_SOURCES = [
    ("klinkenberg", "permeability_horizontal_ke_klinkenberg_c"),
    ("klinkenberg", "klinkenberg_corrected_gas_perm_hor"),
    ("klinkenberg", "permeability_horizontal_ke_klinkenberg_c_2"),
    ("air", "permeability_horizontal_ka_air"),
    ("nitrogen", "nitrogen_permeability_hor"),
    ("gas", "permeability_horizontal_kg_gas_kg_hor"),
    ("unspecified", "permeabilty_without_correction_specified"),
]
KV_SOURCES = [
    ("klinkenberg", "permeability_vertical_ke_klinkenberg_cor"),
    ("klinkenberg", "klinkenberg_corrected_gas_perm_vert"),
    ("klinkenberg", "permeability_vertical_ke_klinkenberg_cor_2"),
    ("air", "permeability_vertical_ka_air"),
    ("nitrogen", "nitrogen_permeability_vert"),
    ("gas", "permeability_vertical_kg_gas_kg_vert"),
    ("unspecified", "permeabilty_without_correction_specified_2"),
]
POROSITY_SOURCES = [
    ("best_available", "porosity_best_of_available"),
    ("helium", "porosity_helium"),
    ("plug_horizontal", "porosity_horizontal_plug"),
    ("plug_vertical", "porosity_vertical_plug"),
    ("fluid_summation", "porosity_measurement_from_fluid_summatio"),
    ("sum", "porosity_sum"),
]


# ------------------------------------------------------------------ reading the sheet

def _column_names(header: list[str | None]) -> list[str]:
    """'Permeability horizontal ka  (air)' -> 'permeability_horizontal_ka_air'; repeats get _2."""
    seen: dict[str, int] = {}
    names = []
    for i, text in enumerate(header):
        name = re.sub(r"[^a-z0-9]+", "_", (text or f"col{i}").lower()).strip("_")[:40] or f"col{i}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def _column_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet_rows(xlsx: str | Path) -> Iterator[dict[str, Any]]:
    """Rows of the first sheet as {column name: text}, plus `row_number` (1-based, sheet row)."""
    with zipfile.ZipFile(xlsx) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
                if el.tag == NS + "si":
                    shared.append("".join(t.text or "" for t in el.iter(NS + "t")))
                    el.clear()
        names: list[str] | None = None
        number = 0
        for _, el in ET.iterparse(z.open("xl/worksheets/sheet1.xml")):
            if el.tag != NS + "row":
                continue
            number += 1
            cells: dict[int, str] = {}
            for c in el.iter(NS + "c"):
                kind, value = c.get("t"), c.find(NS + "v")
                if kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter(NS + "t"))
                elif value is None or value.text is None:
                    continue
                else:
                    text = shared[int(value.text)] if kind == "s" else value.text
                cells[_column_index(c.get("r"))] = text
            el.clear()
            if names is None:
                names = _column_names([cells.get(i) for i in range(max(cells) + 1)])
                continue
            row: dict[str, Any] = {name: cells.get(i) for i, name in enumerate(names)}
            row["row_number"] = number
            yield row


def _raw_parquet(xlsx: Path, raw_dir: Path) -> Path:
    """Cache of the sheet as Parquet (all text), rebuilt when the Excel file changes."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    stat = xlsx.stat()
    out = raw_dir / f"realpore_sheet_{stat.st_size}_{int(stat.st_mtime)}.parquet"
    if out.is_file():
        return out
    tmp = raw_dir / (out.stem + ".csv")
    names: list[str] | None = None
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in read_sheet_rows(xlsx):
            if names is None:
                names = list(row)
                writer.writerow(names)
            writer.writerow(["" if row[n] is None else row[n] for n in names])
    con = duckdb.connect()
    con.execute(
        "copy (select * from read_csv(?, all_varchar = true, header = true)) "
        f"to '{str(out).replace(chr(39), chr(39) * 2)}' (format parquet)", [str(tmp)]
    )
    con.close()
    tmp.unlink()
    return out


def download(dest: str | Path = DEFAULT_XLSX) -> Path:
    """Fetch the Excel file from Zenodo, resuming a partial download."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        files = client.get(ZENODO_RECORD).raise_for_status().json()["files"]
        entry = next(f for f in files if f["key"].lower().endswith(".xlsx"))
        have = dest.stat().st_size if dest.is_file() else 0
        if have < entry["size"]:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with client.stream("GET", entry["links"]["self"], headers=headers) as response:
                response.raise_for_status()
                with dest.open("ab" if have and response.status_code == 206 else "wb") as handle:
                    for chunk in response.iter_bytes(1 << 16):
                        handle.write(chunk)
    return dest


# ------------------------------------------------------------------ building the tables

def _number(column: str | None) -> str:
    return "null" if column is None else f"try_cast(replace(trim(\"{column}\"), ',', '.') as double)"


def _text(column: str | None) -> str:
    return "null" if column is None else f"nullif(trim(\"{column}\"), '')"


def _first(sources: list[tuple[str, str]], have: set[str], valid: str) -> tuple[str, str]:
    """SQL for (value, method): the first source column with a valid number."""
    usable = [(label, _number(col)) for label, col in sources if col in have]
    if not usable:
        return "null", "null"
    value = "coalesce(" + ", ".join(f"case when {valid.format(x=expr)} then {expr} end"
                                    for _, expr in usable) + ")"
    method = "case " + " ".join(f"when {valid.format(x=expr)} then '{label}'"
                                for label, expr in usable) + " end"
    return value, method


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def build_core_samples(
    xlsx: str | Path,
    kg_dir: str | Path,
    all_wellbores: bool = False,
    wellbores: list[str] | None = None,
    raw_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write `core_sample.parquet`. By default only wellbores that have a report loaded."""
    xlsx, kg_dir = Path(xlsx), Path(kg_dir)
    if not (kg_dir / "wellbore.parquet").is_file():
        raise FileNotFoundError(f"No backbone in {kg_dir}: build it before loading core data.")
    raw = _raw_parquet(xlsx, Path(raw_dir) if raw_dir else xlsx.parent)

    con = duckdb.connect()
    con.execute(f"create view raw as select * from read_parquet('{_quote(raw)}')")
    con.execute(f"create view wellbore as select * from read_parquet('{_quote(kg_dir / 'wellbore.parquet')}')")
    tops = kg_dir / "formation_top.parquet"
    if tops.is_file():
        con.execute(f"create view formation_top as select * from read_parquet('{_quote(tops)}')")
    else:
        con.execute("create table formation_top (wellbore_id BIGINT, formation VARCHAR, level VARCHAR, "
                    "top_md DOUBLE, base_md DOUBLE)")
    have = {r[0] for r in con.execute("describe raw").fetchall()}
    col = lambda name: name if name in have else None

    kh, kh_method = _first(KH_SOURCES, have, "{x} > 0 and {x} <= " + str(MAX_PERMEABILITY_MD))
    kv, kv_method = _first(KV_SOURCES, have, "{x} > 0 and {x} <= " + str(MAX_PERMEABILITY_MD))
    phi, phi_method = _first(POROSITY_SOURCES, have,
                             f"{{x}} > {POROSITY_RANGE[0]} and {{x}} <= {POROSITY_RANGE[1]}")
    between = lambda expr, lo, hi: f"case when {expr} between {lo} and {hi} then {expr} end"

    con.execute(f"""
        create table sample as
        select 'rp2-' || row_number as sample_id,
               try_cast(nullif(trim(npdid), '') as bigint) as wellbore_id,
               {_number('measured_depth')} as depth_md,
               {phi} as porosity_pct, {phi_method} as porosity_method,
               {kh} as kh_md, {kh_method} as kh_method,
               {kv} as kv_md, {kv_method} as kv_method,
               {between(_number(col('gain_density_gr_cm3')), *GRAIN_DENSITY_RANGE)} as grain_density_gcc,
               {between(_number(col('pore_sturation_water_sw_sw_s_wtr_wtr')), *SATURATION_RANGE)} as water_saturation_pct,
               {between(_number(col('pore_sturation_oil_so')), *SATURATION_RANGE)} as oil_saturation_pct,
               lower({_text(col('clean_lithology'))}) as lithology,
               lower({_text(col('main_lithology'))}) as lithology_detail,
               lower({_text(col('grain_size'))}) as grain_size,
               lower({_text(col('sorting'))}) as sorting,
               lower({_text(col('cement'))}) as cement,
               {_text(col('formation_description_original'))} as description,
               {_text(col('data_source_file_name'))} as source_file
        from raw
    """)
    total = con.execute("select count(*) from sample").fetchone()[0]
    skipped = {
        "no_wellbore_id": con.execute("select count(*) from sample where wellbore_id is null").fetchone()[0],
        "wellbore_not_in_backbone": con.execute(
            "select count(*) from sample s where wellbore_id is not null and not exists "
            "(select 1 from wellbore w where w.wellbore_id = s.wellbore_id)").fetchone()[0],
        "no_depth": con.execute("select count(*) from sample where depth_md is null "
                                "and wellbore_id is not null").fetchone()[0],
    }

    if all_wellbores:
        selection = "select wellbore_id from wellbore"
        params: list[Any] = []
    elif wellbores:
        keys = [re.sub(r"[\s_]+", "", w.upper()) for w in wellbores]
        selection = ("select wellbore_id from wellbore where "
                     "regexp_replace(upper(name), '[\\s_]+', '', 'g') in (select unnest(?))")
        params = [keys]
    else:
        documents = kg_dir / "document.parquet"
        if not documents.is_file():
            raise FileNotFoundError(f"No reports loaded in {kg_dir}; use --all or --wellbore.")
        selection = f"select distinct wellbore_id from read_parquet('{_quote(documents)}')"
        params = []

    columns = ", ".join(EXTRA_TABLES["core_sample"])
    con.execute(f"""
        create table core_sample as
        select {columns} from (
          select s.*,
            (select f.formation from formation_top f
              where f.wellbore_id = s.wellbore_id and f.level = 'FORMATION'
                and s.depth_md between least(f.top_md, f.base_md) and greatest(f.top_md, f.base_md)
              order by f.top_md desc limit 1) as formation,
            (select f.formation from formation_top f
              where f.wellbore_id = s.wellbore_id and f.level = 'GROUP'
                and s.depth_md between least(f.top_md, f.base_md) and greatest(f.top_md, f.base_md)
              order by f.top_md desc limit 1) as group_name
          from sample s
          where s.depth_md is not null and s.wellbore_id in ({selection})
        )
        order by wellbore_id, depth_md, sample_id
    """, params)
    casts = ", ".join(f"cast({c} as {t}) as {c}" for c, t in EXTRA_TABLES["core_sample"].items())
    con.execute(f"copy (select {casts} from core_sample) "
                f"to '{_quote(kg_dir / 'core_sample.parquet')}' (format parquet)")
    one = lambda sql: con.execute(sql).fetchone()[0]
    stats = {
        "rows_in_source": total,
        "samples_written": one("select count(*) from core_sample"),
        "wellbores": one("select count(distinct wellbore_id) from core_sample"),
        "with_porosity": one("select count(porosity_pct) from core_sample"),
        "with_horizontal_permeability": one("select count(kh_md) from core_sample"),
        "with_both": one("select count(*) from core_sample where porosity_pct is not null and kh_md is not null"),
        "with_lithology": one("select count(lithology) from core_sample"),
        "with_formation": one("select count(formation) from core_sample"),
        "skipped": {k: v for k, v in skipped.items() if v},
    }
    con.close()
    return stats


def build_poro_perm_fits(
    kg_dir: str | Path, min_samples: int = 30, min_wellbores: int = 3
) -> dict[str, Any]:
    """Fit log10(kh) = a + b * porosity per formation and rock type; write `poro_perm_fit.parquet`."""
    kg_dir = Path(kg_dir)
    con = duckdb.connect()
    con.execute(f"create view core_sample as select * from read_parquet('{_quote(kg_dir / 'core_sample.parquet')}')")
    con.execute("""
        create view pair as
        select formation, lithology, wellbore_id, porosity_pct as phi, log10(kh_md) as logk, kh_md
        from core_sample where formation is not null and porosity_pct > 0 and kh_md > 0
    """)
    grouping = """
        select formation, {lith} as lithology, count(*) as samples,
               count(distinct wellbore_id) as wellbores,
               regr_intercept(logk, phi) as intercept, regr_slope(logk, phi) as slope,
               regr_r2(logk, phi) as r2,
               sqrt(greatest(0, 1 - regr_r2(logk, phi)) * var_pop(logk)) as residual_sd,
               quantile_cont(phi, 0.1) as porosity_p10, quantile_cont(phi, 0.5) as porosity_p50,
               quantile_cont(phi, 0.9) as porosity_p90, quantile_cont(kh_md, 0.5) as kh_p50
        from pair {where} group by 1, 2
    """
    columns = ", ".join(f"cast({c} as {t}) as {c}" for c, t in EXTRA_TABLES["poro_perm_fit"].items())
    con.execute(f"""
        create table fit as select {columns} from (
          {grouping.format(lith="'all'", where="")}
          union all
          {grouping.format(lith="lithology", where="where lithology is not null")}
        ) where samples >= ? and wellbores >= ? and slope is not null
        order by formation, lithology
    """, [min_samples, min_wellbores])
    con.execute(f"copy fit to '{_quote(kg_dir / 'poro_perm_fit.parquet')}' (format parquet)")
    stats = {
        "fits": con.execute("select count(*) from fit").fetchone()[0],
        "formations": con.execute("select count(distinct formation) from fit").fetchone()[0],
        "min_samples": min_samples, "min_wellbores": min_wellbores,
    }
    con.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellgraph.onboard.realpore", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    parser.add_argument("--kg", default="data/kg")
    parser.add_argument("--download", action="store_true", help="Fetch the Excel file from Zenodo if needed.")
    parser.add_argument("--all", action="store_true", help="Load every wellbore, not only those with a report.")
    parser.add_argument("--wellbore", action="append", help="Load only this wellbore (repeatable).")
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--min-wellbores", type=int, default=3)
    args = parser.parse_args(argv)
    if args.download:
        download(args.xlsx)
    stats = {"core_samples": build_core_samples(args.xlsx, args.kg, args.all, args.wellbore),
             "poro_perm_fits": build_poro_perm_fits(args.kg, args.min_samples, args.min_wellbores),
             "attribution": ATTRIBUTION}
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
