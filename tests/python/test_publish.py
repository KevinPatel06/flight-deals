import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from factories import TODAY, make_fare
from scanner.check import GoogleCheck
from scanner.places import Places
from scanner.publish import build_deals, google_link, pick_best, validate, write_deals
from scanner.scoring import ScoredFare

PLACES = Places({"LIS": {"name": "Lisbon", "country": "PT"}}, {"PT": {"name": "Portugal"}}, {"Europe": ["PT"]})
NOW = datetime(2026, 10, 8, 10, 5, tzinfo=timezone.utc)
OPTION_KEYS = {
    "airport", "depart", "return", "days", "price", "normal", "pct_off", "baseline", "airline", "stops",
    "carry_on", "checked_bag", "bag_pieces", "bag_kg", "searched_on", "age_days", "badges", "links",
}


def scored(price=500, pct=40, dest="LIS", **fare_fields):
    return ScoredFare(
        fare=make_fare(price=price, dest_city=dest, dest_airport=dest, **fare_fields),
        normal=None if pct is None else 900,
        pct_off=pct,
        baseline=None if pct is None else "month",
        age_days=0,
        badges=("fresh",),
    )


def sample_deals():
    return build_deals(
        [
            scored(500, 40),
            scored(450, 50, depart_date=date(2026, 12, 5), return_date=date(2026, 12, 12)),
            scored(300, None, dest="QQQ"),
        ],
        PLACES,
        origin="YUL",
        scan_date=TODAY,
        rejected={"stops": 3},
        generated_at=NOW,
    )


def test_google_link():
    assert google_link("YUL", "LIS", date(2026, 12, 1), date(2026, 12, 10)) == (
        "https://www.google.com/travel/flights?q=Flights%20from%20YUL%20to%20LIS"
        "%20on%202026-12-01%20through%202026-12-10"
    )


def test_best_is_highest_percent_then_lowest_price():
    options = [
        {"pct_off": 40, "price": 500},
        {"pct_off": 55, "price": 700},
        {"pct_off": 55, "price": 650},
        {"pct_off": None, "price": 100},
    ]
    assert pick_best(options) == {"pct_off": 55, "price": 650}


def test_best_falls_back_to_lowest_price_for_new_routes():
    assert pick_best([{"pct_off": None, "price": 300}, {"pct_off": None, "price": 200}])["price"] == 200


def test_build_deals_groups_by_destination():
    deals = sample_deals()
    assert deals["generated_at"] == "2026-10-08T10:05:00Z"
    assert deals["origin"] == "YUL"
    assert deals["scan"] == {
        "date": "2026-10-08", "fares": 3, "destinations": 2, "rejected": {"stops": 3},
        "google": {"checked_today": 0, "searches_left": None, "note": None},
    }

    lis = next(d for d in deals["destinations"] if d["city"] == "LIS")
    assert (lis["name"], lis["country"], lis["country_code"], lis["region"]) == ("Lisbon", "Portugal", "PT", "Europe")
    assert lis["best"]["price"] == 450
    assert [o["price"] for o in lis["options"]] == [450, 500]
    assert set(lis["best"]) == OPTION_KEYS
    assert "aviasales" not in lis["best"]["links"]

    unknown = next(d for d in deals["destinations"] if d["city"] == "QQQ")
    assert unknown["region"] == "Other"
    assert unknown["best"]["pct_off"] is None


def test_aviasales_link_included_when_present():
    deals = build_deals(
        [scored(link="https://www.aviasales.com/search/X")], PLACES,
        origin="YUL", scan_date=TODAY, rejected={}, generated_at=NOW,
    )
    assert deals["destinations"][0]["best"]["links"]["aviasales"] == "https://www.aviasales.com/search/X"


def test_validate_accepts_built_deals():
    assert validate(sample_deals()) == []


def test_validate_rejects_problems():
    deals = sample_deals()

    empty = copy.deepcopy(deals)
    empty["destinations"] = []
    assert validate(empty)

    free = copy.deepcopy(deals)
    free["destinations"][0]["options"][0]["price"] = 0
    assert any("price" in error for error in validate(free))

    no_link = copy.deepcopy(deals)
    del no_link["destinations"][0]["best"]["links"]["google"]
    assert any("google" in error for error in validate(no_link))


def test_write_refuses_invalid_and_keeps_old_file(tmp_path):
    out = tmp_path / "deals.json"
    out.write_text('{"old": true}', encoding="utf-8")
    with pytest.raises(ValueError):
        write_deals({"destinations": []}, out)
    assert json.loads(out.read_text(encoding="utf-8")) == {"old": True}


def test_write_creates_parent_and_round_trips(tmp_path):
    out = tmp_path / "site" / "deals.json"
    deals = sample_deals()
    write_deals(deals, out)
    assert json.loads(out.read_text(encoding="utf-8")) == deals


def gcheck(price=480, checked_on=TODAY, depart=date(2026, 12, 1), ret=date(2026, 12, 10), **overrides):
    values = dict(checked_on=checked_on, origin="YUL", dest_city="LIS", airport="LIS", depart=depart, return_date=ret,
                  tp_price=500, status="ok", price=price, level="low", typical_low=600, typical_high=800,
                  history=((date(2026, 9, 17), 620), (date(2026, 10, 8), price)),
                  url="https://www.google.com/travel/flights?tfs=abc")
    values.update(overrides)
    return GoogleCheck(**values)


def deals_with(checks, **kwargs):
    return build_deals([scored(500, 40), scored(300, None, dest="QQQ")], PLACES, origin="YUL", scan_date=TODAY,
                       rejected={}, generated_at=NOW, checks=checks, **kwargs)


def lis_best(deals):
    return next(d for d in deals["destinations"] if d["city"] == "LIS")["best"]


def test_build_deals_attaches_the_google_check():
    deals = deals_with([gcheck()], searches_left=230, google_note=None)
    google = lis_best(deals)["google"]
    assert google == {
        "checked_on": "2026-10-08", "status": "ok", "price": 480, "level": "low", "typical": [600, 800],
        "pct_below": 31, "confirmed": True, "history": [["2026-09-17", 620], ["2026-10-08", 480]],
    }
    assert lis_best(deals)["links"]["google"] == "https://www.google.com/travel/flights?tfs=abc"
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": 230, "note": None}
    assert validate(deals) == []


def test_confirmed_compares_with_todays_price():
    assert lis_best(deals_with([gcheck(price=550)]))["google"]["confirmed"] is True   # 500 * 1.10 = 550
    assert lis_best(deals_with([gcheck(price=551)]))["google"]["confirmed"] is False


def test_newest_check_wins_and_none_status_is_short():
    older = gcheck(price=700, checked_on=TODAY - timedelta(days=2))
    newer = gcheck(status="none", price=None, level=None, typical_low=None, typical_high=None, history=(), url=None)
    best = lis_best(deals_with([older, newer]))
    assert best["google"] == {"checked_on": "2026-10-08", "status": "none"}
    assert best["links"]["google"].startswith("https://www.google.com/travel/flights?q=")


def test_build_deals_ignores_old_and_unmatched_checks():
    stale = gcheck(checked_on=TODAY - timedelta(days=7))
    other_dates = gcheck(depart=date(2026, 12, 2))
    deals = deals_with([stale, other_dates])
    assert "google" not in lis_best(deals)
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": None, "note": None}


def test_no_typical_range_gives_null_typical_and_pct():
    google = lis_best(deals_with([gcheck(typical_low=None, typical_high=None)]))["google"]
    assert (google["typical"], google["pct_below"]) == (None, None)


def test_validate_rejects_bad_google_objects():
    deals = deals_with([gcheck()])
    for bad in ("x", {"status": "maybe"}, {"status": "ok", "price": "480"}):
        broken = copy.deepcopy(deals)
        next(d for d in broken["destinations"] if d["city"] == "LIS")["options"][0]["google"] = bad
        assert any("google" in e for e in validate(broken))


def test_checks_from_another_origin_are_not_attached():
    assert "google" not in lis_best(deals_with([gcheck(origin="YYZ")]))
