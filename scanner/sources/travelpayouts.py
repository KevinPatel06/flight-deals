"""Travelpayouts / Aviasales Data API adapter: cached fares -> Fare records."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

from scanner.fare import Fare

AVIASALES = "https://www.aviasales.com"
MIN_TRIP_DAYS = 2
MAX_TRIP_DAYS = 30
MAX_STOPS = 1
MAX_AGE_DAYS = 7

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
