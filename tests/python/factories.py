from datetime import date

from scanner.fare import Fare

TODAY = date(2026, 10, 8)


def make_fare(**overrides) -> Fare:
    values = dict(
        origin="YUL",
        dest_airport="LIS",
        dest_city="LIS",
        depart_date=date(2026, 12, 1),
        return_date=date(2026, 12, 10),
        price=500,
        airline="TP",
        stops=0,
        searched_on=TODAY,
        link="",
        kind="dates",
        scan_date=TODAY,
    )
    values.update(overrides)
    return Fare(**values)
