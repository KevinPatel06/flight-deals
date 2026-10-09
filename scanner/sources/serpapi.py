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
        if not price:
            continue
        try:
            out.append((datetime.fromtimestamp(point[0], timezone.utc).date(), price))
        except (ValueError, OSError, OverflowError):  # NaN or out-of-range timestamps
            continue
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
    flights = [f for key in ("best_flights", "other_flights") if isinstance(data.get(key), list) for f in data[key] if isinstance(f, dict)]
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
        status="ok", price=price, level=level if isinstance(level, str) and level in LEVELS else None, typical_low=low, typical_high=high,
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
