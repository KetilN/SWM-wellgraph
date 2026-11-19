import pytest
import yaml

from wellgraph.graph import schema

VALID_TYPES = {"BIGINT", "INTEGER", "DOUBLE", "VARCHAR", "VARCHAR[]", "DATE", "BOOLEAN"}


def test_contract_is_loaded_from_schema_yaml():
    assert schema.SCHEMA_PATH.name == "schema.yaml" and schema.SCHEMA_PATH.is_file()
    assert list(schema.TABLES) == [
        "wellbore", "formation_top", "hole_section", "document", "page", "event",
    ]
    assert list(schema.EXTRA_TABLES) == ["mud_check", "formation", "daily_report"]


def test_every_column_has_a_valid_type_and_a_description():
    for group in ("tables", "extra_tables"):
        for table, body in schema.SPEC[group].items():
            for column, col in body["columns"].items():
                assert col["type"] in VALID_TYPES, f"{table}.{column}: {col['type']}"
                assert col.get("description"), f"{table}.{column} has no description"


def test_keys_refer_to_real_columns():
    for group in ("tables", "extra_tables"):
        for table, body in schema.SPEC[group].items():
            for column in body.get("key", []):
                assert column in body["columns"], f"{table}: key column {column} missing"


def test_enums_are_exposed_in_file_order_with_descriptions():
    assert schema.EVENT_TYPES[0] == "lost_circulation" and schema.EVENT_TYPES[-1] == "other"
    assert schema.DEPTH_REFS == ["MD", "TVD", "TVDSS", "ASSUMED_MD"]
    assert schema.MUD_WEIGHT_KINDS == ["mud_in_hole", "ecd", "unknown"]
    assert all(schema.EVENT_TYPE_DOCS.values())


def test_units_are_stated_for_depths_and_densities():
    for table, body in schema.SPEC["tables"].items():
        for column, col in body["columns"].items():
            if column.endswith(("_md", "_depth")) or column in {"top_md", "base_md"}:
                assert col.get("unit") == "m", f"{table}.{column}"
            if column.endswith("_sg"):
                assert col.get("unit") == "sg", f"{table}.{column}"
            if column.endswith("_in"):
                assert col.get("unit") == "in", f"{table}.{column}"


def test_broken_schema_files_fail_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        schema.load_schema(tmp_path / "missing.yaml")
    bad = tmp_path / "schema.yaml"
    bad.write_text(yaml.safe_dump({"tables": {"t": {"columns": {"a": {}}}},
                                   "extra_tables": {}, "enums": {}, "source": {}}))
    with pytest.raises(ValueError, match="needs a 'type'"):
        schema.load_schema(bad)
    bad.write_text(yaml.safe_dump({"tables": {}}))
    with pytest.raises(ValueError, match="no 'extra_tables' section"):
        schema.load_schema(bad)
