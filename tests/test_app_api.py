import pytest
from starlette.testclient import TestClient

from wellgraph.mcp_server.server import build_server


@pytest.fixture
def client(kg_dir):
    with TestClient(build_server(kg_dir).http_app()) as c:
        yield c


def test_index_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "wellgraph" in r.text.lower()


def test_describe_and_report_wells(client):
    d = client.get("/api/describe").json()
    assert d["counts"]["wellbore"] == 4 and "depth_bases" in d
    wells = client.get("/api/report_wells").json()["results"]
    assert [w["name"] for w in wells] == ["99/1-1", "99/1-2 A", "99/1-3"]
    assert wells[0]["events"] == 2 and wells[0]["lat"] == 60.0


def test_wells_near_a_wellbore(client):
    r = client.get("/api/wells", params={"near": "99/1-1", "radius_km": 20}).json()
    assert [w["name"] for w in r["results"]] == ["99/1-2 A"]
    assert r["origin"]["name"] == "99/1-1" and r["origin"]["lat"] == 60.0


def test_well_detail_has_everything_the_strip_chart_needs(client):
    w = client.get("/api/well", params={"name": "99_1-1"}).json()
    assert w["wellbore"]["name"] == "99/1-1"
    assert [h["hole_diameter_in"] for h in w["hole_sections"]] == [17.5, 12.25]
    assert len(w["formation_tops"]) == 3 and "mud_checks" in w
    assert {e["event_id"] for e in w["events"]} == {"ev-1", "ev-6"}


def test_events_filters_pass_through(client):
    r = client.get("/api/events", params={
        "event_type": "lost_circulation", "formation": "Shetland Group", "hole_size": "12 1/4",
        "near": "99/1-1", "radius_km": 20, "include_reference": "true"}).json()
    assert sorted(e["mud_weight_sg"] for e in r["results"]) == [1.45, 1.5]
    exact = client.get("/api/events", params={"wellbore": "99/1-1", "exact_depth_only": "1"}).json()
    assert [e["event_id"] for e in exact["results"]] == ["ev-1"]


def test_matrix_drilldown_uses_the_same_group_as_the_matrix_before_limiting(client):
    matrix = client.get("/api/matrix").json()
    for group in matrix["rows"]:
        for event_type in matrix["event_types"]:
            response = client.get("/api/events", params={
                "matrix_group": group["group"], "event_type": event_type, "limit": 1,
            })
            assert response.status_code == 200
            result = response.json()
            assert result["total"] == group["counts"].get(event_type, 0)
            for event in result["results"]:
                units = event["formations"]
                assert (units[0] if units else "(no formation)") == group["group"]


def test_offset_and_evidence(client):
    o = client.get("/api/offset", params={"near": "99/1-1", "radius_km": 35}).json()
    assert o["offset_wells"] == 2 and o["summary"]
    ev = client.get("/api/evidence", params={"id": "ev-1", "page_text": "1"}).json()
    assert ev["pdf_page"] == 42 and "Lost returns at 2500 m" in ev["page_text"]


def test_bad_requests_return_json_errors_not_stack_traces(client):
    r = client.get("/api/well", params={"name": "99/1-9"})
    assert r.status_code == 404 and "99/1-1" in r.json()["error"]
    r = client.get("/api/events", params={"event_type": "losses"})
    assert r.status_code == 400 and "lost_circulation" in r.json()["error"]
    assert client.get("/api/well").status_code == 400
    assert client.get("/api/wells", params={"near": "99/1-1", "radius_km": "far"}).status_code == 400


def test_mcp_endpoint_still_works_next_to_the_app(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"accept": "application/json, text/event-stream"})
    assert r.status_code in (200, 400, 406)  # reachable: the MCP layer answers, not a 404


def test_static_files_are_served_and_nothing_outside_the_folder(client):
    r = client.get("/static/geo/quadrants.geojson")
    assert r.status_code == 200 and r.json()["type"] == "FeatureCollection"
    assert len(r.json()["features"]) > 100
    assert client.get("/static/vendor/leaflet.js").status_code == 200
    assert client.get("/static/../routes.py").status_code == 404
    assert client.get("/static/%2e%2e/routes.py").status_code == 404
    assert client.get("/static/geo/missing.geojson").status_code == 404


def test_base_map_files_are_small_enough_to_ship(client):
    total = sum(len(client.get(f"/static/geo/{n}.geojson").content)
                for n in ("land", "quadrants", "blocks", "fields"))
    assert total < 1_000_000


# --- knowledge graph views -----------------------------------------------------------

def test_wellbore_graph_has_typed_nodes_and_edges_between_existing_nodes(client):
    g = client.get("/api/graph", params={"name": "99/1-1"}).json()
    ids = {n["id"] for n in g["nodes"]}
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"Wellbore", "HoleSection", "GeologicalFormation", "DrillingEvent", "Document",
            "Organisation", "Field"} <= kinds
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    relations = {e["relation"] for e in g["edges"]}
    assert {"SECTION_OF", "PENETRATES", "IN_SECTION", "IN_FORMATION", "DESCRIBES",
            "DRILLED_BY", "PART_OF"} <= relations
    loss = next(n for n in g["nodes"] if n["id"] == "ev:ev-1")
    assert loss["event_type"] == "lost_circulation" and loss["depth_basis"] == "stated"
    # the loss sits in the 12 1/4 in section and in the Tor Formation (its most specific unit)
    targets = {e["target"] for e in g["edges"] if e["source"] == "ev:ev-1"}
    assert targets == {"hs:12.25", "fm:TOR FM"}
    assert client.get("/api/graph", params={"name": "nope"}).status_code == 404


def test_ontology_endpoint_returns_rings_and_row_counts(client):
    o = client.get("/api/ontology").json()
    entities = {e["name"]: e for e in o["entities"]}
    assert entities["Wellbore"]["ring"] == "core" and entities["Wellbore"]["rows"] == 4
    assert entities["DrillingEvent"]["rows"] == 6
    assert entities["Seismic3D"]["ring"] == "planned" and entities["Seismic3D"]["rows"] is None
    assert {"core", "observed", "planned"} == {e["ring"] for e in o["entities"]}
    names = set(entities)
    assert all(r["from"] in names and r["to"] in names for r in o["relationships"])


# --- views added in the UI round ------------------------------------------------------

def test_event_matrix_counts_events_by_group_and_type_across_wells(client):
    m = client.get("/api/matrix").json()
    assert set(m["event_types"]) >= {"lost_circulation", "kick", "tight_hole", "pack_off"}
    rows = {r["group"]: r for r in m["rows"]}
    assert rows["SHETLAND GP"]["counts"]["lost_circulation"] == 3
    assert rows["SHETLAND GP"]["wells"] == 3
    assert rows["(no formation)"]["counts"]["kick"] == 1
    assert m["wells_with_events"] == 3


def test_daily_rows_endpoint_is_empty_but_valid_without_the_table(client):
    d = client.get("/api/daily", params={"name": "99/1-1"}).json()
    assert d == {"wellbore": "99/1-1", "rows": []}
    assert client.get("/api/daily").status_code == 400


def test_graph_without_a_name_joins_all_report_wells_through_shared_nodes(client):
    g = client.get("/api/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert g["wellbores"] >= 1 and len(ids) == len(g["nodes"])
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    assert not any(n["id"] == "wb" for n in g["nodes"])
    for n in g["nodes"]:
        shared = n["kind"] in {"GeologicalFormation", "Organisation", "Field"}
        assert (n["wellbore"] is None) == shared


def test_all_wells_graph_becomes_an_overview_when_there_are_many_wells(kg_dir):
    from wellgraph.graph.store import GraphStore
    store = GraphStore(kg_dir)
    full, overview = store.report_graph(), store.report_graph(detail_limit=1)
    assert full["overview"] is False and overview["overview"] is True
    assert overview["wellbores"] == full["wellbores"]
    kinds = {n["kind"] for n in overview["nodes"]}
    assert "Wellbore" in kinds and kinds <= {"Wellbore", "GeologicalFormation", "Organisation", "Field"}
    ids = {n["id"] for n in overview["nodes"]}
    assert overview["edges"] and all(e["source"] in ids and e["target"] in ids for e in overview["edges"])


def test_well_clusters_endpoint(client):
    d = client.get("/api/core/clusters").json()
    assert d["definition"]["max_distance_km"] == 20.0
    assert d["summary"]["clusters"] == len(d["clusters"])
    for c in d["clusters"]:
        assert (c["r"] is None) == (c["reason"] is not None)
        assert c["span_km"] < 20.0


def test_core_neighbours_endpoint_checks_its_input(client):
    assert client.get("/api/core/neighbours", params={"name": "nope"}).status_code == 404
    assert client.get("/api/core/neighbours").status_code == 400
    d = client.get("/api/core/neighbours", params={"name": "99/1-1", "range_km": 10}).json()
    assert d["wellbore"] == "99/1-1" and d["wells"] == [] and d["points"]["well"] == []


def test_event_matrix_follows_field_wellbore_and_unit_filters(client):
    everything = client.get("/api/matrix").json()
    assert everything["filters"] == {} and everything["events"] == sum(r["total"] for r in everything["rows"])
    alfa = client.get("/api/matrix", params={"field": "alfa"}).json()
    assert alfa["wells_with_events"] == 2 and alfa["events"] < everything["events"]
    assert [f["value"] for f in alfa["options"]["field"]] == ["ALFA"]  # the field list is never narrowed
    one = client.get("/api/matrix", params={"wellbore": "99/1-1"}).json()
    assert one["wells_with_events"] == 1 and one["filters"] == {"wellbore": "99/1-1"}
    unit = client.get("/api/matrix", params={"formation_group": "SHETLAND GP", "hole_size": "12.25"}).json()
    assert [r["group"] for r in unit["rows"]] == ["SHETLAND GP"]
    listed = client.get("/api/events", params={"field": "ALFA", "matrix_group": "SHETLAND GP",
                                               "event_type": "lost_circulation"}).json()
    assert listed["total"] == alfa["rows"][0]["counts"]["lost_circulation"] if alfa["rows"][0]["group"] == "SHETLAND GP" else True
    assert {e["wellbore"] for e in listed["results"]} <= {"99/1-1", "99/1-2 A"}
    assert client.get("/api/matrix", params={"wellbore": "nope"}).status_code == 404


def test_map_wells_lists_every_positioned_wellbore_in_columns(client):
    d = client.get("/api/map_wells").json()
    assert d["columns"][:6] == ["name", "type", "operator", "field", "lat", "lon"]
    by_name = {w[0]: w for w in d["wells"]}
    assert {"99/1-1", "99/1-2 A", "99/1-3"} <= set(by_name)
    one = by_name["99/1-1"]
    assert d["types"][one[1]] == "EXPLORATION" and d["operators"][one[2]] == "Alpha Oil"
    assert d["fields"][one[3]] == "ALFA" and one[7] >= 1
    assert sum(one[9]) == sum(client.get("/api/well", params={"name": "99/1-1"}).json()["event_counts"].values())
    assert by_name["99/1-3"][3] == -1  # no field


def test_pressure_window_returns_mud_leak_off_and_events(client):
    d = client.get("/api/pressure", params={"name": "99/1-1", "range_km": 50}).json()
    assert d["wellbore"] == "99/1-1" and d["range_km"] == 50
    for part in (d["well"], d["offset"]):
        assert {"mud", "lot", "events"} <= set(part)
        for r in part["mud"] + part["lot"] + part["events"]:
            assert 0.8 <= r["sg"] <= 2.6 and r["md"] is not None and r["wellbore"]
    assert all(r["wellbore"] == "99/1-1" for r in d["well"]["mud"] + d["well"]["lot"] + d["well"]["events"])
    assert all(r["event_type"] in ("kick", "lost_circulation") for r in d["well"]["events"] + d["offset"]["events"])
    alone = client.get("/api/pressure", params={"name": "99/1-1", "range_km": 0}).json()
    assert alone["offsets"] == [] and alone["offset"]["mud"] == []
    assert client.get("/api/pressure").status_code == 400
    assert client.get("/api/pressure", params={"name": "nope"}).status_code == 404
