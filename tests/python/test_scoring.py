from datetime import date, timedelta

from factories import TODAY, make_fare
from scanner.scoring import (
    badges_for,
    baseline_for,
    build_baselines,
    percent_off,
    scan_sizes,
    scan_too_small,
    score,
)


def history_for(dest, prices, start=date(2026, 9, 20), depart=date(2026, 12, 1), kind="dates"):
    """One observation per consecutive scan day starting at `start`."""
    return [
        make_fare(
            dest_city=dest,
            dest_airport=dest,
            price=price,
            scan_date=start + timedelta(days=i),
            searched_on=start + timedelta(days=i),
            depart_date=depart,
            return_date=depart + timedelta(days=9),
            kind=kind,
        )
        for i, price in enumerate(prices)
    ]


def test_baseline_is_median_of_daily_minimums():
    history = history_for("LIS", [500, 520, 540, 560, 580, 600, 620])
    # A pricier fare on the first day must not count as an extra observation.
    history.append(make_fare(dest_city="LIS", price=900, scan_date=date(2026, 9, 20), searched_on=date(2026, 9, 20)))
    baselines = build_baselines(history, TODAY)
    assert baselines.month[("LIS", "2026-12")] == 560
    assert baselines.dest["LIS"] == 560


def test_baseline_needs_seven_days():
    baselines = build_baselines(history_for("LIS", [500] * 6), TODAY)
    assert ("LIS", "2026-12") not in baselines.month
    assert "LIS" not in baselines.dest


def test_window_excludes_today_and_days_older_than_90():
    history = history_for("LIS", [500] * 6)
    history.append(make_fare(dest_city="LIS", price=100, scan_date=TODAY, searched_on=TODAY))
    history.append(make_fare(dest_city="LIS", price=100, scan_date=date(2026, 7, 1), searched_on=date(2026, 7, 1)))
    assert "LIS" not in build_baselines(history, TODAY).dest


def test_seed_rows_count_by_search_day():
    seed = [
        make_fare(kind="seed", scan_date=TODAY, searched_on=date(2026, 9, 1) + timedelta(days=i), price=400)
        for i in range(7)
    ]
    assert build_baselines(seed, TODAY).dest["LIS"] == 400


def test_month_baseline_first_then_destination_then_none():
    december = history_for("LIS", [500] * 7, depart=date(2026, 12, 1))
    thin_january = history_for("LIS", [900] * 3, depart=date(2027, 1, 10))
    baselines = build_baselines(december + thin_january, TODAY)
    dec_fare = make_fare(depart_date=date(2026, 12, 5), return_date=date(2026, 12, 15))
    jan_fare = make_fare(depart_date=date(2027, 1, 12), return_date=date(2027, 1, 20))
    assert baseline_for(baselines, dec_fare) == (500, "month")
    assert baseline_for(baselines, jan_fare) == (500, "destination")
    assert baseline_for(baselines, make_fare(dest_city="ROM")) == (None, None)


def test_percent_off():
    assert percent_off(450, 1000) == 55
    assert percent_off(1000, 1000) == 0
    assert percent_off(1100, 1000) == 0


def test_badges():
    assert badges_for(make_fare(stops=0, checked_bag=True), 20, "month", 0) == ("nonstop", "bag", "fresh")
    assert badges_for(make_fare(stops=1), 75, "destination", 3) == ("aging", "rough", "error_fare", "unconfirmed")
    assert badges_for(make_fare(stops=1), None, None, 1) == ("fresh", "new_route")
    assert badges_for(make_fare(stops=1), 45, "month", 1) == ("fresh",)
    assert badges_for(make_fare(stops=1), 45, "month", 2) == ("aging", "unconfirmed")


def test_score_combines_baseline_percent_age_and_badges():
    baselines = build_baselines(history_for("LIS", [1000] * 7), TODAY)
    [scored] = score([make_fare(price=450, searched_on=date(2026, 10, 5))], baselines, TODAY)
    assert (scored.normal, scored.pct_off, scored.baseline, scored.age_days) == (1000, 55, "month", 3)
    assert "unconfirmed" in scored.badges


def test_score_new_route_has_no_percent():
    [scored] = score([make_fare(dest_city="ROM")], build_baselines([], TODAY), TODAY)
    assert (scored.normal, scored.pct_off, scored.baseline) == (None, None, None)
    assert "new_route" in scored.badges


def test_scan_sizes_ignore_seed_rows_today_and_same_day_reruns():
    day1 = [make_fare(dest_city=c, scan_date=date(2026, 10, 6)) for c in ("LIS", "PAR", "ROM")]
    rerun = [make_fare(dest_city=c, scan_date=date(2026, 10, 6)) for c in ("LIS", "PAR", "ROM")]
    day2 = [make_fare(dest_city=c, scan_date=date(2026, 10, 7)) for c in ("LIS", "PAR")]
    seed = [make_fare(kind="seed", dest_city="BCN", scan_date=date(2026, 10, 7))]
    today = [make_fare(scan_date=TODAY)]
    assert scan_sizes(day1 + rerun + day2 + seed + today, TODAY) == [3, 2]


def test_scan_sizes_keep_the_last_n_days():
    history = [make_fare(scan_date=date(2026, 9, 25) + timedelta(days=i)) for i in range(10)]
    assert scan_sizes(history, TODAY) == [1] * 7


def test_scan_too_small():
    assert scan_too_small(10, [100, 100]) is False
    assert scan_too_small(40, [100, 100, 100]) is True
    assert scan_too_small(60, [100, 100, 100]) is False
