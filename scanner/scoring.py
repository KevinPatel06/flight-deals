"""Normal prices, percent off, badges and the small-scan guard. Pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median
from typing import Callable, Hashable

from scanner.fare import Fare

WINDOW_DAYS = 90
MIN_DAYS = 7
FRESH_DAYS = 2
ERROR_PCT = 70
UNCONFIRMED_PCT = 40


@dataclass(frozen=True)
class Baselines:
    month: dict[tuple[str, str], int]
    dest: dict[str, int]


@dataclass(frozen=True)
class ScoredFare:
    fare: Fare
    normal: int | None
    pct_off: int | None
    baseline: str | None
    age_days: int
    badges: tuple[str, ...]


def _month(fare: Fare) -> str:
    return fare.depart_date.strftime("%Y-%m")


def _medians(fares: list[Fare], key: Callable[[Fare], Hashable]) -> dict:
    """Median of each key's daily cheapest price, for keys seen on at least MIN_DAYS days."""
    daily: dict[Hashable, dict[date, int]] = {}
    for fare in fares:
        days = daily.setdefault(key(fare), {})
        days[fare.obs_day] = min(fare.price, days.get(fare.obs_day, fare.price))
    return {k: round(median(days.values())) for k, days in daily.items() if len(days) >= MIN_DAYS}


def build_baselines(history: list[Fare], today: date) -> Baselines:
    start = today - timedelta(days=WINDOW_DAYS)
    window = [fare for fare in history if start <= fare.obs_day < today]
    return Baselines(
        month=_medians(window, lambda f: (f.dest_city, _month(f))),
        dest=_medians(window, lambda f: f.dest_city),
    )


def baseline_for(baselines: Baselines, fare: Fare) -> tuple[int | None, str | None]:
    normal = baselines.month.get((fare.dest_city, _month(fare)))
    if normal:
        return normal, "month"
    normal = baselines.dest.get(fare.dest_city)
    if normal:
        return normal, "destination"
    return None, None


def percent_off(price: int, normal: int) -> int:
    return max(0, round(100 * (1 - price / normal)))


def badges_for(fare: Fare, pct: int | None, baseline: str | None, age_days: int) -> tuple[str, ...]:
    badges = []
    if fare.stops == 0:
        badges.append("nonstop")
    if fare.checked_bag is True:
        badges.append("bag")
    badges.append("fresh" if age_days < FRESH_DAYS else "aging")
    if baseline is None:
        badges.append("new_route")
    elif baseline == "destination":
        badges.append("rough")
    if pct is not None and pct >= ERROR_PCT:
        badges.append("error_fare")
    if pct is not None and pct >= UNCONFIRMED_PCT and age_days >= FRESH_DAYS:
        badges.append("unconfirmed")
    return tuple(badges)


def score(fares: list[Fare], baselines: Baselines, today: date) -> list[ScoredFare]:
    scored = []
    for fare in fares:
        normal, kind = baseline_for(baselines, fare)
        pct = percent_off(fare.price, normal) if normal else None
        age = (today - fare.searched_on).days
        scored.append(ScoredFare(fare, normal, pct, kind, age, badges_for(fare, pct, kind, age)))
    return scored


def scan_sizes(history: list[Fare], today: date, n: int = 7) -> list[int]:
    """Distinct fares per earlier daily scan (last n scans), oldest first. Re-runs don't double count."""
    per_day: dict[date, set] = {}
    for fare in history:
        if fare.kind == "seed" or fare.scan_date >= today:
            continue
        key = (fare.dest_city, fare.depart_date, fare.return_date, fare.checked_bag)
        per_day.setdefault(fare.scan_date, set()).add(key)
    return [len(per_day[day]) for day in sorted(per_day)[-n:]]


def scan_too_small(current: int, previous: list[int]) -> bool:
    """True when this scan is under half the recent average (needs 3+ earlier scans)."""
    if len(previous) < 3:
        return False
    return current < 0.5 * mean(previous)
