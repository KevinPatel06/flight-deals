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
    assert check.trip == ("YUL", "CUN", date(2027, 1, 12), date(2027, 1, 19))
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


def test_parse_check_survives_wrong_container_types():
    body = {
        "best_flights": 5,
        "other_flights": [{"price": 400}],
        "price_insights": {"price_level": [], "price_history": [[1.7e15, 300], [float("nan"), 200], [1786579200, 250]]},
    }
    check = parse_check(body, CUN, TODAY)
    assert (check.status, check.price, check.level) == ("ok", 400, None)
    assert check.history == ((date(2026, 8, 13), 250),)


def test_inverted_typical_range_is_dropped():
    check = parse_check({"price_insights": {"lowest_price": 400, "typical_price_range": [500, 450]}}, CUN, TODAY)
    assert (check.typical_low, check.typical_high, check.pct_below) == (None, None, None)
