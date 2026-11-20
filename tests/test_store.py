import pytest

from wellgraph.graph.store import GraphStore, NotFound, normalise_formation, parse_hole_size


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


def test_offset_coverage_distinguishes_reports_from_matching_events(store):
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=500, hole_size=17.5)
    assert out["coverage"] == {
        "nearby_wellbores": 3,
        "wellbores_with_reports": 2,
        "wellbores_with_extracted_events": 2,
        "wellbores_with_matching_events": 1,
    }
    assert {w["name"]: w["has_report"] for w in out["wells"]} == {
        "99/1-2 A": True, "99/1-3": True, "99/2-1": False,
    }


def test_offset_coverage_is_independent_of_the_well_list_limit(store, monkeypatch):
    monkeypatch.setattr("wellgraph.graph.store.OFFSET_WELL_LIMIT", 1)
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35, hole_size=100)
    assert len(out["wells"]) == 1
    assert out["coverage"]["wellbores_with_reports"] == 2
    assert out["coverage"]["wellbores_with_matching_events"] == 0


def test_offset_md_range_excludes_tvd(store):
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)
    kick = next(row for row in out["summary"] if row["event_type"] == "kick")
    assert kick["events"] == 1
    assert kick["shallowest_md"] is None and kick["deepest_md"] is None


@pytest.mark.parametrize(("kind", "source"), [
    ("unknown", "report"), ("mud_in_hole", "sodir_mud_table"),
])
def test_offset_static_mud_range_excludes_unknown_and_proxy_values(store, kind, source):
    store._con.execute(
        "create or replace temp table alternate_event as select * replace ("
        "case when event_id = 'ev-2' then ? else mud_weight_kind end as mud_weight_kind, "
        "case when event_id = 'ev-2' then ? else mud_weight_source end as mud_weight_source) "
        "from event", [kind, source],
    )
    store._con.execute("create or replace temp view event as select * from alternate_event")
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)
    losses = next(row for row in out["summary"] if row["event_type"] == "lost_circulation")
    assert losses["min_mud_weight_sg"] is None and losses["max_mud_weight_sg"] is None
    assert losses["max_ecd_sg"] == 1.52


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


# --- core data -----------------------------------------------------------------------

@pytest.fixture
def core_store(kg_dir):
    import duckdb

    from wellgraph.graph.schema import EXTRA_TABLES
    con = duckdb.connect()
    cols = ", ".join(f"{c} {t}" for c, t in EXTRA_TABLES["core_sample"].items())
    con.execute(f"create table core_sample ({cols})")
    rows = []
    for well in (1, 2, 3):
        for i in range(12):
            phi = 8.0 + 2.0 * i
            rows.append((f"s{well}-{i}", well, 2450.0 + i, phi, 10 ** (-2.0 + 0.15 * phi), 10 ** (-2.3 + 0.15 * phi),
                         "sandstone" if i % 4 else "siltstone", "TOR FM", "SHETLAND GP",
                         "air" if well == 2 or (well == 3 and i % 2) else "klinkenberg"))
    con.executemany("insert into core_sample (sample_id, wellbore_id, depth_md, porosity_pct, kh_md, kv_md, "
                    "lithology, formation, group_name, kh_method) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"copy core_sample to '{kg_dir}/core_sample.parquet' (format parquet)")
    cols = ", ".join(f"{c} {t}" for c, t in EXTRA_TABLES["poro_perm_fit"].items())
    con.execute(f"create table poro_perm_fit ({cols})")
    con.execute("insert into poro_perm_fit (formation, lithology, samples, wellbores, intercept, slope, r2, residual_sd) "
                "values ('TOR FM', 'all', 36, 3, -2.0, 0.15, 1.0, 0.0)")
    con.execute(f"copy poro_perm_fit to '{kg_dir}/poro_perm_fit.parquet' (format parquet)")
    con.close()
    return GraphStore(kg_dir)


def test_reservoir_quality_summarises_by_formation_with_the_fit(core_store):
    out = core_store.reservoir_quality(formation="Tor Formation")
    (row,) = out["results"]
    assert (row["formation"], row["lithology"], row["samples"], row["wellbores"]) == ("TOR FM", "all", 36, 3)
    assert row["porosity_p50"] == 19.0 and row["kv_over_kh_p50"] == pytest.approx(0.501, abs=0.01)
    assert row["poro_perm_fit"]["slope"] == 0.15 and row["poro_perm_fit"]["samples"] == 36
    assert "geoprovider.no" in out["attribution"]


def test_reservoir_quality_filters_by_wellbore_and_lithology(core_store):
    one = core_store.reservoir_quality(wellbore="99/1-1", lithology="Sandstone")["results"][0]
    assert one["samples"] == 9 and one["wellbores"] == 1 and one["lithology"] == "sandstone"
    assert one["poro_perm_fit"] is None  # no fit stored for that rock type


def test_reservoir_quality_reports_permeability_methods_and_flags_wells(core_store):
    out = core_store.reservoir_quality()
    row = out["results"][0]
    assert (row["kh_klinkenberg"], row["kh_other_method"]) == (18, 18)
    methods = out["permeability_methods"]
    assert methods["preferred"] == "klinkenberg"
    assert {m["method"]: (m["samples"], m["wellbores"]) for m in methods["by_method"]} == {
        "klinkenberg": (18, 2), "air": (18, 2)}
    assert methods["wellbores_without_klinkenberg"] == 1
    assert methods["flagged_wellbores"] == [{
        "wellbore": "99/1-2 A", "samples": 12, "samples_with_kh": 12, "kh_methods": "air",
        "flag": "no Klinkenberg-corrected horizontal permeability"}]
    assert core_store.reservoir_quality(wellbore="99/1-1")["permeability_methods"][
        "flagged_wellbores"] == []


def test_core_samples_and_cross_plot_points(core_store):
    core = core_store.core_samples("99_1-2_A")
    assert core["count"] == 12 and core["samples"][0]["depth_md"] == 2450.0
    plot = core_store.poro_perm("Tor Fm")
    assert plot["pairs"] == 36 and len(plot["points"]) == 36 and plot["fit"]["slope"] == 0.15
    assert plot["wellbores"] == 3 and plot["porosity_p50"] is not None and plot["kh_p50_md"] is not None
    assert plot["lithologies"] == ["sandstone", "siltstone"]
    assert {row["kh_method"] for row in plot["points"]} == {"klinkenberg", "air"}
    assert plot["methods"] == [{"method": "air", "pairs": 18}, {"method": "klinkenberg", "pairs": 18}]
    corrected = core_store.poro_perm("Tor Fm", kh_method="klinkenberg")
    assert corrected["pairs"] == 18 and corrected["fit"] is None
    assert {row["kh_method"] for row in corrected["points"]} == {"klinkenberg"}
    assert corrected["methods"] == plot["methods"]
    assert {row["name"]: row["has_core"] for row in core_store.report_wells()} == {
        "99/1-1": True, "99/1-2 A": True, "99/1-3": True,
    }
    assert core_store.poro_perm("Tor Fm", "siltstone")["pairs"] == 9
    assert core_store.poro_perm("Hod Fm") == {
        "formation": "HOD FM", "lithology": "all", "pairs": 0, "wellbores": 0,
        "porosity_p50": None, "kh_p50_md": None, "lithologies": [], "methods": [],
        "points": [], "fit": None,
    }


def test_evidence_for_an_unknown_event_is_a_clear_not_found(store):
    with pytest.raises(NotFound, match="No event with id"):
        store.evidence("ev-doesnotexist", include_page_text=True)


def test_offset_analysis_caps_the_lists_but_not_the_counts(store, monkeypatch):
    monkeypatch.setattr("wellgraph.graph.store.GROUP_ID_LIMIT", 1)
    out = store.offset_analysis(near_wellbore="99/1-1", radius_km=35)
    assert out["summary"] and all(len(g["event_ids"]) <= 1 for g in out["summary"])
    assert sum(g["events"] for g in out["summary"]) > len(out["summary"]) - 1
    assert "lists_capped" in out


def test_unknown_formation_gives_a_note_and_dotted_spelling_matches(store):
    out = store.query_events(formation="Zorglub Fm.")
    assert out["total"] == 0 and "Zorglub".upper() in out["note"]
    assert normalise_formation("Draupne Fm.") == normalise_formation("Draupne Formation")


def test_core_filter_options_narrow_with_the_other_filters(core_store):
    from wellgraph.app import core_crossplot

    everything = core_crossplot.options(core_store)
    assert everything["field"] == ["(unassigned)", "ALFA"]
    assert everything["kh_method"] == ["air", "klinkenberg"]
    # 99/1-3 has no field and its plugs alternate between methods; ALFA holds wells 1 and 2
    narrowed = core_crossplot.options(core_store, {"kh_method": "klinkenberg", "field": "ALFA"})
    assert narrowed["field"] == ["(unassigned)", "ALFA"]  # the field list is never narrowed
    assert narrowed["kh_method"] == ["air", "klinkenberg"]  # a filter never narrows its own list
    assert core_crossplot.options(core_store, {"lithology": "chalk"})["formation"] == []


def test_core_neighbourhood_lists_cored_wellbores_in_range(core_store):
    from wellgraph.app.well_clusters import neighbourhood

    near = neighbourhood(core_store, "99/1-1", 0.5)
    assert [w["name"] for w in near["wells"]] == ["99/1-1"] and near["wells"][0]["selected"]
    wide = neighbourhood(core_store, "99/1-1", 200)
    assert wide["wells"][0]["name"] == "99/1-1" and wide["wells"][0]["distance_km"] == 0
    assert len(wide["wells"]) == 3 and wide["well_points_total"] == 12
    assert wide["neighbour_points_total"] == 24 == len(wide["points"]["neighbours"])
    (tor,) = wide["formations"]
    assert (tor["formation"], tor["well_samples"], tor["neighbour_wellbores"], tor["neighbour_samples"]) == ("TOR FM", 12, 2, 24)
    for phi, kh, formation, wellbore, depth in wide["points"]["well"]:
        assert kh > 0 and formation == 0 and wide["point_wellbores"][wellbore] == "99/1-1" and depth >= 2450
