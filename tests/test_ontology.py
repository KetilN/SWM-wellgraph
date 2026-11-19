from pathlib import Path

import yaml

from wellgraph.graph.schema import EXTRA_TABLES, TABLES

ONTOLOGY = yaml.safe_load((Path(__file__).parent.parent / "ontology.yaml").read_text())
ENTITIES = ONTOLOGY["entities"]
RELATIONSHIPS = ONTOLOGY["relationships"]
KNOWN_TABLES = set(TABLES) | set(EXTRA_TABLES)


def test_every_entity_has_ring_status_kind_and_description():
    for name, e in ENTITIES.items():
        assert e["ring"] in {"core", "observed", "planned"}, name
        assert e["status"] in {"populated", "defined", "observed", "planned"}, name
        assert e["osdu_kind"] == "local" or "--" in e["osdu_kind"], name
        assert e.get("description"), name


def test_core_entities_point_at_real_tables():
    for name, e in ENTITIES.items():
        if e["ring"] == "core":
            assert "table" in e or "stored_as" in e, name
            if "table" in e:
                assert e["table"] in KNOWN_TABLES, f"{name}: unknown table {e['table']}"
        else:
            assert e["status"] == e["ring"] and "table" not in e, name


def test_every_contract_table_is_covered_by_the_ontology():
    used = {e["table"] for e in ENTITIES.values() if "table" in e}
    used |= {r["table"] for r in RELATIONSHIPS.values() if "table" in r}
    assert set(TABLES) <= used, f"tables with no ontology entry: {set(TABLES) - used}"


def test_relationships_connect_defined_entities():
    for name, r in RELATIONSHIPS.items():
        assert r["from"] in ENTITIES and r["to"] in ENTITIES, name
        assert r["ring"] in {"core", "observed", "planned"}, name
        if r["ring"] == "core":
            assert ENTITIES[r["from"]]["ring"] == ENTITIES[r["to"]]["ring"] == "core", name
            assert "table" in r or "stored_as" in r, name


def test_stored_as_columns_exist_in_the_contract():
    all_tables = {**TABLES, **EXTRA_TABLES}
    for name, r in RELATIONSHIPS.items():
        for ref in str(r.get("stored_as", "")).split(" + "):
            if "." in ref:
                table, column = ref.strip().split(".")
                assert column in all_tables[table], f"{name}: {table}.{column} not in contract"


def test_observed_entities_say_where_they_were_seen_and_what_they_would_enable():
    observed = {n: e for n, e in ENTITIES.items() if e["ring"] == "observed"}
    assert len(observed) >= 10
    for name, e in observed.items():
        assert e["seen_in"]["pages"] > 0 and 1 <= e["seen_in"]["reports"] <= 7, name
        assert e.get("would_enable"), name


def test_each_ring_is_connected_to_the_wellbore_through_relationships():
    # every non-core entity must be reachable from some relationship, or it is an orphan
    linked = {r["from"] for r in RELATIONSHIPS.values()} | {r["to"] for r in RELATIONSHIPS.values()}
    orphans = [n for n in ENTITIES if n not in linked]
    assert orphans == [], orphans
