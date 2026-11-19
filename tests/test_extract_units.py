"""Unit parsing: every form here was seen in real report text unless marked synthetic."""

from __future__ import annotations

import pytest

from wellgraph.extract.units import (
    PPG_PER_SG,
    find_measurements,
    parse_depth_m,
    parse_hole_size_in,
    parse_mud_weight,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.35 S.G.", 1.35),
        ("1,35 sg", 1.35),
        ("1.65sg EMW", 1.65),
        ("1,28 SG SILDRILL MUD", 1.28),
        ("1.15 s.g. gel-mud", 1.15),
        ("1.1 SG", 1.1),
        ("MUD WEIGHT1.33 SG", 1.33),
        ("1.20 g/cm3", 1.20),
        ("1.20 g/cm³", 1.20),
        ("1.20 g/cc", 1.20),
        ("1.45 kg/l", 1.45),
        ("1.30 r.d.", 1.30),
        ("1350 kg/m3", 1.35),
        ("1 350 kg/m3", 1.35),
        ("1,350 kg/m3", 1.35),
        ("10.5 ppg", 10.5 / PPG_PER_SG),
        ("16 ppg", 16 / PPG_PER_SG),
        ("11.8 ppg Polymer", 11.8 / PPG_PER_SG),
        ("9.2 p.p.g.", 9.2 / PPG_PER_SG),
        ("13.15 ppg EMW", 13.15 / PPG_PER_SG),
        ("10,5 lb/gal", 10.5 / PPG_PER_SG),
        ("1.03 (8.6)", 1.03),
        ("| 1,03 (8,6) |", 1.03),
        ("raised mudweight to 1.35 S.G. and reamed", 1.35),
    ],
)
def test_parse_mud_weight(text, expected):
    assert parse_mud_weight(text) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no numbers here",
        "110 kg/m3",  # KCl concentration, not a density
        "160-180 kg/m3",
        "3.5 sg",  # implausible
        "0.5 sg",
        "30 ppg",
        "2 ppg",
        "20 ppb mica",  # pounds per barrel, not ppg
        "1.35",  # bare number: no unit, no claim
        "2926 m",
        "1.03 (12.0)",  # the bracket is not the ppg equivalent of 1.03
        "5 sgs",
        "345 bar",
    ],
)
def test_parse_mud_weight_rejects(text):
    assert parse_mud_weight(text) is None


def test_mud_weight_range_gives_both_ends():
    found = [m.value for m in find_measurements("because of the 1.46-1.50 SG mud weight")]
    assert found == pytest.approx([1.46, 1.50])


def test_sg_with_ppg_in_brackets_gives_two_consistent_values():
    ms = [m for m in find_measurements("pressures increased to 1.85 sg (15.4 ppg EMW) at the top")]
    assert [m.kind for m in ms] == ["mud_weight", "mud_weight"]
    assert ms[0].value == pytest.approx(1.85)
    assert ms[1].value == pytest.approx(1.845, abs=0.01)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2926 m", 2926.0),
        ("2926m", 2926.0),
        ("1215M", 1215.0),
        ("1178,5 M", 1178.5),
        ("2,926.0 m", 2926.0),
        ("2,926 m", 2926.0),
        ("3 150 mMD", 3150.0),
        ("2536mMD", 2536.0),
        ("4190.3 mRKB", 4190.3),
        ("2926 m RKB", 2926.0),
        ("3920m MD", 3920.0),
        ("2083.0 m TVD", 2083.0),
        ("1250 meters", 1250.0),
        ("1250 metres", 1250.0),
        ("9600 ft", 9600 * 0.3048),
        ("9,600 ft", 9600 * 0.3048),
        ("9600 feet", 9600 * 0.3048),
        ("9600 ft MD", 9600 * 0.3048),
        ("Drilled to 2926 m and raised mudweight", 2926.0),
        ("2309.7 m RKB.", 2309.7),
    ],
)
def test_parse_depth_m(text, expected):
    assert parse_depth_m(text) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "30 m3",  # volume
        "97 m3 1.40 sg mud",
        "13.2 m/hr",  # rate
        "0.239 m3/min",
        "30 mm",
        "5 min",
        "10 MT max",  # metric tons
        "1.35 sg",
        "25000 m",  # deeper than any well
        "12 1/4\"",
        "1500 psi",
    ],
)
def test_parse_depth_rejects(text):
    assert parse_depth_m(text) is None


def test_depth_ranges_give_both_ends():
    assert [m.value for m in find_measurements("Drilled 36\" hole from 186 to 260 m.")
            if m.kind == "depth"] == [186.0, 260.0]
    assert [m.value for m in find_measurements("2083.0 - 2159.0 m RKB")] == [2083.0, 2159.0]
    assert [m.value for m in find_measurements("Cut core no. 1, 2062-2080m.")] == [2062.0, 2080.0]
    assert [m.value for m in find_measurements("drilled f/474m to 659m")] == [474.0, 659.0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('12 1/4"', 12.25),
        ('12-1/4"', 12.25),
        ("12¼", 12.25),
        ('12¼"', 12.25),
        ("12.25 in", 12.25),
        ("12.25in Section", 12.25),
        ('17 1/2" hole', 17.5),
        ("8½", 8.5),
        ('8 1/2" hole to 4007m', 8.5),
        ('9 5/8" casing', 9.625),
        ('13 3/8"', 13.375),
        ('26"', 26.0),
        ("26 inch", 26.0),
        ('36" hole', 36.0),
        ('17\\frac{1}{2}"', 17.5),
        ("12 1/4 in", 12.25),
        ("8 1/2” hole", 8.5),
    ],
)
def test_parse_hole_size_in(text, expected):
    assert parse_hole_size_in(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    ["", "2926 m", "1.35 sg", "ran 7 in hole", '90"', "0.5 in", "1/4 riser volume", "page 12"],
)
def test_parse_hole_size_rejects(text):
    assert parse_hole_size_in(text) is None


def test_find_measurements_offsets_and_kinds():
    text = 'Drilled 12 1/4" hole to 2926 m and raised mudweight to 1.35 S.G. Had tight hole.'
    ms = find_measurements(text)
    assert [(m.kind, m.value) for m in ms] == [
        ("hole_size", 12.25),
        ("depth", 2926.0),
        ("mud_weight", 1.35),
    ]
    for m in ms:
        assert text[m.start : m.end] == m.raw
    assert ms[0].raw == '12 1/4"'
    assert ms[1].raw == "2926 m"
    assert ms[2].raw == "1.35 S.G."
    assert [m.start for m in ms] == sorted(m.start for m in ms)


def test_find_measurements_daily_table_row():
    row = "| 0700 | 0830 | 1,50 | 1220,0 | DISPLACING TO 1,28 SG MUD. REAMED FROM 1215M TO 1220M. |"
    got = [(m.kind, m.value) for m in find_measurements(row)]
    assert got == [("mud_weight", 1.28), ("depth", 1215.0), ("depth", 1220.0)]


def test_feet_unit_is_recorded():
    (m,) = find_measurements("TD at 9600 ft")
    assert m.unit == "ft"
    assert m.value == pytest.approx(2926.08)


def test_hole_size_fraction_is_not_read_as_depth_or_mud_weight():
    kinds = {m.kind for m in find_measurements('13 3/8" casing shoe at 1394m')}
    assert kinds == {"hole_size", "depth"}


def test_per_hundred_feet_is_not_a_depth():
    assert find_measurements("yield point 13-14 lbs/100ft") == []
    assert find_measurements("set on 3.4° MTF/100 ft to guarantee") == []
    assert find_measurements("torque 35,000 ft.lbs") == []
    assert [m.value for m in find_measurements("Perforate t/2096.4m t/2107.4m")] == [2096.4, 2107.4]


def test_coordinate_seconds_are_not_a_hole_size():
    assert parse_hole_size_in("58° 21' 30\" N") is None
