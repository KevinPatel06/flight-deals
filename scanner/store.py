"""Fare observations and Google checks, appended to gzipped CSV files named by month."""
from __future__ import annotations

import csv
import gzip
from dataclasses import astuple, fields
from datetime import date
from pathlib import Path

from scanner.check import GoogleCheck
from scanner.fare import Fare

COLUMNS = [f.name for f in fields(Fare)]
_DATES = {"depart_date", "return_date", "searched_on", "scan_date"}
_INTS = {"price", "stops"}
_OPTIONAL_INTS = {"bag_pieces", "bag_kg"}
_OPTIONAL_BOOLS = {"carry_on", "checked_bag"}


def _encode(name: str, value) -> str:
    if value is None:
        return ""
    if name in _OPTIONAL_BOOLS:
        return "1" if value else "0"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _decode(name: str, text: str):
    if name in _DATES:
        return date.fromisoformat(text)
    if name in _INTS:
        return int(text)
    if name in _OPTIONAL_INTS:
        return int(text) if text else None
    if name in _OPTIONAL_BOOLS:
        return None if text == "" else text == "1"
    return text


def _append_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    is_new = not path.exists()
    with gzip.open(path, "at", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(header)
        writer.writerows(rows)


def append(fares: list[Fare], data_dir: Path) -> None:
    folder = Path(data_dir) / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[list[str]]] = {}
    for fare in fares:
        row = [_encode(name, value) for name, value in zip(COLUMNS, astuple(fare))]
        by_month.setdefault(fare.scan_date.strftime("%Y-%m"), []).append(row)
    for month, rows in sorted(by_month.items()):
        _append_csv(folder / f"{month}.csv.gz", COLUMNS, rows)


def load(data_dir: Path, since: date) -> list[Fare]:
    folder = Path(data_dir) / "observations"
    if not folder.exists():
        return []
    first_month = since.strftime("%Y-%m")
    fares = []
    for path in sorted(folder.glob("*.csv.gz")):
        if path.name[:7] < first_month:
            continue
        with gzip.open(path, "rt", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                fare = Fare(**{name: _decode(name, row[name]) for name in COLUMNS})
                if fare.obs_day >= since:
                    fares.append(fare)
    return fares


CHECK_COLUMNS = [
    "checked_on", "origin", "dest_city", "airport", "depart", "return", "tp_price", "status",
    "price", "level", "typical_low", "typical_high", "history", "url",
]


def _opt(value) -> str:
    return "" if value is None else str(value)


def _check_row(c: GoogleCheck) -> list[str]:
    history = ";".join(f"{day.isoformat()}:{price}" for day, price in c.history)
    return [
        c.checked_on.isoformat(), c.origin, c.dest_city, c.airport, c.depart.isoformat(), c.return_date.isoformat(),
        str(c.tp_price), c.status, _opt(c.price), _opt(c.level), _opt(c.typical_low), _opt(c.typical_high),
        history, _opt(c.url),
    ]


def _check_from(row: dict) -> GoogleCheck:
    num = lambda text: int(text) if text else None  # noqa: E731
    pairs = (pair.split(":") for pair in row["history"].split(";") if pair)
    return GoogleCheck(
        checked_on=date.fromisoformat(row["checked_on"]), origin=row["origin"], dest_city=row["dest_city"],
        airport=row["airport"], depart=date.fromisoformat(row["depart"]), return_date=date.fromisoformat(row["return"]),
        tp_price=int(row["tp_price"]), status=row["status"], price=num(row["price"]), level=row["level"] or None,
        typical_low=num(row["typical_low"]), typical_high=num(row["typical_high"]),
        history=tuple((date.fromisoformat(day), int(price)) for day, price in pairs), url=row["url"] or None,
    )


def append_checks(checks: list[GoogleCheck], data_dir: Path) -> None:
    if not checks:
        return
    folder = Path(data_dir) / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[list[str]]] = {}
    for check in checks:
        by_month.setdefault(check.checked_on.strftime("%Y-%m"), []).append(_check_row(check))
    for month, rows in sorted(by_month.items()):
        _append_csv(folder / f"{month}.csv.gz", CHECK_COLUMNS, rows)


def load_checks(data_dir: Path, since: date) -> list[GoogleCheck]:
    folder = Path(data_dir) / "checks"
    if not folder.exists():
        return []
    first_month = since.strftime("%Y-%m")
    checks = []
    for path in sorted(folder.glob("*.csv.gz")):
        if path.name[:7] < first_month:
            continue
        with gzip.open(path, "rt", newline="", encoding="utf-8") as fh:
            checks += [c for c in map(_check_from, csv.DictReader(fh)) if c.checked_on >= since]
    return checks
