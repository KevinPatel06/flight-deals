import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULTS, ageDays, optionMatches, applyFilters, sortResults, toQuery, fromQuery, countDeals, hoursSince, localISODate,
  dealPct, googleTrend, compareOptions,
} from "../../site/filters.js";

const TODAY = "2026-10-08";
const opt = (o = {}) => ({
  airport: "LIS", depart: "2026-12-01", return: "2026-12-10", days: 9, price: 500, normal: 900,
  pct_off: 44, baseline: "month", airline: "TP", stops: 0, carry_on: true, checked_bag: false,
  bag_pieces: null, bag_kg: null, searched_on: "2026-10-07", age_days: 1, badges: [],
  links: { google: "https://www.google.com/travel/flights" }, ...o,
});
const dest = (city, region, options) => ({ city, name: city, country: "", country_code: "", region, best: options[0], options });
const f = (o = {}) => ({ ...DEFAULTS, regions: [], months: [], ...o });

test("ageDays counts whole days since the search", () => {
  assert.equal(ageDays(opt({ searched_on: "2026-10-05" }), TODAY), 3);
});

test("default filters keep a 44% fare", () => {
  assert.equal(optionMatches(opt(), f(), TODAY), true);
});

test("minimum percent off", () => {
  assert.equal(optionMatches(opt({ pct_off: 25 }), f(), TODAY), false);
  assert.equal(optionMatches(opt({ pct_off: 25 }), f({ minPct: 20 }), TODAY), true);
});

test("new routes show only when included", () => {
  const fresh = opt({ pct_off: null, normal: null, baseline: null });
  assert.equal(optionMatches(fresh, f(), TODAY), false);
  assert.equal(optionMatches(fresh, f({ includeNew: true }), TODAY), true);
});

test("max price, months and trip length", () => {
  assert.equal(optionMatches(opt({ price: 900 }), f({ maxPrice: 800 }), TODAY), false);
  assert.equal(optionMatches(opt(), f({ months: ["2027-01"] }), TODAY), false);
  assert.equal(optionMatches(opt(), f({ months: ["2026-12"] }), TODAY), true);
  assert.equal(optionMatches(opt({ days: 20 }), f({ maxDays: 14 }), TODAY), false);
  assert.equal(optionMatches(opt({ days: 3 }), f({ minDays: 5 }), TODAY), false);
});

test("nonstop and checked bag filters", () => {
  assert.equal(optionMatches(opt({ stops: 1 }), f({ nonstop: true }), TODAY), false);
  assert.equal(optionMatches(opt({ checked_bag: null }), f({ bag: true }), TODAY), false);
  assert.equal(optionMatches(opt({ checked_bag: false }), f({ bag: true }), TODAY), false);
  assert.equal(optionMatches(opt({ checked_bag: true }), f({ bag: true }), TODAY), true);
});

test("max age is measured from searched_on to today", () => {
  const old = opt({ searched_on: "2026-10-04" });
  assert.equal(optionMatches(old, f({ maxAge: 3 }), TODAY), false);
  assert.equal(optionMatches(old, f({ maxAge: 7 }), TODAY), true);
});

test("departures on or before today are hidden", () => {
  assert.equal(optionMatches(opt({ depart: TODAY }), f(), TODAY), false);
  assert.equal(optionMatches(opt({ depart: "2026-10-01" }), f(), TODAY), false);
});

test("applyFilters re-picks the best option from the matching ones", () => {
  const cheapNoBag = opt({ price: 400, pct_off: 55, checked_bag: false });
  const withBag = opt({ price: 520, pct_off: 42, checked_bag: true });
  const [result] = applyFilters([dest("LIS", "Europe", [cheapNoBag, withBag])], f({ bag: true }), TODAY);
  assert.equal(result.best.price, 520);
  assert.equal(result.options.length, 1);
});

test("applyFilters drops empty destinations and filters by region, including Other", () => {
  const data = [
    dest("LIS", "Europe", [opt()]),
    dest("QQQ", "Other", [opt()]),
    dest("CUN", "Caribbean", [opt({ pct_off: 10 })]),
  ];
  assert.deepEqual(applyFilters(data, f(), TODAY).map((r) => r.city).sort(), ["LIS", "QQQ"]);
  assert.deepEqual(applyFilters(data, f({ regions: ["Other"] }), TODAY).map((r) => r.city), ["QQQ"]);
});

test("applyFilters on no data", () => {
  assert.deepEqual(applyFilters([], f(), TODAY), []);
});

test("sort orders", () => {
  const a = { city: "A", best: opt({ pct_off: 50, price: 700, depart: "2027-01-01", searched_on: "2026-10-01" }) };
  const b = { city: "B", best: opt({ pct_off: 60, price: 900, depart: "2026-11-01", searched_on: "2026-10-07" }) };
  const c = { city: "C", best: opt({ pct_off: 50, price: 400, depart: "2026-12-01", searched_on: "2026-10-05" }) };
  const order = (sort) => sortResults([a, b, c], sort).map((r) => r.city).join("");
  assert.equal(order("pct"), "BCA");
  assert.equal(order("price"), "CAB");
  assert.equal(order("date"), "BCA");
  assert.equal(order("recent"), "BCA");
  assert.equal(order("bogus"), "BCA");
});

test("query string round trip", () => {
  const filters = f({ minPct: 40, maxPrice: 800, regions: ["Europe", "Caribbean"], months: ["2026-12"], nonstop: true, maxAge: 3, sort: "price" });
  assert.deepEqual(fromQuery(toQuery(filters)), filters);
  assert.equal(toQuery(f()), "");
});

test("fromQuery ignores junk", () => {
  assert.deepEqual(fromQuery("minPct=abc&sort=toString&regions="), f());
});

test("countDeals and hoursSince", () => {
  const data = [
    dest("LIS", "Europe", [opt({ pct_off: 35 })]),
    dest("CUN", "Caribbean", [opt({ pct_off: 20 })]),
    dest("OLD", "Europe", [opt({ pct_off: 80, depart: "2026-10-01" })]),
  ];
  assert.equal(countDeals(data, 30, TODAY), 1);
  assert.equal(hoursSince("2026-10-08T10:00:00Z", Date.parse("2026-10-08T13:30:00Z")), 3);
});

test("localISODate formats the local calendar date without locale data", () => {
  assert.equal(localISODate(new Date(2026, 0, 5, 23, 30)), "2026-01-05");
  assert.equal(localISODate(new Date(2026, 11, 31, 0, 1)), "2026-12-31");
});

const g = (o = {}) => ({ checked_on: "2026-10-08", status: "ok", price: 480, level: "low", typical: [600, 800],
  pct_below: 31, confirmed: true, history: [], ...o });

test("dealPct falls back to Google's percent for new routes", () => {
  assert.equal(dealPct(opt()), 44);
  assert.equal(dealPct(opt({ pct_off: null })), null);
  assert.equal(dealPct(opt({ pct_off: null, google: g() })), 31);
  assert.equal(dealPct(opt({ pct_off: null, google: { checked_on: "2026-10-08", status: "none" } })), null);
  assert.equal(dealPct(opt({ pct_off: 20, google: g() })), 20);
});

test("Google-checked new routes pass the percent filter without includeNew", () => {
  assert.equal(optionMatches(opt({ pct_off: null, google: g() }), f(), TODAY), true);
  assert.equal(optionMatches(opt({ pct_off: null, google: g({ pct_below: 10 }) }), f(), TODAY), false);
});

test("googleOnly keeps only options Google priced", () => {
  assert.equal(optionMatches(opt(), f({ googleOnly: true }), TODAY), false);
  assert.equal(optionMatches(opt({ google: { checked_on: "2026-10-08", status: "none" } }), f({ googleOnly: true }), TODAY), false);
  assert.equal(optionMatches(opt({ google: g() }), f({ googleOnly: true }), TODAY), true);
  assert.equal(fromQuery(toQuery(f({ googleOnly: true }))).googleOnly, true);
});

test("compareOptions and countDeals use the Google percent", () => {
  const checked = opt({ pct_off: null, price: 900, google: g({ pct_below: 50 }) });
  assert.equal([opt(), checked].sort(compareOptions)[0], checked);
  assert.equal(countDeals([dest("A", "Europe", [checked])], 30, TODAY), 1);
});

test("googleTrend describes the last three weeks", () => {
  assert.equal(googleTrend([["2026-09-01", 500], ["2026-09-17", 520], ["2026-10-08", 340]]), "↓35% in 3 weeks · lowest in 37 days");
  assert.equal(googleTrend([["2026-09-17", 400], ["2026-10-08", 480]]), "↑20% in 3 weeks");
  assert.equal(googleTrend([["2026-09-17", 500], ["2026-10-08", 510]]), "steady over 3 weeks");
  assert.equal(googleTrend([["2026-10-08", 500]]), null);
  assert.equal(googleTrend(undefined), null);
});

test("includeNew still shows every new route, Google-checked or not", () => {
  assert.equal(optionMatches(opt({ pct_off: null, google: g({ pct_below: 10 }) }), f({ includeNew: true }), TODAY), true);
});

test("Google's percent counts only when Google confirms our price", () => {
  assert.equal(dealPct(opt({ pct_off: null, price: 800, google: g({ price: 366, confirmed: false, pct_below: 44 }) })), null);
});

test("Google's 0% is not a deal percent", () => {
  assert.equal(dealPct(opt({ pct_off: null, google: g({ pct_below: 0 }) })), null);
});

test("googleTrend names the span it really covers and ignores bad dates", () => {
  assert.equal(googleTrend([["2026-10-01", 500], ["2026-10-08", 400]]), "↓20% in 7 days · lowest in 7 days");
  assert.equal(googleTrend([["2026-10-08", 500], ["2026-10-08", 400]]), null);
  assert.equal(googleTrend([["bad", 500], ["worse", 400]]), null);
});
