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
