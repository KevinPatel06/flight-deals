"""The fare record every source produces and every other module consumes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Fare:
    origin: str
    dest_airport: str
    dest_city: str
    depart_date: date
    return_date: date
    price: int
    airline: str
    stops: int
    searched_on: date
    link: str
    kind: str  # "dates" | "latest" | "seed"
    scan_date: date
    carry_on: bool | None = None
    checked_bag: bool | None = None
    bag_pieces: int | None = None
    bag_kg: int | None = None

    @property
    def trip_days(self) -> int:
        return (self.return_date - self.depart_date).days

    @property
    def obs_day(self) -> date:
        """The day this observation represents: search day for seed rows, scan day otherwise."""
        return self.searched_on if self.kind == "seed" else self.scan_date
