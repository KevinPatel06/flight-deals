"""Choose which fares get a live Google Flights check today, and run those checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from scanner.check import GoogleCheck
from scanner.fare import Fare
from scanner.net import ApiError
from scanner.places import Places
from scanner.scoring import ScoredFare
from scanner.targets import TARGETS, Target, excluded, strong_dollar, target_of

DAILY_LIMIT = 7  # 7 x 31 = 217 of the free plan's 250 searches a month
PRIORITY_SLOTS = 6  # of the daily limit, for the must-check targets: 12 of them come round every other day
REPEAT_DAYS = 3
MIN_LEFT = 10
PICK_PCT = 30
MAX_ERRORS = 2
NONE_REST_DAYS = 14  # a city Google could not price waits this long, whatever its dates
DIRECT_LEAD_DAYS = 56  # a direct search leaves on the first Tuesday at least this far out


def _trip(item: ScoredFare) -> tuple[str, str, date, date]:
    return item.fare.origin, item.fare.dest_airport, item.fare.depart_date, item.fare.return_date


def _checkable(city: str, places: Places) -> bool:
    """International only: no Canada, no United States except Hawaii, no unknown cities."""
    return not excluded(city, places) and places.lookup(city).country_code != ""


def _rank(item: ScoredFare) -> tuple:
    pct = item.pct_off if item.pct_off is not None else -1
    return -pct, item.fare.price


def direct_search(target: Target, origin: str, today: date) -> ScoredFare:
    """A trip of our own choosing, for a target with no cached fare. Price 0 marks it as direct."""
    depart = today + timedelta(days=DIRECT_LEAD_DAYS)
    depart += timedelta(days=(1 - depart.weekday()) % 7)
    fare = Fare(
        origin=origin, dest_airport=target.airport, dest_city=target.cities[0], depart_date=depart,
        return_date=depart + timedelta(days=target.nights), price=0, airline="", stops=0,
        searched_on=today, link="", kind="direct", scan_date=today,
    )
    return ScoredFare(fare=fare, normal=None, pct_off=None, baseline=None, age_days=0, badges=())


def pick(
    scored: list[ScoredFare], places: Places, checks: list[GoogleCheck], today: date,
    limit: int = DAILY_LIMIT, targets: tuple[Target, ...] = TARGETS,
) -> list[ScoredFare]:
    """Today's Google searches, within what is left of today's limit.

    First up to PRIORITY_SLOTS must-check targets: big discounts, then the least recently searched.
    Each sends its best fare not searched in the last REPEAT_DAYS, or a direct search when it has
    no cached fare at all. Then other international cities, strong-dollar countries first, then any
    targets that did not fit.
    """
    origins = {item.fare.origin for item in scored}
    checks = [c for c in checks if c.origin in origins]
    remaining = limit - sum(1 for c in checks if c.checked_on == today)
    if remaining <= 0:
        return []
    recent = {c.trip for c in checks if c.checked_on > today - timedelta(days=REPEAT_DAYS)}
    last_checked: dict[str, date] = {}
    newest: dict[str, GoogleCheck] = {}
    for c in checks:
        last_checked[c.dest_city] = max(last_checked.get(c.dest_city, date.min), c.checked_on)
        if c.dest_city not in newest or c.checked_on >= newest[c.dest_city].checked_on:
            newest[c.dest_city] = c
    resting = {
        city for city, c in newest.items()
        if c.status == "none" and c.checked_on > today - timedelta(days=NONE_REST_DAYS)
    }
    target_fares: dict[Target, list[ScoredFare]] = {target: [] for target in targets}
    target_last: dict[Target, date] = {target: date.min for target in targets}
    for city, day in last_checked.items():
        if (target := target_of(city, places, targets)) is not None:
            target_last[target] = max(target_last[target], day)
    best: dict[str, ScoredFare] = {}
    for item in scored:
        city = item.fare.dest_city
        if not _checkable(city, places) or city in resting:
            continue
        if (target := target_of(city, places, targets)) is not None:
            target_fares[target].append(item)
        elif city not in best or _rank(item) < _rank(best[city]):
            best[city] = item

    priority = []
    for target, items in target_fares.items():
        fresh = [item for item in items if _trip(item) not in recent]
        if fresh:
            priority.append(min(fresh, key=_rank))
        elif not items and len(origins) == 1 and _checkable(target.cities[0], places) and target.cities[0] not in resting:
            priority.append(direct_search(target, next(iter(origins)), today))
    order = {target: index for index, target in enumerate(targets)}

    def due(item: ScoredFare) -> tuple:
        target = target_of(item.fare.dest_city, places, targets)
        big = item.pct_off is not None and item.pct_off >= PICK_PCT
        return (0, *_rank(item)) if big else (1, target_last[target], order[target])

    priority.sort(key=due)

    big, new = [], []
    for city, item in best.items():
        if _trip(item) in recent:
            continue
        if item.pct_off is None:
            new.append(item)
        elif item.pct_off >= PICK_PCT:
            big.append(item)
    big.sort(key=_rank)
    new.sort(key=lambda item: (last_checked.get(item.fare.dest_city, date.min), item.fare.price))
    others = sorted(big + new, key=lambda item: not strong_dollar(item.fare.dest_city, places))
    return (priority[:PRIORITY_SLOTS] + others + priority[PRIORITY_SLOTS:])[:remaining]


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
    if left is not None:
        if left <= MIN_LEFT:
            return CheckRun([], left, f"only {left} Google searches left this month")
        picks = picks[: left - MIN_LEFT]  # never spend below the floor
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
