// Well-clusters map: cored wellbores grouped so that no two are 20 km or more apart, coloured by
// the between-wellbore correlation of formation-mean log10 Klinkenberg horizontal permeability.
const CLUSTER_STOPS = [[-1, [33, 102, 172]], [0, [247, 247, 247]], [1, [178, 24, 43]]];
const CLUSTER_NONE = "#6b7780";
const CLUSTER_DETAIL_ZOOM = 7.5;  // below this, clusters are fixed-size dots; above, true-size circles
let clusterData, clusterLoaded, clusterMap, clusterShapes = new Map(), clusterDots = new Map(), clusterSelected;

function clusterColour(r) {
  const v = Math.max(-1, Math.min(1, r));
  const [lo, hi] = v < 0 ? [CLUSTER_STOPS[0], CLUSTER_STOPS[1]] : [CLUSTER_STOPS[1], CLUSTER_STOPS[2]];
  const t = (v - lo[0]) / (hi[0] - lo[0]);
  return `rgb(${lo[1].map((c, i) => Math.round(c + (hi[1][i] - c) * t)).join(",")})`;
}
const clusterName = c => c.fields.length ? c.fields.slice(0, 2).join(", ") + (c.fields.length > 2 ? " +" + (c.fields.length - 2) : "")
  : c.wells[0].name + (c.wellbores > 1 ? " area" : "");
const clusterValue = c => c.r == null ? "no value" : (c.r >= 0 ? "+" : "−") + Math.abs(c.r).toFixed(2);

async function initWellClusters() {
  if (!clusterData) clusterData = api("/api/core/clusters").catch(err => { clusterData = null; throw err; });
  const data = clusterLoaded = await clusterData;
  const layers = await (mapData || Promise.all(["land", "quadrants", "blocks", "fields"].map(name =>
    fetch(`/static/geo/${name}.geojson`).then(r => r.json()))));
  if (state.view !== "well-clusters") return;
  if (!clusterMap) drawWellClusters(data, layers);
  clusterMap.invalidateSize();
  if (!clusterSelected) {
    clusterMap.fitBounds(L.latLngBounds(data.clusters.map(c => [c.lat, c.lon])), {padding: [24, 24], animate: false});
    showClusterList(data);
  }
}

function drawWellClusters(data, layers) {
  const s = data.summary, d = data.definition;
  clusterMap = createMap("cluster-map", layers);
  clusterMap.attributionControl.addAttribution("Core: © GeoProvider RealPore (CC BY 4.0)");
  // Clusters without a value first, so the coloured ones stay on top and clickable.
  const ordered = [...data.clusters].sort((a, b) => (a.r != null) - (b.r != null) || b.radius_km - a.radius_km);
  const detail = L.layerGroup(), far = L.layerGroup();
  for (const c of ordered) {
    const valued = c.r != null, colour = valued ? clusterColour(c.r) : CLUSTER_NONE;
    const tip = `${esc(clusterName(c))} · ${number(c.wellbores)} wellbore${c.wellbores > 1 ? "s" : ""} · ${valued ? "r = " + clusterValue(c) : "no value: " + esc(c.reason_text)}`;
    const style = {color: valued ? "#22313c" : CLUSTER_NONE, weight: valued ? 1.3 : 1, dashArray: valued ? null : "3 4",
      fillColor: colour, fillOpacity: valued ? .8 : 0};
    const shape = L.circle([c.lat, c.lon], {...style, radius: Math.max(c.radius_km, 1.5) * 1000 + 600})
      .bindTooltip(tip, {sticky: true}).on("click", () => selectCluster(c)).addTo(detail);
    const dot = L.circleMarker([c.lat, c.lon], {...style, fillOpacity: valued ? .92 : 0,
      radius: valued ? 5 + Math.min(5, Math.sqrt(c.wellbores)) : 3.5})
      .bindTooltip(tip, {sticky: true}).on("click", () => selectCluster(c, true)).addTo(far);
    clusterShapes.set(c.id, shape);
    clusterDots.set(c.id, dot);
    for (const w of c.wells) {
      L.circleMarker([w.lat, w.lon], {radius: 2.2, stroke: false, fillColor: valued ? "#22313c" : CLUSTER_NONE,
        fillOpacity: .7, interactive: false}).bindTooltip(esc(w.name)).addTo(detail);
    }
  }
  const showLevel = () => {
    const close = clusterMap.getZoom() >= CLUSTER_DETAIL_ZOOM;
    (close ? detail : far).addTo(clusterMap);
    (close ? far : detail).remove();
  };
  clusterMap.on("zoomend", showLevel);
  showLevel();
  $("cluster-summary").textContent =
    `${number(s.wellbores)} of ${number(s.cored_wellbores)} cored wellbores have Klinkenberg-corrected horizontal permeability. ` +
    `They form ${number(s.clusters)} well-clusters (largest ${number(s.largest_cluster)} wellbores, widest ${fmt(s.largest_span_km, 1)} km); ` +
    `${number(s.with_value)} clusters, holding ${number(s.wellbores_in_valued_clusters)} wellbores, have a correlation.`;
  $("cluster-legend").innerHTML =
    `<span>−1 <i class="cluster-ramp" style="background:linear-gradient(90deg,${clusterColour(-1)},${clusterColour(0)},${clusterColour(1)})"></i> +1 &nbsp;between-well correlation r</span>` +
    `<span><i class="cluster-none"></i>no value (${number(s.clusters - s.with_value)} clusters: ` +
    Object.entries(s.no_value).map(([reason, count]) => `${number(count)} ${esc(reason.toLowerCase())}`).join("; ") + ")</span>";
  $("cluster-definition").innerHTML =
    `<strong>Well-cluster:</strong> wellbores with Klinkenberg-corrected horizontal permeability, grouped so that no two wellbores in a cluster are ${d.max_distance_km} km or more apart ` +
    `(closest wellbores merge first; great-circle distance between SODIR surface positions). Zoomed out, each cluster is one dot at its centre; zoomed in, the circle covers the cluster's wellbores (small dots). ` +
    `<strong>Correlation:</strong> for every pair of wellbores in a cluster and every formation both have cored (${d.min_samples}+ plugs each), the two wellbore means of log10 permeability form a pair; ` +
    `r is the Pearson correlation over all pairs in the cluster, each entered both ways round. r near +1: where one wellbore has a tight or permeable formation, so do its neighbours. ` +
    `r near 0 or below: knowing one wellbore tells little about the next. SODIR's placeholder units (${d.excluded_formations.map(f => f.toLowerCase()).join(", ")}) do not count as a shared formation. A value needs ${d.min_pairs}+ pairs over ${d.min_formations}+ formations; other clusters are drawn dashed with no colour. ` +
    `With few shared formations r is uncertain: check the pair count in the panel.`;
}

function showClusterList(data) {
  const valued = data.clusters.filter(c => c.r != null).sort((a, b) => b.r - a.r);
  $("cluster-detail").innerHTML = `<h4>Clusters with a correlation</h4>
    <p class="small muted">Select a row or a circle on the map.</p>
    <table><tr><th>Cluster</th><th class="num">Wells</th><th class="num">Pairs</th><th class="num">r</th></tr>` +
    valued.map(c => `<tr class="row" data-cluster="${c.id}" style="cursor:pointer"><td><span class="dot" style="background:${clusterColour(c.r)};border:1px solid #22313c"></span> ${esc(clusterName(c))}</td>
      <td class="num">${c.wellbores}</td><td class="num">${c.pairs}</td><td class="num">${clusterValue(c)}</td></tr>`).join("") + "</table>";
  for (const row of $("cluster-detail").querySelectorAll("[data-cluster]"))
    row.onclick = () => selectCluster(data.clusters.find(c => c.id === +row.dataset.cluster), true);
}

function clusterScatter(c) {
  const logs = c.points.flatMap(p => [p.log_a, p.log_b]);
  const lo = Math.floor(Math.min(...logs)), hi = Math.max(lo + 1, Math.ceil(Math.max(...logs)));
  const S = 230, L0 = 44, T = 8, P = S - L0 - 8, pos = v => (v - lo) / (hi - lo) * P;
  const formations = c.formations, hue = f => `hsl(${Math.round(formations.indexOf(f) * 360 / formations.length)},55%,40%)`;
  let svg = `<rect x="${L0}" y="${T}" width="${P}" height="${P}" fill="var(--panel)" stroke="var(--line)"/>
    <line x1="${L0}" y1="${T + P}" x2="${L0 + P}" y2="${T}" stroke="var(--line)" stroke-dasharray="3 3"/>`;
  const tick = Math.ceil((hi - lo) / 4);
  for (let v = lo; v <= hi; v += tick) svg += `<text x="${L0 + pos(v)}" y="${T + P + 13}" text-anchor="middle" font-size="10">${khLabel(10 ** v)}</text>
    <text x="${L0 - 5}" y="${T + P - pos(v) + 3}" text-anchor="end" font-size="10">${khLabel(10 ** v)}</text>`;
  for (const p of c.points) for (const [x, y, a, b] of [[p.log_a, p.log_b, p.a, p.b], [p.log_b, p.log_a, p.b, p.a]])
    svg += `<circle cx="${L0 + pos(x)}" cy="${T + P - pos(y)}" r="2.6" fill="${hue(p.formation)}" fill-opacity=".7"><title>${esc(p.formation)} · ${esc(a)}: ${khLabel(10 ** x)} mD · ${esc(b)}: ${khLabel(10 ** y)} mD</title></circle>`;
  return `<svg viewBox="0 0 ${S} ${S + 14}" role="img" aria-label="Formation-mean permeability of one wellbore against another, for every pair">${svg}
    <text x="${L0 + P / 2}" y="${S + 11}" text-anchor="middle" font-size="10">Formation mean in one wellbore, mD</text></svg>
    <p class="legend small muted">${formations.map(f => `<span><i class="dot" style="background:${hue(f)}"></i>${esc(f)}</span>`).join("")}</p>`;
}

function clusterHighlight(on) {
  for (const shape of [clusterShapes.get(clusterSelected), clusterDots.get(clusterSelected)]) {
    if (!shape) continue;
    shape.setStyle({weight: on ? 3.2 : shape.options.dashArray ? 1 : 1.3});
    if (on && clusterMap.hasLayer(shape)) shape.bringToFront();
  }
}

function selectCluster(c, zoom) {
  clusterHighlight(false);
  clusterSelected = c.id;
  if (zoom) clusterMap.fitBounds(L.latLng(c.lat, c.lon).toBounds(2 * Math.max(c.radius_km, 1.5) * 1000).pad(2), {animate: false});
  clusterHighlight(true);
  $("cluster-detail").innerHTML = `<button id="cluster-back">All clusters</button>
    <h4 style="margin-top:10px">${esc(clusterName(c))}</h4>
    <div class="cluster-value">${c.r == null ? "No value" : "r = " + clusterValue(c)}</div>
    <p class="small muted">${c.r == null ? esc(c.reason_text) + "."
      : `From ${number(c.pairs)} wellbore pairs over ${c.formations.length} shared formations.`}
      ${number(c.wellbores)} wellbore${c.wellbores > 1 ? "s" : ""}, ${number(c.samples)} Klinkenberg plugs, ${fmt(c.span_km, 1)} km across.</p>
    ${c.r == null ? "" : clusterScatter(c)}
    <table><tr><th>Wellbore</th><th class="num">Plugs</th><th class="num" title="Formations with 5 or more plugs">Fms</th><th class="num">Median kh, mD</th></tr>` +
    c.wells.map(w => `<tr><td>${esc(w.name)}</td><td class="num">${number(w.samples)}</td><td class="num">${w.formations}</td><td class="num">${khLabel(w.kh_median_md)}</td></tr>`).join("") + "</table>";
  $("cluster-back").onclick = () => {
    clusterHighlight(false);
    clusterSelected = null;
    showClusterList(clusterLoaded);
  };
}
