"""Business day calendar (Brazilian national holidays, ANBIMA/B3 standard).

Uses the BVMF financial calendar from the `holidays` library. From 2016 to 2026
it matches exactly the days on which the BCB publishes the CDI (SGS series 12).
"""

import bisect
import datetime as dt

import holidays


class BusinessCalendar:
    def __init__(self) -> None:
        self._years: tuple[int, int] | None = None
        self._days: list[dt.date] = []
        self._holidays: holidays.HolidayBase | None = None

    def _ensure(self, *dates: dt.date) -> None:
        first = min(d.year for d in dates)
        last = max(d.year for d in dates)
        if self._years and self._years[0] <= first and last <= self._years[1]:
            return
        if self._years:
            first = min(first, self._years[0])
            last = max(last, self._years[1])
        self._holidays = holidays.financial_holidays(
            "BVMF", years=range(first, last + 1)
        )
        d = dt.date(first, 1, 1)
        end = dt.date(last, 12, 31)
        days = []
        while d <= end:
            if d.weekday() < 5 and d not in self._holidays:
                days.append(d)
            d += dt.timedelta(days=1)
        self._days = days
        self._years = (first, last)

    def is_business_day(self, d: dt.date) -> bool:
        self._ensure(d)
        i = bisect.bisect_left(self._days, d)
        return i < len(self._days) and self._days[i] == d

    def bd(self, a: dt.date, b: dt.date) -> int:
        """Number of business days d with a <= d < b (zero if b <= a)."""
        if b <= a:
            return 0
        self._ensure(a, b)
        return bisect.bisect_left(self._days, b) - bisect.bisect_left(
            self._days, a
        )

    def business_days(self, a: dt.date, b: dt.date) -> list[dt.date]:
        """Business days d with a <= d <= b."""
        if b < a:
            return []
        self._ensure(a, b)
        return self._days[
            bisect.bisect_left(self._days, a) : bisect.bisect_right(
                self._days, b
            )
        ]
