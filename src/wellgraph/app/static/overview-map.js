// Overview map: every SODIR wellbore with a position, screened and coloured by what the user picks.
const OV_GREY = "#b4bec6";
const OV_RAMP = ["#c9ddec", "#8fb6d6", "#4f87ba", "#1f4e79"];
const OV_DECADES = [[1980, "before 1980", "#d6e3ee"], [1990, "1980s", "#b0cbe0"], [2000, "1990s", "#86add0"],
  [2010, "2000s", "#5a8cbd"], [2020, "2010s", "#356ba3"], [Infinity, "2020s", "#173f6b"]];
const OV_TYPES = {EXPLORATION: "#16549a", DEVELOPMENT: "#a86800", OTHER: "#6b7780"};
const OV_REGIONS = {north: [[56, 1.3], [62, 7.5]], norwegian: [[62, 1], [69.5, 14.5]], barents: [[69.5, 14], [76.5, 37]]};
const OV_CLASSES = [[1, 5], [6, 20], [21, 60], [61, Infinity]];
const OV_SCREENS = ["operator", "field", "type", "data", "event"];
let ov;  // {wells, lookups, map, layers...}

const ovTitle = name => name.toLowerCase().replace(/(^|[\s/-])(\p{L})/gu, (m, a, b) => a + b.toUpperCase());
const ovClass = n => OV_CLASSES.findIndex(([lo, hi]) => n >= lo && n <= hi);
const ovClassText = ([lo, hi]) => hi === Infinity ? `${lo} or more` : `${lo}–${hi}`;
const ovBlock = name => (name.match(/^\d+\/\d+/) || [""])[0];

function ovEvents(w) {
  if (!w.events) return 0;
  const type = $("ov-event").value;
  return type ? w.events[ov.eventTypes.indexOf(type)] : w.total;
}
function ovMatches(w) {
  const v = Object.fromEntries(OV_SCREENS.map(k => [k, $("ov-" + k).value]));
  if (v.operator && w.operator !== v.operator) return false;
  if (v.field && w.field !== v.field) return false;
  if (v.type && w.type !== v.type) return false;
  if (v.event && !ovEvents(w)) return false;
  if (v.data === "report" && !w.documents) return false;
  if (v.data === "events" && !w.total) return false;
  if (v.data === "core" && !w.core) return false;
  if (v.data === "both" && !(w.total && w.core)) return false;
  if (v.data === "none" && (w.documents || w.core)) return false;
  return true;
}
function ovColour(w) {
  const mode = $("ov-colour").value;
  if (mode === "type") return OV_TYPES[w.type] || OV_GREY;
  if (mode === "decade") return w.year ? OV_DECADES.find(([before]) => w.year < before)[2] : OV_GREY;
  if (mode === "events") { const n = ovEvents(w); return n ? OV_RAMP[ovClass(n)] : "#e3e8ec"; }
  return w.total ? css("--signal") : w.documents ? css("--report-dot") : w.core ? "#7c6aa3" : "#e3e8ec";
}
function ovRank(w) {  // drawn last = on top
  const mode = $("ov-colour").value;
  if (mode === "events") return ovEvents(w);
  if (mode === "decade") return w.year || 0;
  if (mode === "type") return w.type === "EXPLORATION" ? 2 : 1;
  return w.total ? 3 : w.documents ? 2 : w.core ? 1 : 0;
}
function ovLegend(shadeBlocks) {
  const mode = $("ov-colour").value, event = $("ov-event").value;
  const row = (colour, text, square) => `<div><i class="${square ? "sq" : ""}" style="background:${colour}"></i>${esc(text)}</div>`;
  let rows = "";
  if (mode === "data") rows = row(css("--signal"), "Wellbore with extracted drilling events") + row(css("--report-dot"), "Report loaded, no events") +
    row("#7c6aa3", "Core measurements, no report") + row("#e3e8ec", "No report and no core");
  else if (mode === "type") rows = Object.entries(OV_TYPES).map(([type, colour]) => row(colour, ovTitle(type) + " wellbore")).join("");
  else if (mode === "decade") rows = OV_DECADES.map(([, text, colour]) => row(colour, "Drilling started " + text)).join("") + row(OV_GREY, "No start date");
  else rows = OV_CLASSES.map((c, i) => row(OV_RAMP[i], `${ovClassText(c)} ${event ? label(event) + " " : ""}events`)).join("") + row("#e3e8ec", "No such events");
  rows += row(OV_GREY, "Wellbore outside the screens (small dot)");
  rows += `<div><i class="sq" style="background:${css("--field")};opacity:.45"></i>Field outline</div>`;
  if (shadeBlocks) rows += OV_CLASSES.map((c, i) => row(OV_RAMP[i], `${ovClassText(c)} matching wellbores in the block`, true)).join("");
  $("ov-legend").innerHTML = rows;
}
function ovInspect(html) {
  $("ov-inspector").innerHTML = html || "Hover a wellbore, field or block. Select a wellbore to open it.";
}
function ovWellCard(w) {
  const events = w.events ? ov.eventTypes.map((t, i) => [t, w.events[i]]).filter(([, n]) => n).sort((a, b) => b[1] - a[1]) : [];
  return `<strong>${esc(w.name)}</strong><dl>
    <dt>Type</dt><dd>${esc(w.type ? ovTitle(w.type) : "–")}</dd><dt>Operator</dt><dd>${esc(w.operator || "–")}</dd>
    <dt>Field</dt><dd>${esc(w.field ? ovTitle(w.field) : "–")}</dd><dt>Drilled</dt><dd>${w.year || "–"}</dd>
    <dt>Reports</dt><dd>${number(w.documents)}</dd><dt>Events</dt><dd>${number(w.total)}${events.length ? " · " + events.slice(0, 3).map(([t, n]) => `${label(t)} ${n}`).join(", ") : ""}</dd>
    <dt>Core plugs</dt><dd>${number(w.core)}</dd></dl>`;
}

// Field names for the fields with most matching wellbores; more of them as the map zooms in.
function ovLabels() {
  ov.labelLayer.clearLayers();
  if (!$("ov-l-labels").checked) return;
  const zoom = ov.map.getZoom(), limit = zoom < 5 ? 8 : zoom < 6.5 ? 16 : zoom < 8 ? 30 : 80;
  for (const [field] of [...ov.perField].sort((a, b) => b[1] - a[1]).slice(0, limit)) {
    const centre = ov.fieldCentres.get(field);
    if (centre) L.marker(centre, {interactive: false, icon: L.divIcon({className: "ov-label", html: esc(ovTitle(field)), iconSize: [0, 0], iconAnchor: [-6, 8]})}).addTo(ov.labelLayer);
  }
}

function ovDraw() {
  const {map, wells} = ov, show = id => $("ov-l-" + id).checked;
  const matching = wells.filter(ovMatches), set = new Set(matching);
  ov.matching = matching;
  ov.wellLayer.clearLayers(); ov.otherLayer.clearLayers(); ov.labelLayer.clearLayers(); ov.blockLayer.clearLayers();
  if (show("others")) for (const w of wells) if (!set.has(w))
    L.circleMarker([w.lat, w.lon], {radius: 1.4, stroke: false, fillColor: OV_GREY, fillOpacity: .55, interactive: false}).addTo(ov.otherLayer);
  if (show("wells")) for (const w of [...matching].sort((a, b) => ovRank(a) - ovRank(b)))
    L.circleMarker([w.lat, w.lon], {radius: w.type === "EXPLORATION" ? 3.2 : 2.5, weight: .5, color: "#ffffff", fillColor: ovColour(w), fillOpacity: .9})
      .on("mouseover", () => ovInspect(ovWellCard(w))).on("click", () => openWell(w.name).catch(showError))
      .bindTooltip(esc(w.name)).addTo(ov.wellLayer);
  if (show("fields")) ov.fieldLayer.addTo(map).bringToBack(); else ov.fieldLayer.remove();
  const perField = new Map();
  for (const w of matching) if (w.field) perField.set(w.field, (perField.get(w.field) || 0) + 1);
  ov.perField = perField;
  ovLabels();
  if (show("blocks")) {
    const perBlock = new Map();
    for (const w of matching) { const b = ovBlock(w.name); if (b) perBlock.set(b, (perBlock.get(b) || 0) + 1); }
    L.geoJSON(ov.blocks, {
      filter: f => perBlock.has(String(f.properties.name)),
      style: f => ({stroke: false, fillColor: OV_RAMP[ovClass(perBlock.get(String(f.properties.name)))], fillOpacity: .75}),
      onEachFeature: (f, layer) => layer.on("mouseover", () => ovInspect(`<strong>Block ${esc(f.properties.name)}</strong><dl><dt>Matching wellbores</dt><dd>${number(perBlock.get(String(f.properties.name)))}</dd></dl>`))
    }).addTo(ov.blockLayer);
    ov.blockLayer.eachLayer(l => l.bringToBack());
    if (show("fields")) ov.fieldLayer.bringToBack();
    ov.base.forEach(l => l.bringToBack());
  }
  // counts for what is shown
  const sum = get => matching.reduce((t, w) => t + get(w), 0), count = test => matching.filter(test).length;
  const event = $("ov-event").value, withReport = count(w => w.documents), stats = [
    [number(matching.length), matching.length === wells.length ? "wellbores on the shelf" : `of ${number(wells.length)} wellbores match`],
    [number(withReport), `with a report (${pct(withReport, matching.length)})`],
    [number(count(w => ovEvents(w))), event ? `with ${label(event)} events` : "with extracted events"],
    [number(sum(ovEvents)), event ? `${label(event)} events` : "drilling events"],
    [number(count(w => w.core)), "with core measurements"],
    [number(perField.size), "fields"]];
  $("ov-stats").innerHTML = stats.map(([value, text]) => `<div><strong>${value}</strong><span>${esc(text)}</span></div>`).join("");
  const chosen = OV_SCREENS.map(k => $("ov-" + k)).filter(sel => sel.value).map(sel => sel.selectedOptions[0].textContent.replace(/ \([\d,]+\)$/, ""));
  $("ov-scope").textContent = chosen.length ? "Showing: " + chosen.join(" · ") : "Showing every wellbore. Wellbores outside a screen stay on the map as small grey dots.";
  $("ov-clear").disabled = !chosen.length;
  $("overview-mapnote").textContent = "Select a wellbore to open it. Positions are SODIR surface locations; wellbores without a position are not drawn.";
  ovLegend(show("blocks"));
}

function ovZoom(region) {
  const {map} = ov;
  map.invalidateSize();
  if (region === "selection") {
    if (ov.matching.length) map.fitBounds(L.latLngBounds(ov.matching.map(w => [w.lat, w.lon])).pad(.08), {maxZoom: 10, animate: false});
  } else map.fitBounds(OV_REGIONS[region] || ov.shelf, {padding: [16, 16], animate: false});
}

async function loadOverviewMap(bounds) {
  mapData = Promise.all(["land", "quadrants", "blocks", "fields"].map(async name => {
    const r = await fetch(`/static/geo/${name}.geojson`);
    if (!r.ok) throw new Error("Map layer unavailable: " + name);
    return r.json();
  }));
  const [[land, quadrants, blocks, fields], data] = await Promise.all([mapData, api("/api/map_wells")]);
  const wells = data.wells.map(([name, type, operator, field, lat, lon, year, documents, core, events]) => ({
    name, type: data.types[type] || null, operator: data.operators[operator] || null, field: data.fields[field] || null,
    lat, lon, year, documents, core, events, total: events ? events.reduce((a, b) => a + b, 0) : 0}));
  const map = overviewMap = L.map("map", {zoomSnap: .25, minZoom: 3, maxZoom: 13, preferCanvas: true}).setView([64, 7], 4.5);
  map.attributionControl.setPrefix(false).addAttribution("Quadrants, blocks, fields, wells: SODIR (NLOD 2.0) · Land: Natural Earth");
  L.control.scale({imperial: false}).addTo(map);
  const base = [
    L.geoJSON(quadrants, {interactive: false, style: {color: css("--grid"), weight: .7, opacity: .7, fill: false}}).addTo(map),
    L.geoJSON(land, {interactive: false, style: {color: css("--grid"), weight: .6, fillColor: css("--land"), fillOpacity: 1}}).addTo(map)];
  const fieldCentres = new Map();
  const fieldLayer = L.geoJSON(fields, {
    style: {color: css("--field"), weight: .8, fillColor: css("--field"), fillOpacity: .22},
    onEachFeature: (f, layer) => {
      fieldCentres.set(f.properties.name, layer.getBounds().getCenter());
      layer.on("mouseover", () => ovInspect(`<strong>${esc(ovTitle(f.properties.name))}</strong><dl><dt>Kind</dt><dd>Field</dd>
        <dt>Contains</dt><dd>${esc((f.properties.hc || "–").toLowerCase())}</dd><dt>Status</dt><dd>${esc(f.properties.status || "–")}</dd></dl>`));
      layer.on("click", () => { $("ov-field").value = f.properties.name; if ($("ov-field").value === f.properties.name) { ovDraw(); ovZoom("selection"); } });
    }});
  const gridLayer = L.geoJSON(blocks, {interactive: false, style: {color: css("--grid"), weight: .45, opacity: .7, fill: false}});
  map.on("zoomend", () => { if (map.getZoom() >= 7) gridLayer.addTo(map).bringToBack(); else gridLayer.remove(); if (ov) ovLabels(); });
  ov = {map, wells, blocks, base, fieldLayer, fieldCentres, eventTypes: data.event_types, matching: wells,
    blockLayer: L.layerGroup().addTo(map), otherLayer: L.layerGroup().addTo(map), wellLayer: L.layerGroup().addTo(map), labelLayer: L.layerGroup().addTo(map),
    perField: new Map(), shelf: L.latLngBounds([56, 0.5], [74.6, 33])};
  // screens, each option with its wellbore count
  const options = (id, all, get, text = v => v, byCount = false) => {
    const counts = new Map();
    for (const w of wells) { const v = get(w); if (v) counts.set(v, (counts.get(v) || 0) + 1); }
    const values = [...counts.keys()].sort(byCount ? (a, b) => counts.get(b) - counts.get(a) : (a, b) => a.localeCompare(b, "en", {numeric: true}));
    $(id).innerHTML = `<option value="">${all}</option>` + values.map(v => `<option value="${esc(v)}">${esc(text(v))} (${number(counts.get(v))})</option>`).join("");
  };
  options("ov-operator", "All operators", w => w.operator, v => v, true);
  options("ov-field", "All fields", w => w.field, ovTitle);
  options("ov-type", "All types", w => w.type, ovTitle);
  $("ov-event").innerHTML = '<option value="">Any</option>' + data.event_types.map((t, i) =>
    `<option value="${t}">${esc(label(t))} (${number(wells.filter(w => w.events && w.events[i]).length)})</option>`).join("");
  for (const id of ["colour", ...OV_SCREENS]) $("ov-" + id).onchange = ovDraw;
  for (const id of ["wells", "others", "fields", "labels", "blocks"]) $("ov-l-" + id).onchange = ovDraw;
  $("ov-clear").onclick = () => { for (const k of OV_SCREENS) $("ov-" + k).value = ""; ovDraw(); };
  for (const button of document.querySelectorAll("[data-region]")) button.onclick = () => ovZoom(button.dataset.region);
  ovDraw();
  if (state.section === "overview") ovZoom("shelf");
}
