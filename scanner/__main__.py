"""Daily scan: python -m scanner   |   first-time history: python -m scanner --seed"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner import store
from scanner.places import Places
from scanner.publish import CONFIRM_TOLERANCE, build_deals, write_deals
from scanner.scoring import WINDOW_DAYS, build_baselines, scan_sizes, scan_too_small, score
from scanner.sources.serpapi import SerpApiChecker
from scanner.sources.travelpayouts import TravelpayoutsSource
from scanner.verify import CheckRun, pick, run_checks

ROOT = Path(__file__).resolve().parent.parent
TOKEN_VAR = "TRAVELPAYOUTS_TOKEN"
KEY_VAR = "SERPAPI_KEY"


def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a .env file; tolerates a BOM, CRLF, comments and quotes."""
    path = Path(path)
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m scanner", description="Scan cached fares and publish deals.")
    parser.add_argument("--seed", action="store_true", help="store a year of past prices as history, then exit")
    parser.add_argument("--origin", default="YUL")
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--reference", default=str(ROOT / "data" / "reference"))
    parser.add_argument("--out", default=str(ROOT / "site" / "deals.json"))
    return parser.parse_args(argv)


def main(argv=None, *, source=None, checker=None, now=None, env_file=None) -> int:
    args = parse_args(argv)
    now = now or datetime.now(timezone.utc)
    today = now.date()
    data_dir = Path(args.data)

    if source is None:
        env = load_env(env_file or ROOT / ".env")
        token = os.environ.get(TOKEN_VAR) or env.get(TOKEN_VAR)
        if not token:
            print(f"error: {TOKEN_VAR} is not set (environment variable or .env file)", file=sys.stderr)
            return 2
        source = TravelpayoutsSource(token)
        key = os.environ.get(KEY_VAR) or env.get(KEY_VAR)
        if checker is None and key:
            checker = SerpApiChecker(key)

    if args.seed:
        result = source.seed(args.origin, today)
        store.append(result.fares, data_dir)
        print(f"seeded {len(result.fares)} fares; rejected {result.rejected}; failed {result.failed}")
        return 0 if result.fares else 1

    result = source.fetch(args.origin, today)
    if not result.fares:
        print(f"error: scan returned no usable fares; failed {result.failed}", file=sys.stderr)
        return 1
    history = store.load(data_dir, since=today - timedelta(days=WINDOW_DAYS))
    previous = scan_sizes(history, today)
    if scan_too_small(len(result.fares), previous):
        print(
            f"error: scan has {len(result.fares)} fares, under half the recent average of {previous}; not publishing",
            file=sys.stderr,
        )
        return 1

    store.append(result.fares, data_dir)
    scored = score(result.fares, build_baselines(history, today), today)
    places = Places.load(Path(args.reference))
    checks = store.load_checks(data_dir, since=today - timedelta(days=WINDOW_DAYS))
    if checker is None:
        run = CheckRun(note=f"{KEY_VAR} not set")
    else:
        run = run_checks(checker, pick(scored, places, checks, today), today)
        store.append_checks(run.checks, data_dir)
    deals = build_deals(
        scored,
        places,
        origin=args.origin,
        scan_date=today,
        rejected=result.rejected,
        generated_at=now,
        checks=checks + run.checks,
        searches_left=run.searches_left,
        google_note=run.note,
    )
    write_deals(deals, Path(args.out))
    big = sum(1 for item in scored if item.pct_off is not None and item.pct_off >= 30)
    print(
        f"scan {today}: {len(result.fares)} fares, {deals['scan']['destinations']} destinations, "
        f"{big} fares >= 30% off; rejected {result.rejected}; failed {result.failed}"
    )
    confirmed = sum(1 for c in run.checks if c.status == "ok" and c.price <= c.tp_price * (1 + CONFIRM_TOLERANCE))
    left = "unknown" if run.searches_left is None else run.searches_left
    print(f"google: {len(run.checks)} checked, {confirmed} confirmed, searches left {left}" + (f"; {run.note}" if run.note else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
