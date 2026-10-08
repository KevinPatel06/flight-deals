"""Every fare observation, appended to gzipped CSV files named by scan month."""
from __future__ import annotations

import csv
import gzip
from dataclasses import astuple, fields
from datetime import date
from pathlib import Path

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


def append(fares: list[Fare], data_dir: Path) -> None:
    folder = Path(data_dir) / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[Fare]] = {}
    for fare in fares:
        by_month.setdefault(fare.scan_date.strftime("%Y-%m"), []).append(fare)
    for month, rows in sorted(by_month.items()):
        path = folder / f"{month}.csv.gz"
        is_new = not path.exists()
        with gzip.open(path, "at", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(COLUMNS)
            for fare in rows:
                writer.writerow(_encode(name, value) for name, value in zip(COLUMNS, astuple(fare)))


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
