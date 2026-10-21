from datetime import date, timedelta

from factories import TODAY, make_fare
from scanner.check import GoogleCheck
from scanner.net import ApiError
from scanner.places import Places
from scanner.scoring import ScoredFare
from scanner.targets import TARGETS, Target, excluded, target_of
from scanner.verify import CheckRun, pick, run_checks

NO_TARGETS = ()  # tests of the "other destinations" ordering on its own

PLACES = Places(
    {c: {"name": c, "country": cc} for c, cc in
     [("LIS", "PT"), ("PAR", "FR"), ("CUN", "MX"), ("PUJ", "DO"), ("YVR", "CA"), ("YYC", "CA"), ("YTO", "CA"), ("TYO", "JP"),
      ("NYC", "US"), ("MIA", "US"), ("HNL", "US"), ("OGG", "US"), ("KOA", "US")]},
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
    assert cities(pick(items, PLACES, [], TODAY, targets=NO_TARGETS)) == ["LIS"]


def test_strong_dollar_countries_first_then_big_discounts_then_new_routes():
    items = [scored("LIS", 35), scored("PAR", 72), scored("CUN", None, price=400), scored("PUJ", 20), scored("TYO", None, price=900)]
    # PT, MX and JP are strong-dollar countries, France is not; PUJ's 20% off is not worth a search.
    assert cities(pick(items, PLACES, [], TODAY, targets=NO_TARGETS)) == ["LIS", "CUN", "TYO", "PAR"]


def test_each_city_sends_only_its_best_option():
    items = [scored("LIS", 35), scored("LIS", 45, depart_date=date(2026, 12, 5))]
    picked = pick(items, PLACES, [], TODAY, targets=NO_TARGETS)
    assert [(i.fare.dest_city, i.pct_off) for i in picked] == [("LIS", 45)]


def test_new_routes_least_recently_checked_first():
    cun, tyo, puj = scored("CUN", None, price=300), scored("TYO", None, price=900), scored("PUJ", None, price=400)
    old = [check_for(cun, TODAY - timedelta(days=20), depart=date(2027, 3, 1)),
           check_for(puj, TODAY - timedelta(days=40), depart=date(2027, 3, 1))]
    assert cities(pick([cun, tyo, puj], PLACES, old, TODAY, targets=NO_TARGETS)) == ["TYO", "PUJ", "CUN"]


def test_same_trip_is_not_rechecked_within_three_days():
    lis = scored("LIS", 50)
    assert pick([lis], PLACES, [check_for(lis, TODAY - timedelta(days=2))], TODAY, targets=NO_TARGETS) == []
    assert cities(pick([lis], PLACES, [check_for(lis, TODAY - timedelta(days=3))], TODAY, targets=NO_TARGETS)) == ["LIS"]


def test_pick_counts_todays_checks_against_the_cap():
    items = [scored(c, 50) for c in ("LIS", "PAR", "CUN", "PUJ", "TYO")]
    done_today = [check_for(scored("ZZZ", 50), TODAY, dest_city=f"Z{i}", airport=f"Z{i}") for i in range(5)]
    assert len(pick(items, PLACES, done_today, TODAY, targets=NO_TARGETS)) == 2
    assert pick(items, PLACES, done_today * 2, TODAY, targets=NO_TARGETS) == []
    assert len(pick(items, PLACES, [], TODAY, limit=3, targets=NO_TARGETS)) == 3


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


class BrokenChecker(FakeChecker):
    def __init__(self, quota_error=False):
        super().__init__()
        self.quota_error = quota_error

    def searches_left(self):
        if self.quota_error:
            raise KeyError("plan_searches_left")
        return 100

    def check(self, fare, today):
        self.checked.append(fare.dest_city)
        raise TypeError("'int' object is not iterable")


def test_unexpected_errors_become_a_note_and_never_raise():
    run = run_checks(BrokenChecker(), [scored("LIS", 50), scored("PAR", 45), scored("CUN", 40)], TODAY)
    assert run.checks == [] and run.note == "check failed: TypeError"
    run = run_checks(BrokenChecker(quota_error=True), [scored("LIS", 50)], TODAY)
    assert run.checks == [] and run.note == "quota check failed: KeyError"


def test_united_states_is_skipped_except_hawaii():
    items = [scored("NYC", 60), scored("MIA", None), scored("HNL", 40), scored("OGG", None), scored("KOA", None, price=700)]
    assert cities(pick(items, PLACES, [], TODAY, targets=NO_TARGETS)) == ["HNL", "OGG", "KOA"]


def test_cities_missing_from_reference_data_are_skipped():
    assert pick([scored("QQQ", 50)], PLACES, [], TODAY, targets=NO_TARGETS) == []


def test_city_google_could_not_price_rests_two_weeks_whatever_the_dates():
    lis = scored("LIS", 50)
    none = check_for(lis, TODAY - timedelta(days=13), status="none", price=None, depart=date(2027, 5, 1))
    assert pick([lis], PLACES, [none], TODAY, targets=NO_TARGETS) == []
    older = check_for(lis, TODAY - timedelta(days=14), status="none", price=None, depart=date(2027, 5, 1))
    assert cities(pick([lis], PLACES, [older], TODAY, targets=NO_TARGETS)) == ["LIS"]
    priced_later = check_for(lis, TODAY - timedelta(days=5), depart=date(2027, 6, 1))
    assert cities(pick([lis], PLACES, [none, priced_later], TODAY, targets=NO_TARGETS)) == ["LIS"]


def test_checks_from_another_origin_do_not_block_or_count():
    lis = scored("LIS", 50)
    other = check_for(lis, TODAY, origin="YYZ")
    assert cities(pick([lis], PLACES, [other], TODAY, targets=NO_TARGETS)) == ["LIS"]
    assert len(pick([scored(c, 50) for c in ("LIS", "PAR")], PLACES, [other] * 7, TODAY, targets=NO_TARGETS)) == 2


def test_run_checks_never_spends_below_the_floor():
    checker = FakeChecker(left=12)
    run = run_checks(checker, [scored(c, 50) for c in ("LIS", "PAR", "CUN", "PUJ")], TODAY)
    assert checker.checked == ["LIS", "PAR"]
    assert (run.searches_left, run.note) == (10, None)
    checker = FakeChecker(left=10)
    assert run_checks(checker, [scored("LIS", 50)], TODAY) == CheckRun([], 10, "only 10 Google searches left this month")


# Must-check targets

TARGET_PLACES = Places(
    {c: {"name": c, "country": cc} for c, cc in
     [("LIS", "PT"), ("PAR", "FR"), ("YVR", "CA"), ("NYC", "US"), ("HNL", "US"), ("KOA", "US"), ("OGG", "US"),
      ("CUN", "MX"), ("PVR", "MX"), ("OAX", "MX"), ("HAN", "VN"), ("CPT", "ZA"), ("REK", "IS"), ("TYO", "JP")]
     + [(f"T{i}", "XX") for i in range(1, 9)]},
    {}, {},
)
EIGHT = tuple(Target(f"T{i}", (f"T{i}",), f"T{i}", 7) for i in range(1, 9))


def test_targets_take_six_slots_and_others_get_the_seventh():
    items = [scored(f"T{i}", None) for i in range(1, 9)] + [scored("LIS", 50), scored("PAR", 60)]
    assert cities(pick(items, TARGET_PLACES, [], TODAY, targets=EIGHT)) == ["T1", "T2", "T3", "T4", "T5", "T6", "LIS"]


def test_targets_fill_slots_others_leave_unused():
    items = [scored(f"T{i}", None) for i in range(1, 9)]
    assert cities(pick(items, TARGET_PLACES, [], TODAY, targets=EIGHT)) == ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]


def test_targets_rotate_least_recently_searched_first_and_big_discounts_jump_the_queue():
    t1, t2, t3, t4 = (scored(f"T{i}", None) for i in range(1, 5))
    old = [check_for(t1, TODAY - timedelta(days=1), depart=date(2027, 3, 1)),
           check_for(t2, TODAY - timedelta(days=5), depart=date(2027, 3, 1))]
    assert cities(pick([t1, t2, t3], TARGET_PLACES, old, TODAY, targets=EIGHT[:3])) == ["T3", "T2", "T1"]
    t1_big = scored("T1", 45)
    assert cities(pick([t1_big, t2, t3, t4], TARGET_PLACES, old, TODAY, targets=EIGHT[:4])) == ["T1", "T3", "T4", "T2"]


def test_target_sends_its_best_fare_not_searched_lately():
    cun = Target("Cancun", ("CUN",), "CUN", 7)
    cheap, dearer = scored("CUN", None, price=300), scored("CUN", None, price=400, depart_date=date(2026, 12, 5))
    picked = pick([cheap, dearer], TARGET_PLACES, [check_for(cheap, TODAY - timedelta(days=1))], TODAY, targets=(cun,))
    assert [i.fare.price for i in picked] == [400]
    both_recent = [check_for(cheap, TODAY - timedelta(days=1)), check_for(dearer, TODAY - timedelta(days=2))]
    assert pick([cheap, dearer], TARGET_PLACES, both_recent, TODAY, targets=(cun,)) == []  # no direct search either


def test_target_with_no_cached_fare_gets_a_direct_search():
    hawaii = Target("Hawaii", ("HNL", "KOA"), "HNL", 7)
    tokyo = Target("Tokyo", ("TYO",), "HND", 14)
    picked = pick([scored("LIS", 10)], TARGET_PLACES, [], TODAY, targets=(hawaii, tokyo))
    assert [(i.fare.dest_city, i.fare.dest_airport, i.fare.price, i.fare.kind) for i in picked] == [
        ("HNL", "HNL", 0, "direct"), ("TYO", "HND", 0, "direct")]
    # TODAY is Thursday 2026-10-08: 56 days later is Thursday 2026-12-03, so the next Tuesday.
    assert [(i.fare.depart_date, i.fare.return_date) for i in picked] == [
        (date(2026, 12, 8), date(2026, 12, 15)), (date(2026, 12, 8), date(2026, 12, 22))]


def test_direct_search_skips_resting_and_unknown_cities():
    hawaii = Target("Hawaii", ("HNL",), "HNL", 7)
    mystery = Target("Mystery", ("QQQ",), "QQQ", 7)
    lis = scored("LIS", 10)
    none = check_for(lis, TODAY - timedelta(days=3), dest_city="HNL", airport="HNL", status="none", price=None)
    assert pick([lis], TARGET_PLACES, [none], TODAY, targets=(hawaii, mystery)) == []


def test_hawaii_is_one_target_sending_its_best_island():
    items = [scored("NYC", 60), scored("HNL", 20), scored("KOA", 40), scored("OGG", None)]
    picked = pick(items, TARGET_PLACES, [], TODAY, targets=TARGETS[:1])
    assert cities(picked) == ["KOA"]


def test_target_membership_and_exclusions():
    def name(city):
        target = target_of(city, TARGET_PLACES)
        return target.name if target else None

    assert [name(c) for c in ("CUN", "PVR", "OAX", "HAN", "CPT", "KOA", "REK", "TYO", "NYC", "LIS")] == [
        "Cancun", "Mexico", "Mexico", "Vietnam", "South Africa", "Hawaii", "Reykjavik", "Tokyo", None, None]
    assert [excluded(c, TARGET_PLACES) for c in ("NYC", "YVR", "HNL", "LIS", "QQQ")] == [True, True, False, False, False]
