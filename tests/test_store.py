import pytest

from wellgraph.graph.store import GraphStore, NotFound, parse_hole_size


@pytest.fixture
def store(kg_dir):
    return GraphStore(kg_dir)


def names(rows):
    return [r["name"] for r in rows]


# --- input normalisation -------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [("12 1/4", 12.25), ('12 1/4"', 12.25), ("12.25", 12.25), (12.25, 12.25),
     ("8 1/2 in", 8.5), ("17 1/2", 17.5), ("26", 26.0), ("12-1/4", 12.25)],
)
def test_parse_hole_size(text, expected):
    assert parse_hole_size(text) == expected


def test_parse_hole_size_rejects_garbage():
    with pytest.raises(ValueError):
        parse_hole_size("big")


@pytest.mark.parametrize("query", ["99/1-2 A", "99/1-2A", "99_1-2_A", "99/1-2 a"])
def test_resolve_wellbore_accepts_spelling_variants(store, query):
    assert store.resolve_wellbore(query)["name"] == "99/1-2 A"


def test_resolve_unknown_wellbore_suggests_candidates(store):
    with pytest.raises(NotFound) as err:
        store.resolve_wellbore("99/1-9")
    assert "99/1-1" in str(err.value)


# --- find_wells ----------------------------------------------------------------------

def test_find_wells_by_block(store):
    out = store.find_wells(block="99/1")
    assert names(out["results"]) == ["99/1-1", "99/1-2 A", "99/1-3"]


def test_find_wells_by_formation_accepts_long_form(store):
    out = store.find_wells(formation="Shetland Group")
    assert names(out["results"]) == ["99/1-1", "99/1-2 A", "99/1-3"]


def test_find_wells_by_operator_is_case_insensitive(store):
    assert names(store.find_wells(operator="beta energy")["results"]) == ["99/1-3", "99/2-1"]


def test_find_wells_near_orders_by_distance_and_excludes_reference(store):
    out = store.find_wells(near_wellbore="99/1-1", radius_km=20)
    assert names(out["results"]) == ["99/1-2 A"]
    assert out["results"][0]["distance_km"] == pytest.approx(10.0, abs=0.2)


def test_find_wells_limit_reports_truncation(store):
    out = store.find_wells(block="99", limit=2)
    assert out["count"] == 2 and out["total"] == 4 and out["truncated"] is True


# --- get_well_summary ----------------------------------------------------------------

def test_well_summary_joins_backbone_and_events(store):
    s = store.well_summary("99/1-1")
    assert s["wellbore"]["operator"] == "Alpha Oil"
    assert [f["formation"] for f in s["formation_tops"]] == ["NORDLAND GP", "SHETLAND GP", "TOR FM"]
    assert [h["hole_diameter_in"] for h in s["hole_sections"]] == [17.5, 12.25]
    assert s["event_counts"] == {"lost_circulation": 1, "pack_off": 1}
    assert s["documents"][0]["document_id"] == "doc-1"


# --- query_events --------------------------------------------------------------------

def test_query_events_demo_question(store):
    """Losses in the Shetland Group in the 12 1/4 in section within 20 km, and mud weight."""
    out = store.query_events(
        event_type="lost_circulation", formation="Shetland Group", hole_size="12 1/4",
        near_wellbore="99/1-1", radius_km=20, include_reference=True,
    )
    got = {(e["wellbore"], e["mud_weight_sg"]) for e in out["results"]}
    assert got == {("99/1-1", 1.45), ("99/1-2 A", 1.50)}
    assert all("evidence" in e and e["evidence"]["page_no"] is not None for e in out["results"])


def test_query_events_formation_matches_child_unit(store):
    out = store.query_events(formation="Tor Formation")
    assert {e["event_id"] for e in out["results"]} == {"ev-1", "ev-2", "ev-6"}


def test_query_events_by_wellbore_and_mud_weight(store):
    out = store.query_events(wellbore="99/1-3", min_mud_weight_sg=1.55)
    assert [e["event_id"] for e in out["results"]] == ["ev-5"]


def test_query_events_rejects_unknown_type(store):
    with pytest.raises(ValueError) as err:
        store.query_events(event_type="losses")
    assert "lost_circulation" in str(err.value)


# --- offset_well_analysis ------------------------------------------------------------

def test_offset_analysis_aggregates_by_type_and_section(store):
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)
    assert out["offset_wells"] == 2  # 99/1-2 A and 99/1-3; reference excluded
    rows = {(r["event_type"], r["hole_diameter_in"]): r for r in out["summary"]}
    losses = rows[("lost_circulation", 12.25)]
    assert losses["events"] == 2 and losses["wells"] == 2
    assert set(losses["event_ids"]) == {"ev-2", "ev-4"}
    assert ("kick", 8.5) in rows and ("tight_hole", 17.5) in rows


def test_offset_analysis_from_coordinates(store):
    out = store.offset_analysis(lat=60.0, lon=2.0, radius_km=5)
    assert out["offset_wells"] == 1  # only 99/1-1 itself is within 5 km of that point


def test_offset_analysis_needs_a_location(store):
    with pytest.raises(ValueError):
        store.offset_analysis(radius_km=10)


# --- get_evidence --------------------------------------------------------------------

def test_evidence_returns_quote_and_citation(store):
    ev = store.evidence("ev-1")
    assert ev["text_span"].startswith("Lost returns at 2500 m")
    assert ev["document"]["title"] == "COMPLETION_REPORT.PDF"
    assert ev["page_no"] == 41 and ev["pdf_page"] == 42
    assert "zenodo" in ev["source"]["url"]


def test_evidence_unknown_id(store):
    with pytest.raises(NotFound):
        store.evidence("ev-999")


# --- describe / degraded graph -------------------------------------------------------

def test_describe_reports_counts_and_coverage(store):
    d = store.describe()
    assert d["counts"]["wellbore"] == 4 and d["counts"]["event"] == 6
    assert d["coverage"]["wellbores_with_events"] == 3
    assert "lost_circulation" in d["event_types"]


def test_backbone_only_graph_still_answers(backbone_only_dir):
    store = GraphStore(backbone_only_dir)
    assert store.describe()["counts"]["event"] == 0
    assert len(store.find_wells(block="99/1")["results"]) == 3
    out = store.query_events(event_type="kick")
    assert out["results"] == [] and "not been loaded" in out["note"]


def test_missing_graph_directory_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        GraphStore(tmp_path / "nope")


# --- depth reference, mud weight kind, page text -------------------------------------

def test_events_expose_depth_ref_and_mud_weight_kind(store):
    rows = {e["event_id"]: e for e in store.query_events(limit=50)["results"]}
    assert rows["ev-5"]["depth_ref"] == "TVD"
    assert rows["ev-4"]["mud_weight_kind"] == "ecd" and rows["ev-4"]["mud_weight_raw"] == "12.7 ppg"


def test_offset_mud_weight_range_ignores_ecd(store):
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)
    losses = {(r["event_type"], r["hole_diameter_in"]): r for r in out["summary"]}[
        ("lost_circulation", 12.25)
    ]
    # ev-2 is mud in hole at 1.50; ev-4 (1.52) is an ECD and must not widen the range
    assert losses["min_mud_weight_sg"] == 1.50 and losses["max_mud_weight_sg"] == 1.50
    assert losses["events"] == 2


def test_query_events_can_filter_by_mud_weight_kind(store):
    out = store.query_events(event_type="lost_circulation", mud_weight_kind="mud_in_hole")
    assert {e["event_id"] for e in out["results"]} == {"ev-1", "ev-2"}
    with pytest.raises(ValueError):
        store.query_events(mud_weight_kind="heavy")


def test_evidence_includes_page_summary_and_optional_page_text(store):
    brief = store.evidence("ev-1")
    assert brief["page_summary"] == "Daily report: losses at 2500 m."
    assert "page_text" not in brief
    full = store.evidence("ev-1", include_page_text=True)
    assert "12 1/4 in hole" in full["page_text"] and full["page_text_truncated"] is False
    assert full["depth_ref"] == "ASSUMED_MD" and full["mud_weight_raw"] == "1.45 sg"


def test_evidence_without_stored_page_still_works(store):
    ev = store.evidence("ev-4", include_page_text=True)
    assert ev["page_summary"] is None and ev["page_text"] is None


def test_describe_lists_depth_refs_and_new_event_types(store):
    d = store.describe()
    assert "TVD" in d["depth_refs"] and "ecd" in d["mud_weight_kinds"]
    assert {"pack_off", "washout", "fishing", "sidetrack"} <= set(d["event_types"])
    assert d["counts"]["page"] == 2


def test_resolve_technical_sidetrack_falls_back_to_parent(store):
    wb = store.resolve_wellbore("99/1-2 AT2")
    assert wb["name"] == "99/1-2 A" and wb["matched_as_sidetrack_of"] == "99/1-2 A"
    assert store.resolve_wellbore("99/1-1 T3")["name"] == "99/1-1"


# --- stated depth versus context depth -----------------------------------------------

def test_context_depth_event_is_found_by_section_and_flagged(store):
    out = store.query_events(event_type="pack_off", hole_size="12 1/4")
    (e,) = out["results"]
    assert e["depth_basis"] == "context_interval" and e["top_md"] is None
    assert (e["context_top_md"], e["context_base_md"]) == (2400.0, 2900.0)


def test_exact_depth_only_filter_excludes_context_events(store):
    all_ids = {e["event_id"] for e in store.query_events(wellbore="99/1-1")["results"]}
    exact = {e["event_id"] for e in store.query_events(wellbore="99/1-1", exact_depth_only=True)["results"]}
    assert all_ids == {"ev-1", "ev-6"} and exact == {"ev-1"}


def test_describe_explains_depth_bases(store):
    bases = store.describe()["depth_bases"]
    assert list(bases) == ["stated", "context_point", "context_interval", "none"]
    assert all(bases.values())


def test_evidence_shows_basis_context_and_joins(store):
    ev = store.evidence("ev-6")
    assert ev["depth_basis"] == "context_interval" and ev["context_base_md"] == 2900.0
    assert ev["hole_diameter_in"] == 12.25 and ev["formations"] == ["SHETLAND GP", "TOR FM"]


def test_mud_weight_source_is_exposed_and_explained(store):
    rows = {e["event_id"]: e for e in store.query_events(limit=50)["results"]}
    assert rows["ev-1"]["mud_weight_source"] == "report" and rows["ev-6"]["mud_weight_source"] is None
    assert list(store.describe()["mud_weight_sources"]) == ["report", "sodir_mud_table"]
    losses = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)["summary"][0]
    assert "mud_weights_from_report" in losses and "mud_weights_from_sodir" in losses
