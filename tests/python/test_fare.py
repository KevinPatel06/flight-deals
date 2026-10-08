from datetime import date

from factories import make_fare


def test_trip_days():
    fare = make_fare(depart_date=date(2026, 12, 1), return_date=date(2026, 12, 10))
    assert fare.trip_days == 9


def test_obs_day_is_scan_date_for_daily_rows():
    fare = make_fare(kind="dates", scan_date=date(2026, 10, 8), searched_on=date(2026, 10, 5))
    assert fare.obs_day == date(2026, 10, 8)


def test_obs_day_is_search_date_for_seed_rows():
    fare = make_fare(kind="seed", scan_date=date(2026, 10, 8), searched_on=date(2026, 10, 5))
    assert fare.obs_day == date(2026, 10, 5)


def test_bag_fields_default_to_unknown():
    fare = make_fare()
    assert (fare.carry_on, fare.checked_bag, fare.bag_pieces, fare.bag_kg) == (None, None, None, None)
