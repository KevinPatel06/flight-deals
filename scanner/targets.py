"""Where scans and Google searches go: the must-check destinations, then countries where the
US/Canadian dollar goes further. Never Canada, never the United States except Hawaii."""
from __future__ import annotations

from dataclasses import dataclass

from scanner.places import Places

EXCLUDED_COUNTRIES = {"CA", "US"}
HAWAII = ("HNL", "OGG", "KOA", "LIH", "ITO", "MKK", "LNY", "JHM", "MUE")


@dataclass(frozen=True)
class Target:
    name: str
    cities: tuple[str, ...]  # city codes scanned one by one; the first is where a direct search goes
    airport: str  # Google arrival airport for a direct search
    nights: int  # trip length for a direct search
    country: str = ""  # also claims every other city in this country not claimed by an earlier target


# In the order the user listed them; it breaks ties in the daily rotation.
TARGETS = (
    Target("Hawaii", HAWAII, "HNL", 7),
    Target("Mumbai", ("BOM",), "BOM", 14),
    Target("Tokyo", ("TYO",), "HND", 14),
    Target("Costa Rica", ("LIR", "SJO"), "LIR", 7, country="CR"),
    Target("Reykjavik", ("REK",), "KEF", 7),
    Target("Cancun", ("CUN",), "CUN", 7),
    Target("Punta Cana", ("PUJ",), "PUJ", 7),
    Target("South Africa", ("CPT", "JNB"), "CPT", 14, country="ZA"),
    Target("Vietnam", ("SGN", "HAN", "DAD"), "SGN", 14, country="VN"),
    Target("Thailand", ("BKK", "HKT", "CNX", "USM", "KBV"), "BKK", 14, country="TH"),
    Target("Bali", ("DPS",), "DPS", 14),
    Target("Mexico", ("PVR", "SJD", "MEX", "GDL", "OAX", "MID", "HUX", "ZIH"), "PVR", 7, country="MX"),
)

# Countries where the US dollar (and the Canadian dollar, which tracks it) goes further, from 2026
# roundups (Frommer's, Peter Greenberg, Quartz, Afar, Daily Passport) plus long-running bargains.
# Rates move; revisit this list a couple of times a year.
STRONG_DOLLAR = {
    "AR", "EG", "ID", "IN", "JP", "MX", "PE", "TR", "VN", "ZA",  # named in the 2026 roundups
    "AL", "PT", "RO",
    "BG", "BR", "CO", "CZ", "DO", "GE", "GT", "HU", "KH", "LA", "LK", "MA", "MY", "NP", "PH", "PL", "TH",
}

FOCUS_CITIES = tuple(city for target in TARGETS for city in target.cities)


def excluded(city: str, places: Places) -> bool:
    """Canada, or the United States outside Hawaii."""
    return city not in HAWAII and places.lookup(city).country_code in EXCLUDED_COUNTRIES


def target_of(city: str, places: Places, targets: tuple[Target, ...] = TARGETS) -> Target | None:
    country = places.lookup(city).country_code
    for target in targets:
        if city in target.cities or (target.country and country == target.country):
            return target
    return None


def strong_dollar(city: str, places: Places) -> bool:
    return places.lookup(city).country_code in STRONG_DOLLAR
