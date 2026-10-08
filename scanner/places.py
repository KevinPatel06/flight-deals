"""City code -> display name, country and region."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Place:
    city: str
    name: str
    country: str
    country_code: str
    region: str


class Places:
    def __init__(self, cities: dict, countries: dict, regions: dict[str, list[str]]):
        self._cities = cities
        self._countries = countries
        self._region_of = {code: region for region, codes in regions.items() for code in codes}

    @classmethod
    def load(cls, ref_dir: Path) -> "Places":
        def read(name: str):
            return json.loads((Path(ref_dir) / name).read_text(encoding="utf-8"))

        return cls(read("cities.json"), read("countries.json"), read("regions.json"))

    def lookup(self, city_code: str) -> Place:
        city = self._cities.get(city_code, {})
        country_code = city.get("country", "")
        return Place(
            city=city_code,
            name=city.get("name", city_code),
            country=self._countries.get(country_code, {}).get("name", country_code),
            country_code=country_code,
            region=self._region_of.get(country_code, "Other"),
        )
