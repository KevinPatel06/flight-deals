"""Choose which fares get a live Google Flights check today, and run those checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from scanner.check import GoogleCheck
from scanner.net import ApiError
from scanner.places import Places
from scanner.scoring import ScoredFare

DAILY_LIMIT = 7  # 7 x 31 = 217 of the free plan's 250 searches a month
REPEAT_DAYS = 3
MIN_LEFT = 10
PICK_PCT = 30
MAX_ERRORS = 2
DOMESTIC = "CA"


def _trip(item: ScoredFare) -> tuple[str, date, date]:
    return item.fare.dest_airport, item.fare.depart_date, item.fare.return_date


def _rank(item: ScoredFare) -> tuple:
    pct = item.pct_off if item.pct_off is not None else -1
    return -pct, item.fare.price


def pick(scored: list[ScoredFare], places: Places, checks: list[GoogleCheck], today: date, limit: int = DAILY_LIMIT) -> list[ScoredFare]:
    """Today's international fares worth a Google check, best first, within what is left of today's limit."""
    remaining = limit - sum(1 for c in checks if c.checked_on == today)
    if remaining <= 0:
        return []
    recent = {c.trip for c in checks if c.checked_on > today - timedelta(days=REPEAT_DAYS)}
    last_checked: dict[str, date] = {}
    for c in checks:
        last_checked[c.dest_city] = max(last_checked.get(c.dest_city, date.min), c.checked_on)
    best: dict[str, ScoredFare] = {}
    for item in scored:
        city = item.fare.dest_city
        if city not in best or _rank(item) < _rank(best[city]):
            best[city] = item
    big, new = [], []
    for city, item in best.items():
        if places.lookup(city).country_code == DOMESTIC or _trip(item) in recent:
            continue
        if item.pct_off is None:
            new.append(item)
        elif item.pct_off >= PICK_PCT:
            big.append(item)
    big.sort(key=_rank)
    new.sort(key=lambda item: (last_checked.get(item.fare.dest_city, date.min), item.fare.price))
    return (big + new)[:remaining]


@dataclass
class CheckRun:
    checks: list[GoogleCheck] = field(default_factory=list)
    searches_left: int | None = None
    note: str | None = None


def _describe(err: Exception) -> str:
    """ApiError text is key-free by construction; anything else is named by type only, in case it carries a URL."""
    return str(err) if isinstance(err, ApiError) else type(err).__name__


def run_checks(checker, picks: list[ScoredFare], today: date) -> CheckRun:
    """Check each pick on Google. Errors become a note; they never raise."""
    if not picks:
        return CheckRun()
    try:
        left = checker.searches_left()
    except Exception as err:  # noqa: BLE001  a check must never stop the publish
        return CheckRun(note=f"quota check failed: {_describe(err)}")
    if left is not None and left < MIN_LEFT:
        return CheckRun([], left, f"only {left} Google searches left this month")
    done: list[GoogleCheck] = []
    errors, note = 0, None
    for item in picks:
        try:
            done.append(checker.check(item.fare, today))
            errors = 0
        except Exception as err:  # noqa: BLE001  a check must never stop the publish
            errors, note = errors + 1, f"check failed: {_describe(err)}"
            if errors >= MAX_ERRORS:
                break
    return CheckRun(done, None if left is None else left - len(done), note)
