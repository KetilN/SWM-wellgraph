import pytest

from wellgraph.app.well_clusters import complete_linkage, correlation


def test_complete_linkage_does_not_chain():
    # 0 - 1 - 2 in a line, 15 km apart: 0 and 2 are 30 km apart, so they cannot share a cluster.
    edges = [(0, 1, 15.0), (1, 2, 15.5)]
    assert complete_linkage(4, edges, 20.0) == [[0, 1], [2], [3]]


def test_complete_linkage_merges_when_every_pair_is_close():
    edges = [(0, 1, 5.0), (1, 2, 6.0), (0, 2, 10.0), (2, 3, 4.0), (1, 3, 19.0)]
    # 3 is 4 km from 2 but has no link to 0, so {2, 3} forms and {0, 1} stays apart.
    assert complete_linkage(4, edges, 20.0) == [[0, 1], [2, 3]]


def test_correlation_needs_enough_shared_formations():
    assert correlation({1: {"A": 1.0}})["reason"] == "single_well"
    assert correlation({1: {"A": 1.0}, 2: {"B": 2.0}})["reason"] == "no_shared_formation"
    assert correlation({1: {"A": 1.0, "B": 2.0}, 2: {"A": 1.1, "B": 2.2}})["reason"] == "too_few_shared"


def test_correlation_is_high_when_wells_agree_and_symmetric():
    agree = {w: {"A": 0.0 + w / 100, "B": 1.0 + w / 100, "C": 2.0 - w / 100} for w in (1, 2, 3)}
    out = correlation(agree)
    assert out["pairs"] == 9 and out["formations"] == ["A", "B", "C"]
    assert out["r"] == pytest.approx(1.0, abs=0.01)
    disagree = {1: {"A": 0.0, "B": 1.0, "C": 2.0}, 2: {"A": 2.0, "B": 1.0, "C": 0.0},
                3: {"A": 1.0, "B": 2.0, "C": 0.0}}
    assert correlation(disagree)["r"] < 0
