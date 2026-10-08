import http.client
import urllib.error
from datetime import date

import pytest

from factories import TODAY
from scanner.sources import travelpayouts as tp
from scanner.sources.travelpayouts import ApiError, TravelpayoutsSource, http_get_json, months_from


def dates_row(dest="LIS", depart="2026-12-01", ret="2026-12-10", price=500, search="06102026"):
    return {
        "origin_airport": "YUL",
        "destination": dest,
        "destination_airport": dest,
        "departure_at": f"{depart}T10:00:00-05:00",
        "return_at": f"{ret}T10:00:00+00:00",
        "price": price,
        "airline": "TP",
        "transfers": 0,
        "return_transfers": 0,
        "link": f"/search/X?search_date={search}&static_fare_key=TY%7CH0%7CL0",
    }


def latest_row(dest="PAR", depart="2027-01-10", ret="2027-01-20", price=600, found="2026-10-07T01:00:00Z"):
    return {
        "destination": dest,
        "depart_date": f"{depart}T10:00:00-05:00",
        "return_date": f"{ret}T10:00:00+01:00",
        "value": price,
        "number_of_changes": 1,
        "found_at": found,
    }


def ok(rows):
    return {"success": True, "data": rows}


class FakeApi:
    """Stands in for http_get_json. Responses keyed by (endpoint, month-or-year, page)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, params, headers):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params), dict(headers)))
        key = (endpoint, params.get("departure_at") or params.get("beginning_of_period"), params["page"])
        value = self.responses.get(key, ok([]))
        if isinstance(value, Exception):
            raise value
        return value

    def params_for(self, endpoint):
        return [params for name, params, _ in self.calls if name == endpoint]


def test_months_from():
    assert months_from(date(2026, 11, 15), 3) == ["2026-11", "2026-12", "2027-01"]


def test_fetch_queries_every_month_and_latest():
    api = FakeApi({
        ("prices_for_dates", "2026-12", "1"): ok([dates_row()]),
        ("get_latest_prices", "2026", "1"): ok([latest_row()]),
    })
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)

    assert [p["departure_at"] for p in api.params_for("prices_for_dates")] == months_from(TODAY, 12)
    first = api.params_for("prices_for_dates")[0]
    assert (first["origin"], first["currency"], first["market"], first["one_way"]) == ("YUL", "cad", "ca", "false")
    assert api.calls[0][2]["X-Access-Token"] == "tok"
    assert sorted(f.dest_city for f in result.fares) == ["LIS", "PAR"]
    assert result.failed == []


def test_fetch_pages_until_a_short_page(monkeypatch):
    monkeypatch.setattr(tp, "PAGE_LIMIT", 2)
    api = FakeApi({
        ("prices_for_dates", "2026-12", "1"): ok([dates_row(), dates_row(dest="ROM")]),
        ("prices_for_dates", "2026-12", "2"): ok([dates_row(dest="BCN")]),
    })
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)

    december = [p for p in api.params_for("prices_for_dates") if p["departure_at"] == "2026-12"]
    assert [p["page"] for p in december] == ["1", "2"]
    assert december[0]["limit"] == "2"
    assert {"LIS", "ROM", "BCN"} <= {f.dest_city for f in result.fares}


def test_failed_month_is_recorded_and_others_continue():
    api = FakeApi({
        ("prices_for_dates", "2026-11", "1"): ApiError("HTTP 500"),
        ("prices_for_dates", "2026-12", "1"): ok([dates_row()]),
    })
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)
    assert result.failed == ["dates 2026-11"]
    assert [f.dest_city for f in result.fares] == ["LIS"]


def test_error_body_counts_as_failure():
    api = FakeApi({("prices_for_dates", "2026-12", "1"): {"success": False, "error": "bad token", "data": None}})
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)
    assert result.failed == ["dates 2026-12"]
    assert result.fares == []


def test_rejections_are_counted():
    api = FakeApi({
        ("prices_for_dates", "2026-12", "1"): ok([dates_row(), dates_row(ret="2026-12-02"), {"price": "x"}]),
    })
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)
    assert result.rejected == {"trip_length": 1, "invalid": 1}
    assert len(result.fares) == 1


def test_seed_reads_previous_and_current_year_as_seed_rows():
    api = FakeApi({
        ("get_latest_prices", "2025", "1"): ok([latest_row(depart="2025-06-01", ret="2025-06-10", found="2025-05-01T00:00:00Z")]),
        ("get_latest_prices", "2026", "1"): ok([latest_row()]),
    })
    result = TravelpayoutsSource("tok", get_json=api).seed("YUL", TODAY)
    assert [p["beginning_of_period"] for p in api.params_for("get_latest_prices")] == ["2025", "2026"]
    assert len(result.fares) == 2
    assert {f.kind for f in result.fares} == {"seed"}


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_from(outcomes):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("https://api.example/x", code, "error", {}, None)


def test_http_get_json_retries_then_succeeds():
    sleeps = []
    opener = opener_from([http_error(429), http_error(503), b'{"success": true, "data": []}'])
    body = http_get_json("https://api.example/x", {"a": "1"}, {}, opener=opener, sleep=sleeps.append)
    assert body == {"success": True, "data": []}
    assert sleeps == [2, 4]
    assert opener.calls[0] == "https://api.example/x?a=1"


def test_http_get_json_does_not_retry_auth_errors():
    sleeps = []
    with pytest.raises(ApiError, match="401"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([http_error(401)]), sleep=sleeps.append)
    assert sleeps == []


def test_http_get_json_gives_up_after_three_retries():
    sleeps = []
    with pytest.raises(ApiError, match="429"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([http_error(429)] * 4), sleep=sleeps.append)
    assert sleeps == [2, 4, 8]


def test_http_get_json_rejects_non_json():
    with pytest.raises(ApiError, match="invalid JSON"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([b"<html>"]), sleep=lambda s: None)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionResetError("reset by peer"),
        http.client.RemoteDisconnected("closed"),
        http.client.IncompleteRead(b""),
    ],
)
def test_http_get_json_retries_low_level_network_errors(error):
    sleeps = []
    opener = opener_from([error, b'{"success": true, "data": []}'])
    assert http_get_json("https://api.example/x", {}, {}, opener=opener, sleep=sleeps.append) == {"success": True, "data": []}
    assert sleeps == [2]


def test_http_get_json_turns_repeated_timeouts_into_api_error():
    with pytest.raises(ApiError, match="network error"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([TimeoutError("t")] * 4), sleep=lambda s: None)


def test_non_object_rows_are_counted_invalid():
    api = FakeApi({("prices_for_dates", "2026-12", "1"): ok([dates_row(), "junk", None])})
    result = TravelpayoutsSource("tok", get_json=api).fetch("YUL", TODAY)
    assert result.rejected == {"invalid": 2}
    assert len(result.fares) == 1
