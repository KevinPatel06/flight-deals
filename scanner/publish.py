"""Shape scored fares into site/deals.json, validate it, and write it safely."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from scanner.check import GoogleCheck
from scanner.places import Places
from scanner.scoring import ScoredFare

SHOW_DAYS = 7
CONFIRM_TOLERANCE = 0.10


def google_link(origin: str, airport: str, depart: date, ret: date) -> str:
    query = f"Flights from {origin} to {airport} on {depart.isoformat()} through {ret.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + quote(query)


def _rank(option: dict) -> tuple:
    pct = option["pct_off"] if option["pct_off"] is not None else -1
    return -pct, option["price"]


def pick_best(options: list[dict]) -> dict:
    """Highest percent off; ties and new routes go to the lowest price."""
    return min(options, key=_rank)


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


def fare_json(scored: ScoredFare, check: GoogleCheck | None = None) -> dict:
    fare = scored.fare
    links = {"google": google_link(fare.origin, fare.dest_airport, fare.depart_date, fare.return_date)}
    if fare.link:
        links["aviasales"] = fare.link
    option = {
        "airport": fare.dest_airport,
        "depart": fare.depart_date.isoformat(),
        "return": fare.return_date.isoformat(),
        "days": fare.trip_days,
        "price": fare.price,
        "normal": scored.normal,
        "pct_off": scored.pct_off,
        "baseline": scored.baseline,
        "airline": fare.airline,
        "stops": fare.stops,
        "carry_on": fare.carry_on,
        "checked_bag": fare.checked_bag,
        "bag_pieces": fare.bag_pieces,
        "bag_kg": fare.bag_kg,
        "searched_on": fare.searched_on.isoformat(),
        "age_days": scored.age_days,
        "badges": list(scored.badges),
        "links": links,
    }
    if check is not None:
        option["google"] = google_json(check, fare.price)
        if check.status == "ok" and check.url:
            option["links"]["google"] = check.url
    return option


def build_deals(
    scored: list[ScoredFare],
    places: Places,
    *,
    origin: str,
    scan_date: date,
    rejected: dict[str, int],
    generated_at: datetime,
    checks=(),
    searches_left: int | None = None,
    google_note: str | None = None,
) -> dict:
    latest = latest_checks(checks, scan_date)
    by_city: dict[str, list[ScoredFare]] = {}
    for item in scored:
        by_city.setdefault(item.fare.dest_city, []).append(item)
    destinations = []
    for city, items in sorted(by_city.items()):
        place = places.lookup(city)
        options = sorted(
            (fare_json(item, latest.get((item.fare.dest_airport, item.fare.depart_date, item.fare.return_date))) for item in items),
            key=_rank,
        )
        destinations.append({
            "city": city,
            "name": place.name,
            "country": place.country,
            "country_code": place.country_code,
            "region": place.region,
            "best": options[0],
            "options": options,
        })
    return {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin": origin,
        "scan": {
            "date": scan_date.isoformat(),
            "fares": len(scored),
            "destinations": len(destinations),
            "rejected": dict(sorted(rejected.items())),
            "google": {
                "checked_today": sum(1 for c in checks if c.checked_on == scan_date),
                "searches_left": searches_left,
                "note": google_note,
            },
        },
        "destinations": destinations,
    }


def _option_errors(option, where: str) -> list[str]:
    if not isinstance(option, dict):
        return [f"{where}: not an object"]
    errors = []
    price = option.get("price")
    if not isinstance(price, int) or price <= 0:
        errors.append(f"{where}: bad price {price!r}")
    for key in ("depart", "return", "searched_on"):
        value = option.get(key)
        if not (isinstance(value, str) and len(value) == 10):
            errors.append(f"{where}: bad {key} {value!r}")
    pct = option.get("pct_off")
    if pct is not None and not isinstance(pct, int):
        errors.append(f"{where}: bad pct_off {pct!r}")
    if not isinstance(option.get("badges"), list):
        errors.append(f"{where}: badges missing")
    links = option.get("links")
    if not (isinstance(links, dict) and isinstance(links.get("google"), str)):
        errors.append(f"{where}: google link missing")
    if "google" in option:
        google = option["google"]
        if not isinstance(google, dict) or google.get("status") not in ("ok", "none"):
            errors.append(f"{where}: bad google")
        elif google["status"] == "ok" and not (isinstance(google.get("price"), int) and google["price"] > 0):
            errors.append(f"{where}: bad google price")
    return errors


def validate(deals: dict) -> list[str]:
    """Problems that would break the page; an empty list means the file is safe to publish."""
    errors = []
    for key in ("generated_at", "origin"):
        if not isinstance(deals.get(key), str):
            errors.append(f"missing {key}")
    if not isinstance(deals.get("scan"), dict):
        errors.append("missing scan")
    destinations = deals.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        return errors + ["no destinations"]
    for index, dest in enumerate(destinations):
        where = f"destinations[{index}]"
        for key in ("city", "name", "region"):
            if not isinstance(dest.get(key), str):
                errors.append(f"{where}: missing {key}")
        options = dest.get("options")
        if not isinstance(options, list) or not options:
            errors.append(f"{where}: no options")
            continue
        errors += _option_errors(dest.get("best"), f"{where}.best")
        for opt_index, option in enumerate(options):
            errors += _option_errors(option, f"{where}.options[{opt_index}]")
    return errors


def write_deals(deals: dict, path: Path) -> None:
    errors = validate(deals)
    if errors:
        raise ValueError("invalid deals.json: " + "; ".join(errors[:5]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(deals, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
