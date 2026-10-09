# Google Flights Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each daily scan checks up to 7 international deals on Google Flights (SerpApi free plan) and shows the live price, Google's verdict, typical range, % below typical and a 60-day trend on the card.

**Architecture:** A new SerpApi source turns one search into a `GoogleCheck`; a pure `pick` chooses which fares to check; checks are stored in gzipped monthly CSVs beside observations; `publish` attaches the newest in-window check to matching options; the page renders it and uses Google's % for new routes.

**Tech Stack:** Python 3.10+ stdlib only (urllib, csv, gzip, dataclasses), pytest; vanilla ES modules, `node --test`; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-08-google-checks-design.md` (extends `docs/superpowers/specs/2026-10-08-flight-deals-design.md`)

## Global Constraints

- Python ≥ 3.10, no new runtime dependencies; pytest only for tests.
- `SERPAPI_KEY` is never printed, committed, placed on a command line, or included in an error message. `ApiError` messages name the URL without its query string; callers print `str(err)` only.
- No checks for destinations whose country code is `CA` (Vancouver and Calgary included).
- `DAILY_LIMIT = 7` per UTC day, `REPEAT_DAYS = 3`, `MIN_LEFT = 10`, `PICK_PCT = 30`, `MAX_ERRORS = 2`, `SHOW_DAYS = 7`, `CONFIRM_TOLERANCE = 0.10`.
- A check failure never changes the scan's exit code or blocks publishing.
- SerpApi search params: `engine=google_flights, type=1, stops=2, currency=CAD, gl=ca, hl=en`.
- Page colours come from existing CSS tokens (soft stone theme); no new colours.

## Review Focus

1. A malformed SerpApi body (non-dict, `price_insights` missing/odd types, string prices) → parse returns a sane `GoogleCheck` or raises `ApiError`, never `TypeError`/`KeyError`. Test: `test_parse_check_tolerates_odd_types` (Task 2).
2. Key leakage: an `HTTPError` from SerpApi must surface as `ApiError("HTTP 401 for https://serpapi.com/search.json")` with no `api_key`. Test: `test_check_error_message_has_no_key` (Task 2).
3. Same-day re-runs (code pushes) must not exceed 7 checks per day. Test: `test_pick_counts_todays_checks_against_the_cap` (Task 4) and `test_second_run_same_day_spends_nothing_more` (Task 6).
4. Old `deals.json` options without `google`, and `google.status == "none"` objects, must render and filter without errors. Tests: `dealPct falls back...` and `googleOnly...` (Task 7).
5. A check whose trip is no longer in today's scan is not shown; a check older than 7 days is not shown. Test: `test_build_deals_ignores_old_and_unmatched_checks` (Task 5).

---

### Task 1: Move the HTTP helper to `scanner/net.py`

**Files:**
- Create: `scanner/net.py`, `tests/python/test_net.py`
- Modify: `scanner/sources/travelpayouts.py` (remove lines 4, 6–10 imports that become unused, constants `BACKOFF`/`RETRY_STATUS` at 27–28, `ApiError` and `http_get_json` at 190–212; import them from `scanner.net`)
- Modify: `tests/python/test_travelpayouts_source.py` (move the HTTP tests out, lines 132–205 except `test_non_object_rows_are_counted_invalid`)

**Interfaces:**
- Produces: `scanner.net.ApiError`, `scanner.net.http_get_json(url, params, headers, *, opener=urllib.request.urlopen, sleep=time.sleep) -> dict`, `scanner.net.BACKOFF`, `scanner.net.RETRY_STATUS`. `scanner.sources.travelpayouts` keeps exporting `ApiError` and `http_get_json` (same objects).

- [ ] **Step 1: Write the failing test file** `tests/python/test_net.py` — move these from `test_travelpayouts_source.py` verbatim: `FakeResponse`, `opener_from`, `http_error`, `test_http_get_json_retries_then_succeeds`, `test_http_get_json_does_not_retry_auth_errors`, `test_http_get_json_gives_up_after_three_retries`, `test_http_get_json_rejects_non_json`, `test_http_get_json_retries_low_level_network_errors`, `test_http_get_json_turns_repeated_timeouts_into_api_error`. Header:

```python
import http.client
import urllib.error

import pytest

from scanner import net
from scanner.net import ApiError, http_get_json
from scanner.sources import travelpayouts as tp
```

Add:

```python
def test_travelpayouts_re_exports_the_shared_helper():
    assert tp.http_get_json is net.http_get_json
    assert tp.ApiError is net.ApiError


def test_error_messages_leave_out_the_query_string():
    with pytest.raises(ApiError) as info:
        http_get_json("https://api.example/x", {"api_key": "SECRET"}, {}, opener=opener_from([http_error(401)]), sleep=lambda s: None)
    assert "SECRET" not in str(info.value)
```

Remove the moved tests and the now-unused imports (`http.client`, `urllib.error`) from `test_travelpayouts_source.py`; its import line becomes `from scanner.sources.travelpayouts import ApiError, TravelpayoutsSource, months_from` only if `ApiError` is still used there (check with grep; drop it otherwise).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/python/test_net.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.net'`

- [ ] **Step 3: Create `scanner/net.py`**

```python
"""JSON-over-HTTPS GET with retries, shared by every source.

Error messages name the URL without its query string, so API keys passed as parameters never reach logs.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BACKOFF = (2, 4, 8)
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    """A request failed for good (after retries, or with a non-retryable status)."""


def http_get_json(url: str, params: dict, headers: dict, *, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
    """GET url?params as JSON. Retries 429/5xx and network errors with 2s, 4s, 8s waits."""
    request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers)
    for attempt in range(len(BACKOFF) + 1):
        last = attempt == len(BACKOFF)
        try:
            with opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code not in RETRY_STATUS or last:
                raise ApiError(f"HTTP {err.code} for {url}") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            # Timeouts and dropped connections while reading surface as bare OSError/HTTPException.
            if last:
                raise ApiError(f"network error for {url}: {getattr(err, 'reason', err)!r}") from None
        except ValueError:
            raise ApiError(f"invalid JSON from {url}") from None
        sleep(BACKOFF[attempt])
    raise AssertionError("unreachable")
```

(`from None` drops the chained `HTTPError`, whose `.url` holds the full query string, so even a traceback cannot show the key.)

In `scanner/sources/travelpayouts.py`: delete `import http.client`, `import json`, `import time`, `import urllib.error`, `import urllib.parse`, `import urllib.request` (keep `from urllib.parse import unquote`; first grep that `json`/`time` are not used elsewhere in the file), delete `BACKOFF`, `RETRY_STATUS`, `class ApiError`, `def http_get_json`, and add after `from scanner.fare import Fare`:

```python
from scanner.net import ApiError, http_get_json  # noqa: F401  (re-exported for callers and tests)
```

- [ ] **Step 4: Run the whole Python suite**

Run: `python -m pytest -q`
Expected: all pass (86 before; now 88 — 2 new tests, the moved ones counted once).

- [ ] **Step 5: Commit**

```bash
git add scanner/net.py scanner/sources/travelpayouts.py tests/python/test_net.py tests/python/test_travelpayouts_source.py
git commit -m "refactor: shared HTTP helper in scanner.net; errors never chain the full URL"
```

---

### Task 2: `GoogleCheck` and the SerpApi source

**Files:**
- Create: `scanner/check.py`, `scanner/sources/serpapi.py`, `tests/python/fixtures/serpapi_cun.json`, `tests/python/test_serpapi.py`

**Interfaces:**
- Consumes: `scanner.net.ApiError`, `scanner.net.http_get_json`; `scanner.fare.Fare`.
- Produces:
  - `scanner.check.GoogleCheck(checked_on, origin, dest_city, airport, depart, return_date, tp_price, status, price=None, level=None, typical_low=None, typical_high=None, history=(), url=None)` with `.trip -> (airport, depart, return_date)` and `.pct_below -> int | None`.
  - `scanner.sources.serpapi.parse_check(data, fare, checked_on) -> GoogleCheck`
  - `scanner.sources.serpapi.SerpApiChecker(key, get_json=None)` with `.check(fare, today) -> GoogleCheck` and `.searches_left() -> int | None`.

- [ ] **Step 1: Create the fixture** from the spike's saved real response (no key inside: the spike removed `search_parameters.api_key`; this trim also drops ids and endpoints):

```bash
python -I - "$TMP/spike/serp_CUN.json" tests/python/fixtures/serpapi_cun.json <<'EOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))
out = {
    "search_metadata": {"status": d["search_metadata"]["status"], "google_flights_url": d["search_metadata"]["google_flights_url"]},
    "best_flights": [{"price": f["price"], "type": f.get("type")} for f in d["best_flights"]],
    "other_flights": [{"price": f["price"], "type": f.get("type")} for f in d["other_flights"]],
    "price_insights": d["price_insights"],
}
assert "api_key" not in json.dumps(out)
open(dst, "w", encoding="utf-8").write(json.dumps(out, indent=1))
EOF
```

Expected: file written; `price_insights.lowest_price == 366`, `typical_price_range == [410, 495]`, 61 history points, first price 557.

- [ ] **Step 2: Write the failing tests** `tests/python/test_serpapi.py`

```python
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from factories import TODAY, make_fare
from scanner.check import GoogleCheck
from scanner.net import ApiError, http_get_json
from scanner.sources.serpapi import ACCOUNT_URL, SEARCH_URL, SerpApiChecker, parse_check

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "serpapi_cun.json").read_text(encoding="utf-8"))
CUN = make_fare(dest_city="CUN", dest_airport="CUN", depart_date=date(2027, 1, 12), return_date=date(2027, 1, 19), price=380)


def test_parse_check_reads_real_response():
    check = parse_check(FIXTURE, CUN, TODAY)
    assert check.status == "ok"
    assert (check.price, check.level, check.typical_low, check.typical_high) == (366, "low", 410, 495)
    assert len(check.history) == 61
    assert check.history[0] == (date(2026, 8, 9), 557)
    assert check.url.startswith("https://www.google.com/travel/flights")
    assert check.trip == ("CUN", date(2027, 1, 12), date(2027, 1, 19))
    assert (check.tp_price, check.dest_city, check.checked_on) == (380, "CUN", TODAY)


def test_pct_below_uses_the_middle_of_the_typical_range():
    assert parse_check(FIXTURE, CUN, TODAY).pct_below == 19  # 1 - 366 / 452.5
    base = dict(checked_on=TODAY, origin="YUL", dest_city="X", airport="X", depart=TODAY, return_date=TODAY, tp_price=1)
    assert GoogleCheck(status="ok", price=600, typical_low=400, typical_high=500, **base).pct_below == 0
    assert GoogleCheck(status="ok", price=600, **base).pct_below is None


def test_price_falls_back_to_cheapest_flight():
    check = parse_check({"best_flights": [{"price": 420}], "other_flights": [{"price": 399}, {"x": 1}]}, CUN, TODAY)
    assert (check.status, check.price, check.level, check.history, check.url) == ("ok", 399, None, (), None)


def test_no_results_is_status_none():
    body = {"error": "Google Flights hasn't returned any results for this query."}
    assert parse_check(body, CUN, TODAY).status == "none"
    assert parse_check({"best_flights": []}, CUN, TODAY).status == "none"


def test_other_errors_raise():
    with pytest.raises(ApiError, match="Invalid"):
        parse_check({"error": "Invalid departure_id"}, CUN, TODAY)
    with pytest.raises(ApiError):
        parse_check(["not", "a", "dict"], CUN, TODAY)


def test_parse_check_tolerates_odd_types():
    body = {
        "search_metadata": {"google_flights_url": "javascript:alert(1)"},
        "price_insights": {"lowest_price": "366", "typical_price_range": [410], "price_history": [[1, "x"], "junk", [1786579200, 300]],
                           "price_level": 5},
        "best_flights": [{"price": 370}, "junk"],
        "other_flights": None,
    }
    check = parse_check(body, CUN, TODAY)
    assert (check.price, check.level, check.typical_low, check.url) == (370, None, None, None)
    assert check.history == ((date(2026, 8, 13), 300),)


class FakeGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params)))
        return self.responses.pop(0)


def test_check_sends_the_search_parameters():
    get = FakeGet([FIXTURE])
    check = SerpApiChecker("KEY", get_json=get).check(CUN, TODAY)
    url, params = get.calls[0]
    assert url == SEARCH_URL
    assert params == {
        "engine": "google_flights", "departure_id": "YUL", "arrival_id": "CUN", "outbound_date": "2027-01-12",
        "return_date": "2027-01-19", "type": "1", "stops": "2", "currency": "CAD", "gl": "ca", "hl": "en", "api_key": "KEY",
    }
    assert check.price == 366


def test_searches_left_reads_the_account():
    get = FakeGet([{"plan_searches_left": 244}, {"oops": 1}])
    checker = SerpApiChecker("KEY", get_json=get)
    assert checker.searches_left() == 244
    assert checker.searches_left() is None
    assert get.calls[0] == (ACCOUNT_URL, {"api_key": "KEY"})


def test_check_error_message_has_no_key():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    checker = SerpApiChecker("SECRETKEY", get_json=lambda u, p, h: http_get_json(u, p, h, opener=opener, sleep=lambda s: None))
    with pytest.raises(ApiError) as info:
        checker.check(CUN, TODAY)
    assert "SECRETKEY" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/python/test_serpapi.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.check'`

- [ ] **Step 4: Create `scanner/check.py`**

```python
"""One live Google Flights check of a fare's exact trip."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class GoogleCheck:
    checked_on: date
    origin: str
    dest_city: str
    airport: str
    depart: date
    return_date: date
    tp_price: int  # the Travelpayouts price when the check ran
    status: str  # "ok" or "none" (Google found no matching flights)
    price: int | None = None
    level: str | None = None  # Google's verdict: "low", "typical" or "high"
    typical_low: int | None = None
    typical_high: int | None = None
    history: tuple[tuple[date, int], ...] = ()
    url: str | None = None

    @property
    def trip(self) -> tuple[str, date, date]:
        return self.airport, self.depart, self.return_date

    @property
    def pct_below(self) -> int | None:
        """Percent below the middle of Google's typical range; None without a range."""
        if self.price is None or self.typical_low is None or self.typical_high is None:
            return None
        middle = (self.typical_low + self.typical_high) / 2
        return max(0, round(100 * (1 - self.price / middle)))
```

- [ ] **Step 5: Create `scanner/sources/serpapi.py`**

```python
"""SerpApi Google Flights adapter: one live search per fare -> GoogleCheck."""
from __future__ import annotations

from datetime import date, datetime, timezone

from scanner.check import GoogleCheck
from scanner.fare import Fare
from scanner.net import ApiError, http_get_json

SEARCH_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
NO_RESULTS = "hasn't returned any results"
LEVELS = {"low", "typical", "high"}


def _price(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _history(points) -> tuple[tuple[date, int], ...]:
    out = []
    for point in points if isinstance(points, list) else []:
        if not (isinstance(point, list) and len(point) == 2 and isinstance(point[0], (int, float))):
            continue
        price = _price(point[1])
        if price:
            out.append((datetime.fromtimestamp(point[0], timezone.utc).date(), price))
    return tuple(out)


def parse_check(data, fare: Fare, checked_on: date) -> GoogleCheck:
    trip = dict(
        checked_on=checked_on, origin=fare.origin, dest_city=fare.dest_city, airport=fare.dest_airport,
        depart=fare.depart_date, return_date=fare.return_date, tp_price=fare.price,
    )
    if not isinstance(data, dict):
        raise ApiError("unexpected SerpApi response")
    error = data.get("error")
    if isinstance(error, str):
        if NO_RESULTS in error:
            return GoogleCheck(status="none", **trip)
        raise ApiError(f"SerpApi: {error[:120]}")
    insights = data.get("price_insights") if isinstance(data.get("price_insights"), dict) else {}
    flights = [f for key in ("best_flights", "other_flights") for f in (data.get(key) or []) if isinstance(f, dict)]
    price = _price(insights.get("lowest_price")) or min(filter(None, (_price(f.get("price")) for f in flights)), default=None)
    if price is None:
        return GoogleCheck(status="none", **trip)
    typical = insights.get("typical_price_range")
    low = high = None
    if isinstance(typical, list) and len(typical) == 2 and all(_price(v) for v in typical):
        low, high = typical
    level = insights.get("price_level")
    meta = data.get("search_metadata") if isinstance(data.get("search_metadata"), dict) else {}
    url = meta.get("google_flights_url")
    return GoogleCheck(
        status="ok", price=price, level=level if level in LEVELS else None, typical_low=low, typical_high=high,
        history=_history(insights.get("price_history")),
        url=url if isinstance(url, str) and url.startswith("https://") else None,
        **trip,
    )


class SerpApiChecker:
    def __init__(self, key: str, get_json=None):
        self._key = key
        self._get_json = get_json or http_get_json

    def check(self, fare: Fare, today: date) -> GoogleCheck:
        params = {
            "engine": "google_flights", "departure_id": fare.origin, "arrival_id": fare.dest_airport,
            "outbound_date": fare.depart_date.isoformat(), "return_date": fare.return_date.isoformat(),
            "type": "1", "stops": "2", "currency": "CAD", "gl": "ca", "hl": "en", "api_key": self._key,
        }
        return parse_check(self._get_json(SEARCH_URL, params, {}), fare, today)

    def searches_left(self) -> int | None:
        """Searches left this month; this account call does not use a search."""
        data = self._get_json(ACCOUNT_URL, {"api_key": self._key}, {})
        left = data.get("plan_searches_left") if isinstance(data, dict) else None
        return left if isinstance(left, int) and not isinstance(left, bool) else None
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/python/test_serpapi.py -q` then `python -m pytest -q`
Expected: all pass. (Timestamp 1786579200 is 2026-08-13 UTC.)

- [ ] **Step 7: Commit**

```bash
git add scanner/check.py scanner/sources/serpapi.py tests/python/fixtures/serpapi_cun.json tests/python/test_serpapi.py
git commit -m "feat: SerpApi Google Flights source and GoogleCheck"
```

---

### Task 3: Store checks

**Files:**
- Modify: `scanner/store.py`
- Test: `tests/python/test_store.py`

**Interfaces:**
- Consumes: `GoogleCheck` (Task 2).
- Produces: `store.CHECK_COLUMNS`, `store.append_checks(checks: list[GoogleCheck], data_dir) -> None`, `store.load_checks(data_dir, since: date) -> list[GoogleCheck]` (files under `data_dir/checks/YYYY-MM.csv.gz` by `checked_on` month; returns checks with `checked_on >= since` in file order).

- [ ] **Step 1: Write the failing tests** (append to `tests/python/test_store.py`; add `from scanner.check import GoogleCheck` to imports)

```python
def make_check(**overrides) -> GoogleCheck:
    values = dict(
        checked_on=TODAY, origin="YUL", dest_city="CUN", airport="CUN", depart=date(2027, 1, 12),
        return_date=date(2027, 1, 19), tp_price=380, status="ok", price=366, level="low", typical_low=410,
        typical_high=495, history=((date(2026, 8, 9), 557), (date(2026, 8, 10), 545)),
        url="https://www.google.com/travel/flights?tfs=abc&curr=CAD",
    )
    values.update(overrides)
    return GoogleCheck(**values)


def test_checks_round_trip_including_history_and_empty_fields(tmp_path):
    full = make_check()
    none = make_check(dest_city="XXX", airport="XXX", status="none", price=None, level=None, typical_low=None,
                      typical_high=None, history=(), url=None)
    store.append_checks([full, none], tmp_path)
    store.append_checks([make_check(tp_price=390)], tmp_path)
    loaded = store.load_checks(tmp_path, since=date(2026, 10, 1))
    assert loaded == [full, none, make_check(tp_price=390)]
    with gzip.open(tmp_path / "checks" / "2026-10.csv.gz", "rt", encoding="utf-8") as fh:
        assert fh.read().count("checked_on,origin") == 1


def test_load_checks_filters_by_check_day(tmp_path):
    old = make_check(checked_on=date(2026, 9, 20))
    store.append_checks([old, make_check()], tmp_path)
    assert sorted(p.name for p in (tmp_path / "checks").iterdir()) == ["2026-09.csv.gz", "2026-10.csv.gz"]
    assert store.load_checks(tmp_path, since=date(2026, 10, 1)) == [make_check()]
    assert store.load_checks(tmp_path / "nothing", since=TODAY) == []


def test_append_no_checks_writes_nothing(tmp_path):
    store.append_checks([], tmp_path)
    assert not (tmp_path / "checks").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/python/test_store.py -q`
Expected: FAIL — `AttributeError: module 'scanner.store' has no attribute 'append_checks'`

- [ ] **Step 3: Implement** — in `scanner/store.py`, update the docstring to `"""Fare observations and Google checks, appended to gzipped CSV files named by month."""`, add `from scanner.check import GoogleCheck`, replace the body of `append`'s per-month loop with a shared helper, and add the check functions:

```python
def _append_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    is_new = not path.exists()
    with gzip.open(path, "at", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(header)
        writer.writerows(rows)


def append(fares: list[Fare], data_dir: Path) -> None:
    folder = Path(data_dir) / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[list[str]]] = {}
    for fare in fares:
        row = [_encode(name, value) for name, value in zip(COLUMNS, astuple(fare))]
        by_month.setdefault(fare.scan_date.strftime("%Y-%m"), []).append(row)
    for month, rows in sorted(by_month.items()):
        _append_csv(folder / f"{month}.csv.gz", COLUMNS, rows)
```

```python
CHECK_COLUMNS = [
    "checked_on", "origin", "dest_city", "airport", "depart", "return", "tp_price", "status",
    "price", "level", "typical_low", "typical_high", "history", "url",
]


def _opt(value) -> str:
    return "" if value is None else str(value)


def _check_row(c: GoogleCheck) -> list[str]:
    history = ";".join(f"{day.isoformat()}:{price}" for day, price in c.history)
    return [
        c.checked_on.isoformat(), c.origin, c.dest_city, c.airport, c.depart.isoformat(), c.return_date.isoformat(),
        str(c.tp_price), c.status, _opt(c.price), _opt(c.level), _opt(c.typical_low), _opt(c.typical_high),
        history, _opt(c.url),
    ]


def _check_from(row: dict) -> GoogleCheck:
    num = lambda text: int(text) if text else None  # noqa: E731
    pairs = (pair.split(":") for pair in row["history"].split(";") if pair)
    return GoogleCheck(
        checked_on=date.fromisoformat(row["checked_on"]), origin=row["origin"], dest_city=row["dest_city"],
        airport=row["airport"], depart=date.fromisoformat(row["depart"]), return_date=date.fromisoformat(row["return"]),
        tp_price=int(row["tp_price"]), status=row["status"], price=num(row["price"]), level=row["level"] or None,
        typical_low=num(row["typical_low"]), typical_high=num(row["typical_high"]),
        history=tuple((date.fromisoformat(day), int(price)) for day, price in pairs), url=row["url"] or None,
    )


def append_checks(checks: list[GoogleCheck], data_dir: Path) -> None:
    if not checks:
        return
    folder = Path(data_dir) / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[list[str]]] = {}
    for check in checks:
        by_month.setdefault(check.checked_on.strftime("%Y-%m"), []).append(_check_row(check))
    for month, rows in sorted(by_month.items()):
        _append_csv(folder / f"{month}.csv.gz", CHECK_COLUMNS, rows)


def load_checks(data_dir: Path, since: date) -> list[GoogleCheck]:
    folder = Path(data_dir) / "checks"
    if not folder.exists():
        return []
    first_month = since.strftime("%Y-%m")
    checks = []
    for path in sorted(folder.glob("*.csv.gz")):
        if path.name[:7] < first_month:
            continue
        with gzip.open(path, "rt", newline="", encoding="utf-8") as fh:
            checks += [c for c in map(_check_from, csv.DictReader(fh)) if c.checked_on >= since]
    return checks
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scanner/store.py tests/python/test_store.py
git commit -m "feat: store Google checks in monthly gzipped CSVs"
```

---

### Task 4: Pick fares and run checks (`scanner/verify.py`)

**Files:**
- Create: `scanner/verify.py`, `tests/python/test_verify.py`

**Interfaces:**
- Consumes: `ScoredFare` (`scanner.scoring`), `Places.lookup(code).country_code`, `GoogleCheck.trip`, `ApiError`; a checker with `.check(fare, today)` and `.searches_left()`.
- Produces: `verify.pick(scored, places, checks, today, limit=DAILY_LIMIT) -> list[ScoredFare]`; `verify.CheckRun(checks: list[GoogleCheck], searches_left: int | None, note: str | None)`; `verify.run_checks(checker, picks, today) -> CheckRun`; constants `DAILY_LIMIT, REPEAT_DAYS, MIN_LEFT, PICK_PCT, MAX_ERRORS`.

- [ ] **Step 1: Write the failing tests** `tests/python/test_verify.py`

```python
from datetime import date, timedelta

from factories import TODAY, make_fare
from scanner.check import GoogleCheck
from scanner.net import ApiError
from scanner.places import Places
from scanner.scoring import ScoredFare
from scanner.verify import CheckRun, pick, run_checks

PLACES = Places(
    {c: {"name": c, "country": cc} for c, cc in
     [("LIS", "PT"), ("PAR", "FR"), ("CUN", "MX"), ("PUJ", "DO"), ("YVR", "CA"), ("YYC", "CA"), ("YTO", "CA"), ("TYO", "JP")]},
    {}, {},
)


def scored(city, pct, price=500, **fare_fields):
    fare = make_fare(dest_city=city, dest_airport=city, price=price, **fare_fields)
    return ScoredFare(fare=fare, normal=None, pct_off=pct, baseline=None, age_days=0, badges=())


def check_for(item, checked_on=TODAY, **overrides):
    f = item.fare
    values = dict(checked_on=checked_on, origin="YUL", dest_city=f.dest_city, airport=f.dest_airport,
                  depart=f.depart_date, return_date=f.return_date, tp_price=f.price, status="ok", price=f.price)
    values.update(overrides)
    return GoogleCheck(**values)


def cities(items):
    return [i.fare.dest_city for i in items]


def test_canada_is_never_checked_including_vancouver_and_calgary():
    items = [scored("YVR", 60), scored("YYC", 55), scored("YTO", None), scored("LIS", 35)]
    assert cities(pick(items, PLACES, [], TODAY)) == ["LIS"]


def test_big_discounts_first_then_new_routes_then_nothing_in_between():
    items = [scored("LIS", 35), scored("PAR", 72), scored("CUN", None, price=400), scored("PUJ", 20), scored("TYO", None, price=900)]
    assert cities(pick(items, PLACES, [], TODAY)) == ["PAR", "LIS", "CUN", "TYO"]


def test_each_city_sends_only_its_best_option():
    items = [scored("LIS", 35), scored("LIS", 45, depart_date=date(2026, 12, 5))]
    picked = pick(items, PLACES, [], TODAY)
    assert [(i.fare.dest_city, i.pct_off) for i in picked] == [("LIS", 45)]


def test_new_routes_least_recently_checked_first():
    cun, tyo, puj = scored("CUN", None, price=300), scored("TYO", None, price=900), scored("PUJ", None, price=400)
    old = [check_for(cun, TODAY - timedelta(days=20), depart=date(2027, 3, 1)),
           check_for(puj, TODAY - timedelta(days=40), depart=date(2027, 3, 1))]
    assert cities(pick([cun, tyo, puj], PLACES, old, TODAY)) == ["TYO", "PUJ", "CUN"]


def test_same_trip_is_not_rechecked_within_three_days():
    lis = scored("LIS", 50)
    assert pick([lis], PLACES, [check_for(lis, TODAY - timedelta(days=2))], TODAY) == []
    assert cities(pick([lis], PLACES, [check_for(lis, TODAY - timedelta(days=3))], TODAY)) == ["LIS"]


def test_pick_counts_todays_checks_against_the_cap():
    items = [scored(c, 50) for c in ("LIS", "PAR", "CUN", "PUJ", "TYO")]
    done_today = [check_for(scored("ZZZ", 50), TODAY, dest_city=f"Z{i}", airport=f"Z{i}") for i in range(5)]
    assert len(pick(items, PLACES, done_today, TODAY)) == 2
    assert pick(items, PLACES, done_today * 2, TODAY) == []
    assert len(pick(items, PLACES, [], TODAY, limit=3)) == 3


class FakeChecker:
    def __init__(self, left=200, fail=()):
        self.left, self.fail, self.checked = left, set(fail), []

    def searches_left(self):
        if self.left == "error":
            raise ApiError("HTTP 401 for https://serpapi.com/account.json")
        return self.left

    def check(self, fare, today):
        self.checked.append(fare.dest_city)
        if fare.dest_city in self.fail:
            raise ApiError("HTTP 500 for https://serpapi.com/search.json")
        return check_for(ScoredFare(fare, None, None, None, 0, ()), today)


def test_run_checks_checks_every_pick():
    checker = FakeChecker(left=100)
    run = run_checks(checker, [scored("LIS", 50), scored("PAR", 40)], TODAY)
    assert checker.checked == ["LIS", "PAR"]
    assert [c.dest_city for c in run.checks] == ["LIS", "PAR"]
    assert (run.searches_left, run.note) == (98, None)


def test_run_checks_respects_the_monthly_floor():
    checker = FakeChecker(left=9)
    assert run_checks(checker, [scored("LIS", 50)], TODAY) == CheckRun([], 9, "only 9 Google searches left this month")
    assert checker.checked == []


def test_run_checks_with_nothing_to_check_skips_the_quota_call():
    checker = FakeChecker(left="error")
    assert run_checks(checker, [], TODAY) == CheckRun([], None, None)


def test_run_checks_skips_a_failed_pick_and_stops_after_two_errors_in_a_row():
    checker = FakeChecker(fail={"PAR"})
    run = run_checks(checker, [scored("LIS", 50), scored("PAR", 45), scored("CUN", 40)], TODAY)
    assert [c.dest_city for c in run.checks] == ["LIS", "CUN"]
    assert "HTTP 500" in run.note

    checker = FakeChecker(fail={"LIS", "PAR"})
    run = run_checks(checker, [scored("LIS", 50), scored("PAR", 45), scored("CUN", 40)], TODAY)
    assert checker.checked == ["LIS", "PAR"]
    assert run.checks == []


def test_run_checks_quota_error_is_a_note_not_a_crash():
    run = run_checks(FakeChecker(left="error"), [scored("LIS", 50)], TODAY)
    assert run.checks == [] and "HTTP 401" in run.note
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/python/test_verify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner.verify'`

- [ ] **Step 3: Create `scanner/verify.py`**

```python
"""Choose which fares get a live Google Flights check today, and run those checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from scanner.check import GoogleCheck
from scanner.net import ApiError
from scanner.places import Places
from scanner.scoring import ScoredFare

DAILY_LIMIT = 7  # 7 x 31 = 217 of the free plan's 250 searches a month
REPEAT_DAYS = 3
MIN_LEFT = 10
PICK_PCT = 30
MAX_ERRORS = 2
DOMESTIC = "CA"


def _trip(item: ScoredFare) -> tuple[str, date, date]:
    return item.fare.dest_airport, item.fare.depart_date, item.fare.return_date


def _rank(item: ScoredFare) -> tuple:
    pct = item.pct_off if item.pct_off is not None else -1
    return -pct, item.fare.price


def pick(scored: list[ScoredFare], places: Places, checks: list[GoogleCheck], today: date, limit: int = DAILY_LIMIT) -> list[ScoredFare]:
    """Today's international fares worth a Google check, best first, within what is left of today's limit."""
    remaining = limit - sum(1 for c in checks if c.checked_on == today)
    if remaining <= 0:
        return []
    recent = {c.trip for c in checks if c.checked_on > today - timedelta(days=REPEAT_DAYS)}
    last_checked: dict[str, date] = {}
    for c in checks:
        last_checked[c.dest_city] = max(last_checked.get(c.dest_city, date.min), c.checked_on)
    best: dict[str, ScoredFare] = {}
    for item in scored:
        city = item.fare.dest_city
        if city not in best or _rank(item) < _rank(best[city]):
            best[city] = item
    big, new = [], []
    for city, item in best.items():
        if places.lookup(city).country_code == DOMESTIC or _trip(item) in recent:
            continue
        if item.pct_off is None:
            new.append(item)
        elif item.pct_off >= PICK_PCT:
            big.append(item)
    big.sort(key=_rank)
    new.sort(key=lambda item: (last_checked.get(item.fare.dest_city, date.min), item.fare.price))
    return (big + new)[:remaining]


@dataclass
class CheckRun:
    checks: list[GoogleCheck] = field(default_factory=list)
    searches_left: int | None = None
    note: str | None = None


def run_checks(checker, picks: list[ScoredFare], today: date) -> CheckRun:
    """Check each pick on Google. Errors become a note; they never raise."""
    if not picks:
        return CheckRun()
    try:
        left = checker.searches_left()
    except ApiError as err:
        return CheckRun(note=f"quota check failed: {err}")
    if left is not None and left < MIN_LEFT:
        return CheckRun([], left, f"only {left} Google searches left this month")
    done: list[GoogleCheck] = []
    errors, note = 0, None
    for item in picks:
        try:
            done.append(checker.check(item.fare, today))
            errors = 0
        except ApiError as err:
            errors, note = errors + 1, f"check failed: {err}"
            if errors >= MAX_ERRORS:
                break
    return CheckRun(done, None if left is None else left - len(done), note)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scanner/verify.py tests/python/test_verify.py
git commit -m "feat: pick international fares for Google checks within the daily budget"
```

---

### Task 5: Attach checks in `deals.json`

**Files:**
- Modify: `scanner/publish.py`
- Test: `tests/python/test_publish.py`

**Interfaces:**
- Consumes: `GoogleCheck` (Task 2).
- Produces: `build_deals(scored, places, *, origin, scan_date, rejected, generated_at, checks=(), searches_left=None, google_note=None)`; `publish.SHOW_DAYS = 7`, `publish.CONFIRM_TOLERANCE = 0.10`; `publish.google_json(check, price) -> dict`; options gain an optional `google` object; `scan.google = {"checked_today", "searches_left", "note"}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/python/test_publish.py`; add `from datetime import timedelta` and `from scanner.check import GoogleCheck`)

```python
def gcheck(price=480, checked_on=TODAY, depart=date(2026, 12, 1), ret=date(2026, 12, 10), **overrides):
    values = dict(checked_on=checked_on, origin="YUL", dest_city="LIS", airport="LIS", depart=depart, return_date=ret,
                  tp_price=500, status="ok", price=price, level="low", typical_low=600, typical_high=800,
                  history=((date(2026, 9, 17), 620), (date(2026, 10, 8), price)),
                  url="https://www.google.com/travel/flights?tfs=abc")
    values.update(overrides)
    return GoogleCheck(**values)


def deals_with(checks, **kwargs):
    return build_deals([scored(500, 40), scored(300, None, dest="QQQ")], PLACES, origin="YUL", scan_date=TODAY,
                       rejected={}, generated_at=NOW, checks=checks, **kwargs)


def lis_best(deals):
    return next(d for d in deals["destinations"] if d["city"] == "LIS")["best"]


def test_build_deals_attaches_the_google_check():
    deals = deals_with([gcheck()], searches_left=230, google_note=None)
    google = lis_best(deals)["google"]
    assert google == {
        "checked_on": "2026-10-08", "status": "ok", "price": 480, "level": "low", "typical": [600, 800],
        "pct_below": 31, "confirmed": True, "history": [["2026-09-17", 620], ["2026-10-08", 480]],
    }
    assert lis_best(deals)["links"]["google"] == "https://www.google.com/travel/flights?tfs=abc"
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": 230, "note": None}
    assert validate(deals) == []


def test_confirmed_compares_with_todays_price():
    assert lis_best(deals_with([gcheck(price=550)]))["google"]["confirmed"] is True   # 500 * 1.10 = 550
    assert lis_best(deals_with([gcheck(price=551)]))["google"]["confirmed"] is False


def test_newest_check_wins_and_none_status_is_short():
    older = gcheck(price=700, checked_on=TODAY - timedelta(days=2))
    newer = gcheck(status="none", price=None, level=None, typical_low=None, typical_high=None, history=(), url=None)
    best = lis_best(deals_with([older, newer]))
    assert best["google"] == {"checked_on": "2026-10-08", "status": "none"}
    assert best["links"]["google"].startswith("https://www.google.com/travel/flights?q=")


def test_build_deals_ignores_old_and_unmatched_checks():
    stale = gcheck(checked_on=TODAY - timedelta(days=7))
    other_dates = gcheck(depart=date(2026, 12, 2))
    deals = deals_with([stale, other_dates])
    assert "google" not in lis_best(deals)
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": None, "note": None}


def test_no_typical_range_gives_null_typical_and_pct():
    google = lis_best(deals_with([gcheck(typical_low=None, typical_high=None)]))["google"]
    assert (google["typical"], google["pct_below"]) == (None, None)


def test_validate_rejects_bad_google_objects():
    deals = deals_with([gcheck()])
    for bad in ("x", {"status": "maybe"}, {"status": "ok", "price": "480"}):
        broken = copy.deepcopy(deals)
        next(d for d in broken["destinations"] if d["city"] == "LIS")["options"][0]["google"] = bad
        assert any("google" in e for e in validate(broken))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/python/test_publish.py -q`
Expected: FAIL — `TypeError: build_deals() got an unexpected keyword argument 'checks'`

- [ ] **Step 3: Implement** in `scanner/publish.py`:

Add imports `from datetime import date, datetime, timedelta` (replace the existing date/datetime import) and `from scanner.check import GoogleCheck`; constants after imports:

```python
SHOW_DAYS = 7
CONFIRM_TOLERANCE = 0.10
```

Add:

```python
def google_json(check: GoogleCheck, price: int) -> dict:
    """The page's view of a check; `confirmed` compares Google with today's fare price."""
    if check.status != "ok":
        return {"checked_on": check.checked_on.isoformat(), "status": check.status}
    typical = [check.typical_low, check.typical_high] if check.typical_low is not None else None
    return {
        "checked_on": check.checked_on.isoformat(),
        "status": "ok",
        "price": check.price,
        "level": check.level,
        "typical": typical,
        "pct_below": check.pct_below,
        "confirmed": check.price <= price * (1 + CONFIRM_TOLERANCE),
        "history": [[day.isoformat(), value] for day, value in check.history],
    }


def latest_checks(checks, today: date) -> dict[tuple, GoogleCheck]:
    """Newest check per trip from the last SHOW_DAYS days."""
    latest: dict[tuple, GoogleCheck] = {}
    for check in checks:
        if check.checked_on <= today - timedelta(days=SHOW_DAYS):
            continue
        if check.trip not in latest or check.checked_on >= latest[check.trip].checked_on:
            latest[check.trip] = check
    return latest
```

Change `fare_json(scored)` to `fare_json(scored, check=None)` and, just before `return`, build the dict into `option = {...}` then:

```python
    if check is not None:
        option["google"] = google_json(check, fare.price)
        if check.status == "ok" and check.url:
            option["links"]["google"] = check.url
    return option
```

Change `build_deals` signature to add `checks=(), searches_left: int | None = None, google_note: str | None = None`, compute `latest = latest_checks(checks, scan_date)` at the top, build options with
`fare_json(item, latest.get((item.fare.dest_airport, item.fare.depart_date, item.fare.return_date)))`, and add to the `"scan"` dict:

```python
            "google": {
                "checked_today": sum(1 for c in checks if c.checked_on == scan_date),
                "searches_left": searches_left,
                "note": google_note,
            },
```

Add to `_option_errors` before `return errors`:

```python
    if "google" in option:
        google = option["google"]
        if not isinstance(google, dict) or google.get("status") not in ("ok", "none"):
            errors.append(f"{where}: bad google")
        elif google["status"] == "ok" and not (isinstance(google.get("price"), int) and google["price"] > 0):
            errors.append(f"{where}: bad google price")
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: all pass (existing `OPTION_KEYS` test still passes: unchecked options have no `google` key).

- [ ] **Step 5: Commit**

```bash
git add scanner/publish.py tests/python/test_publish.py
git commit -m "feat: attach recent Google checks to deals.json options"
```

---

### Task 6: Run checks during the scan (`scanner/__main__.py`)

**Files:**
- Modify: `scanner/__main__.py`
- Test: `tests/python/test_main.py`

**Interfaces:**
- Consumes: `SerpApiChecker` (Task 2), `store.append_checks/load_checks` (Task 3), `pick`, `run_checks`, `CheckRun` (Task 4), `build_deals(..., checks, searches_left, google_note)` (Task 5).
- Produces: `main(argv=None, *, source=None, checker=None, now=None, env_file=None) -> int`; `KEY_VAR = "SERPAPI_KEY"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/python/test_main.py`; add imports `from scanner.check import GoogleCheck` and `from scanner.net import ApiError`)

```python
class FakeChecker:
    def __init__(self, left=200, error=False):
        self.left, self.error, self.calls = left, error, []

    def searches_left(self):
        return self.left

    def check(self, fare, today):
        self.calls.append(fare.dest_city)
        if self.error:
            raise ApiError("HTTP 500 for https://serpapi.com/search.json")
        return GoogleCheck(checked_on=today, origin=fare.origin, dest_city=fare.dest_city, airport=fare.dest_airport,
                           depart=fare.depart_date, return_date=fare.return_date, tp_price=fare.price, status="ok",
                           price=fare.price - 10, level="low", typical_low=500, typical_high=700)


def read_deals(tmp_path):
    return json.loads((tmp_path / "site" / "deals.json").read_text(encoding="utf-8"))


def test_scan_checks_new_international_route_on_google(tmp_path, capsys):
    write_reference(tmp_path / "ref")
    checker = FakeChecker()
    assert main(cli_args(tmp_path), source=FakeSource([make_fare(price=450)]), checker=checker, now=NOW) == 0
    assert checker.calls == ["LIS"]
    deals = read_deals(tmp_path)
    assert deals["destinations"][0]["best"]["google"]["price"] == 440
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": 199, "note": None}
    assert len(store.load_checks(tmp_path / "data", since=date(2026, 10, 1))) == 1
    assert "google: 1 checked" in capsys.readouterr().out


def test_second_run_same_day_spends_nothing_more(tmp_path):
    write_reference(tmp_path / "ref")
    main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=FakeChecker(), now=NOW)
    again = FakeChecker()
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=again, now=NOW) == 0
    assert again.calls == []
    assert read_deals(tmp_path)["destinations"][0]["best"]["google"]["status"] == "ok"


def test_quota_floor_skips_checks(tmp_path):
    write_reference(tmp_path / "ref")
    checker = FakeChecker(left=3)
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=checker, now=NOW) == 0
    assert checker.calls == []
    assert "3 Google searches left" in read_deals(tmp_path)["scan"]["google"]["note"]


def test_check_errors_still_publish(tmp_path):
    write_reference(tmp_path / "ref")
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=FakeChecker(error=True), now=NOW) == 0
    deals = read_deals(tmp_path)
    assert "google" not in deals["destinations"][0]["best"]
    assert "HTTP 500" in deals["scan"]["google"]["note"]


def test_injected_source_without_checker_makes_no_google_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "should-not-be-used")
    write_reference(tmp_path / "ref")
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), now=NOW) == 0
    assert read_deals(tmp_path)["scan"]["google"]["note"] == "SERPAPI_KEY not set"


def test_seed_runs_no_checks(tmp_path):
    checker = FakeChecker()
    seed = make_fare(kind="seed", searched_on=date(2026, 9, 1))
    main(cli_args(tmp_path) + ["--seed"], source=FakeSource([], [seed]), checker=checker, now=NOW)
    assert checker.calls == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/python/test_main.py -q`
Expected: FAIL — `TypeError: main() got an unexpected keyword argument 'checker'`

- [ ] **Step 3: Implement** in `scanner/__main__.py`:

Imports: change the publish import to `from scanner.publish import CONFIRM_TOLERANCE, build_deals, write_deals`; add `from scanner.sources.serpapi import SerpApiChecker` and `from scanner.verify import CheckRun, pick, run_checks`. Constant: `KEY_VAR = "SERPAPI_KEY"`.

Replace the `if source is None:` block with:

```python
    if source is None:
        env = load_env(env_file or ROOT / ".env")
        token = os.environ.get(TOKEN_VAR) or env.get(TOKEN_VAR)
        if not token:
            print(f"error: {TOKEN_VAR} is not set (environment variable or .env file)", file=sys.stderr)
            return 2
        source = TravelpayoutsSource(token)
        key = os.environ.get(KEY_VAR) or env.get(KEY_VAR)
        if checker is None and key:
            checker = SerpApiChecker(key)
```

Replace everything from `store.append(result.fares, data_dir)` to the final `return 0` with:

```python
    store.append(result.fares, data_dir)
    scored = score(result.fares, build_baselines(history, today), today)
    places = Places.load(Path(args.reference))
    checks = store.load_checks(data_dir, since=today - timedelta(days=WINDOW_DAYS))
    if checker is None:
        run = CheckRun(note=f"{KEY_VAR} not set")
    else:
        run = run_checks(checker, pick(scored, places, checks, today), today)
        store.append_checks(run.checks, data_dir)
    deals = build_deals(
        scored,
        places,
        origin=args.origin,
        scan_date=today,
        rejected=result.rejected,
        generated_at=now,
        checks=checks + run.checks,
        searches_left=run.searches_left,
        google_note=run.note,
    )
    write_deals(deals, Path(args.out))
    big = sum(1 for item in scored if item.pct_off is not None and item.pct_off >= 30)
    print(
        f"scan {today}: {len(result.fares)} fares, {deals['scan']['destinations']} destinations, "
        f"{big} fares >= 30% off; rejected {result.rejected}; failed {result.failed}"
    )
    confirmed = sum(1 for c in run.checks if c.status == "ok" and c.price <= c.tp_price * (1 + CONFIRM_TOLERANCE))
    left = "unknown" if run.searches_left is None else run.searches_left
    print(f"google: {len(run.checks)} checked, {confirmed} confirmed, searches left {left}" + (f"; {run.note}" if run.note else ""))
    return 0
```

Also change the `main` signature to `def main(argv=None, *, source=None, checker=None, now=None, env_file=None) -> int:`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: all pass (including the existing `test_missing_token_exits_2`).

- [ ] **Step 5: Commit**

```bash
git add scanner/__main__.py tests/python/test_main.py
git commit -m "feat: run daily Google checks during the scan"
```

---

### Task 7: Page logic — Google percent, filter and trend (`site/filters.js`)

**Files:**
- Modify: `site/filters.js`
- Test: `tests/js/filters.test.js`

**Interfaces:**
- Consumes: option `google` objects (Task 5).
- Produces: `dealPct(o) -> number | null`, `googleTrend(history) -> string | null`, `DEFAULTS.googleOnly = false`; `optionMatches`, `compareOptions`, `countDeals` use `dealPct`.

- [ ] **Step 1: Write the failing tests** (append to `tests/js/filters.test.js`; extend the import list with `dealPct, googleTrend, compareOptions`)

```js
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
  assert.equal(googleTrend([["2026-09-01", 500], ["2026-09-17", 520], ["2026-10-08", 340]]), "↓35% in 3 weeks · lowest in 60 days");
  assert.equal(googleTrend([["2026-09-17", 400], ["2026-10-08", 480]]), "↑20% in 3 weeks");
  assert.equal(googleTrend([["2026-09-17", 500], ["2026-10-08", 510]]), "steady over 3 weeks");
  assert.equal(googleTrend([["2026-10-08", 500]]), null);
  assert.equal(googleTrend(undefined), null);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test "tests/js/*.test.js"`
Expected: FAIL — `SyntaxError: The requested module '../../site/filters.js' does not provide an export named 'dealPct'`

- [ ] **Step 3: Implement** in `site/filters.js`:

- `DEFAULTS`: add `googleOnly: false` (after `includeNew`).
- `BOOLEANS`: `["nonstop", "bag", "includeNew", "googleOnly"]`.
- Add after `ageDays`:

```js
// Our own percent off when history exists; otherwise Google's percent below its typical price.
export function dealPct(o) {
  return o.pct_off ?? o.google?.pct_below ?? null;
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
```

- `optionMatches`: replace the `pct_off` block with

```js
  const pct = dealPct(o);
  if (pct === null) {
    if (!f.includeNew) return false;
  } else if (pct < f.minPct) {
    return false;
  }
  if (f.googleOnly && o.google?.status !== "ok") return false;
```

- `compareOptions`: `return (dealPct(b) ?? -1) - (dealPct(a) ?? -1) || a.price - b.price;`
- `countDeals`: `... o.depart > today && dealPct(o) !== null && dealPct(o) >= minPct ...`

- [ ] **Step 4: Run the tests**

Run: `node --test "tests/js/*.test.js"`
Expected: all pass (16 existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add site/filters.js tests/js/filters.test.js
git commit -m "feat: Google percent, Google-only filter and price trend in page logic"
```

---

### Task 8: Page rendering (`site/app.js`, `site/index.html`, `site/style.css`)

**Files:**
- Modify: `site/app.js`, `site/index.html`, `site/style.css`

**Interfaces:**
- Consumes: `dealPct`, `googleTrend` (Task 7); `option.google`, `scan.google` (Task 5).

- [ ] **Step 1: `site/index.html`** — after the `includeNew` label add:

```html
        <label class="check"><input type="checkbox" name="googleOnly"> Google-checked only</label>
```

- [ ] **Step 2: `site/app.js`**

Import line: add `dealPct, googleTrend`.

Add after `links(o)`:

```js
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
```

Replace `pctBadge` with:

```js
function pctBadge(o) {
  if (o.pct_off !== null) return `<span class="pct">−${o.pct_off}%</span>`;
  const pct = dealPct(o);
  return pct === null
    ? `<span class="pct new">New</span>`
    : `<span class="pct google" title="Below Google's typical price">−${pct}% vs Google</span>`;
}
```

In `optionRow`: `const pct = dealPct(o) === null ? "" : \` (−${dealPct(o)}%${o.pct_off === null ? " vs Google" : ""})\`;` and add `const checked = o.google?.status === "ok" ? \` · ${o.google.confirmed ? "✓" : "⚠"} Google ${money(o.google.price)}\` : "";` appended after `${bag}` in the `.age` span.

In `card`: insert `${googleLine(b)}` on the line after the `.dates` paragraph.

In `readForm`: add `googleOnly: data.has("googleOnly"),`. In `writeForm`: add `el.googleOnly.checked = f.googleOnly;`.

In `start`, replace the status line with:

```js
  const google = data.scan.google;
  const checks = google ? ` · Google checked ${google.checked_today} today${google.searches_left !== null ? ` (${google.searches_left} left this month)` : ""}` : "";
  status.textContent = `Last scan: ${ago} ago · ${destinations.length} destinations · ${countDeals(destinations, 30, today)} deals ≥ 30%${checks}`;
```

- [ ] **Step 3: `site/style.css`** — append after the `.links a` rule:

```css
.google { margin: 2px 0; font-size: 0.88rem; }
.google p { margin: 0; }
.google.warn strong { color: var(--warn); }
.google.muted { color: var(--muted); }
.spark { display: block; width: 100%; height: 22px; margin-top: 2px; }
.spark polyline { fill: none; stroke: var(--accent); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.pct.google { background: var(--chip); color: var(--text); }
```

- [ ] **Step 4: Verify in a browser without spending searches**

Inject the saved CUN fixture into a copy of the current `deals.json` (no API calls):

```bash
cp site/deals.json "$TMP/deals.backup.json"
PYTHONPATH=. python - <<'PY'
import json
from datetime import date
from pathlib import Path
from factories import make_fare
from scanner.publish import google_json
from scanner.sources.serpapi import parse_check
fixture = json.loads(Path("tests/python/fixtures/serpapi_cun.json").read_text(encoding="utf-8"))
deals = json.loads(Path("site/deals.json").read_text(encoding="utf-8"))
dests = deals["destinations"]
for i, d in enumerate(dests[:3]):
    o = d["best"]
    fare = make_fare(price=o["price"], dest_city=d["city"], dest_airport=o["airport"])
    check = parse_check(fixture if i < 2 else {"best_flights": []}, fare, date.today())
    g = google_json(check, o["price"] if i == 0 else 300)
    o["google"] = g
    d["options"][0]["google"] = g
deals["scan"]["google"] = {"checked_today": 3, "searches_left": 241, "note": None}
Path("site/deals.json").write_text(json.dumps(deals), encoding="utf-8")
PY
```

(`PYTHONPATH` must also include `tests/python` for `factories`: use `PYTHONPATH=".;tests/python"` on Windows Git Bash if `:` fails.) Serve `site/` with the `.js → text/javascript` server:

```bash
python -c "import functools,http.server as h; H=h.SimpleHTTPRequestHandler; H.extensions_map['.js']='text/javascript'; h.ThreadingHTTPServer(('127.0.0.1',8765),functools.partial(H,directory='site')).serve_forever()"
```

Expected: header shows "Google checked 3 today (241 left this month)"; card 1 shows "Google ✓ $366 · low · typical $410–495 · 19% below typical · <trend>" and a sparkline; card 2 shows "⚠ Google shows $366" in warn colour; card 3 shows "Google: no matching flights found"; "Google-checked only" leaves cards 1–2. Afterwards restore: `cp "$TMP/deals.backup.json" site/deals.json` and confirm `git status` shows `site/deals.json` unchanged.

- [ ] **Step 5: Commit**

```bash
git add site/app.js site/index.html site/style.css
git commit -m "feat: show Google check line, sparkline and Google-only filter on cards"
```

---

### Task 9: Workflow secret and docs

**Files:**
- Modify: `.github/workflows/scan.yml`, `tests/python/test_workflow.py`, `docs/superpowers/specs/2026-10-08-flight-deals-design.md` (§12)

- [ ] **Step 1: Write the failing test** (append to `tests/python/test_workflow.py`)

```python
def test_scan_step_receives_the_serpapi_key():
    text = WORKFLOW.read_text(encoding="utf-8")
    scan_step = text.split("- name: Scan\n", 1)[1].split("- name:", 1)[0]
    assert "SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}" in scan_step
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/python/test_workflow.py -q`
Expected: FAIL — assertion error.

- [ ] **Step 3: Implement** — in `scan.yml`'s `Scan` step `env:` add `SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}` under the token line. In the main spec §12, change the "Live verification" bullet to: `- **Live verification:** done — see 2026-10-08-google-checks-design.md.`

- [ ] **Step 4: Run all tests**

Run: `python -m pytest -q` and `node --test "tests/js/*.test.js"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/scan.yml tests/python/test_workflow.py docs/superpowers/specs/2026-10-08-flight-deals-design.md
git commit -m "ci: pass SERPAPI_KEY to the scan"
```

---

## After the tasks (finishing, needs the user's go-ahead already given: "go for it")

1. Final whole-branch review (fresh reviewer, most capable model), fix pass for Critical/Important.
2. Merge `feature/google-checks` into `main` locally, full suite green.
3. `gh secret set -f .env --repo KevinPatel06/flight-deals` (sets both keys from `.env`; nothing printed).
4. `git push origin main` — the push triggers a scan; verify the run, `deals.json` `scan.google`, and the live page.
