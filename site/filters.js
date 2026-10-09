// Pure filter, sort and URL-state logic for the deals page. No DOM access, so Node can test it.

export const DEFAULTS = Object.freeze({
  minPct: 30, maxPrice: null, regions: [], months: [], minDays: 2, maxDays: 30,
  nonstop: false, bag: false, maxAge: 7, includeNew: false, googleOnly: false, sort: "pct",
});

const DAY_MS = 86_400_000;
const NUMBERS = ["minPct", "maxPrice", "minDays", "maxDays", "maxAge"];
const BOOLEANS = ["nonstop", "bag", "includeNew", "googleOnly"];
const LISTS = ["regions", "months"];

export function ageDays(option, today) {
  return Math.floor((Date.parse(today) - Date.parse(option.searched_on)) / DAY_MS);
}

// Our own percent off when history exists; otherwise Google's percent below its typical price,
// but only when Google confirmed our fare (otherwise that percent describes Google's fare, not ours).
export function dealPct(o) {
  return o.pct_off ?? (o.google?.confirmed ? o.google.pct_below : null) ?? null;
}

export function googleTrend(history) {
  if (!Array.isArray(history) || history.length < 2) return null;
  const [lastDay, last] = history[history.length - 1];
  const cutoff = Date.parse(lastDay) - 21 * DAY_MS;
  const before = history.find(([day]) => Date.parse(day) >= cutoff)[1];
  const change = Math.round((100 * (last - before)) / before);
  const parts = [Math.abs(change) < 5 ? "steady over 3 weeks" : `${change < 0 ? "↓" : "↑"}${Math.abs(change)}% in 3 weeks`];
  if (last <= Math.min(...history.map(([, price]) => price))) parts.push("lowest in 60 days");
  return parts.join(" · ");
}

export function optionMatches(o, f, today) {
  if (o.depart <= today) return false;
  // "Include new routes" shows every route without our own history, even when Google priced it.
  const pct = dealPct(o);
  if (o.pct_off === null && f.includeNew) {
    // shown regardless of percent
  } else if (pct === null || pct < f.minPct) {
    return false;
  }
  if (f.googleOnly && o.google?.status !== "ok") return false;
  if (f.maxPrice !== null && o.price > f.maxPrice) return false;
  if (f.months.length && !f.months.includes(o.depart.slice(0, 7))) return false;
  if (o.days < f.minDays || o.days > f.maxDays) return false;
  if (f.nonstop && o.stops !== 0) return false;
  if (f.bag && o.checked_bag !== true) return false;
  if (ageDays(o, today) > f.maxAge) return false;
  return true;
}

export function compareOptions(a, b) {
  return (dealPct(b) ?? -1) - (dealPct(a) ?? -1) || a.price - b.price;
}

const SORTERS = {
  pct: (a, b) => compareOptions(a.best, b.best),
  price: (a, b) => a.best.price - b.best.price,
  date: (a, b) => a.best.depart.localeCompare(b.best.depart),
  recent: (a, b) => b.best.searched_on.localeCompare(a.best.searched_on),
};

export function sortResults(results, sort) {
  const sorter = Object.hasOwn(SORTERS, sort) ? SORTERS[sort] : SORTERS.pct;
  return [...results].sort(sorter);
}

export function applyFilters(destinations, f, today) {
  const results = [];
  for (const d of destinations) {
    if (f.regions.length && !f.regions.includes(d.region)) continue;
    const options = d.options.filter((o) => optionMatches(o, f, today)).sort(compareOptions);
    if (options.length) results.push({ ...d, best: options[0], options });
  }
  return sortResults(results, f.sort);
}

export function countDeals(destinations, minPct, today) {
  return destinations.filter((d) => d.options.some((o) => o.depart > today && dealPct(o) !== null && dealPct(o) >= minPct)).length;
}

// YYYY-MM-DD for the viewer's local calendar day, built by hand: toLocaleDateString output varies by browser.
export function localISODate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function hoursSince(iso, nowMs) {
  return Math.floor((nowMs - Date.parse(iso)) / 3_600_000);
}

export function toQuery(f) {
  const params = new URLSearchParams();
  for (const key of LISTS) if (f[key].length) params.set(key, f[key].join(","));
  for (const key of BOOLEANS) if (f[key]) params.set(key, "1");
  for (const key of NUMBERS) if (f[key] !== null && f[key] !== DEFAULTS[key]) params.set(key, String(f[key]));
  if (f.sort !== DEFAULTS.sort) params.set("sort", f.sort);
  return params.toString();
}

export function fromQuery(search) {
  const params = new URLSearchParams(search);
  const f = { ...DEFAULTS, regions: [], months: [] };
  for (const key of NUMBERS) {
    if (!params.get(key)) continue;
    const n = Number(params.get(key));
    if (Number.isFinite(n)) f[key] = n;
  }
  for (const key of BOOLEANS) if (params.has(key)) f[key] = params.get(key) === "1";
  for (const key of LISTS) if (params.get(key)) f[key] = params.get(key).split(",");
  const sort = params.get("sort");
  if (sort && Object.hasOwn(SORTERS, sort)) f.sort = sort;
  return f;
}
