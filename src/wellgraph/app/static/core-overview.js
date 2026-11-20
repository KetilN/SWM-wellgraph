const CROSS_FILTERS = {
  field: "All fields", formation: "All formations", lithology: "All rock types",
  grain_size: "All grain sizes", kh_method: "All methods"
};
const CROSS_COLOURS = ["#16549a", "#16795b", "#a86800", "#ad414e", "#784795"];
let crossFiltersReady, crossRequest = 0, crossResult;

function crossCategory(value, key) {
  if (value === "(unassigned)") return "Unassigned";
  return key === "kh_method" ? methodLabel(value) : value;
}

async function initCoreOverview() {
  if (!crossFiltersReady) {
    crossFiltersReady = api("/api/core/filters").then(options => {
      for (const [key, title] of Object.entries(CROSS_FILTERS)) {
        const select = $("cross-" + key);
        select.innerHTML = `<option value="">${title}</option>` + options[key].map(value =>
          `<option value="${esc(value)}">${esc(crossCategory(value, key))}</option>`).join("");
        select.onchange = () => loadCoreOverview().catch(showError);
      }
      $("cross-colour").onchange = () => loadCoreOverview().catch(showError);
      $("cross-context").onchange = renderCoreOverview;
      $("cross-trends").onchange = renderCoreOverview;
      $("cross-reset").onclick = () => {
        for (const key of Object.keys(CROSS_FILTERS)) $("cross-" + key).value = "";
        loadCoreOverview().catch(showError);
      };
    }).catch(err => { crossFiltersReady = null; throw err; });
  }
  await crossFiltersReady;
  if (state.view !== "core-overview") return;
  if (crossResult) renderCoreOverview();
  else await loadCoreOverview();
}

async function loadCoreOverview() {
  const request = ++crossRequest;
  const filters = Object.fromEntries(Object.keys(CROSS_FILTERS).map(key => [key, $("cross-" + key).value]));
  const colour = $("cross-colour").value, filtered = Object.values(filters).some(Boolean);
  $("cross-note").textContent = "Loading paired core measurements…";
  try {
    const data = await api("/api/core/overview", {...filters, colour_by: colour});
    if (request !== crossRequest) return;
    crossResult = {data, colour, filtered};
    if (state.view === "core-overview") renderCoreOverview();
  } catch (err) {
    if (request === crossRequest && state.view === "core-overview") {
      $("cross-note").textContent = "Could not load this selection.";
      throw err;
    }
  }
}

function crossMarker(i, x, y, colour, title = "") {
  const start = `fill="${colour}" fill-opacity=".78"`;
  const shapes = [
    () => `<circle cx="${x}" cy="${y}" r="3.1" ${start}>`,
    () => `<rect x="${x - 3}" y="${y - 3}" width="6" height="6" ${start}>`,
    () => `<path d="M${x} ${y - 4} l3.5 6 h-7 Z" ${start}>`,
    () => `<path d="M${x} ${y - 4} l4 4 -4 4 -4 -4 Z" ${start}>`,
    () => `<circle cx="${x}" cy="${y}" r="3.1" fill="none" stroke="${colour}" stroke-width="1.4">`,
  ];
  const tags = ["circle", "rect", "path", "path", "circle"];
  return shapes[i]() + title + `</${tags[i]}>`;
}

function renderCoreOverview() {
  if (!crossResult) return;
  const {data: d, filtered, colour} = crossResult;
  const {phi_max: phiMax, log_min: low, log_max: high} = d.bounds;
  const [nx, ny] = d.resolution;
  const W = 820, L = 77, R = 18, T = 18, B = 414;
  const plotWidth = W - R - L, plotHeight = B - T;
  const x = phi => L + phi / phiMax * plotWidth;
  const y = log => B - (log - low) / (high - low) * plotHeight;
  const trends = $("cross-trends").checked;
  $("cross-context").disabled = !filtered;
  $("cross-reset").disabled = !filtered;
  $("cross-trends").disabled = !d.series.some(s => Number.isFinite(s.slope) && Number.isFinite(s.intercept));
  const backdrop = !filtered || $("cross-context").checked;
  const fullPeak = Math.max(1, ...d.bins.map(bin => bin[2]));
  const matchPeak = Math.max(1, ...d.bins.map(bin => bin[3]));
  const baseColour = d.series.length || filtered ? "#7f9aab" : "#086c80";
  const filterColour = "#ba592c";
  let svg = `<defs><clipPath id="cross-clip"><rect x="${L}" y="${T}" width="${plotWidth}" height="${plotHeight}"/></clipPath></defs>
    <rect x="${L}" y="${T}" width="${plotWidth}" height="${plotHeight}" fill="var(--panel)" stroke="var(--line)"/>`;
  for (let phi = 0; phi <= phiMax; phi += 10) {
    svg += `<line x1="${x(phi)}" x2="${x(phi)}" y1="${T}" y2="${B}" stroke="var(--line)"/>
      <text x="${x(phi)}" y="${B + 17}" text-anchor="middle">${phi}</text>`;
  }
  const step = Math.max(1, Math.ceil((high - low) / 10));
  for (let log = low; log <= high; log += step) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(log)}" y2="${y(log)}" stroke="var(--line)"/>
      <text x="${L - 8}" y="${y(log) + 4}" text-anchor="end">${khLabel(10 ** log)}</text>`;
  }
  svg += `<text x="${L + plotWidth / 2}" y="463" text-anchor="middle">Core porosity (%)</text>
    <text x="19" y="${(T + B) / 2}" transform="rotate(-90 19 ${(T + B) / 2})" text-anchor="middle">Horizontal permeability (mD, log scale)</text>
    <g clip-path="url(#cross-clip)">`;
  const density = (index, peak, colour, layer, alpha) => `<g data-layer="${layer}">` + d.bins.filter(bin => bin[index] > 0).map(bin => {
    const [col, row, count, matching] = bin;
    const intensity = Math.log1p(bin[index]) / Math.log1p(peak);
    return `<rect x="${L + col / nx * plotWidth}" y="${B - (row + 1) / ny * plotHeight}" width="${plotWidth / nx + .15}"
      height="${plotHeight / ny + .15}" fill="${colour}" opacity="${alpha * (.15 + .85 * intensity)}"><title>${number(count)} plugs in bin${filtered ? ` · ${number(matching)} match filters` : ""}</title></rect>`;
  }).join("") + "</g>";
  if (backdrop) svg += density(2, fullPeak, baseColour, "full", d.series.length || filtered ? .56 : 1);
  if (filtered) svg += density(3, matchPeak, filterColour, "selection", d.series.length ? .48 : 1);
  for (const [i, group] of d.series.entries()) {
    const ink = CROSS_COLOURS[i], name = crossCategory(group.category, colour);
    svg += `<g data-layer="series">${group.points.map(([phi, log]) =>
      crossMarker(i, x(phi), y(log), ink, `<title>${esc(name)} · ${fmt(phi, 1)}% · ${khLabel(10 ** log)} mD</title>`)).join("")}</g>`;
    if (trends && Number.isFinite(group.slope) && Number.isFinite(group.intercept) && group.phi_p90 > group.phi_p10) {
      const fit = phi => group.intercept + phi * group.slope;
      svg += `<line data-layer="trend" x1="${x(group.phi_p10)}" x2="${x(group.phi_p90)}"
        y1="${y(fit(group.phi_p10))}" y2="${y(fit(group.phi_p90))}" stroke="${ink}"
        stroke-width="2.4" stroke-dasharray="5 4"><title>${esc(name)} · fit from ${number(group.pairs)} pairs</title></line>`;
    }
  }
  if (!d.pairs || (filtered && !d.matching && !backdrop)) {
    svg += `<text x="${L + plotWidth / 2}" y="${(T + B) / 2}" text-anchor="middle">${d.pairs ? "No paired core data match these filters." : "No paired core data are loaded."}</text>`;
  }
  $("core-density").innerHTML = svg + "</g>";
  const key = (label, colour) => `<span><i class="core-density-key" style="background:linear-gradient(90deg,#f0f5f8,${colour})"></i>${label}</span>`;
  $("cross-legend").innerHTML = (backdrop ? key("All paired plugs", baseColour) : "") +
    (filtered ? key("Filter matches", filterColour) : "");
  $("cross-scope").textContent = filtered ? "Filter matches" : "All paired core";
  const values = [["Paired plugs", filtered ? `${number(d.matching)} / ${number(d.pairs)}` : number(d.pairs)],
    ["Wellbores", number(d.wellbores)], ["Median porosity", d.median_phi == null ? "–" : fmt(d.median_phi, 1) + "%"],
    ["Median horizontal permeability", d.median_kh == null ? "–" : khLabel(d.median_kh) + " mD"]];
  $("cross-stats").innerHTML = values.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
  $("cross-series").hidden = !d.series.length;
  $("cross-series").innerHTML = d.series.map((group, i) =>
    `<li><svg viewBox="0 0 14 14" aria-hidden="true">${crossMarker(i, 7, 7, CROSS_COLOURS[i])}</svg>
      <span><strong>${esc(crossCategory(group.category, colour))}</strong>${number(group.points.length)} shown · ${number(group.pairs)} total</span></li>`).join("");
  $("cross-note").textContent = !d.pairs ? "This snapshot has no positive paired measurements."
    : filtered && !d.matching ? "No paired measurements match these filters."
    : "Darker cells contain more plugs. Each density layer has its own scale." +
      (d.series.length ? " Colored markers show samples from up to five largest named groups. Dashed lines fit all pairs in each eligible group over its central 80% porosity range." : "");
}
