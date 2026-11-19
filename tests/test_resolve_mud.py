from wellgraph.resolve.mud import mud_weight_at

CHECKS = [
    {"md": 1000.0, "mud_weight_sg": 1.10},
    {"md": 2000.0, "mud_weight_sg": 1.35},
    {"md": 2500.0, "mud_weight_sg": 1.45},
    {"md": 2600.0, "mud_weight_sg": 12.04},  # ppg typed as sg in the source: ignored
    {"md": 2700.0, "mud_weight_sg": None},
]


def test_nearest_measurement_and_its_distance():
    assert mud_weight_at(CHECKS, 2100.0) == (1.35, 100.0)
    assert mud_weight_at(CHECKS, 2480.0) == (1.45, 20.0)


def test_implausible_and_missing_values_are_skipped():
    assert mud_weight_at(CHECKS, 2610.0) == (1.45, 110.0)


def test_nothing_within_reach_gives_none():
    assert mud_weight_at(CHECKS, 1500.0) is None  # 500 m from either neighbour
    assert mud_weight_at(CHECKS, 1500.0, max_gap_m=600.0) == (1.1, 500.0)
    assert mud_weight_at(CHECKS, None) is None
    assert mud_weight_at([], 2000.0) is None and mud_weight_at(None, 2000.0) is None
