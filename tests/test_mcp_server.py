import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from wellgraph.mcp_server.server import build_server

pytestmark = pytest.mark.anyio  # anyio's pytest plugin ships with FastMCP's dependencies


@pytest.fixture
def anyio_backend():
    return "asyncio"


TOOLS = {
    "describe_graph", "find_wells", "get_well_summary", "query_events",
    "offset_well_analysis", "get_evidence", "reservoir_quality",
}


@pytest.fixture
def server(kg_dir):
    return build_server(kg_dir)


async def call(server, tool, **args):
    async with Client(server) as client:
        return (await client.call_tool(tool, args)).data


async def test_lists_exactly_the_expected_tools_with_descriptions(server):
    async with Client(server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == TOOLS
    assert all(t.description and len(t.description) > 40 for t in tools)
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


async def test_server_instructions_tell_the_agent_where_to_start(server):
    async with Client(server) as client:
        instructions = client.instructions
    assert "describe_graph" in instructions and "get_evidence" in instructions


async def test_describe_graph(server):
    d = await call(server, "describe_graph")
    assert d["counts"]["wellbore"] == 4
    assert d["example_questions"] and d["attribution"]


async def test_demo_question_end_to_end(server):
    out = await call(
        server, "query_events",
        event_type="lost_circulation", formation="Shetland Group", hole_size="12 1/4",
        near_wellbore="99/1-1", radius_km=20, include_reference=True,
    )
    assert sorted(e["mud_weight_sg"] for e in out["results"]) == [1.45, 1.50]
    ev = await call(server, "get_evidence", event_id=out["results"][0]["event_id"])
    assert ev["text_span"] and ev["pdf_page"] >= 1


async def test_find_wells_and_summary(server):
    wells = await call(server, "find_wells", formation="Tor Fm")
    assert [w["name"] for w in wells["results"]] == ["99/1-1", "99/1-2 A"]
    summary = await call(server, "get_well_summary", wellbore="99_1-2_A")
    assert summary["wellbore"]["name"] == "99/1-2 A"


async def test_offset_well_analysis(server):
    out = await call(server, "offset_well_analysis", near_wellbore="99/1-1", radius_km=35)
    assert out["offset_wells"] == 2 and out["summary"]


async def test_bad_input_becomes_a_helpful_tool_error(server):
    with pytest.raises(ToolError) as err:
        await call(server, "get_well_summary", wellbore="99/1-9")
    assert "99/1-1" in str(err.value)  # suggestions, not a stack trace
    with pytest.raises(ToolError):
        await call(server, "query_events", event_type="losses")


async def test_limit_is_capped(server):
    out = await call(server, "find_wells", block="99", limit=100000)
    assert out["count"] <= 200


async def test_reservoir_quality_reports_no_core_data_cleanly(server):
    out = await call(server, "reservoir_quality", formation="Tor Formation")
    assert out["results"] == [] and "not been loaded" in out["note"]
    assert "geoprovider.no" in out["attribution"]
