import json
from datetime import date, datetime, timezone

from factories import make_fare
from scanner import store
from scanner.__main__ import load_env, main
from scanner.check import GoogleCheck
from scanner.net import ApiError
from scanner.sources.travelpayouts import FetchResult

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, fares, seed_fares=()):
        self.fares = list(fares)
        self.seed_fares = list(seed_fares)

    def fetch(self, origin, today):
        return FetchResult(fares=list(self.fares), rejected={"stops": 1})

    def seed(self, origin, today):
        return FetchResult(fares=list(self.seed_fares))


def write_reference(ref):
    ref.mkdir(parents=True)
    (ref / "cities.json").write_text(json.dumps({"LIS": {"name": "Lisbon", "country": "PT"}}), encoding="utf-8")
    (ref / "countries.json").write_text(json.dumps({"PT": {"name": "Portugal"}}), encoding="utf-8")
    (ref / "regions.json").write_text(json.dumps({"Europe": ["PT"]}), encoding="utf-8")


def cli_args(tmp_path):
    return [
        "--data", str(tmp_path / "data"),
        "--reference", str(tmp_path / "ref"),
        "--out", str(tmp_path / "site" / "deals.json"),
    ]


def test_load_env_handles_windows_files(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes('﻿# comment\r\nTRAVELPAYOUTS_TOKEN="abc123"\r\n\r\nOTHER = x \r\n'.encode("utf-8"))
    assert load_env(env) == {"TRAVELPAYOUTS_TOKEN": "abc123", "OTHER": "x"}


def test_load_env_missing_file(tmp_path):
    assert load_env(tmp_path / ".env") == {}


def test_missing_token_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRAVELPAYOUTS_TOKEN", raising=False)
    assert main(cli_args(tmp_path), now=NOW, env_file=tmp_path / ".env") == 2
    assert "TRAVELPAYOUTS_TOKEN" in capsys.readouterr().err


def test_scan_writes_deals_and_observations(tmp_path):
    write_reference(tmp_path / "ref")
    fare = make_fare(price=450)
    assert main(cli_args(tmp_path), source=FakeSource([fare]), now=NOW) == 0
    deals = json.loads((tmp_path / "site" / "deals.json").read_text(encoding="utf-8"))
    assert deals["destinations"][0]["name"] == "Lisbon"
    assert deals["scan"]["rejected"] == {"stops": 1}
    assert store.load(tmp_path / "data", since=date(2026, 10, 1)) == [fare]


def test_small_scan_is_not_published_or_stored(tmp_path):
    write_reference(tmp_path / "ref")
    earlier = [
        make_fare(dest_city=f"C{i:02d}", scan_date=day, searched_on=day)
        for day in (date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7))
        for i in range(10)
    ]
    store.append(earlier, tmp_path / "data")
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), now=NOW) == 1
    assert not (tmp_path / "site" / "deals.json").exists()
    assert len(store.load(tmp_path / "data", since=date(2026, 10, 1))) == 30


def test_empty_scan_fails(tmp_path):
    write_reference(tmp_path / "ref")
    assert main(cli_args(tmp_path), source=FakeSource([]), now=NOW) == 1
    assert not (tmp_path / "site" / "deals.json").exists()


def test_seed_only_stores_history(tmp_path):
    seed = make_fare(kind="seed", searched_on=date(2026, 9, 1))
    assert main(cli_args(tmp_path) + ["--seed"], source=FakeSource([], [seed]), now=NOW) == 0
    assert store.load(tmp_path / "data", since=date(2026, 8, 1)) == [seed]
    assert not (tmp_path / "site" / "deals.json").exists()


class FakeChecker:
    def __init__(self, left=200, error=False):
        self.left, self.error, self.calls = left, error, []

    def searches_left(self):
        return self.left

    def check(self, fare, today):
        self.calls.append(fare.dest_city)
        if self.error:
            raise ApiError("HTTP 500 for https://serpapi.com/search.json")
        return GoogleCheck(checked_on=today, origin=fare.origin, dest_city=fare.dest_city, airport=fare.dest_airport,
                           depart=fare.depart_date, return_date=fare.return_date, tp_price=fare.price, status="ok",
                           price=fare.price - 10, level="low", typical_low=500, typical_high=700)


def read_deals(tmp_path):
    return json.loads((tmp_path / "site" / "deals.json").read_text(encoding="utf-8"))


def test_scan_checks_new_international_route_on_google(tmp_path, capsys):
    write_reference(tmp_path / "ref")
    checker = FakeChecker()
    assert main(cli_args(tmp_path), source=FakeSource([make_fare(price=450)]), checker=checker, now=NOW) == 0
    assert checker.calls == ["LIS"]
    deals = read_deals(tmp_path)
    assert deals["destinations"][0]["best"]["google"]["price"] == 440
    assert deals["scan"]["google"] == {"checked_today": 1, "searches_left": 199, "note": None}
    assert len(store.load_checks(tmp_path / "data", since=date(2026, 10, 1))) == 1
    assert "google: 1 checked" in capsys.readouterr().out


def test_second_run_same_day_spends_nothing_more(tmp_path):
    write_reference(tmp_path / "ref")
    main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=FakeChecker(), now=NOW)
    again = FakeChecker()
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=again, now=NOW) == 0
    assert again.calls == []
    assert read_deals(tmp_path)["destinations"][0]["best"]["google"]["status"] == "ok"


def test_quota_floor_skips_checks(tmp_path):
    write_reference(tmp_path / "ref")
    checker = FakeChecker(left=3)
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=checker, now=NOW) == 0
    assert checker.calls == []
    assert "3 Google searches left" in read_deals(tmp_path)["scan"]["google"]["note"]


def test_check_errors_still_publish(tmp_path):
    write_reference(tmp_path / "ref")
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), checker=FakeChecker(error=True), now=NOW) == 0
    deals = read_deals(tmp_path)
    assert "google" not in deals["destinations"][0]["best"]
    assert "HTTP 500" in deals["scan"]["google"]["note"]


def test_injected_source_without_checker_makes_no_google_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "should-not-be-used")
    write_reference(tmp_path / "ref")
    assert main(cli_args(tmp_path), source=FakeSource([make_fare()]), now=NOW) == 0
    assert read_deals(tmp_path)["scan"]["google"]["note"] == "SERPAPI_KEY not set"


def test_seed_runs_no_checks(tmp_path):
    checker = FakeChecker()
    seed = make_fare(kind="seed", searched_on=date(2026, 9, 1))
    main(cli_args(tmp_path) + ["--seed"], source=FakeSource([], [seed]), checker=checker, now=NOW)
    assert checker.calls == []
