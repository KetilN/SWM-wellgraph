// Pressure window: mud weight used, leak-off tests, kicks and losses against depth, for the
// selected wellbore and the wellbores within a chosen range. Everything is in sg equivalent mud weight.
const PW = {mud: "#23475e", lot: "#a86800", kick: "#ad3948", loss: "#087b8d", offset: "#8a98a3"};
let pwKey, pwData;

async function initPressure() {
  const well = state.well, range = $("pw-range").value;
  $("pw-range").onchange = () => initPressure().catch(showError);
  const key = well + "|" + range;
  if (key === pwKey) return;
  $("pw-summary").textContent = "Loading pressure data around " + well + "…";
  const d = await api("/api/pressure", {name: well, range_km: range});
  if (state.view !== "pressure" || state.well !== well || $("pw-range").value !== range) return;
  pwKey = key;
  pwData = d;
  renderPressure(d);
}

function pwShape(kind, x, y, colour, filled, extra = "") {
  const fill = filled ? colour : "var(--panel)", common = `fill="${fill}" stroke="${colour}" stroke-width="1.4" ${extra}`;
  if (kind === "lot") return `<path d="M${x} ${y - 5}l5 5-5 5-5-5Z" ${common}>`;
  if (kind === "kick") return `<path d="M${x} ${y - 5}l5 9h-10Z" ${common}>`;
  if (kind === "loss") return `<path d="M${x} ${y + 5}l5-9h-10Z" ${common}>`;
  return `<circle cx="${x}" cy="${y}" r="${filled ? 3.2 : 2}" fill="${colour}" fill-opacity="${filled ? 1 : .4}" ${extra}>`;
}

function renderPressure(d) {
  const w = d.well, o = d.offset, all = [...w.mud, ...w.lot, ...w.events, ...o.mud, ...o.lot, ...o.events];
  $("pw-title").textContent = `Pressure window: ${d.wellbore}${d.range_km ? ` and wellbores within ${d.range_km} km` : ""}`;
  const count = (rows, type) => rows.filter(r => !type || r.event_type === type).length;
  $("pw-summary").textContent = all.length
    ? `${d.wellbore}: ${w.mud.length} mud checks, ${w.lot.length} leak-off tests, ${count(w.events, "kick")} kicks and ${count(w.events, "lost_circulation")} losses with a stated mud weight.` +
      (d.range_km ? ` ${d.offsets.length}${d.offsets_capped ? " nearest" : ""} offset wellbores with pressure data: ${o.mud.length} mud checks, ${o.lot.length} leak-off tests, ${count(o.events, "kick")} kicks, ${count(o.events, "lost_circulation")} losses.` : "")
    : `No mud checks, leak-off tests, or kicks and losses with a stated mud weight for ${d.wellbore}${d.range_km ? " or the wellbores in range" : ""}. Try a wider range.`;
  // ---- plot
  const W = 560, H = 660, m = {l: 58, r: 96, t: 34, b: 16};
  // the depth axis ends just below the selected wellbore; deeper offset data (long deviated wells) is left off
  const own = Math.max(d.total_depth_md || 0, ...[...w.mud, ...w.lot, ...w.events].map(r => r.md));
  const depthMax = Math.max(500, Math.ceil((own || Math.max(0, ...all.map(r => r.md))) / 500) * 500);
  for (const part of [o, w]) for (const key of ["mud", "lot", "events"]) part[key] = part[key].filter(r => r.md <= depthMax);
  const sgs = all.map(r => r.sg), x0 = Math.min(1.0, Math.floor(Math.min(1.0, ...sgs) * 10) / 10), x1 = Math.max(2.0, Math.ceil(Math.max(2.0, ...sgs) * 10) / 10);
  const x = v => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r), y = v => m.t + v / depthMax * (H - m.t - m.b);
  let s = `<rect x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="var(--panel)" stroke="var(--line)"/>`;
  w.tops.forEach((t, i) => {
    const a = y(Math.min(t.top_md, depthMax)), b = y(Math.min(t.base_md ?? depthMax, depthMax));
    if (b - a < 1) return;
    s += `<rect x="${m.l}" y="${a}" width="${W - m.l - m.r}" height="${b - a}" fill="${i % 2 ? "var(--strata-b)" : "var(--strata-a)"}" fill-opacity=".35"/>` +
      (b - a > 11 ? `<text x="${W - m.r + 6}" y="${(a + b) / 2 + 4}">${esc(t.formation.replace(/ GP$/, "").toLowerCase().replace(/^./, c => c.toUpperCase()))}</text>` : "");
  });
  for (let v = Math.ceil(x0 * 10) / 10; v <= x1 + 1e-9; v += .1) {
    const major = Math.round(v * 10) % 2 === 0;
    s += `<line x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${H - m.b}" stroke="var(--grid)" stroke-width="${major ? .6 : .25}"/>` + (major ? `<text x="${x(v)}" y="${m.t - 6}" text-anchor="middle">${v.toFixed(1)}</text>` : "");
  }
  for (let v = 0; v <= depthMax; v += 500) s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width=".5"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${number(v)}</text>`;
  s += `<text x="${(m.l + W - m.r) / 2}" y="12" text-anchor="middle" style="fill:var(--ink)">Equivalent mud weight, sg</text><text x="12" y="${m.t - 8}">m MD</text>
    <line x1="${x(1.03)}" x2="${x(1.03)}" y1="${m.t}" y2="${H - m.b}" stroke="var(--muted)" stroke-dasharray="5 4"/>`;
  for (const h of w.shoes) if (h.casing_depth_md != null && h.casing_depth_md <= depthMax)
    s += `<path d="M${m.l} ${y(h.casing_depth_md)}h9l-9 -9Z" fill="var(--ink)"/><title>${inches(h.casing_diameter_in)} casing shoe at ${fmt(h.casing_depth_md)} m</title>`;
  const tip = (r, what) => `<title>${esc(r.wellbore)} · ${what} · ${fmt(r.sg, 2)} sg at ${fmt(r.md)} m MD${r.unit ? " · " + esc(r.unit) : ""}${r.description ? " · " + esc(r.description.slice(0, 140)) : ""}</title>`;
  // offsets first, faint
  s += o.mud.map(r => pwShape("mud", x(r.sg).toFixed(1), y(r.md).toFixed(1), PW.offset, false) + tip(r, "mud check") + "</circle>").join("");
  s += o.lot.map(r => pwShape("lot", x(r.sg).toFixed(1), y(r.md).toFixed(1), PW.lot, false, 'opacity=".75"') + tip(r, `leak-off test below ${inches(r.casing_diameter_in)} casing`) + "</path>").join("");
  s += o.events.map(r => pwShape(r.event_type === "kick" ? "kick" : "loss", x(r.sg).toFixed(1), y(r.md).toFixed(1), r.event_type === "kick" ? PW.kick : PW.loss, false, 'opacity=".8"') + tip(r, r.event_type === "kick" ? "kick" : "losses") + "</path>").join("");
  // the selected wellbore on top
  if (w.mud.length > 1) s += `<path d="${w.mud.map((r, i) => (i ? "L" : "M") + x(r.sg).toFixed(1) + " " + y(r.md).toFixed(1)).join("")}" fill="none" stroke="${PW.mud}" stroke-width="1.8"/>`;
  s += w.mud.map(r => pwShape("mud", x(r.sg).toFixed(1), y(r.md).toFixed(1), PW.mud, true) + tip(r, "mud check") + "</circle>").join("");
  s += w.lot.map(r => pwShape("lot", x(r.sg).toFixed(1), y(r.md).toFixed(1), PW.lot, true) + tip(r, `leak-off test below ${inches(r.casing_diameter_in)} casing`) + "</path>").join("");
  s += w.events.map(r => pwShape(r.event_type === "kick" ? "kick" : "loss", x(r.sg).toFixed(1), y(r.md).toFixed(1), r.event_type === "kick" ? PW.kick : PW.loss, true, `data-event="${esc(r.event_id)}"`) + tip(r, r.event_type === "kick" ? "kick" : "losses") + "</path>").join("");
  $("pw-plot").setAttribute("viewBox", `0 0 ${W} ${H}`);
  $("pw-plot").innerHTML = s;
  for (const node of $("pw-plot").querySelectorAll("[data-event]")) node.onclick = () => select(node.dataset.event);
  // ---- legend
  const icon = (kind, colour, filled) => `<svg viewBox="0 0 16 14">${pwShape(kind, 8, 7, colour, filled)}${kind === "mud" ? "</circle>" : "</path>"}</svg>`;
  $("pw-legend").innerHTML = `
    <span>${icon("mud", PW.mud, true)}Mud weight used in ${esc(d.wellbore)} (SODIR mud checks, joined by a line)</span>
    <span>${icon("lot", PW.lot, true)}Leak-off test at a casing shoe: the fracture side</span>
    <span>${icon("loss", PW.loss, true)}Losses, at the mud weight the report states: the well took mud at or near this weight</span>
    <span>${icon("kick", PW.kick, true)}Kick, at the mud weight the report states: pore pressure was at or above this weight</span>
    <span>${icon("lot", PW.offset, false)}Hollow or faint symbols: the same from offset wellbores</span>
    <span><svg viewBox="0 0 16 14"><line x1="8" x2="8" y1="0" y2="14" stroke="var(--muted)" stroke-dasharray="4 3"/></svg>Seawater gradient, 1.03 sg</span>
    <span><svg viewBox="0 0 16 14"><path d="M3 11h9l-9-9Z" fill="var(--ink)"/></svg>Casing shoe of ${esc(d.wellbore)}. Select one of its kicks or losses to read the report text.</span>`;
  // ---- depth table
  const v = n => n == null ? "–" : fmt(n, 2);
  $("pw-bins").innerHTML = `<tr><th>Depth, m MD</th><th class="num">Mud used, sg (min – median – max)</th><th class="num">Wellbores</th><th class="num">Leak-off, sg</th><th class="num">Kicks</th><th class="num">Highest mud at a kick</th><th class="num">Losses</th><th class="num">Lowest mud at losses</th></tr>` +
    d.bins.filter(b => b.from_md < depthMax).map(b => `<tr><td>${number(b.from_md)}–${number(b.to_md)}</td><td class="num">${b.mud_checks ? `${v(b.mud_min_sg)} – ${v(b.mud_median_sg)} – ${v(b.mud_max_sg)}` : "–"}</td><td class="num">${b.mud_wellbores || "–"}</td>
      <td class="num">${b.lot_tests ? (b.lot_min_sg === b.lot_max_sg ? v(b.lot_min_sg) : `${v(b.lot_min_sg)} – ${v(b.lot_max_sg)}`) + ` (${b.lot_tests})` : "–"}</td>
      <td class="num">${b.kicks || "–"}</td><td class="num">${v(b.kick_max_sg)}</td><td class="num">${b.losses || "–"}</td><td class="num">${v(b.loss_min_sg)}</td></tr>`).join("");
  $("pw-note").textContent = "Pore pressure itself (formation pressure tests and prognosis curves) is not extracted from the reports yet, so the pore side shows only where a kick marks it. " +
    `The plot and table stop at ${number(depthMax)} m, just below ${d.wellbore}. ` + "Depths are measured depths, not true vertical: in deviated wellbores the same pressure plots deeper. Mud weights at kicks and losses are those stated in the report; SODIR-filled values are left out. " +
    "Mud checks and leak-off tests come from SODIR and exist almost only for exploration wellbores." + (d.offsets_capped ? " Only the 80 nearest offset wellbores with data are used." : "");
}
