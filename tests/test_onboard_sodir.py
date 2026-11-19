"""SODIR FactPages backbone: CSV -> Parquet transforms.

Fixtures in tests/fixtures/sodir are invented (block 99/x does not exist) but copy the
quirks of the real exports: UTF-8 BOM, DD.MM.YYYY dates, padded strings, 0 meaning
"not set", hole sizes as fraction strings.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import duckdb
import httpx
import pandas as pd
import pytest

from wellgraph.graph.schema import TABLES
from wellgraph.graph.store import GraphStore
from wellgraph.onboard import sodir

RAW = Path(__file__).parent / "fixtures" / "sodir"


def by(df, column, value):
    rows = df[df[column] == value]
    assert len(rows) == 1, f"{column}={value!r}: {len(rows)} rows"
    return rows.iloc[0]


def isnull(value):
    return value is None or bool(pd.isna(value))


# --- parsing helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 1/4", 12.25),
        ("8  1/2", 8.5),
        ("36    ", 36.0),
        ("13 3/8", 13.375),
        ("15.95", 15.95),
        ("5/8", 0.625),
        ("", None),
        ("   ", None),
        (None, None),
        ("0", None),
        ("12 174", None),  # typo in the real data: not guessed
        ("42x36", None),
        ("9 5/9", None),  # impossible denominator: a typo, not a size
    ],
)
def test_parse_diameter(text, expected):
    assert sodir.parse_diameter(text) == expected


# --- wellbore ------------------------------------------------------------------------


def test_wellbore_union_and_types():
    df = sodir.build_wellbore(RAW)
    assert list(df.columns) == list(TABLES["wellbore"])
    assert len(df) == 6
    assert df["wellbore_id"].is_unique
    assert dict(df["wellbore_type"].value_counts()) == {
        "EXPLORATION": 3, "DEVELOPMENT": 2, "OTHER": 1,
    }
    w = by(df, "wellbore_id", 101)
    assert w["name"] == "99/1-1"
    assert w["well_name"] == "99/1-1"
    assert w["wellbore_type"] == "EXPLORATION"
    assert w["purpose"] == "WILDCAT"
    assert w["operator"] == "Alpha Oil AS"
    assert w["field"] == "ALFA"
    assert w["drilling_facility"] == "RIG ONE"
    assert w["entry_date"] == dt.date(1989, 3, 20)
    assert w["completion_date"] == dt.date(1989, 6, 4)
    assert w["total_depth_md"] == 3574.0
    assert w["water_depth"] == 72.0
    assert w["lat"] == 60.0 and w["lon"] == 2.0
    assert w["factpage_url"] == "https://example.test/exploration/101"


def test_wellbore_blanks_become_null():
    df = sodir.build_wellbore(RAW)
    w = by(df, "name", "99/1-3")
    for column in ("purpose", "field", "drilling_facility", "entry_date",
                   "completion_date", "total_depth_md"):
        assert isnull(w[column]), column
    assert isnull(w["water_depth"])  # 0.0 in SODIR means "not set"
    assert w["lat"] == 60.27
    dev = by(df, "name", "99/1-A-2 AH")
    assert dev["wellbore_type"] == "DEVELOPMENT"
    assert dev["purpose"] == "INJECTION"  # padding stripped
    assert isnull(dev["completion_date"])
    other = by(df, "name", "99/1-U-1")
    assert other["wellbore_type"] == "OTHER"
    assert isnull(other["field"])  # the "other" export has no field column


def test_wellbore_without_optional_other_table(tmp_path):
    for path in RAW.glob("*.csv"):
        if path.stem != "wellbore_other_all":
            shutil.copy(path, tmp_path / path.name)
    df = sodir.build_wellbore(tmp_path)
    assert len(df) == 5
    assert "OTHER" not in set(df["wellbore_type"])


def test_wellbore_zero_coordinates_become_null(tmp_path):
    for path in RAW.glob("*.csv"):
        shutil.copy(path, tmp_path / path.name)
    other = tmp_path / "wellbore_other_all.csv"
    text = other.read_text(encoding="utf-8-sig").replace("60.0200,2.0200", "0.0000,0.0000")
    other.write_text(text, encoding="utf-8-sig")
    w = by(sodir.build_wellbore(tmp_path), "name", "99/1-U-1")
    assert isnull(w["lat"]) and isnull(w["lon"])


def test_wellbore_duplicate_id_is_an_error(tmp_path):
    for path in RAW.glob("*.csv"):
        shutil.copy(path, tmp_path / path.name)
    dev = tmp_path / "wellbore_development_all.csv"
    text = dev.read_text(encoding="utf-8-sig").replace(",201,", ",101,")
    dev.write_text(text, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="101"):
        sodir.build_wellbore(tmp_path)


# --- formation tops ------------------------------------------------------------------


def test_formation_top():
    df = sodir.build_formation_top(RAW)
    assert list(df.columns) == list(TABLES["formation_top"])
    assert len(df) == 5
    tor = by(df, "formation", "TOR FM")
    assert tor["wellbore_id"] == 101
    assert tor["level"] == "FORMATION"
    assert tor["parent"] == "SHETLAND GP"
    assert tor["top_md"] == 2400.0 and tor["base_md"] == 2650.5
    group = df[(df["wellbore_id"] == 101) & (df["formation"] == "NORDLAND GP")].iloc[0]
    assert isnull(group["parent"])


# --- hole sections -------------------------------------------------------------------


def test_hole_section():
    df = sodir.build_hole_section(RAW)
    assert list(df.columns) == list(TABLES["hole_section"])
    assert len(df) == 6  # one row per casing row
    w = df[df["wellbore_id"] == 101].sort_values("casing_depth_md").reset_index(drop=True)

    conductor = w.iloc[0]
    assert conductor["casing_type"] == "CONDUCTOR"
    assert conductor["casing_diameter_in"] == 30.0
    assert conductor["hole_diameter_in"] == 36.0
    assert conductor["casing_depth_md"] == 150.0
    assert isnull(conductor["base_md"])  # hole depth 0.0 -> not set
    assert isnull(conductor["lot_mud_density_sg"])  # 0.00 -> no test

    interm = w.iloc[1]
    assert interm["casing_diameter_in"] == 13.375
    assert interm["hole_diameter_in"] == 17.5
    assert interm["base_md"] == 1200.0
    assert interm["lot_mud_density_sg"] == 1.55

    typo = w.iloc[2]
    assert typo["casing_diameter_in"] == 9.625
    assert isnull(typo["hole_diameter_in"])  # '12 174'
    assert typo["lot_mud_density_sg"] == 1.78

    open_hole = w.iloc[3]
    assert open_hole["casing_type"] == "OPEN HOLE"
    assert isnull(open_hole["casing_diameter_in"])
    assert open_hole["hole_diameter_in"] == 8.5

    liner = by(df, "wellbore_id", 102)
    assert liner["hole_diameter_in"] == 15.95
    assert liner["casing_diameter_in"] == 7.0


# --- extra tables --------------------------------------------------------------------


def test_mud_check():
    df = sodir.build_mud_check(RAW)
    assert list(df.columns) == list(sodir.EXTRA_TABLES["mud_check"])
    assert len(df) == 4
    spud = by(df, "md", 171.0)
    assert spud["wellbore_id"] == 101
    assert spud["mud_weight_sg"] == 1.04
    assert isnull(spud["viscosity_mpas"])  # 0.0 -> not measured
    assert isnull(spud["yield_point_pa"])  # 0.0 with no viscosity -> not measured
    assert spud["mud_type"] == "WATER BASED"
    assert spud["date_measured"] == dt.date(1989, 3, 29)
    mid = by(df, "md", 2500.0)
    assert mid["viscosity_mpas"] == 23.0 and mid["yield_point_pa"] == 9.5
    assert mid["mud_type"] == "KCl/Polymer"  # free text kept as SODIR wrote it
    assert isnull(mid["date_measured"])
    dummy = by(df, "md", 3000.0)
    assert isnull(dummy["mud_weight_sg"])
    assert isnull(dummy["mud_type"])  # 'DUMMY' placeholder
    assert dummy["yield_point_pa"] == 0.0  # a real zero: viscosity was measured


def test_formation():
    df = sodir.build_formation(RAW)
    assert list(df.columns) == list(sodir.EXTRA_TABLES["formation"])
    assert len(df) == 3
    tor = by(df, "name", "TOR FM")
    assert tor["formation_id"] == 170
    assert tor["level"] == "FORMATION"
    assert tor["parent"] == "SHETLAND GP"
    assert tor["parent_id"] == 143
    assert isnull(by(df, "name", "NORDLAND GP")["parent"])


# --- end to end ----------------------------------------------------------------------


def test_build_backbone_matches_schema_and_loads(tmp_path):
    report = sodir.build_backbone(RAW, tmp_path)
    assert report["rows"] == {
        "wellbore": 6, "formation_top": 4, "hole_section": 5, "mud_check": 4, "formation": 3,
    }
    # rows for wellbore 999, which is in no wellbore table, are dropped and counted
    assert report["dropped_unknown_wellbore"] == {
        "formation_top": 1, "hole_section": 1, "mud_check": 0,
    }
    con = duckdb.connect()
    backbone_extras = {t: sodir.EXTRA_TABLES[t] for t in ("mud_check", "formation")}
    expected = {**{t: TABLES[t] for t in sodir.SCHEMA_TABLES}, **backbone_extras}
    for table, columns in expected.items():
        rows = con.execute(
            f"describe select * from read_parquet('{tmp_path}/{table}.parquet')"
        ).fetchall()
        assert {r[0]: r[1] for r in rows} == columns, table
        assert [r[0] for r in rows] == list(columns), table

    store = GraphStore(tmp_path)
    assert store.describe()["counts"]["wellbore"] == 6
    summary = store.well_summary("99_1-1")
    assert summary["wellbore"]["entry_date"] == dt.date(1989, 3, 20)
    assert [t["formation"] for t in summary["formation_tops"]] == [
        "NORDLAND GP", "SHETLAND GP", "TOR FM",
    ]
    assert len(summary["hole_sections"]) == 4
    found = store.find_wells(formation="Shetland Group")
    assert [r["name"] for r in found["results"]] == ["99/1-1"]


def test_build_backbone_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    sodir.build_backbone(RAW, a)
    sodir.build_backbone(RAW, b)
    con = duckdb.connect()
    for table in ("wellbore", "formation_top", "hole_section"):
        rows = [
            con.execute(f"select * from read_parquet('{d}/{table}.parquet')").fetchall()
            for d in (a, b)
        ]
        assert rows[0] == rows[1]


def test_missing_required_table_names_the_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="wellbore_exploration_all.csv"):
        sodir.build_wellbore(tmp_path)


# --- download ------------------------------------------------------------------------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_caches_and_never_refetches(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"\xef\xbb\xbfa,b\r\n1,2\r\n")

    with _client(handler) as client:
        paths = sodir.download_tables(tmp_path, tables=["wellbore_mud"], client=client)
        assert paths["wellbore_mud"].read_bytes().endswith(b"1,2\r\n")
        assert "tableview/wellbore_mud&" in calls[0] and "Top100=false" in calls[0]
        sodir.download_tables(tmp_path, tables=["wellbore_mud"], client=client)
        assert len(calls) == 1  # cached
        sodir.download_tables(tmp_path, tables=["wellbore_mud"], client=client, force=True)
        assert len(calls) == 2
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_html_error_page(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"<html><head><title>Error</title></head></html>")

    with _client(handler) as client, pytest.raises(RuntimeError, match="wellbore_mud"):
        sodir.download_tables(tmp_path, tables=["wellbore_mud"], client=client)
    assert not (tmp_path / "wellbore_mud.csv").exists()


def test_download_optional_table_failure_is_skipped(tmp_path):
    def handler(request):
        return httpx.Response(500)

    with _client(handler) as client:
        paths = sodir.download_tables(
            tmp_path, tables=["wellbore_other_all"], client=client
        )
    assert paths == {}
