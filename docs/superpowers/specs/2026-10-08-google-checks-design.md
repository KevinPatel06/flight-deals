# Google Flights checks — design

Date: 2026-10-08. Status: approved in conversation, pending spec review.
Extends `2026-10-08-flight-deals-design.md` (§12 "Live verification").

## 1. Summary

Each scan checks up to 7 international deals per day on Google Flights through
SerpApi (free plan: 250 searches/month). A check returns the live price,
Google's verdict (`low` / `typical` / `high`), Google's typical price range,
and 60 days of price history for that exact trip. The page shows this on the
card, and Google's "% below typical" gives new routes a percent-off before our
own history exists.

## 2. Findings that shaped this (spike, 2026-10-08, 6 searches)

| Trip (YUL →)      | Departs in | Google now | Typical      | Level   | 60-day history |
|-------------------|-----------:|-----------:|--------------|---------|----------------|
| CDG Nov 17–24     | 40 d       | 732        | 670–750      | typical | 671 → 732, rising |
| FLL Nov 17–24     | 40 d       | 431        | 340–440      | typical | 335–525, bouncy |
| CUN Jan 12–19     | 96 d       | 366        | 410–495      | low     | 557 → 348, sale |
| BOG Jan 12–19     | 96 d       | 771        | 610–1050     | typical | 687–793, slow rise |
| PUJ Feb 16–23     | 131 d      | 424        | 465–710      | low     | 420–520 |
| HND Mar 9–23      | 152 d      | 1880       | 1250–2450    | typical | 2073 → 1880, falling |

- Prices move in dips of 10–40% within 60 days; catching dips beats calendar rules.
- Travelpayouts data (266 usable fares) only supports Europe-level patterns
  (Tue/Wed departures ~2–4% cheaper; Dec and Jul ~13% above normal). Region
  tips wait until our own history is larger (re-run the analysis in 1–2 months).
- Day-of-week-you-book: no evidence (8 days of search dates).

## 3. Goals and non-goals

Goals:
- Check the most promising **international** fares daily, within the free plan.
- Show live price, Google verdict, typical range, % below typical, and a
  60-day trend on checked cards.
- Never let a check failure stop or delay the daily publish.

Non-goals:
- Checks for destinations in Canada (all of them, including Vancouver and
  Calgary) or the United States (except Hawaii), and for cities missing from
  the reference data; the regular scan covers those.
- Deal-blog feed (next project), region tips (later, from our own history),
  alerts, extra origins.
- Mixing Google prices into Travelpayouts baselines.

## 4. Selection (`scanner/verify.py`, pure)

Constants: `DAILY_LIMIT = 7`, `REPEAT_DAYS = 3`, `MIN_LEFT = 10`, `PICK_PCT = 30`,
`MAX_ERRORS = 2` (in `verify.py`); `SHOW_DAYS = 7`, `CONFIRM_TOLERANCE = 0.10`
(in `publish.py`). `pick` receives the last 90 days of checks so "most recently
checked" looks back further than the 7 days shown on the page.

`pick(scored, places, checks, today, limit=DAILY_LIMIT) -> list[ScoredFare]`

1. `remaining = limit − (checks with checked_on == today)`; if ≤ 0, pick nothing.
2. Group today's scored fares by `dest_city`; drop cities whose country code is
   `CA`, `US` (except Hawaii: HNL, OGG, KOA, LIH, ITO, MKK, LNY, JHM, MUE) or
   empty (unknown city). Only checks from the same origin count anywhere below.
   A city whose newest check was `none` rests `NONE_REST_DAYS = 14`, whatever its dates. Each city contributes only its
   best option (same ranking as `publish.pick_best`).
3. Skip a city whose best option (same `airport`, `depart`, `return`) has a
   check with `checked_on > today − REPEAT_DAYS`.
4. Tier 1: best option `pct_off ≥ PICK_PCT`, ordered by `pct_off` desc, then price asc.
   Tier 2: best option `pct_off is None`, ordered by the city's most recent
   `checked_on` (never-checked first), then price asc.
   Cities with `pct_off` 0–29 are not checked.
5. Return the first `remaining` of tier 1 + tier 2.

## 5. SerpApi source (`scanner/sources/serpapi.py`)

- `GoogleCheck` (frozen dataclass in `scanner/check.py`, so storage does not import the source): `checked_on: date`, `origin`, `dest_city`,
  `airport`, `depart: date`, `return_date: date`, `tp_price: int`,
  `status: "ok" | "none"`, `price: int | None`, `level: str | None`,
  `typical_low/typical_high: int | None`, `history: tuple[(date, int), ...]`,
  `url: str | None`. Properties: `trip` and `pct_below` (see §7).
- Search: `GET https://serpapi.com/search.json` with `engine=google_flights`,
  `departure_id`, `arrival_id` (= `fare.dest_airport`), `outbound_date`,
  `return_date`, `type=1`, `stops=2` (one stop or fewer, matching the scan's
  `MAX_STOPS = 1`), `currency=CAD`, `gl=ca`, `hl=en`, `api_key`.
- `parse_check(data, fare, checked_on) -> GoogleCheck`:
  - `price` = `price_insights.lowest_price`, else min `price` across
    `best_flights` + `other_flights`; none found → `status="none"`.
  - `level` = `price_insights.price_level`; `typical_*` from
    `typical_price_range` (2 ints, else None); `history` from
    `price_history` (`[unix_ts, price]` → UTC date); `url` =
    `search_metadata.google_flights_url` when it is an `https://` URL.
  - Response `error` containing "hasn't returned any results" → `status="none"`.
    Any other `error` → raises `ApiError`.
- Quota: `GET https://serpapi.com/account.json` (free, does not use a search) →
  `plan_searches_left` (int). If fewer than `MIN_LEFT`, no searches run.
- HTTP goes through `scanner/net.py` (not `http.py`, which would shadow the stdlib `http` package) (`http_get_json`, `ApiError`, retries),
  moved from `sources/travelpayouts.py` (which re-exports both names).
  `ApiError` messages contain the URL without query string, so the key never
  appears in logs; callers print `str(err)` only, never tracebacks.

## 6. Storage

`data/checks/YYYY-MM.csv.gz` by `checked_on` month, gzip members appended,
header once (same pattern as observations). Columns: `checked_on, origin,
dest_city, airport, depart, return, tp_price, status, price, level,
typical_low, typical_high, history, url`. `history` is `YYYY-MM-DD:price`
pairs joined with `;`. `store.append_checks(checks, data_dir)` and
`store.load_checks(data_dir, since)` (filters `checked_on >= since`).

## 7. Output: `deals.json`

For each option, the newest check with the same `airport`, `depart`,
`return` and `checked_on > today − SHOW_DAYS` is attached as `google`:

```json
"google": {
  "checked_on": "2026-10-08", "status": "ok",
  "price": 366, "level": "low", "typical": [410, 495],
  "pct_below": 19, "confirmed": true,
  "history": [["2026-08-09", 557], ["2026-08-10", 557]],
  "url": "https://www.google.com/travel/flights?..."
}
```

- `pct_below = max(0, round(100 × (1 − price / ((low + high) / 2))))`;
  null when there is no typical range.
- `confirmed = price ≤ option price today × (1 + CONFIRM_TOLERANCE)` (today's fare, not the price on the check day).
- `status: "none"` → `{"checked_on", "status"}` only.
- When `url` exists it replaces `links.google` for that option.
- `scan.google = {"checked_today": n, "searches_left": n | null, "note": str | null}`.
- `validate` accepts options with or without `google`; when present it must be
  a dict with `status` in `ok/none`, and `ok` requires an int `price`.

## 8. Page

- Card line for a checked best option:
  `Google ✓ $366 · low · typical $410–495 · 19% below typical · ↓34% in 3 weeks` + sparkline.
  - ✓ when `confirmed`; otherwise `⚠ Google shows $X` in warn style.
  - `status:"none"` → `Google: no matching flights found` (muted).
  - Trend: last history price vs the point 21 days earlier; `|change| < 5%`
    → "steady over 3 weeks"; add "lowest in 60 days" when last ≤ min.
  - Sparkline: inline SVG polyline of `history`, `aria-hidden`, text carries meaning.
- Option rows (other dates) show a small `✓ Google $X` when checked.
- Deal percent: `dealPct(o) = o.pct_off ?? o.google?.pct_below ?? null` drives
  the min-% filter, `includeNew`, sorting and the header count. When the value
  comes from Google the card label reads `−19% vs Google`.
- New filter `googleOnly` (checkbox "Google-checked only"), in the URL query
  like the others; default off.

## 9. Scan flow (`scanner/__main__.py`)

After scoring, before `build_deals`:
1. If no checker: skip with note. A checker is built from `SERPAPI_KEY`
   (environment, then `.env`) only when the Travelpayouts source is not
   injected; tests pass a fake checker explicitly.
2. `checks = load_checks(data, since=today − 90 days)`; `picks = pick(...)`.
3. If picks: read quota; if `< MIN_LEFT` skip with note. Else check each pick;
   on `ApiError` print one line and move to the next pick; stop after
   `MAX_ERRORS` consecutive errors (bad key, outage). Failed checks are not saved.
4. `append_checks(new)`; `build_deals(..., checks=checks + new)`.
5. Print `google: n checked, n confirmed, searches left n` (or the note).
`--seed` and failed/too-small scans never run checks.

## 10. Workflow and secrets

- `scan.yml` scan step gets `SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}`; the
  existing commit step already adds `data/`.
- Secret set from `.env` by Claude (`gh secret set -f .env`); the key is
  never printed, committed or placed on a command line.

## 11. Testing

- Fixture `tests/python/fixtures/serpapi_cun.json`, trimmed from the real
  CUN response (ids and `search_parameters` removed).
- `pick`: Canada excluded (incl. YVR/YYC); tier order; repeat-day skip;
  daily cap counts today's checks; nothing when cap reached.
- `parse_check`: fixture values; fallback to flight prices; no-results error →
  `none`; other error → `ApiError`; non-https URL dropped.
- `pct_below`, `confirmed`; store round trip of checks incl. history.
- `build_deals` attaches newest in-window check and swaps the link; validate.
- `main`: checks run with a fake checker, quota floor skips, `ApiError`
  keeps the publish, missing key prints a note, seed runs no checks.
- JS: `dealPct`, filter/sort/count with Google pct, `googleOnly`, trend text.

## 12. Risks

- Google's typical range is for the searched trip; Travelpayouts fare may have
  a different airline/stops. Mitigated by tolerance and showing both prices.
- SerpApi free plan terms or quota may change; quota floor keeps us from
  overrunning, and failures degrade to the current page.
- `dest_airport` codes Google doesn't know → `none`, not retried for 3 days.

## 13. Follow-up changes (2026-10-09)

- Trip identity includes the origin: `(origin, airport, depart, return)`.
- Typical ranges with low > high are dropped.
- The quota floor is never crossed: a run spends at most `searches_left − MIN_LEFT`.
- The page treats Google's `pct_below` as a deal percent only when the check
  is `confirmed` and the percent is above 0; the trend names the span it covers.
- The workflow's bot push retries up to 3 times after `git pull --rebase`, so a
  push to main during a scan cannot discard the day's checks.
