# Flight Deals Finder — Design Spec

**Date:** 2026-10-08
**Status:** Draft, awaiting review

## 1. Summary

A personal tool that scans cached flight prices out of Montréal (YUL) once a
day, works out what each destination *normally* costs from its own collected
history, and publishes a static web page where deals can be explored and
filtered by percent off, price, region, month, trip length, stops and
freshness.

Inspired by lesvolsdalexi.com, but without subscriptions, emails, or tiers:
every deal is visible, and the filters do the work.

## 2. Goals and non-goals

**Goals**
- Find round-trip fares from YUL that are well below the usual price for that
  destination.
- Let the user explore deals with filters, the primary one being minimum % off.
- Run unattended and free: GitHub Actions + GitHub Pages, no server, no paid APIs.
- Be honest about data quality: show how old every price is.

**Non-goals (v1)**
- Accounts, subscriptions, email/push alerts.
- Origins other than YUL (the design allows adding them later).
- Booking, hotels, packages.
- Live price verification (planned upgrade, see §12).

## 3. Data source findings (coverage test, 2026-10-06/08)

Source: Travelpayouts / Aviasales Data API (free token, cached prices from
Aviasales users' searches). Amadeus Self-Service was shut down 2026-07-17
and is not an option.

| Measure | Result |
|---|---|
| Fares in one 12-month `prices_for_dates` scan from YUL | 719 |
| `get_latest_prices` (year) extra coverage | 694 fares, 158 destinations |
| Destinations with a usable fare (2–30 days, ≤1 stop each way) | 106 |
| Destinations with a nonstop fare | 26 |
| Depth | 62 of 106 destinations had fares in only one departure month |
| Distribution | Heavy in the next 3 months; sparse 6+ months out |
| Freshness (`search_date` in link) | 26% 0–1 days, 14% 2 days, 60% 3–7 days |
| Rate limit | 600 requests/minute |

Spot check against Google Flights (same itineraries, CAD, 2026-10-08):
5 of 6 within 10%. The miss (Paris, French Bee) was 30% too low and was the
only "deal-like" fare: the cached price had expired. **Conclusion:** accuracy is
acceptable for regular fares; outlier fares are the most likely to be stale,
so freshness must be visible and filterable.

Cached prices are lowest fares and may exclude checked bags.

**Baggage signal (undocumented):** each `prices_for_dates` booking link carries
a `static_fare_key` such as `TY|P0|H1|L1_1_23|CH0|R0|TBC1`. Observed pattern:
`H0`/`H1` = carry-on not included / included; `L0` = no checked bag,
`L1_<pieces>_<kg>` = checked bag included (e.g. `L1_1_23` = 1 × 23 kg).
Airline distribution matches known policies (Qatar fares ~all `L1`; Air Transat
and Air Canada cheapest fares mostly `L0`). Treated as "likely" information,
not a guarantee. `get_latest_prices` rows carry no link, so their bag status is
unknown.

## 4. Architecture

```
GitHub Actions (daily cron)
  └─ scanner (Python 3.12 in CI, 3.10+ compatible, stdlib only at runtime)
       1. fetch     Source adapter → list[Fare]
       2. store     append to data/observations/YYYY-MM.csv.gz
       3. baseline  normal price per destination (and per dest+month when enough data)
       4. score     percent off, badges
       5. publish   site/deals.json (validated)
  └─ git commit data/ + site/deals.json
  └─ deploy site/ to GitHub Pages
Browser: site/index.html loads deals.json once, filters/sorts client-side.
```

### Repository layout

```
scanner/
  __main__.py          CLI entry: python -m scanner [--seed]
  fare.py              Fare dataclass (the shared record type)
  sources/
    base.py            Source protocol: fetch(origin) -> list[Fare]
    travelpayouts.py   Travelpayouts adapter (HTTP, paging, retries, normalization)
  places.py            IATA city -> name, country, region lookup
  store.py             read/append compressed monthly observation files
  scoring.py           daily-min, baselines, percent off, badges (pure functions)
  publish.py           build + validate deals.json
data/
  observations/YYYY-MM.csv.gz
  reference/cities.json, countries.json   (downloaded once from Travelpayouts)
  reference/regions.json                  (hand-maintained country -> region map)
site/
  index.html, style.css, app.js
  filters.js           pure filter/sort logic (tested)
  deals.json           generated
tests/
  python/              pytest tests + recorded API fixtures
  js/                  node:test tests for filters.js
.github/workflows/scan.yml
.env                   (git-ignored) TRAVELPAYOUTS_TOKEN for local runs
```

Each unit has one job and a narrow interface: `sources` only produce `Fare`
records; `scoring` only consumes records and returns scores; `publish` only
shapes output. Swapping or adding a source touches nothing else.

## 5. Fetching

**Daily scan** (`TravelpayoutsSource.fetch("YUL")`):
1. `GET /aviasales/v3/prices_for_dates` with `origin=YUL`, no destination,
   `departure_at` = each of the next 12 months (`YYYY-MM`), `one_way=false`,
   `currency=cad`, `market=ca`, `sorting=price`, `unique=false`,
   `limit=1000`, paged until a page returns < 1000 rows.
2. `GET /aviasales/v3/get_latest_prices` with `origin=YUL`, `currency=cad`,
   `period_type=year`, `beginning_of_period=<current year>`, `one_way=false`,
   `show_to_affiliates=false`, `market=ca`, `limit=1000`, paged.

Token sent in the `X-Access-Token` header, read from env `TRAVELPAYOUTS_TOKEN`.

**Normalization to `Fare`:**

| Field | prices_for_dates | get_latest_prices |
|---|---|---|
| `origin` | `origin_airport` | `origin` |
| `dest_airport` | `destination_airport` | `destination` (city code) |
| `dest_city` | `destination` | `destination` |
| `depart`, `return` | dates of `departure_at`, `return_at` | dates of `depart_date`, `return_date` |
| `trip_days` | computed | computed |
| `price` | `price` (CAD) | `value` (CAD) |
| `airline` | `airline` | empty |
| `stops` | `max(transfers, return_transfers)` | `number_of_changes` |
| `searched_on` | `search_date` param parsed from `link` (DDMMYYYY) | date of `found_at` |
| `link` | `https://www.aviasales.com` + `link` | empty (Google link used) |
| `carry_on` | `H1` → true, `H0` → false, missing → null | null |
| `checked_bag` | `L1_…` → true, `L0` → false, missing → null | null |
| `bag_pieces`, `bag_kg` | parsed from `L1_<pieces>_<kg>` (kg 0 → null) | null |
| `kind` | `dates` | `latest` (or `seed` when run with `--seed`) |

Plus `scan_date` (the run date, UTC) on every record.

**Kept only if:** `2 ≤ trip_days ≤ 30`, `stops ≤ 1`, `price > 0`, valid dates,
`depart` in the future, `searched_on` within the last 7 days (seed rows exempt
from the 7-day rule). Rejections are counted and logged by reason.

**De-duplication** within a scan: group by `(dest_city, depart, return,
obs_day)` and keep the lowest price, preferring `dates` rows on ties (they
carry airline, link and bag info). If the kept fare does not include a checked
bag, the cheapest bag-included fare in the group is kept as well, so a
bag-included option survives even when a cheaper no-bag fare exists.

**Reliability:** HTTP 429/5xx retried 3 times with backoff (2s, 4s, 8s). A
failed month is logged and skipped; the scan continues.

**Seeding:** `python -m scanner --seed` runs `get_latest_prices` for the current
and previous year and stores rows as `kind=seed` so baselines exist on day one.

## 6. Storage

`data/observations/YYYY-MM.csv.gz`, one file per scan month, appended each
run. Columns are the `Fare` fields above. Estimated volume: ~1,000–1,500 rows
per scan → a few MB per year compressed. Text + monthly rotation keeps git
history efficient. The baseline reads only the last 90 days (at most 4 files).

## 7. Baseline and scoring

**Observation day:** `obs_day = scan_date` for `dates`/`latest` rows and
`obs_day = searched_on` for `seed` rows (a seed run happens on one date but
represents many past days of searches).

**Daily minimum:** for each `(obs_day, dest_city)` and each
`(obs_day, dest_city, depart month)` keep only the cheapest price, so routes
with many date combinations don't dominate.

**Normal price** (computed from history *excluding* today's scan, trailing
90 days):
- **Month baseline**: median of daily minimums for `(dest_city, depart month)`,
  used when it has ≥ 7 distinct `obs_day`s.
- **Destination baseline**: otherwise, median of daily minimums for `dest_city`
  across all months, when ≥ 7 distinct `obs_day`s. Labelled "rough estimate".
- Otherwise no baseline: fare shown as **"new route"** with no percent off.

**Percent off** = `max(0, round(100 × (1 − price / normal)))`.

**Badges**
- `nonstop`: `stops == 0`
- `bag`: `checked_bag == true` (shows pieces × kg when known)
- `fresh`: age (today − `searched_on`) under 2 days; otherwise `aging` with age in days
- `error_fare`: percent off ≥ 70 ("possible error fare, verify")
- `unconfirmed`: percent off ≥ 40 and age ≥ 2 days ("verify before booking")
- `rough`: destination-level baseline used
- `new_route`: no baseline

## 8. Output: `site/deals.json`

```json
{
  "generated_at": "2026-10-08T10:05:00Z",
  "origin": "YUL",
  "scan": { "date": "2026-10-08", "fares": 1180, "destinations": 141,
            "rejected": { "trip_length": 101, "stops": 270, "stale": 40 } },
  "destinations": [
    {
      "city": "LIS", "name": "Lisbon", "country": "Portugal", "region": "Europe",
      "best": { "...": "Fare (below)" },
      "options": [ { "...": "Fare (below)" } ]
    }
  ]
}
```

Fare object: `airport, depart, return, days, price, normal, pct_off (null if
new route), baseline ("month"|"destination"|null), airline, stops,
carry_on, checked_bag, bag_pieces, bag_kg, searched_on, age_days, badges[],
links { aviasales?, google }`.

Baselines are computed from all fares (lowest fare, bag or not), so a
bag-included fare's percent off is measured against the usual lowest fare and
tends to look smaller. That is intended: it reflects the real price difference.

`best` is the option with the highest percent off (ties → lowest price;
new routes → lowest price). Google link format:
`https://www.google.com/travel/flights?q=Flights%20from%20YUL%20to%20{AIRPORT}%20on%20{DEPART}%20through%20{RETURN}`.

All scored destinations are included (not just deals) so the 0% end of the
slider works. Before writing, the file is validated (required keys, types,
non-empty destinations); invalid output fails the run.

## 9. Web page

Static `index.html` + `app.js` + `filters.js` + `style.css`. No build step,
no framework.

**Header:** "Last scan: N hours ago · X destinations · Y deals ≥ 30%". Turns
orange when the scan is > 24 h old.
Note: "Lowest fares found; may not include checked bags. Prices change fast,
so verify before booking."

**Filters** (sidebar on desktop, collapsible panel on mobile):
- Minimum % off: slider 0–90, default 30
- Max price
- Region / country (multi-select)
- Departure months (multi-select)
- Trip length min/max days
- Nonstop only
- Checked bag included (hides fares where it is false or unknown; when on,
  each card's best fare is re-picked from bag-included options)
- Max price age: 2 / 3 / 7 days (default 7)
- Include new routes (default off)

**Sort:** % off (default), price, departure date, most recently searched.

**Card per destination:** name + flag, best price vs normal, % off badge,
nonstop/airline, best dates, "searched N days ago", Aviasales + Google Flights
buttons, expandable list of other date options (each with price, % off, age,
links). Badges from §7 shown as small labels.

Filter state is mirrored in the URL query string so views can be bookmarked.

## 10. Operations and error handling

**Hosting:** public repo `KevinPatel06/flight-deals` + GitHub Pages (free).
Nothing secret is committed; the token lives only in Actions secrets.

**Workflow** `.github/workflows/scan.yml`:
- Triggers: daily cron (10:00 UTC), manual `workflow_dispatch` (optional
  seed), and pushes to `main` that change `scanner/`, `site/`, `tests/` or the
  workflow (so code changes deploy). Bot commits do not re-trigger runs.
- Steps: checkout → setup Python 3.12 → `pytest` → `node --test` →
  `python -m scanner` → commit `data/` and `site/deals.json` if changed →
  deploy `site/` with `actions/upload-pages-artifact` + `actions/deploy-pages`.
- Secret: `TRAVELPAYOUTS_TOKEN`. Permissions: `contents: write`,
  `pages: write`, `id-token: write`.

**Failure behaviour:**
- Tests fail → no scan, no publish.
- Scan returns < 50% of the trailing 7-scan average fare count → fail without
  publishing (skipped when fewer than 3 prior scans exist).
- Invalid `deals.json` → fail without publishing.
- Any failure leaves the previous `deals.json` live. GitHub emails the repo
  owner on failed runs; the stale-scan header is the second signal.

**Secrets:** the token never appears in code or committed files. Local runs
read it from a git-ignored `.env`. The token was shared in a chat session and
should be regenerated in Travelpayouts once setup is complete.

## 11. Testing

**Python (pytest, no network):**
- `scoring`: daily-min collapsing, 90-day window, exclusion of today, ≥7-day
  rule, month vs destination fallback, percent-off clamp at 0, every badge rule.
- `sources/travelpayouts`: recorded real responses as fixtures → paging,
  normalization (both endpoints), `search_date` parsing, `static_fare_key`
  baggage parsing (`L0`, `L1_1_23`, `L1_2_0`, missing key), filters, de-dup
  (including bag/no-bag twins), retry on 429/5xx (stubbed HTTP).
- `store`: append + read across month boundaries, gzip round-trip.
- `publish`: schema validation rejects malformed output; `best` selection.
- Small-scan guard.

**JavaScript (`node --test`):** `filters.js` — each filter, combined filters,
sort orders, URL state round-trip.

**Manual acceptance:** after the first week of scans, spot-check 5 top deals
against Google Flights, and 3 bag-included fares against the airline's fare
details to confirm the `static_fare_key` reading.

## 12. Future upgrades (not in v1)

- **Live verification:** done — see 2026-10-08-google-checks-design.md.
- Additional origins (YQB, YOW, BTV/PBG) via the `origin` parameter.
- Alerts (email / push) for deals above a chosen threshold.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Stale outlier fares look like deals | Freshness badges, max-age filter, `unconfirmed` flag, Google check link |
| Thin data 6+ months out | Accepted; destination-level fallback baseline; coverage grows daily |
| Baselines weak in the first weeks | Seed run; "new route" / "rough" labels |
| Seasonality hidden by destination-level baseline | Month baseline preferred once it has ≥ 7 days |
| Travelpayouts API changes | Isolated adapter + fixture tests make breakage obvious and local |
| Public Pages URL | Only public fare data is published; no secrets |
| Baggage field is undocumented and may change or be misread | Parsed defensively (unknown → null); label says "bag likely included"; fixture tests; manual acceptance check |
