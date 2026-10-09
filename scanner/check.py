"""One live Google Flights check of a fare's exact trip."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class GoogleCheck:
    checked_on: date
    origin: str
    dest_city: str
    airport: str
    depart: date
    return_date: date
    tp_price: int  # the Travelpayouts price when the check ran
    status: str  # "ok" or "none" (Google found no matching flights)
    price: int | None = None
    level: str | None = None  # Google's verdict: "low", "typical" or "high"
    typical_low: int | None = None
    typical_high: int | None = None
    history: tuple[tuple[date, int], ...] = ()
    url: str | None = None

    @property
    def trip(self) -> tuple[str, str, date, date]:
        return self.origin, self.airport, self.depart, self.return_date

    @property
    def pct_below(self) -> int | None:
        """Percent below the middle of Google's typical range; None without a range."""
        if self.price is None or self.typical_low is None or self.typical_high is None:
            return None
        middle = (self.typical_low + self.typical_high) / 2
        return max(0, round(100 * (1 - self.price / middle)))
