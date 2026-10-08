import copy
import json
from datetime import date, datetime, timezone

import pytest

from factories import TODAY, make_fare
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
    assert deals["scan"] == {"date": "2026-10-08", "fares": 3, "destinations": 2, "rejected": {"stops": 3}}

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
