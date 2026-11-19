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
