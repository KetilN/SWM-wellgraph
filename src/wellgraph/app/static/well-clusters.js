// Well-cluster view: core porosity and horizontal permeability of the selected wellbore against the
// cored wellbores within a chosen range of it.
const CLUSTER_ME = "#ad3948", CLUSTER_NEIGHBOUR = "#6b7780";
const CLUSTER_KH_CLASSES = [[1, "#dfe9ee"], [10, "#a9c6d4"], [100, "#6c9fb5"], [1000, "#2f7591"], [Infinity, "#0b4660"]];
let clusterData, clusterKey, clusterMap, clusterLayer;

const clusterKhColour = kh => kh == null ? "#ffffff" : CLUSTER_KH_CLASSES.find(([limit]) => kh < limit)[1];

async function initWellClusters() {
  const well = state.well, range = +$("cluster-range").value;
  $("cluster-range").onchange = () => initWellClusters().catch(showError);
  $("cluster-formation").onchange = drawClusterPlot;
  const key = well + "|" + range;
  if (clusterKey === key) { if (clusterMap) clusterMap.invalidateSize(); return; }
  $("cluster-summary").textContent = "Loading core data around " + well + "…";
  const [data, layers] = await Promise.all([api("/api/core/neighbours", {name: well, range_km: range}),
    mapData || Promise.all(["land", "quadrants", "blocks", "fields"].map(name => fetch(`/static/geo/${name}.geojson`).then(r => r.json())))]);
  if (state.view !== "well-clusters" || state.well !== well || +$("cluster-range").value !== range) return;
  clusterKey = key;
  clusterData = data;
  const others = data.wells.filter(w => !w.selected), me = data.wells.find(w => w.selected);
  $("cluster-title").textContent = `Well-cluster: ${data.wellbore} and cored wellbores within ${fmt(data.range_km)} km`;
  $("cluster-summary").textContent = data.note || (
    (me ? `${data.wellbore} has ${number(data.well_points_total)} core plugs with both porosity and horizontal permeability. `
        : `${data.wellbore} has no core data in the graph. `) +
    (others.length ? `${number(others.length)} other cored wellbore${others.length > 1 ? "s" : ""} within ${fmt(data.range_km)} km, with ${number(data.neighbour_points_total)} such plugs.`
        : `No other cored wellbore within ${fmt(data.range_km)} km: try a wider range.`));
  const previous = $("cluster-formation").value;
  $("cluster-formation").innerHTML = '<option value="">All formations</option>' + data.formations.map((f, i) =>
    `<option value="${i}">${esc(f.formation)} (${number(f.well_samples)} + ${number(f.neighbour_samples)})</option>`).join("");
  const keep = data.formations.findIndex(f => f.formation === previous);
  $("cluster-formation").value = keep < 0 ? "" : String(keep);
  $("cluster-formation").dataset.name = "";
  drawClusterPlot();
  drawClusterTables(data);
  drawClusterMap(data, layers);
  $("cluster-definition").textContent = `Range is the great-circle distance between SODIR surface positions, so a deviated wellbore can reach further at reservoir depth. ` +
    `A plug counts when it has both porosity and a horizontal permeability above zero, whatever the measurement method. ` +
    `Formation comes from the plug depth against SODIR formation tops; plugs from wellbores without tops are listed as "No formation".`;
}

function drawClusterPlot() {
  const data = clusterData, chosen = $("cluster-formation").value;
  const pick = rows => chosen === "" ? rows : rows.filter(p => p[2] === +chosen);
  const mine = pick(data.points.well), near = pick(data.points.neighbours), all = [...near, ...mine];
  if (!all.length) { $("cluster-plot").innerHTML = '<p class="small muted">No plugs with both porosity and permeability in this selection.</p>'; $("cluster-legend").innerHTML = ""; return; }
  const W = 640, H = 420, L0 = 62, T = 14, PW = W - L0 - 14, PH = H - T - 44;
  const logs = all.map(p => Math.log10(p[1]));
  const y0 = Math.floor(Math.min(...logs)), y1 = Math.max(y0 + 1, Math.ceil(Math.max(...logs)));
  const x1 = Math.max(10, Math.ceil(Math.max(...all.map(p => p[0])) / 5) * 5);
  const x = v => L0 + v / x1 * PW, y = v => T + PH - (v - y0) / (y1 - y0) * PH;
  let svg = `<rect x="${L0}" y="${T}" width="${PW}" height="${PH}" fill="var(--panel)" stroke="var(--line)"/>`;
  for (let v = 0; v <= x1; v += 5) svg += `<line x1="${x(v)}" y1="${T}" x2="${x(v)}" y2="${T + PH}" stroke="var(--grid)" stroke-width=".5"/><text x="${x(v)}" y="${T + PH + 15}" text-anchor="middle" font-size="11">${v}</text>`;
  for (let v = y0; v <= y1; v++) svg += `<line x1="${L0}" y1="${y(v)}" x2="${L0 + PW}" y2="${y(v)}" stroke="var(--grid)" stroke-width=".5"/><text x="${L0 - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11">${khLabel(10 ** v)}</text>`;
  const dot = (p, colour, r, opacity) => `<circle cx="${x(p[0]).toFixed(1)}" cy="${y(Math.log10(p[1])).toFixed(1)}" r="${r}" fill="${colour}" fill-opacity="${opacity}"><title>${esc(data.point_wellbores[p[3]])} · ${esc(data.formations[p[2]].formation)} · ${fmt(p[4], 1)} m MD · ${fmt(p[0], 1)}% · ${khLabel(p[1])} mD</title></circle>`;
  svg += near.map(p => dot(p, CLUSTER_NEIGHBOUR, 2, .32)).join("") + mine.map(p => dot(p, CLUSTER_ME, 2.8, .8)).join("");
  svg += `<text x="${L0 + PW / 2}" y="${H - 6}" text-anchor="middle" font-size="12">Porosity, %</text>
    <text transform="translate(14 ${T + PH / 2}) rotate(-90)" text-anchor="middle" font-size="12">Horizontal permeability, mD</text>`;
  $("cluster-plot").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Porosity against horizontal permeability for the selected wellbore and its neighbours">${svg}</svg>`;
  const median = values => { const s = [...values].sort((a, b) => a - b); return s.length ? s[Math.floor((s.length - 1) / 2)] : null; };
  const line = (rows, total) => rows.length ? `${number(rows.length)}${chosen === "" && total > rows.length ? " of " + number(total) : ""} plugs · median ${fmt(median(rows.map(p => p[0])), 1)}% · ${khLabel(median(rows.map(p => p[1])))} mD` : "no plugs";
  $("cluster-legend").innerHTML = `<span><i class="dot" style="background:${CLUSTER_ME}"></i>${esc(data.wellbore)}: ${line(mine, data.well_points_total)}</span>
    <span><i class="dot" style="background:${CLUSTER_NEIGHBOUR}"></i>Neighbours within ${fmt(data.range_km)} km: ${line(near, data.neighbour_points_total)}</span>`;
}

function drawClusterTables(data) {
  const value = (v, digits) => v == null ? "–" : digits == null ? khLabel(v) : fmt(v, digits);
  $("cluster-formations").innerHTML = `<tr><th>Formation</th><th class="num">${esc(data.wellbore)} plugs</th><th class="num">Median porosity, %</th><th class="num">Median kh, mD</th>
    <th class="num">Neighbour wellbores</th><th class="num">Neighbour plugs</th><th class="num">Median porosity, %</th><th class="num">Median kh, mD</th></tr>` +
    data.formations.map(f => `<tr><td>${esc(f.formation)}</td><td class="num">${number(f.well_samples)}</td><td class="num">${value(f.well_porosity_p50, 1)}</td><td class="num">${value(f.well_kh_p50_md)}</td>
      <td class="num">${number(f.neighbour_wellbores)}</td><td class="num">${number(f.neighbour_samples)}</td><td class="num">${value(f.neighbour_porosity_p50, 1)}</td><td class="num">${value(f.neighbour_kh_p50_md)}</td></tr>`).join("");
  $("cluster-wells").innerHTML = `<tr><th>Wellbore</th><th>Field</th><th class="num">Distance, km</th><th class="num">Plugs</th><th class="num">Median porosity, %</th><th class="num">Median kh, mD</th><th class="num">Core interval, m MD</th></tr>` +
    data.wells.map(w => `<tr class="${w.selected ? "me" : ""}" ${w.selected ? "" : `data-open="${esc(w.name)}" title="Open ${esc(w.name)}"`}><td>${esc(w.name)}${w.selected ? " (selected)" : ""}</td><td>${esc(w.field || "")}</td>
      <td class="num">${fmt(w.distance_km, 1)}</td><td class="num">${number(w.paired || 0)}</td><td class="num">${value(w.porosity_p50, 1)}</td><td class="num">${value(w.kh_p50_md)}</td>
      <td class="num">${fmt(w.shallowest_md)}–${fmt(w.deepest_md)}</td></tr>`).join("");
  for (const row of $("cluster-wells").querySelectorAll("[data-open]"))
    row.onclick = () => openWell(row.dataset.open, "reservoir", "well-clusters").catch(showError);
}

function drawClusterMap(data, layers) {
  if (!clusterMap) {
    clusterMap = createMap("cluster-map", layers);
    clusterMap.attributionControl.addAttribution("Core: © GeoProvider RealPore (CC BY 4.0)");
    clusterLayer = L.layerGroup().addTo(clusterMap);
  }
  clusterLayer.clearLayers();
  clusterMap.invalidateSize();
  if (data.lat == null) return;
  const ring = L.circle([data.lat, data.lon], {radius: data.range_km * 1000, color: CLUSTER_ME, weight: 1.4, dashArray: "5 5", fill: false, interactive: false}).addTo(clusterLayer);
  for (const w of data.wells.filter(w => !w.selected))
    L.circleMarker([w.lat, w.lon], {radius: 5.5, color: "#22313c", weight: 1, fillColor: clusterKhColour(w.kh_p50_md), fillOpacity: .95})
      .bindTooltip(`${esc(w.name)} · ${fmt(w.distance_km, 1)} km · median ${w.porosity_p50 == null ? "–" : fmt(w.porosity_p50, 1) + "%"} · ${khLabel(w.kh_p50_md)} mD`)
      .on("click", () => openWell(w.name, "reservoir", "well-clusters").catch(showError)).addTo(clusterLayer);
  const me = data.wells.find(w => w.selected);
  L.circleMarker([data.lat, data.lon], {radius: 7.5, color: CLUSTER_ME, weight: 3, fillColor: clusterKhColour(me ? me.kh_p50_md : null), fillOpacity: 1})
    .bindTooltip(`${esc(data.wellbore)} (selected)`).addTo(clusterLayer);
  clusterMap.fitBounds(ring.getBounds().pad(.08), {animate: false});
  $("cluster-map-legend").innerHTML = "<span>Median kh, mD:</span>" + CLUSTER_KH_CLASSES.map(([limit, colour], i) =>
    `<span><i class="dot" style="background:${colour};border:1px solid #22313c"></i>${i === 0 ? "under 1" : limit === Infinity ? "1,000 or more" : `${number(CLUSTER_KH_CLASSES[i - 1][0])}–${number(limit)}`}</span>`).join("") +
    `<span><i class="dot" style="background:#fff;border:2px solid ${CLUSTER_ME}"></i>selected wellbore</span><span>Dashed ring: ${fmt(data.range_km)} km. Select a wellbore to open it.</span>`;
}
