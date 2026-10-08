import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULTS, ageDays, optionMatches, applyFilters, sortResults, toQuery, fromQuery, countDeals, hoursSince, localISODate,
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
