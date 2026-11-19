import math
import zipfile

import duckdb
import pytest

from wellgraph.graph.schema import EXTRA_TABLES, TABLES
from wellgraph.onboard import realpore

HEADER = [
    "Data source file name", "Well Name in file", "Measured Depth", None, "Comments", "NPDID",
    "Plug or sample number", "Permeability horizontal ka  (air)",
    "Permeability horizontal ke  (klinkenberg corrected)  also called kl. KL",
    "Permeability vertical ka  (air)", "Permeability vertical ke  (klinkenberg corrected)",
    "Permeability horizontal kg  (gas) KG HOR",
    "Permeability horizontal ke  (klinkenberg corrected)  also called kl. KL (2)",
    "Permeability vertical kg  (gas) KG VERT", "Permeability vertical ke  (klinkenberg corrected) (2)",
    "Nitrogen Permeability. Hor.", "Klinkenberg corrected gas perm. Hor.",
    "Nitrogen Permeability. Vert.", "Klinkenberg corrected gas perm. Vert.",
    "Permeabilty without correction specified KH horizontal 1/pm",
    "Permeabilty without correction specified KV vertical 1/pm", "Porosity. Horizontal PLUG",
    "Porosity. Vertical PLUG", "Porosity measurement from fluid summation", "Porosity helium",
    "porosity best of available", "Porosity Sum", "Pore Sturation Oil  (So)",
    "Pore Sturation Water Sw  Sw. S wtr. WTR.", "gain density gr/cm3",
    "Formation description original", "Remarks on the table", "Main Lithology Origin",
    "Non sorted Transcription", "main lithology", "main lithology", "clean lithology",
    "clean lithology", "color", "grain size", "rounding", "cement", "sorting", "sed structures",
]


def row(**kw):
    """One sheet row by header position; unspecified cells are empty."""
    cells = [None] * len(HEADER)
    positions = {"file": 0, "well": 1, "depth": 2, "npdid": 5, "kh_air": 7, "kh_klink": 8,
                 "kv_air": 9, "kh_n2": 15, "kh_unspec": 19, "phi_plug_h": 21, "phi_he": 24,
                 "phi_best": 25, "so": 27, "sw": 28, "rho": 29, "desc": 30, "lith_detail": 34,
                 "lith": 36, "grain": 39, "cement": 41, "sorting": 42}
    for key, value in kw.items():
        cells[positions[key]] = value
    return cells


def write_xlsx(path, rows):
    """A minimal workbook: one sheet, inline strings, numbers as numbers."""
    def ref(col, r):
        name = ""
        col += 1
        while col:
            col, rem = divmod(col - 1, 26)
            name = chr(65 + rem) + name
        return f"{name}{r}"

    body = []
    for r, cells in enumerate([HEADER, *rows], start=1):
        out = []
        for c, value in enumerate(cells):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                out.append(f'<c r="{ref(c, r)}"><v>{value}</v></c>')
            else:
                out.append(f'<c r="{ref(c, r)}" t="inlineStr"><is><t>{value}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(out)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="ORGINAL" sheetId="1"/></sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(body)}</sheetData></worksheet>')
    return path


ROWS = [
    row(file="99/1 - 1", well="99_1-1", depth=2500.0, npdid=1, kh_air=120.0, kh_klink=100.0, kv_air=40.0,
        phi_best=22.5, phi_he=22.0, rho=2.65, sw=35.0, so=20.0, desc="Sst.Lt-gry.F-gr", lith="Sandstone",
        lith_detail="sandstone calcareous", grain="fine grained", cement="well cemented", sorting="well sorted"),
    row(file="99/1 - 1", well="99_1-1", depth="2501,5", npdid=1, kh_air=12.0, phi_plug_h="18,2", rho=9.64, lith="sandstone"),
    row(file="99/1 - 1", well="99_1-1", depth=2600.0, npdid=1, kh_n2=0.5, phi_best=103.0, lith="siltstone"),
    row(file="99/1 - 1", well="99_1-1", depth=3500.0, npdid=1, kh_unspec=0, phi_best=5.0),   # below the tops
    row(file="99/9 - 9", well="99_9-9", depth=1000.0, npdid=999, kh_air=1.0, phi_best=10.0),  # unknown wellbore
    row(file="99/1 - 2", well="99_1-2", depth=2500.0, kh_air=1.0, phi_best=10.0),             # no NPDID
]


@pytest.fixture
def kg(tmp_path):
    con = duckdb.connect()
    for table, rows, columns in [
        ("wellbore", [(1, "99/1-1"), (2, "99/1-2 A")], ["wellbore_id", "name"]),
        ("formation_top", [(1, "SHETLAND GP", "GROUP", None, 2400.0, 2900.0),
                           (1, "TOR FM", "FORMATION", "SHETLAND GP", 2400.0, 2550.0),
                           (1, "HOD FM", "FORMATION", "SHETLAND GP", 2550.0, 2900.0)],
         list(TABLES["formation_top"])),
        ("document", [("d1", 1, "R.PDF", "x", 10)], list(TABLES["document"])),
    ]:
        cols = ", ".join(f"{c} {t}" for c, t in TABLES[table].items())
        con.execute(f"create table {table} ({cols})")
        con.executemany(f"insert into {table} ({', '.join(columns)}) values ({', '.join('?' for _ in columns)})", rows)
        con.execute(f"copy {table} to '{tmp_path}/{table}.parquet' (format parquet)")
    con.close()
    return tmp_path


def samples(kg):
    con = duckdb.connect()
    cur = con.execute(f"select * from read_parquet('{kg}/core_sample.parquet') order by depth_md")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def test_rows_are_read_from_the_sheet_without_a_spreadsheet_library(tmp_path):
    path = write_xlsx(tmp_path / "rp.xlsx", ROWS)
    rows = list(realpore.read_sheet_rows(path))
    assert len(rows) == 6 and rows[0]["row_number"] == 2
    assert rows[0]["measured_depth"] == "2500.0" and rows[1]["measured_depth"] == "2501,5"
    assert rows[0]["clean_lithology"] == "Sandstone"


def test_samples_are_normalised_joined_and_written_to_the_contract(tmp_path, kg):
    path = write_xlsx(tmp_path / "rp.xlsx", ROWS)
    stats = realpore.build_core_samples(path, kg, all_wellbores=True, raw_dir=tmp_path / "raw")
    rows = samples(kg)

    assert list(rows[0]) == list(EXTRA_TABLES["core_sample"])
    assert stats["rows_in_source"] == 6 and stats["samples_written"] == 4
    assert stats["skipped"] == {"no_wellbore_id": 1, "wellbore_not_in_backbone": 1}

    first, second, third, deep = rows
    # Klinkenberg is preferred over air; the method travels with the value
    assert (first["kh_md"], first["kh_method"]) == (100.0, "klinkenberg")
    assert (first["kv_md"], first["kv_method"]) == (40.0, "air")
    assert (first["porosity_pct"], first["porosity_method"]) == (22.5, "best_available")
    assert first["lithology"] == "sandstone" and first["lithology_detail"] == "sandstone calcareous"
    assert (first["formation"], first["group_name"]) == ("TOR FM", "SHETLAND GP")
    assert first["sample_id"] == "rp2-2" and first["water_saturation_pct"] == 35.0
    # decimal commas are read; an impossible grain density is dropped, the sample is kept
    assert second["depth_md"] == 2501.5 and second["porosity_pct"] == 18.2
    assert second["porosity_method"] == "plug_horizontal" and second["grain_density_gcc"] is None
    # an impossible porosity is dropped; nitrogen permeability is used when nothing better exists
    assert third["porosity_pct"] is None and (third["kh_md"], third["kh_method"]) == (0.5, "nitrogen")
    assert third["formation"] == "HOD FM"
    # zero permeability is not a measurement; a depth below the tops gets no formation
    assert deep["kh_md"] is None and deep["formation"] is None and deep["group_name"] is None


def test_default_run_loads_only_wellbores_that_have_a_report(tmp_path, kg):
    more = [*ROWS, row(file="99/1 - 2", well="99_1-2", depth=2000.0, npdid=2, kh_air=5.0, phi_best=12.0)]
    path = write_xlsx(tmp_path / "rp.xlsx", more)
    stats = realpore.build_core_samples(path, kg, raw_dir=tmp_path / "raw")
    assert stats["samples_written"] == 4 and stats["wellbores"] == 1          # wellbore 2 has no report
    stats = realpore.build_core_samples(path, kg, wellbores=["99/1-2 A"], raw_dir=tmp_path / "raw")
    assert stats["samples_written"] == 1


def test_poro_perm_fit_recovers_a_known_relationship(tmp_path, kg):
    rows = []
    for well in (1, 2, 3):
        for i in range(12):
            phi = 8.0 + 2.0 * i
            rows.append(row(file="f", well="w", depth=2410.0 + i, npdid=1, lith="sandstone",
                            kh_klink=10 ** (-2.0 + 0.15 * phi) * (1.02 if well == 2 else 1.0), phi_best=phi))
    path = write_xlsx(tmp_path / "rp.xlsx", rows)
    realpore.build_core_samples(path, kg, all_wellbores=True, raw_dir=tmp_path / "raw")
    fits = realpore.build_poro_perm_fits(kg, min_samples=30, min_wellbores=1)
    con = duckdb.connect()
    out = con.execute(f"select formation, lithology, samples, slope, intercept, r2 from read_parquet('{kg}/poro_perm_fit.parquet') order by lithology").fetchall()
    assert fits["fits"] == 2 and [r[1] for r in out] == ["all", "sandstone"]
    formation, _, n, slope, intercept, r2 = out[1]
    assert formation == "TOR FM" and n == 36
    assert math.isclose(slope, 0.15, abs_tol=0.005) and math.isclose(intercept, -2.0, abs_tol=0.05) and r2 > 0.99


def test_fits_need_enough_samples_and_wells(tmp_path, kg):
    path = write_xlsx(tmp_path / "rp.xlsx", ROWS)
    realpore.build_core_samples(path, kg, all_wellbores=True, raw_dir=tmp_path / "raw")
    assert realpore.build_poro_perm_fits(kg)["fits"] == 0
    assert (kg / "poro_perm_fit.parquet").is_file()  # an empty table is still written


def test_missing_backbone_is_a_clear_error(tmp_path):
    path = write_xlsx(tmp_path / "rp.xlsx", ROWS)
    with pytest.raises(FileNotFoundError, match="backbone"):
        realpore.build_core_samples(path, tmp_path, raw_dir=tmp_path / "raw")
