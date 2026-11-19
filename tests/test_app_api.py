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
