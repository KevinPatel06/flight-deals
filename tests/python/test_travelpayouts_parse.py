from datetime import date

import pytest

from factories import TODAY, make_fare
from scanner.sources.travelpayouts import (
    dedupe,
    normalize_dates_row,
    normalize_latest_row,
    parse_bags,
    parse_search_date,
    rejection_reason,
)

LINK = (
    "/search/YUL0212POS16121?t=UA1796&search_date=06102026"
    "&static_fare_key=TY%7CP0%7CH1%7CL1_1_23%7CCH0%7CR0%7CTBC1&expected_price=347"
)

DATES_ROW = {
    "origin": "YMQ",
    "origin_airport": "YUL",
    "destination": "POS",
    "destination_airport": "POS",
    "departure_at": "2026-12-02T15:00:00-05:00",
    "return_at": "2026-12-16T01:25:00-04:00",
    "price": 347,
    "airline": "UA",
    "transfers": 1,
    "return_transfers": 0,
    "link": LINK,
}

LATEST_ROW = {
    "origin": "YMQ",
    "destination": "PAR",
    "depart_date": "2027-03-30T06:45:00-04:00",
    "return_date": "2027-04-06T10:00:00-04:00",
    "value": 612,
    "number_of_changes": 0,
    "found_at": "2026-10-03T02:33:38Z",
    "gate": "Farehutz",
}


def test_parse_search_date():
    assert parse_search_date(LINK) == date(2026, 10, 6)


def test_parse_search_date_missing_or_invalid():
    assert parse_search_date("") is None
    assert parse_search_date("/search/X?search_date=32132026") is None


def test_parse_bags_checked_bag_included():
    assert parse_bags(LINK) == (True, True, 1, 23)


def test_parse_bags_no_bag():
    assert parse_bags("/s?static_fare_key=TY%7CP1%7CH0%7CL0%7CCH0") == (False, False, None, None)


def test_parse_bags_unknown_weight():
    assert parse_bags("/s?static_fare_key=TY%7CH1%7CL1_2_0%7CCH0") == (True, True, 2, None)


def test_parse_bags_missing_key():
    assert parse_bags("/search/X?search_date=06102026") == (None, None, None, None)


def test_normalize_dates_row():
    fare = normalize_dates_row(DATES_ROW, "YUL", TODAY)
    assert fare.origin == "YUL"
    assert (fare.dest_city, fare.dest_airport) == ("POS", "POS")
    assert (fare.depart_date, fare.return_date) == (date(2026, 12, 2), date(2026, 12, 16))
    assert (fare.price, fare.airline, fare.stops) == (347, "UA", 1)
    assert fare.searched_on == date(2026, 10, 6)
    assert (fare.kind, fare.scan_date) == ("dates", TODAY)
    assert fare.link == "https://www.aviasales.com" + LINK
    assert (fare.carry_on, fare.checked_bag, fare.bag_pieces, fare.bag_kg) == (True, True, 1, 23)


def test_normalize_dates_row_coerces_string_price():
    assert normalize_dates_row({**DATES_ROW, "price": "347.0"}, "YUL", TODAY).price == 347


@pytest.mark.parametrize(
    "change",
    [
        {"price": "abc"},
        {"price": 0},
        {"price": -5},
        {"return_at": None},
        {"transfers": None},
        {"link": "/search/no-date"},
        {"destination": None},
    ],
)
def test_normalize_dates_row_rejects_bad_rows(change):
    assert normalize_dates_row({**DATES_ROW, **change}, "YUL", TODAY) is None


def test_normalize_latest_row():
    fare = normalize_latest_row(LATEST_ROW, "YUL", TODAY, "latest")
    assert fare.origin == "YUL"
    assert (fare.dest_city, fare.dest_airport) == ("PAR", "PAR")
    assert (fare.depart_date, fare.return_date) == (date(2027, 3, 30), date(2027, 4, 6))
    assert (fare.price, fare.stops, fare.airline, fare.link) == (612, 0, "", "")
    assert fare.searched_on == date(2026, 10, 3)
    assert fare.kind == "latest"
    assert fare.checked_bag is None


def test_normalize_latest_row_seed_kind():
    assert normalize_latest_row(LATEST_ROW, "YUL", TODAY, "seed").kind == "seed"


def test_normalize_latest_row_rejects_bad_rows():
    assert normalize_latest_row({**LATEST_ROW, "value": None}, "YUL", TODAY, "latest") is None
    assert normalize_latest_row({**LATEST_ROW, "found_at": "bad"}, "YUL", TODAY, "latest") is None


def test_rejection_reasons():
    assert rejection_reason(make_fare(), TODAY) is None
    assert rejection_reason(make_fare(return_date=date(2026, 12, 2)), TODAY) == "trip_length"
    assert rejection_reason(make_fare(return_date=date(2027, 1, 1)), TODAY) == "trip_length"
    assert rejection_reason(make_fare(stops=2), TODAY) == "stops"
    past = make_fare(depart_date=TODAY, return_date=date(2026, 10, 15))
    assert rejection_reason(past, TODAY) == "past"
    assert rejection_reason(make_fare(searched_on=date(2026, 9, 30)), TODAY) == "stale"
    assert rejection_reason(make_fare(searched_on=date(2026, 10, 1)), TODAY) is None


def test_seed_rows_skip_past_and_stale_rules():
    seed = make_fare(
        kind="seed",
        depart_date=date(2026, 3, 1),
        return_date=date(2026, 3, 10),
        searched_on=date(2026, 2, 1),
    )
    assert rejection_reason(seed, TODAY) is None


def test_dedupe_keeps_cheapest_per_itinerary():
    a, b = make_fare(price=500), make_fare(price=450)
    assert dedupe([a, b]) == [b]


def test_dedupe_prefers_dates_rows_on_tie():
    latest = make_fare(price=450, kind="latest", link="")
    dated = make_fare(price=450, kind="dates", link="https://www.aviasales.com/search/X")
    assert dedupe([latest, dated]) == [dated]


def test_dedupe_keeps_cheapest_bag_included_twin():
    no_bag = make_fare(price=400, checked_bag=False)
    bag = make_fare(price=520, checked_bag=True)
    pricier_bag = make_fare(price=600, checked_bag=True)
    assert dedupe([no_bag, bag, pricier_bag]) == [no_bag, bag]


def test_dedupe_keeps_seed_rows_from_different_search_days():
    a = make_fare(kind="seed", searched_on=date(2026, 9, 1))
    b = make_fare(kind="seed", searched_on=date(2026, 9, 2))
    assert len(dedupe([a, b])) == 2
