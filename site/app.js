import {
  DEFAULTS, ageDays, applyFilters, countDeals, dealPct, fromQuery, googleTrend, hoursSince, localISODate, toQuery,
} from "./filters.js";

const AIRLINES = {
  AA: "American", AC: "Air Canada", AF: "Air France", AM: "Aeroméxico", AT: "Royal Air Maroc",
  AV: "Avianca", B6: "JetBlue", BA: "British Airways", BF: "French Bee", CM: "Copa", DL: "Delta",
  DM: "Arajet", EI: "Aer Lingus", EK: "Emirates", F8: "Flair", FI: "Icelandair", IB: "Iberia",
  KL: "KLM", LH: "Lufthansa", LX: "SWISS", OS: "Austrian", PD: "Porter", QR: "Qatar Airways",
  S4: "Azores Airlines", SN: "Brussels Airlines", TK: "Turkish Airlines", TP: "TAP Air Portugal",
  TS: "Air Transat", UA: "United", UX: "Air Europa", WS: "WestJet",
};
const FRESH_DAYS = 2;
const ERROR_PCT = 70;
const UNCONFIRMED_PCT = 40;

const today = localISODate(new Date());
const form = document.getElementById("filters");
const results = document.getElementById("results");
const status = document.getElementById("status");
let destinations = [];

const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (n) => "$" + n.toLocaleString("en-CA");
const fmtDate = (iso) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });
const fmtMonth = (ym) => new Date(`${ym}-15T12:00:00`).toLocaleDateString("en-CA", { month: "long", year: "numeric" });
const flag = (cc) =>
  /^[A-Z]{2}$/.test(cc || "") ? String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)) : "✈️";
const airline = (code) => (code ? AIRLINES[code] ?? code : "Airline n/a");
const stopsText = (o) => (o.stops === 0 ? "Nonstop" : `${o.stops} stop${o.stops > 1 ? "s" : ""}`);

function ageText(o) {
  const n = ageDays(o, today);
  return n <= 0 ? "today" : n === 1 ? "yesterday" : `${n} days ago`;
}

function bagText(o) {
  const kg = o.bag_kg ? ` × ${o.bag_kg} kg` : "";
  return `🧳 ${o.bag_pieces ?? 1}${kg} bag likely included`;
}

function badges(o) {
  const age = ageDays(o, today);
  const items = [[age < FRESH_DAYS ? "Fresh" : "Aging", ""]];
  if (o.checked_bag === true) items.push([bagText(o), ""]);
  if (o.baseline === "destination") items.push(["Rough estimate", ""]);
  if (o.pct_off === null) items.push(["New route", ""]);
  if (o.pct_off !== null && o.pct_off >= ERROR_PCT) items.push(["Possible error fare", "warn"]);
  if (o.pct_off !== null && o.pct_off >= UNCONFIRMED_PCT && age >= FRESH_DAYS) items.push(["Verify before booking", "warn"]);
  return `<ul class="badges">${items.map(([text, cls]) => `<li class="${cls}">${esc(text)}</li>`).join("")}</ul>`;
}

function links(o) {
  const aviasales = o.links.aviasales
    ? `<a href="${esc(o.links.aviasales)}" target="_blank" rel="noopener">Aviasales</a>`
    : "";
  return `<div class="links">${aviasales}<a href="${esc(o.links.google)}" target="_blank" rel="noopener">Google Flights</a></div>`;
}

function sparkline(history) {
  const prices = history.map(([, price]) => price);
  const min = Math.min(...prices);
  const span = Math.max(...prices) - min || 1;
  const points = prices
    .map((p, i) => `${((i * 100) / (prices.length - 1)).toFixed(1)},${(19 - ((p - min) * 18) / span).toFixed(1)}`)
    .join(" ");
  return `<svg class="spark" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}"/></svg>`;
}

function googleLine(o) {
  const g = o.google;
  if (!g) return "";
  if (g.status !== "ok") return `<p class="google muted">Google: no matching flights found (${esc(g.checked_on)})</p>`;
  const head = g.confirmed ? `Google ✓ ${money(g.price)}` : `⚠ Google shows ${money(g.price)}`;
  const parts = [
    g.level,
    g.typical ? `typical ${money(g.typical[0])}–${money(g.typical[1])}` : null,
    g.pct_below > 0 ? `${g.pct_below}% below typical` : null,
    googleTrend(g.history),
  ].filter(Boolean);
  const spark = Array.isArray(g.history) && g.history.length > 1 ? sparkline(g.history) : "";
  return `<div class="google${g.confirmed ? "" : " warn"}"><p><strong>${esc(head)}</strong>${parts.length ? " · " + esc(parts.join(" · ")) : ""}</p>${spark}</div>`;
}

function pctBadge(o) {
  if (o.pct_off !== null) return `<span class="pct">−${o.pct_off}%</span>`;
  const pct = dealPct(o);
  return pct === null
    ? `<span class="pct new">New</span>`
    : `<span class="pct google" title="Below Google's typical price">−${pct}% vs Google</span>`;
}

function optionRow(o) {
  const pct = dealPct(o) === null ? "" : ` (−${dealPct(o)}%${o.pct_off === null ? " vs Google" : ""})`;
  const bag = o.checked_bag === true ? " · 🧳" : "";
  const checked = o.google?.status === "ok" ? ` · ${o.google.confirmed ? "✓" : "⚠"} Google ${money(o.google.price)}` : "";
  return `<li>
    <span><strong>${money(o.price)}</strong>${pct} · ${fmtDate(o.depart)} – ${fmtDate(o.return)} (${o.days} days)</span>
    <span class="age">${esc(stopsText(o))} · ${esc(airline(o.airline))} · searched ${ageText(o)}${bag}${checked}</span>
    ${links(o)}
  </li>`;
}

function card(d) {
  const b = d.best;
  const more = d.options.slice(1);
  const normal = b.normal ? ` <span class="normal">normally ~${money(b.normal)}</span>` : "";
  const extra = more.length
    ? `<details class="more"><summary>${more.length} more date option${more.length > 1 ? "s" : ""}</summary><ul>${more.map(optionRow).join("")}</ul></details>`
    : "";
  return `<article class="card">
    <header>
      <h2>${flag(d.country_code)} ${esc(d.name)}<span class="country">${esc(d.country || d.city)} · ${esc(b.airport)}</span></h2>
      ${pctBadge(b)}
    </header>
    <p class="price"><strong>${money(b.price)}</strong>${normal}</p>
    <p class="meta">${esc(stopsText(b))} · ${esc(airline(b.airline))}</p>
    <p class="dates">${fmtDate(b.depart)} – ${fmtDate(b.return)} (${b.days} days)</p>
    ${googleLine(b)}
    ${badges(b)}
    <p class="age">Searched ${ageText(b)}</p>
    ${links(b)}
    ${extra}
  </article>`;
}

function readForm() {
  const data = new FormData(form);
  const num = (key) => {
    const value = data.get(key);
    return value === null || value === "" ? null : Number(value);
  };
  return {
    minPct: num("minPct") ?? DEFAULTS.minPct,
    maxPrice: num("maxPrice"),
    regions: data.getAll("regions"),
    months: data.getAll("months"),
    minDays: num("minDays") ?? DEFAULTS.minDays,
    maxDays: num("maxDays") ?? DEFAULTS.maxDays,
    nonstop: data.has("nonstop"),
    bag: data.has("bag"),
    maxAge: num("maxAge") ?? DEFAULTS.maxAge,
    includeNew: data.has("includeNew"),
    googleOnly: data.has("googleOnly"),
    sort: data.get("sort") || DEFAULTS.sort,
  };
}

function writeForm(f) {
  const el = form.elements;
  el.minPct.value = f.minPct;
  el.maxPrice.value = f.maxPrice ?? "";
  for (const option of el.regions.options) option.selected = f.regions.includes(option.value);
  for (const option of el.months.options) option.selected = f.months.includes(option.value);
  el.minDays.value = f.minDays;
  el.maxDays.value = f.maxDays;
  el.nonstop.checked = f.nonstop;
  el.bag.checked = f.bag;
  el.includeNew.checked = f.includeNew;
  el.googleOnly.checked = f.googleOnly;
  el.maxAge.value = String(f.maxAge);
  el.sort.value = f.sort;
}

function fillOptions(select, values, label) {
  select.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc(label(v))}</option>`).join("");
}

function render() {
  const f = readForm();
  document.getElementById("minPct-out").textContent = `${f.minPct}%`;
  const query = toQuery(f);
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
  const shown = applyFilters(destinations, f, today);
  results.innerHTML = shown.length
    ? shown.map(card).join("")
    : `<p class="empty">No deals match these filters. Try a lower minimum % off, allow older prices, or tick "Include new routes" (price history is still building for many destinations).</p>`;
}

async function start() {
  if (matchMedia("(max-width: 800px)").matches) document.getElementById("filters-panel").open = false;
  let data;
  try {
    const response = await fetch("deals.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(String(response.status));
    data = await response.json();
  } catch {
    status.textContent = "No scan data yet. Check back after the first scan.";
    return;
  }
  destinations = data.destinations;
  const hours = hoursSince(data.generated_at, Date.now());
  const ago = hours < 1 ? "under an hour" : `${hours} hour${hours === 1 ? "" : "s"}`;
  const google = data.scan.google;
  const checks = google
    ? ` · Google checked ${google.checked_today} today${google.searches_left !== null ? ` (${google.searches_left} left this month)` : ""}`
    : "";
  status.textContent = `Last scan: ${ago} ago · ${destinations.length} destinations · ${countDeals(destinations, 30, today)} deals ≥ 30%${checks}`;
  status.classList.toggle("stale", hours > 24);

  fillOptions(form.elements.regions, [...new Set(destinations.map((d) => d.region))].sort(), (r) => r);
  const months = new Set(destinations.flatMap((d) => d.options.map((o) => o.depart.slice(0, 7))));
  fillOptions(form.elements.months, [...months].sort(), fmtMonth);

  writeForm(fromQuery(location.search));
  form.addEventListener("input", render);
  form.addEventListener("change", render);
  form.addEventListener("reset", () => setTimeout(render));
  render();
}

start();
