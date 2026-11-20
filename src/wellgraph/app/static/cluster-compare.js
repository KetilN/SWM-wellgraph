// Cluster comparison: two clusters of wellbores (everything within a range of a reference wellbore),
// compared on core porosity and horizontal permeability, formation by formation.
const CC_COLOURS = ["#2a78d6", "#eb6834"];
let ccData, ccKey;

const ccKh = k => k == null ? "–" : k >= 100 ? number(Math.round(k)) : k >= 10 ? k.toFixed(0) : k >= 1 ? k.toFixed(1) : k >= 0.1 ? k.toFixed(2) : k.toFixed(3);
const ccShort = f => f.replace(/ FM$/, "").toLowerCase().replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase()).replace(/ Gp$/, " Gp");
const ccOne = (v, digits = 1) => v == null ? "–" : Number(v).toFixed(digits);
const ccKm = (a, b) => `${(a / 1000).toFixed(1)}–${(b / 1000).toFixed(1)}`;
const ccLin = (d0, d1, r0, r1) => v => r0 + (v - d0) / (d1 - d0) * (r1 - r0);
const ccLog = (d0, d1, r0, r1) => v => r0 + (Math.log10(v) - Math.log10(d0)) / (Math.log10(d1) - Math.log10(d0)) * (r1 - r0);

function initClusterCompare() {
  const list = $("cc-wells");
  if (!list.options.length) {
    const names = typeof ov !== "undefined" && ov ? ov.wells.map(w => w.name) : state.reportWells.map(w => w.name);
    list.innerHTML = names.map(n => `<option value="${esc(n)}">`).join("");
  }
  // Clicking into a wellbore box empties it so the whole list shows; leaving it empty puts the name back.
  for (const id of ["cc-a", "cc-b"]) {
    const input = $(id);
    const open = () => { if (!input.value) return; input.dataset.previous = input.value; input.placeholder = input.value; input.value = ""; };
    input.onfocus = open;
    input.onmousedown = open;
    input.onblur = () => { if (!input.value.trim()) input.value = input.dataset.previous || ""; };
    input.onchange = () => { if (input.value.trim() && input.value !== input.dataset.previous) { input.dataset.previous = input.value; input.blur(); loadClusterCompare().catch(ccError); } };
  }
  $("cc-form").onsubmit = e => { e.preventDefault(); loadClusterCompare().catch(ccError); };
  $("cc-range").onchange = () => loadClusterCompare().catch(ccError);
  $("cc-all").onchange = () => loadClusterCompare().catch(ccError);
  return ccData ? Promise.resolve() : loadClusterCompare().catch(ccError);
}
function ccError(err) {
  $("cc-status").textContent = err.message;
}
async function loadClusterCompare() {
  const a = $("cc-a").value.trim(), b = $("cc-b").value.trim(), range = $("cc-range").value, all = $("cc-all").checked;
  if (!a || !b) return;
  const key = [a, b, range, all].join("|");
  if (key === ccKey) return;
  $("cc-status").textContent = "Comparing…";
  const data = await api("/api/core/compare", {a, b, range_km: range, all_wellbores: all ? 1 : ""});
  ccKey = key;
  ccData = data;
  $("cc-status").textContent = "";
  $("cc-a").value = data.clusters[0].reference;
  $("cc-b").value = data.clusters[1].reference;
  renderClusterCompare(data);
}

function renderClusterCompare(d) {
  const [A, B] = d.clusters, R = d.range_km, get = (c, f) => c.formations.find(p => p.formation === f);
  if (A.name === B.name) { A.name = A.reference; B.name = B.reference; }
  $("cc-body").hidden = false;
  $("cc-title").textContent = `${A.name} against ${B.name}: two ${R} km well clusters`;
  const bestA = get(A, A.best_formation), bestB = get(B, B.best_formation), mostB = get(B, B.most_cored_formation), mostA = get(A, A.most_cored_formation);

  // ---- opening sentence and tiles
  let lede;
  if (!bestA || !bestB) {
    const empty = [A, B].filter(c => !get(c, c.best_formation)).map(c => c.name).join(" and ");
    lede = `No core data with both porosity and permeability in the ${empty} cluster at this range${d.exploration_only ? " (exploration wellbores only)" : ""}. Try a wider range or include development wellbores.`;
  } else {
    const [hi, lo, bh, bl] = bestA.kh_p50_md >= bestB.kh_p50_md ? [A, B, bestA, bestB] : [B, A, bestB, bestA];
    const describe = (p, verb) => `the ${ccShort(p.formation)} ${p.formation.endsWith(" FM") ? "Formation" : "unit"}, ${verb} a pooled median porosity of ${p.porosity_p50.toFixed(0)}% and a median permeability near ${ccKh(p.kh_p50_md)} mD at about ${ccKm(p.top_md, p.base_md).replace("–", " to ")} km depth`;
    const mostLo = lo === A ? mostA : mostB;
    lede = `The ${hi.name} cluster has the better rock. Its best unit, ${describe(bh, "has")}. In the ${lo.name} cluster the best unit is ${describe(bl, "with")}` +
      (mostLo && mostLo !== bl && mostLo.kh_p50_md != null ? `, and its most-cored unit, ${ccShort(mostLo.formation)}, only ${mostLo.porosity_p50.toFixed(0)}% and ${ccKh(mostLo.kh_p50_md)} mD` : "") +
      `. ${A.wellbores === B.wellbores ? "Both clusters hold" : (A.wellbores > B.wellbores ? A : B).name + " is more densely drilled:"} ${number(Math.max(A.wellbores, B.wellbores))} wellbores against ${number(Math.min(A.wellbores, B.wellbores))}.`;
  }
  $("cc-lede").textContent = lede;
  const depth = c => { const p = get(c, c.best_formation); return p ? ccKm(p.top_md, p.base_md) : "–"; };
  const tiles = [
    [`${bestA ? bestA.porosity_p50.toFixed(0) + "%" : "–"} vs ${bestB ? bestB.porosity_p50.toFixed(0) + "%" : "–"}`, `Median porosity, best unit (${bestA ? ccShort(bestA.formation) : "–"} vs ${bestB ? ccShort(bestB.formation) : "–"})`],
    [`${bestA ? ccKh(bestA.kh_p50_md) : "–"} vs ${bestB ? ccKh(bestB.kh_p50_md) : "–"} mD`, "Median permeability, best unit"],
    [`${depth(A)} vs ${depth(B)} km`, "Cored depth of the best unit, measured"],
    [`${number(A.samples)} vs ${number(B.samples)}`, `Core plugs (${A.cored_wellbores.length} vs ${B.cored_wellbores.length} wellbores)`]];
  $("cc-tiles").innerHTML = tiles.map(([v, k]) => `<div><strong>${esc(v)}</strong><span>${esc(k)}</span></div>`).join("");

  ccLocator(d).catch(() => { $("cc-ncs").innerHTML = ""; });

  // ---- cluster maps: zoomable, on the shelf map
  ccMaps(d).catch(ccError);
  [A, B].forEach((c, i) => {
    const t = c.by_type;
    $("cc-cap-" + i).innerHTML = `<i class="dot" style="background:${CC_COLOURS[i]}"></i> <strong>${esc(c.name)} cluster.</strong> ${number(c.wellbores)} wellbores within ${R} km of ${esc(c.reference)}: ${number(t.EXPLORATION || 0)} exploration, ${number(t.DEVELOPMENT || 0)} development, ${number(t.OTHER || 0)} other. Core data used from ${c.cored_wellbores.length}.`;
  });
  $("cc-map-legend").innerHTML = `<span><i class="dot" style="background:#111;border:2px solid #fff;outline:1px solid #111"></i>reference wellbore</span>
    <span><i class="dot" style="background:${CC_COLOURS[0]}"></i><i class="dot" style="background:${CC_COLOURS[1]}"></i>wellbore with core data used</span>
    <span><i class="dot" style="background:#fff;border:1.5px solid #52606b"></i>exploration wellbore, no core data used</span>
    <span><i class="dot" style="background:#7d8a94;width:7px;height:7px"></i>development wellbore</span><span><i class="dot" style="background:#b4bec6;width:5px;height:5px"></i>other wellbore</span>
    <span>Scroll or use + and − to zoom; "Fit cluster" returns to the circle.</span>`;

  // ---- formation charts: every formation with enough plugs, each cluster in depth order
  const charted = [A, B].flatMap((c, i) => c.formations.filter(p => p.samples >= d.min_chart_samples && (p.porosity_p50 != null || p.kh_p50_md != null)).map(p => ({...p, c, i})));
  const khValues = charted.flatMap(p => [p.kh_min_md, p.kh_max_md, p.kh_p50_md]).filter(v => v > 0);
  const khLo = 10 ** Math.floor(Math.log10(Math.min(0.01, ...khValues))), khHi = 10 ** Math.ceil(Math.log10(Math.max(1000, ...khValues)));
  const phiHi = Math.max(25, Math.ceil(Math.max(0, ...charted.map(p => p.porosity_p90 || 0)) / 5) * 5);
  const khTicks = []; for (let v = khLo; v <= khHi * 1.001; v *= 10) khTicks.push(v);
  const formationChart = (id, kind) => {
    const W = 520, rowH = 24, m = {l: 118, r: 66, t: 10, b: 34}, H = m.t + m.b + rowH * Math.max(1, charted.length);
    const x = kind === "phi" ? ccLin(0, phiHi, m.l, W - m.r) : ccLog(khLo, khHi, m.l, W - m.r);
    const ticks = kind === "phi" ? Array.from({length: phiHi / 5 + 1}, (_, k) => k * 5) : khTicks;
    let s = ticks.map(t => `<line x1="${x(t)}" x2="${x(t)}" y1="${m.t}" y2="${H - m.b}" stroke="var(--line)"/><text x="${x(t)}" y="${H - m.b + 14}" text-anchor="middle">${kind === "phi" ? t : t >= 1 ? number(t) : t}</text>`).join("");
    s += `<text x="${(m.l + W - m.r) / 2}" y="${H - 4}" text-anchor="middle">${kind === "phi" ? "Porosity, %" : "Horizontal permeability, mD"}</text>`;
    charted.forEach((p, k) => {
      const y = m.t + rowH * (k + .5), colour = CC_COLOURS[p.i];
      if (k && charted[k - 1].i !== p.i) s += `<line x1="8" x2="${W - 4}" y1="${m.t + rowH * k}" y2="${m.t + rowH * k}" stroke="var(--grid)" stroke-dasharray="3 3"/>`;
      s += `<text x="${m.l - 10}" y="${y + 4}" text-anchor="end" style="fill:var(--ink);font-size:12px">${esc(ccShort(p.formation))}</text>`;
      const tip = `<title>${esc(ccShort(p.formation))} (${esc(p.c.name)}) · ${p.wellbores} wellbore${p.wellbores > 1 ? "s" : ""}, ${number(p.samples)} plugs · ` +
        (kind === "phi" ? `porosity P10 / P50 / P90: ${[p.porosity_p10, p.porosity_p50, p.porosity_p90].map(v => ccOne(v)).join(" / ")}%` : `median kh ${ccKh(p.kh_p50_md)} mD (wellbore medians ${ccKh(p.kh_min_md)} to ${ccKh(p.kh_max_md)})`) + "</title>";
      if (kind === "phi" && p.porosity_p50 != null) {
        s += `<rect x="${x(p.porosity_p10)}" y="${y - 7}" width="${x(p.porosity_p90) - x(p.porosity_p10)}" height="14" rx="4" fill="${colour}" fill-opacity=".28">${tip}</rect>
          <line x1="${x(p.porosity_p50)}" x2="${x(p.porosity_p50)}" y1="${y - 10}" y2="${y + 10}" stroke="${colour}" stroke-width="3" stroke-linecap="round"/>
          <text x="${W - 4}" y="${y + 4}" text-anchor="end" style="fill:var(--ink)">${p.porosity_p50.toFixed(0)}%</text>`;
      } else if (kind === "kh" && p.kh_p50_md != null) {
        s += `<line x1="${x(p.kh_min_md)}" x2="${x(p.kh_max_md)}" y1="${y}" y2="${y}" stroke="${colour}" stroke-opacity=".28" stroke-width="14" stroke-linecap="round">${tip}</line>
          <line x1="${x(p.kh_p50_md)}" x2="${x(p.kh_p50_md)}" y1="${y - 10}" y2="${y + 10}" stroke="${colour}" stroke-width="3" stroke-linecap="round"/>
          <text x="${W - 4}" y="${y + 4}" text-anchor="end" style="fill:var(--ink)">${ccKh(p.kh_p50_md)} mD</text>`;
      }
      for (const r of p.c.core.filter(r => r.formation === p.formation)) {
        const v = kind === "phi" ? r.porosity_p50 : (r.with_kh > 0 ? r.kh_p50_md : null);
        if (v == null || (kind === "kh" && v <= 0)) continue;
        s += `<circle cx="${x(v)}" cy="${y}" r="4" fill="${colour}" stroke="var(--panel)" stroke-width="1.5"><title>${esc(r.wellbore)} · ${esc(ccShort(r.formation))} · ${kind === "phi" ? "median porosity " + r.porosity_p50 + "%" : "median kh " + ccKh(r.kh_p50_md) + " mD"}${r.note ? " (" + esc(r.note) + ")" : ""} · ${number(r.samples)} plugs, ${r.top_md}–${r.base_md} m MD</title></circle>`;
      }
    });
    [A, B].forEach((c, i) => { const k0 = charted.findIndex(p => p.i === i); if (k0 >= 0) s += `<text x="8" y="${m.t + rowH * k0 + 11}" style="fill:${CC_COLOURS[i]};font-weight:700;font-size:10px;letter-spacing:.04em">${esc(c.name.toUpperCase().slice(0, 12))}</text>`; });
    $(id).setAttribute("viewBox", `0 0 ${W} ${H}`);
    $(id).innerHTML = s;
  };
  formationChart("cc-phi", "phi");
  formationChart("cc-kh", "kh");

  // ---- against depth
  const names = new Set(charted.map(p => p.i + "|" + p.formation));
  const dots = [A, B].flatMap((c, i) => c.core.filter(r => names.has(i + "|" + r.formation) && r.samples >= d.min_chart_samples).map(r => ({...r, i})));
  const mids = dots.map(r => (r.top_md + r.base_md) / 2), d0 = Math.floor(Math.min(1500, ...mids) / 500) * 500, d1 = Math.ceil(Math.max(3000, ...mids) / 500) * 500;
  const depthChart = (id, kind) => {
    const W = 500, H = 300, m = {l: 52, r: 14, t: 26, b: 38}, x = ccLin(d0, d1, m.l, W - m.r);
    const y = kind === "phi" ? ccLin(0, phiHi, H - m.b, m.t) : ccLog(khLo, khHi, H - m.b, m.t);
    let s = "";
    for (let t = Math.ceil(d0 / 1000) * 1000; t <= d1; t += 1000) s += `<line x1="${x(t)}" x2="${x(t)}" y1="${m.t}" y2="${H - m.b}" stroke="var(--line)"/><text x="${x(t)}" y="${H - m.b + 14}" text-anchor="middle">${number(t)}</text>`;
    for (const t of (kind === "phi" ? Array.from({length: phiHi / 5 + 1}, (_, k) => k * 5) : khTicks)) s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}" stroke="var(--line)"/><text x="${m.l - 6}" y="${y(t) + 4}" text-anchor="end">${t >= 1 ? number(t) : t}</text>`;
    s += `<text x="${(m.l + W - m.r) / 2}" y="${H - 6}" text-anchor="middle">Mid depth of cored interval, m MD</text><text x="${m.l - 40}" y="12" style="fill:var(--ink)">${kind === "phi" ? "Median porosity, %" : "Median kh, mD"}</text>`;
    for (const r of dots) {
      const v = kind === "phi" ? r.porosity_p50 : (r.with_kh > 4 ? r.kh_p50_md : null);
      if (v == null || (kind === "kh" && v <= 0)) continue;
      s += `<circle cx="${x((r.top_md + r.base_md) / 2)}" cy="${y(v)}" r="5" fill="${CC_COLOURS[r.i]}" fill-opacity=".85" stroke="var(--panel)" stroke-width="2"><title>${esc(r.wellbore)} · ${esc(ccShort(r.formation))} · ${r.top_md}–${r.base_md} m MD · median porosity ${r.porosity_p50 == null ? "–" : r.porosity_p50 + "%"}, kh ${ccKh(r.kh_p50_md)} mD</title></circle>`;
    }
    s += [A, B].map((c, i) => `<circle cx="${m.l + 10 + i * 150}" cy="12" r="5" fill="${CC_COLOURS[i]}"/><text x="${m.l + 20 + i * 150}" y="16" style="fill:var(--ink)">${esc(c.name)}</text>`).join("").replace(/cx="(\d+)"/g, (mm, v) => `cx="${+v + 110}"`).replace(/<text x="(\d+)" y="16"/g, (mm, v) => `<text x="${+v + 110}" y="16"`);
    $(id).innerHTML = s;
  };
  depthChart("cc-dphi", "phi");
  depthChart("cc-dkh", "kh");

  // ---- tables
  const tag = i => `<i class="dot" style="background:${CC_COLOURS[i]}"></i> ${esc(d.clusters[i].name)}`;
  $("cc-summary").innerHTML = `<tr><th>Cluster</th><th>Formation</th><th class="num">Wellbores</th><th class="num">Plugs</th><th class="num">Porosity P10, %</th><th class="num">P50, %</th><th class="num">P90, %</th><th class="num">kh median, mD</th><th class="num">kh wellbore range, mD</th><th class="num">Cored depth, m MD</th></tr>` +
    [A, B].flatMap((c, i) => c.formations.filter(p => p.samples >= d.min_chart_samples).map(p => `<tr><td>${tag(i)}</td><td>${esc(ccShort(p.formation))}</td><td class="num">${p.wellbores}</td><td class="num">${number(p.samples)}</td>
      <td class="num">${ccOne(p.porosity_p10)}</td><td class="num">${ccOne(p.porosity_p50)}</td><td class="num">${ccOne(p.porosity_p90)}</td><td class="num">${ccKh(p.kh_p50_md)}</td>
      <td class="num">${p.kh_min_md == null ? "–" : ccKh(p.kh_min_md) + " – " + ccKh(p.kh_max_md)}</td><td class="num">${number(p.top_md)} – ${number(p.base_md)}</td></tr>`)).join("");
  $("cc-rows").innerHTML = `<tr><th>Cluster</th><th>Wellbore</th><th>Formation</th><th class="num">Plugs</th><th class="num">With kh</th><th class="num">Porosity P10, %</th><th class="num">P50, %</th><th class="num">P90, %</th><th class="num">kh P50, mD</th><th class="num">kv/kh</th><th class="num">Top, m MD</th><th class="num">Base, m MD</th><th>Note</th></tr>` +
    [A, B].flatMap((c, i) => c.core.map(r => `<tr><td>${tag(i)}</td><td>${esc(r.wellbore)}</td><td>${esc(ccShort(r.formation))}</td><td class="num">${r.samples}</td><td class="num">${r.with_kh}</td>
      <td class="num">${ccOne(r.porosity_p10)}</td><td class="num">${ccOne(r.porosity_p50)}</td><td class="num">${ccOne(r.porosity_p90)}</td><td class="num">${ccKh(r.kh_p50_md)}</td><td class="num">${r.kv_over_kh_p50 ?? "–"}</td>
      <td class="num">${r.top_md}</td><td class="num">${r.base_md}</td><td>${esc(r.note || "")}</td></tr>`)).join("");
  $("cc-wells-table").innerHTML = `<tr><th>Cluster</th><th>Wellbore</th><th>Type</th><th>Field</th><th class="num">Distance, km</th><th>Core data</th></tr>` +
    [A, B].flatMap((c, i) => c.wells.map(w => `<tr><td>${tag(i)}</td><td>${esc(w.name)}${w.name === c.reference ? " (reference)" : ""}</td><td>${esc((w.type || "").toLowerCase())}</td><td>${esc(w.field || "")}</td>
      <td class="num">${w.distance_km.toFixed(1)}</td><td>${w.used ? "used" : w.has_core ? "available, not used" : "none"}</td></tr>`)).join("");

  // ---- notes
  const notUsed = A.cored_not_used.length + B.cored_not_used.length, flagged = [A, B].flatMap(c => c.core.filter(r => r.note).map(r => `${r.wellbore} ${ccShort(r.formation)}: ${r.note}`));
  const notes = [
    `A cluster here is every wellbore within ${R} km of the reference wellbore (great-circle distance between SODIR surface positions), so the two need not be the same size: ${number(A.wellbores)} wellbores in ${A.name} and ${number(B.wellbores)} in ${B.name}.` +
      [A, B].filter(c => c.fields.length > 1).map(c => ` The ${c.name} circle also takes in ${c.fields.filter(f => f.toLowerCase() !== c.name.toLowerCase()).map(f => f[0] + f.slice(1).toLowerCase()).join(", ")}.`).join(""),
    d.exploration_only ? `Core data is used from exploration and appraisal wellbores only: ${A.cored_wellbores.length} in ${A.name} and ${B.cored_wellbores.length} in ${B.name}.${notUsed ? ` ${number(notUsed)} development or other wellbores with core are left out; tick "Include core from development wellbores" to add them.` : ""}`
      : `Core data is used from every cored wellbore in range: ${A.cored_wellbores.length} in ${A.name} and ${B.cored_wellbores.length} in ${B.name}.`,
    "Pooled values are plug-weighted averages of each wellbore's percentiles, and for permeability a weighted geometric mean of the wellbore medians. They are close approximations, not percentiles of all plugs together.",
    "Depths are measured depths along the hole. In deviated wellbores true vertical depths are shallower.",
    flagged.length ? `Check these rows before comparing: ${flagged.slice(0, 12).join("; ")}${flagged.length > 12 ? "; and " + (flagged.length - 12) + " more in the table" : ""}. Air permeability reads higher than Klinkenberg-corrected values.` : "",
    `Formation names come from joining plug depths to SODIR formation tops, so a plug near a boundary can land in the neighbouring formation. Formations with fewer than ${d.min_chart_samples} plugs are not charted.`];
  $("cc-notes").innerHTML = notes.filter(Boolean).map(n => `<li>${esc(n)}</li>`).join("");
}

// Shelf locator: the coastline with both cluster circles at true size (equirectangular, true at 66° N).
async function ccLocator(d) {
  const layers = await (mapData || Promise.all(["land", "quadrants", "blocks", "fields"].map(name => fetch(`/static/geo/${name}.geojson`).then(r => r.json()))));
  if (ccData !== d) return;
  const land = layers[0], W = 330, H = 380, K = Math.cos(66 * Math.PI / 180);
  const points = d.clusters.flatMap(c => [[c.lon, c.lat]]).concat([[1, 57], [32, 57], [1, 74.5], [32, 74.5]]);
  const xs = points.map(p => p[0] * K), ys = points.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const scale = Math.min((W - 16) / (x1 - x0), (H - 16) / (y1 - y0)), ox = (W - (x1 - x0) * scale) / 2, oy = (H - (y1 - y0) * scale) / 2;
  const xy = (lon, lat) => [ox + (lon * K - x0) * scale, H - oy - (lat - y0) * scale];
  let path = "";
  const ring = r => { r.forEach((c, i) => { const p = xy(c[0], c[1]); path += (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }); path += "Z"; };
  for (const f of land.features || []) {
    const g = f.geometry;
    if (!g) continue;
    for (const polygon of g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : []) polygon.forEach(ring);
  }
  let s = `<defs><clipPath id="cc-ncs-clip"><rect x="0" y="0" width="${W}" height="${H}" rx="6"/></clipPath></defs>
    <rect x="0" y="0" width="${W}" height="${H}" rx="6" fill="var(--sea)"/><g clip-path="url(#cc-ncs-clip)">
    <path d="${path}" fill="var(--land)" stroke="var(--grid)" stroke-width=".6"/>`;
  for (const [text, lon, lat] of [["North Sea", 2.6, 58.4], ["Norwegian Sea", 4, 67.6], ["Barents Sea", 29, 73.4], ["Norway", 9.5, 61.3]]) {
    const p = xy(lon, lat);
    s += `<text x="${p[0]}" y="${p[1]}" text-anchor="middle" font-style="italic">${text}</text>`;
  }
  d.clusters.forEach((c, i) => {
    const p = xy(c.lon, c.lat), r = d.range_km / 111.19 * scale, left = p[0] > W / 2;
    s += `<circle cx="${p[0]}" cy="${p[1]}" r="${Math.max(r, 1.5)}" fill="${CC_COLOURS[i]}" fill-opacity=".3" stroke="${CC_COLOURS[i]}" stroke-width="1.5"/>
      <circle cx="${p[0]}" cy="${p[1]}" r="${Math.max(r + 8, 13)}" fill="none" stroke="${CC_COLOURS[i]}" stroke-dasharray="2 3"/>
      <text x="${p[0] + (left ? -18 : 18)}" y="${p[1] - 8}" text-anchor="${left ? "end" : "start"}" style="fill:var(--ink);font-weight:600;font-size:12px;paint-order:stroke;stroke:var(--sea);stroke-width:3px">${esc(c.name)}</text>
      <text x="${p[0] + (left ? -18 : 18)}" y="${p[1] + 6}" text-anchor="${left ? "end" : "start"}" style="paint-order:stroke;stroke:var(--sea);stroke-width:3px">${number(c.wellbores)} wellbores</text>`;
  });
  $("cc-ncs").innerHTML = s + "</g>";
}

// The two cluster maps: Leaflet on the shelf layers, fitted to the cluster circle.
const ccLeaflet = [null, null];
async function ccMaps(d) {
  const layers = await (mapData || Promise.all(["land", "quadrants", "blocks", "fields"].map(name => fetch(`/static/geo/${name}.geojson`).then(r => r.json()))));
  if (ccData !== d) return;
  d.clusters.forEach((c, i) => {
    if (!ccLeaflet[i]) {
      const map = createMap("cc-map-" + i, layers);
      L.control.scale({imperial: false}).addTo(map);
      const fit = L.control({position: "topright"});
      fit.onAdd = () => { const b = L.DomUtil.create("button"); b.type = "button"; b.textContent = "Fit cluster"; b.onclick = e => { e.stopPropagation(); ccLeaflet[i].fit(); }; return b; };
      fit.addTo(map);
      ccLeaflet[i] = {map, group: L.layerGroup().addTo(map)};
    }
    const {map, group} = ccLeaflet[i], colour = CC_COLOURS[i];
    group.clearLayers();
    const ring = L.circle([c.lat, c.lon], {radius: d.range_km * 1000, color: colour, weight: 1.6, fillColor: colour, fillOpacity: .06, interactive: false}).addTo(group);
    L.circle([c.lat, c.lon], {radius: d.range_km * 500, color: "#7d8a94", weight: 1, dashArray: "3 4", fill: false, interactive: false}).addTo(group);
    const order = {OTHER: 0, DEVELOPMENT: 1, EXPLORATION: 2};
    for (const w of [...c.wells].sort((a, b) => (order[a.type] ?? 0) - (order[b.type] ?? 0) || a.used - b.used)) {
      if (w.name === c.reference) continue;
      const style = w.used ? {radius: 5, color: "#fff", weight: 1, fillColor: colour, fillOpacity: .95}
        : w.type === "EXPLORATION" ? {radius: 4.5, color: "#52606b", weight: 1.5, fillColor: "#fff", fillOpacity: .9}
        : w.type === "DEVELOPMENT" ? {radius: 3, stroke: false, fillColor: "#7d8a94", fillOpacity: .85}
        : {radius: 2.2, stroke: false, fillColor: "#b4bec6", fillOpacity: .9};
      L.circleMarker([w.lat, w.lon], style).bindTooltip(`${esc(w.name)} · ${esc((w.type || "other").toLowerCase())} · ${w.distance_km} km${w.used ? " · core data used" : w.has_core ? " · core data, not used" : " · no core data"}${w.field ? " · " + esc(w.field) : ""}`).addTo(group);
    }
    L.circleMarker([c.lat, c.lon], {radius: 6.5, color: "#fff", weight: 2, fillColor: "#111", fillOpacity: 1})
      .bindTooltip(esc(c.reference), {permanent: true, direction: "right", offset: [8, 0], className: "ov-label"}).addTo(group);
    ccLeaflet[i].fit = () => { map.invalidateSize(); map.fitBounds(ring.getBounds(), {padding: [10, 10], animate: false}); };
    ccLeaflet[i].fit();
  });
}
