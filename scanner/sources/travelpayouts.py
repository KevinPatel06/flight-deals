"""Travelpayouts / Aviasales Data API adapter: cached fares -> Fare records."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import unquote

from scanner.fare import Fare
from scanner.net import ApiError, http_get_json  # noqa: F401  (re-exported for callers and tests)

AVIASALES = "https://www.aviasales.com"
MIN_TRIP_DAYS = 2
MAX_TRIP_DAYS = 30
MAX_STOPS = 1
MAX_AGE_DAYS = 7

API = "https://api.travelpayouts.com"
PAGE_LIMIT = 1000
MAX_PAGES = 10

_SEARCH_DATE = re.compile(r"search_date=(\d{2})(\d{2})(\d{4})")
_FARE_KEY = re.compile(r"static_fare_key=([^&]+)")


def parse_search_date(link: str) -> date | None:
    """The day the fare was found, from the link's search_date=DDMMYYYY parameter."""
    match = _SEARCH_DATE.search(link or "")
    if not match:
        return None
    day, month, year = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_bags(link: str) -> tuple[bool | None, bool | None, int | None, int | None]:
    """(carry_on, checked_bag, bag_pieces, bag_kg) from the undocumented static_fare_key.

    H0/H1 = carry-on excluded/included; L0 = no checked bag; L1_<pieces>_<kg> = bag included.
    """
    match = _FARE_KEY.search(link or "")
    if not match:
        return None, None, None, None
    carry_on = checked = None
    pieces = kg = None
    for part in unquote(match.group(1)).split("|"):
        if part in ("H0", "H1"):
            carry_on = part == "H1"
        elif part == "L0":
            checked = False
        elif part.startswith("L1"):
            checked = True
            bits = part.split("_")
            if len(bits) == 3 and bits[1].isdigit() and bits[2].isdigit():
                pieces = int(bits[1])
                kg = int(bits[2]) or None
    return carry_on, checked, pieces, kg


def _day(value) -> date | None:
    """Local calendar date from an ISO timestamp like 2026-12-02T15:00:00-05:00 or ...Z."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def normalize_dates_row(row: dict, origin: str, scan_date: date) -> Fare | None:
    """A /v3/prices_for_dates row as a Fare, or None if it is unusable."""
    if not isinstance(row, dict):
        return None
    depart = _day(row.get("departure_at"))
    ret = _day(row.get("return_at"))
    price = _int(row.get("price"))
    dest_city = row.get("destination")
    out_stops = _int(row.get("transfers"))
    back_stops = _int(row.get("return_transfers"))
    link = row.get("link") or ""
    searched = parse_search_date(link)
    if not (depart and ret and dest_city and searched) or price is None or price <= 0:
        return None
    if out_stops is None or back_stops is None:
        return None
    carry_on, checked, pieces, kg = parse_bags(link)
    return Fare(
        origin=row.get("origin_airport") or origin,
        dest_airport=row.get("destination_airport") or dest_city,
        dest_city=dest_city,
        depart_date=depart,
        return_date=ret,
        price=price,
        airline=row.get("airline") or "",
        stops=max(out_stops, back_stops),
        searched_on=searched,
        link=AVIASALES + link if link.startswith("/") else link,
        kind="dates",
        scan_date=scan_date,
        carry_on=carry_on,
        checked_bag=checked,
        bag_pieces=pieces,
        bag_kg=kg,
    )


def normalize_latest_row(row: dict, origin: str, scan_date: date, kind: str) -> Fare | None:
    """A /v3/get_latest_prices row as a Fare (kind "latest" or "seed"), or None."""
    if not isinstance(row, dict):
        return None
    depart = _day(row.get("depart_date"))
    ret = _day(row.get("return_date"))
    price = _int(row.get("value"))
    stops = _int(row.get("number_of_changes"))
    searched = _day(row.get("found_at"))
    dest_city = row.get("destination")
    if not (depart and ret and dest_city and searched) or price is None or price <= 0 or stops is None:
        return None
    return Fare(
        origin=origin,
        dest_airport=dest_city,
        dest_city=dest_city,
        depart_date=depart,
        return_date=ret,
        price=price,
        airline="",
        stops=stops,
        searched_on=searched,
        link="",
        kind=kind,
        scan_date=scan_date,
    )


def rejection_reason(fare: Fare, today: date) -> str | None:
    """Why a fare should be dropped, or None to keep it. Seed rows may be past or old."""
    if not MIN_TRIP_DAYS <= fare.trip_days <= MAX_TRIP_DAYS:
        return "trip_length"
    if fare.stops > MAX_STOPS:
        return "stops"
    if fare.kind != "seed":
        if fare.depart_date <= today:
            return "past"
        if (today - fare.searched_on).days > MAX_AGE_DAYS:
            return "stale"
    return None


def dedupe(fares: list[Fare]) -> list[Fare]:
    """One fare per (city, dates, observation day): the cheapest, preferring "dates" rows on ties.

    If that fare does not include a checked bag, the cheapest bag-included twin is kept too.
    """
    groups: dict[tuple, list[Fare]] = {}
    for fare in fares:
        key = (fare.dest_city, fare.depart_date, fare.return_date, fare.obs_day)
        groups.setdefault(key, []).append(fare)

    def rank(fare: Fare) -> tuple:
        return fare.price, fare.kind != "dates"

    kept: list[Fare] = []
    for group in groups.values():
        best = min(group, key=rank)
        kept.append(best)
        if best.checked_bag is not True:
            bagged = [fare for fare in group if fare.checked_bag is True]
            if bagged:
                kept.append(min(bagged, key=rank))
    return kept


def months_from(today: date, count: int) -> list[str]:
    """count consecutive 'YYYY-MM' strings starting with today's month."""
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


@dataclass
class FetchResult:
    fares: list[Fare] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


GetJson = Callable[[str, dict, dict], dict]


class TravelpayoutsSource:
    """Fetches cached round-trip fares from one origin."""

    def __init__(self, token: str, get_json: GetJson | None = None, months_ahead: int = 12):
        self._headers = {"X-Access-Token": token, "Accept": "application/json"}
        self._get_json = get_json or http_get_json
        self._months_ahead = months_ahead

    def fetch(self, origin: str, today: date) -> FetchResult:
        result = FetchResult()
        candidates: list[Fare | None] = []
        for month in months_from(today, self._months_ahead):
            rows = self._pages(result, f"dates {month}", "/aviasales/v3/prices_for_dates", {
                "origin": origin,
                "departure_at": month,
                "one_way": "false",
                "currency": "cad",
                "market": "ca",
                "sorting": "price",
                "unique": "false",
            })
            candidates += [normalize_dates_row(row, origin, today) for row in rows]
        rows = self._latest(result, "latest", origin, today.year)
        candidates += [normalize_latest_row(row, origin, today, "latest") for row in rows]
        return _finish(candidates, today, result)

    def seed(self, origin: str, today: date) -> FetchResult:
        result = FetchResult()
        candidates: list[Fare | None] = []
        for year in (today.year - 1, today.year):
            rows = self._latest(result, f"seed {year}", origin, year)
            candidates += [normalize_latest_row(row, origin, today, "seed") for row in rows]
        return _finish(candidates, today, result)

    def _latest(self, result: FetchResult, label: str, origin: str, year: int) -> list[dict]:
        return self._pages(result, label, "/aviasales/v3/get_latest_prices", {
            "origin": origin,
            "currency": "cad",
            "period_type": "year",
            "beginning_of_period": str(year),
            "one_way": "false",
            "show_to_affiliates": "false",
            "market": "ca",
            "sorting": "price",
        })

    def _pages(self, result: FetchResult, label: str, path: str, params: dict) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            query = {**params, "limit": str(PAGE_LIMIT), "page": str(page)}
            try:
                body = self._get_json(API + path, query, self._headers)
            except ApiError as err:
                print(f"warning: {label} page {page} failed: {err}")
                result.failed.append(label)
                return rows
            data = body.get("data") if isinstance(body, dict) else None
            if not (isinstance(body, dict) and body.get("success") and isinstance(data, list)):
                error = body.get("error") if isinstance(body, dict) else body
                print(f"warning: {label} page {page} returned an error: {error}")
                result.failed.append(label)
                return rows
            rows.extend(data)
            if len(data) < PAGE_LIMIT:
                break
        return rows


def _finish(candidates: list[Fare | None], today: date, result: FetchResult) -> FetchResult:
    kept = []
    for fare in candidates:
        reason = "invalid" if fare is None else rejection_reason(fare, today)
        if reason:
            result.rejected[reason] = result.rejected.get(reason, 0) + 1
        else:
            kept.append(fare)
    result.fares = dedupe(kept)
    return result
