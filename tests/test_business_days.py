import datetime as dt

import pytest

from br_fixed_income.business_days import BRASILIA, today_in_brazil


D = dt.date


@pytest.mark.parametrize(
    "holiday",
    [
        D(2026, 2, 16),  # Carnaval (Monday)
        D(2026, 2, 17),  # Carnaval (Tuesday)
        D(2026, 4, 3),  # Good Friday
        D(2026, 6, 4),  # Corpus Christi
        D(2025, 6, 19),  # Corpus Christi
        D(2024, 11, 20),  # Consciência Negra, national holiday since 2024
        D(2025, 11, 20),
        D(2026, 11, 20),
        D(2025, 12, 25),
        D(2026, 1, 1),
    ],
)
def test_holidays_are_not_business_days(cal, holiday):
    assert not cal.is_business_day(holiday)


@pytest.mark.parametrize(
    "day",
    [
        D(2023, 11, 20),  # before it became a national holiday
        D(2026, 2, 18),  # Ash Wednesday
        D(2025, 12, 24),  # Christmas Eve: business day in the ANBIMA standard
        D(2025, 12, 31),
    ],
)
def test_business_days(cal, day):
    assert cal.is_business_day(day)


def test_weekend(cal):
    assert not cal.is_business_day(D(2026, 10, 3))
    assert not cal.is_business_day(D(2026, 10, 4))
    assert cal.business_days(D(2026, 10, 2), D(2026, 10, 5)) == [
        D(2026, 10, 2),
        D(2026, 10, 5),
    ]


def test_bd_convention(cal):
    assert cal.bd(D(2026, 10, 5), D(2026, 10, 6)) == 1
    assert cal.bd(D(2026, 10, 5), D(2026, 10, 5)) == 0
    assert cal.bd(D(2026, 10, 2), D(2026, 10, 5)) == 1  # Friday -> Monday
    assert cal.bd(D(2026, 10, 3), D(2026, 10, 5)) == 0  # Saturday -> Monday
    # February 2026: 20 weekdays minus the 2 Carnaval days.
    assert cal.bd(D(2026, 2, 1), D(2026, 3, 1)) == 18


def test_range_across_years(cal):
    assert cal.bd(D(2025, 12, 30), D(2026, 1, 5)) == 3  # 12-30, 12-31, 01-02


def test_brasilia_date_lags_utc_late_in_the_evening():
    # 01:00 UTC on Oct 8 is 22:00 on Oct 7 in Brasília.
    utc = dt.datetime(2026, 10, 8, 1, 0, tzinfo=dt.UTC)
    assert utc.astimezone(BRASILIA).date() == D(2026, 10, 7)


def test_today_in_brazil_matches_utc_minus_3():
    expected = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)).date()
    assert today_in_brazil() == expected
