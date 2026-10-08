import gzip
from datetime import date

from factories import TODAY, make_fare
from scanner import store


def test_round_trip_preserves_every_field(tmp_path):
    a = make_fare(carry_on=True, checked_bag=False, link="https://www.aviasales.com/search/X")
    b = make_fare(dest_city="PAR", dest_airport="ORY", checked_bag=True, bag_pieces=1, bag_kg=23, airline="")
    store.append([a, b], tmp_path)
    assert store.load(tmp_path, since=date(2026, 9, 1)) == [a, b]


def test_append_twice_writes_header_once(tmp_path):
    a, b = make_fare(price=400), make_fare(price=410)
    store.append([a], tmp_path)
    store.append([b], tmp_path)
    assert store.load(tmp_path, since=date(2026, 9, 1)) == [a, b]
    with gzip.open(tmp_path / "observations" / "2026-10.csv.gz", "rt", encoding="utf-8") as fh:
        assert fh.read().count("origin,dest_airport") == 1


def test_files_split_by_scan_month_and_load_filters_by_obs_day(tmp_path):
    old = make_fare(scan_date=date(2026, 9, 30), searched_on=date(2026, 9, 30))
    new = make_fare(scan_date=date(2026, 10, 1), searched_on=date(2026, 10, 1))
    store.append([old, new], tmp_path)
    names = sorted(p.name for p in (tmp_path / "observations").iterdir())
    assert names == ["2026-09.csv.gz", "2026-10.csv.gz"]
    assert store.load(tmp_path, since=date(2026, 10, 1)) == [new]
    assert store.load(tmp_path, since=date(2026, 9, 15)) == [old, new]


def test_seed_rows_are_filtered_by_search_day(tmp_path):
    seed = make_fare(kind="seed", scan_date=TODAY, searched_on=date(2026, 5, 1))
    store.append([seed], tmp_path)
    assert store.load(tmp_path, since=date(2026, 7, 10)) == []
    assert store.load(tmp_path, since=date(2026, 4, 1)) == [seed]


def test_load_from_missing_directory(tmp_path):
    assert store.load(tmp_path / "nothing", since=TODAY) == []
