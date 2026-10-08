import json
import re
from pathlib import Path

from scanner.places import Place, Places
from scanner.reference import trim

ROOT = Path(__file__).resolve().parents[2]
CITIES = {"LIS": {"name": "Lisbon", "country": "PT"}, "XXX": {"name": "Nowhere", "country": "ZZ"}}
COUNTRIES = {"PT": {"name": "Portugal"}}
REGIONS = {"Europe": ["PT"]}


def test_lookup_known_city():
    assert Places(CITIES, COUNTRIES, REGIONS).lookup("LIS") == Place("LIS", "Lisbon", "Portugal", "PT", "Europe")


def test_unknown_city_falls_back_to_its_code():
    assert Places(CITIES, COUNTRIES, REGIONS).lookup("QQQ") == Place("QQQ", "QQQ", "", "", "Other")


def test_city_in_unmapped_country_is_other():
    assert Places(CITIES, COUNTRIES, REGIONS).lookup("XXX") == Place("XXX", "Nowhere", "ZZ", "ZZ", "Other")


def test_load_reads_reference_directory(tmp_path):
    (tmp_path / "cities.json").write_text(json.dumps(CITIES), encoding="utf-8")
    (tmp_path / "countries.json").write_text(json.dumps(COUNTRIES), encoding="utf-8")
    (tmp_path / "regions.json").write_text(json.dumps(REGIONS), encoding="utf-8")
    assert Places.load(tmp_path).lookup("LIS").region == "Europe"


def test_shipped_regions_file_is_consistent():
    regions = json.loads((ROOT / "data" / "reference" / "regions.json").read_text(encoding="utf-8"))
    codes = [code for group in regions.values() for code in group]
    assert len(codes) == len(set(codes)), "a country is listed in two regions"
    assert all(re.fullmatch(r"[A-Z]{2}", code) for code in codes)
    assert {"CA", "US", "FR", "MX", "DO", "JP"} <= set(codes)


def test_trim_keeps_code_name_and_country():
    records = [
        {"code": "YMQ", "name": "Montreal", "country_code": "CA", "coordinates": {"lat": 45.5}},
        {"code": "ZZZ", "name": None, "name_translations": {"en": "Zed"}},
        {"code": None, "name": "No code"},
    ]
    assert trim(records) == {"YMQ": {"name": "Montreal", "country": "CA"}, "ZZZ": {"name": "Zed"}}
