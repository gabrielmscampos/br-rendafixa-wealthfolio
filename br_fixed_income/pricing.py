"""Price series calculation.

Bank bonds: price(t) = purchase_price * factor(t), with factor(purchase_date) = 1.
Tesouro: market price from the official sources.
"""

import datetime as dt
from dataclasses import dataclass

from .business_days import BusinessCalendar


class PricingError(Exception):
    pass


@dataclass(frozen=True)
class Quote:
    date: dt.date
    price: float
    source: str


@dataclass
class Series:
    quotes: list[Quote]
    warnings: list[str]


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def add_months(d: dt.date, months: int) -> dt.date:
    total = d.year * 12 + d.month - 1 + months
    return dt.date(total // 12, total % 12 + 1, 1)


# --------------------------------------------------------------------------
# Fixed rate (prefixado)
# --------------------------------------------------------------------------


def pre_series(
    purchase_price: float,
    annual_rate: float,
    start: dt.date,
    end: dt.date,
    cal: BusinessCalendar,
) -> Series:
    """annual_rate in % per year."""
    base = 1 + annual_rate / 100
    quotes = [
        Quote(
            d,
            purchase_price * base ** (cal.bd(start, d) / 252),
            f"fixed {annual_rate:g}% p.a.",
        )
        for d in cal.business_days(start, end)
    ]
    return Series(quotes, [])


# --------------------------------------------------------------------------
# Percentage of CDI or Selic
# --------------------------------------------------------------------------


def percent_of_index_series(
    purchase_price: float,
    percent: float,
    index: dict[dt.date, float],
    start: dt.date,
    end: dt.date,
    cal: BusinessCalendar,
    source: str,
) -> Series:
    """factor(t) = product, over business days start <= d < t, of (1 + index_d/100 * percent/100).

    `index` in % per day. The series ends on the last day whose factor can be
    computed from the rates already published; a gap in the middle is an error.
    """
    p = percent / 100
    days = cal.business_days(start, end)
    last_published = max((d for d in index if start <= d <= end), default=None)
    quotes: list[Quote] = []
    warnings: list[str] = []
    factor = 1.0
    for d in days:
        quotes.append(Quote(d, purchase_price * factor, source))
        if d == days[-1]:
            break
        if d not in index:
            if last_published is not None and last_published > d:
                raise PricingError(
                    f"{source}: rate missing for business day {d} (later data exists)"
                )
            warnings.append(
                f"{source} published up to {last_published or '-'}; series ends on {d}"
            )
            break
        factor *= 1 + index[d] / 100 * p
    return Series(quotes, warnings)


# --------------------------------------------------------------------------
# IPCA + rate
# --------------------------------------------------------------------------


PROJECTION_FOCUS = "Focus survey median"
PROJECTION_LAST = "last published"


def ipca_factor(
    start: dt.date,
    t: dt.date,
    ipca: dict[dt.date, float],
    lag: int,
    cal: BusinessCalendar,
    expectations: dict[dt.date, float] | None = None,
) -> tuple[float, dict[dt.date, str]]:
    """Accumulated IPCA factor from start to t, and the projected months.

    Each calendar month overlapping [start, t) contributes
    (1 + ipca_m/100) ^ (elapsed_bd / month_bd), where ipca_m is the IPCA of the
    month shifted `lag` months back. Full months have exponent 1.
    A month not yet published uses `expectations` (Focus survey) when it has
    that month, and otherwise repeats the last published IPCA.
    `ipca` and `expectations` are keyed by the first day of the month; the
    returned dict maps each projected month to the method used.
    """
    last_month = max(ipca) if ipca else None
    factor = 1.0
    projected: dict[dt.date, str] = {}
    month = _month_start(start)
    while month < t:
        next_month = add_months(month, 1)
        elapsed = cal.bd(max(start, month), min(t, next_month))
        if elapsed:
            ref = add_months(month, -lag)
            if ref in ipca:
                value = ipca[ref]
            elif last_month is not None and ref > last_month:
                if expectations is not None and ref in expectations:
                    value = expectations[ref]
                    projected[ref] = PROJECTION_FOCUS
                else:
                    value = ipca[last_month]
                    projected[ref] = PROJECTION_LAST
            else:
                raise PricingError(f"IPCA for {ref:%m/%Y} not found")
            factor *= (1 + value / 100) ** (
                elapsed / cal.bd(month, next_month)
            )
        month = next_month
    return factor, projected


def ipca_series(
    purchase_price: float,
    annual_rate: float,
    ipca: dict[dt.date, float],
    lag: int,
    start: dt.date,
    end: dt.date,
    cal: BusinessCalendar,
    expectations: dict[dt.date, float] | None = None,
) -> Series:
    """factor(t) = ipca_factor(start, t) * (1 + annual_rate/100) ^ (bd(start, t) / 252)."""
    base = 1 + annual_rate / 100
    source = f"SGS 433 (IPCA) + {annual_rate:g}% p.a."
    quotes = []
    projected: dict[dt.date, str] = {}
    for d in cal.business_days(start, end):
        f_ipca, proj = ipca_factor(start, d, ipca, lag, cal, expectations)
        projected |= proj
        quotes.append(
            Quote(
                d,
                purchase_price * f_ipca * base ** (cal.bd(start, d) / 252),
                source,
            )
        )
    warnings = []
    for method in (PROJECTION_FOCUS, PROJECTION_LAST):
        months = sorted(m for m, how in projected.items() if how == method)
        if months:
            listed = ", ".join(f"{m:%m/%Y}" for m in months)
            warnings.append(f"IPCA projected ({method}) for {listed}")
    return Series(quotes, warnings)


# --------------------------------------------------------------------------
# Tesouro Direto
# --------------------------------------------------------------------------

SOURCE_TESOURO_CSV = "Tesouro Transparente CSV"
SOURCE_RESGATAR = "Tesouro Direto resgatar API"


def tesouro_series(
    csv_prices: dict[dt.date, float],
    start: dt.date,
    end: dt.date,
    cal: BusinessCalendar,
    current_price: tuple[dt.date, float] | None = None,
) -> Series:
    """CSV prices in the range; `current_price` (from resgatar) only fills a date the CSV lacks."""
    warnings: list[str] = []
    prices = {
        d: (p, SOURCE_TESOURO_CSV)
        for d, p in csv_prices.items()
        if start <= d <= end
    }

    non_business = [d for d in prices if not cal.is_business_day(d)]
    for d in non_business:
        del prices[d]
    if non_business:
        warnings.append(
            f"{len(non_business)} CSV date(s) outside business days ignored"
        )

    if current_price is not None:
        date, price = current_price
        if (
            start <= date <= end
            and date not in prices
            and cal.is_business_day(date)
        ):
            prices[date] = (price, SOURCE_RESGATAR)

    if not prices:
        raise PricingError(f"no Tesouro price between {start} and {end}")

    dates = sorted(prices)
    missing = [
        d for d in cal.business_days(start, dates[-1]) if d not in prices
    ]
    if missing:
        warnings.append(
            f"{len(missing)} business day(s) without a CSV price (e.g. {missing[0]})"
        )
    return Series([Quote(d, *prices[d]) for d in dates], warnings)
