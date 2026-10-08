import json
from datetime import date, datetime, timezone

from factories import make_fare
from scanner import store
from scanner.__main__ import load_env, main
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
