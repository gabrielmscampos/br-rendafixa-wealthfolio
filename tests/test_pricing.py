import datetime as dt

import pytest

from br_fixed_income.pricing import (
    ACCRUAL_ANNIVERSARY,
    SOURCE_RESGATAR,
    SOURCE_TESOURO_CSV,
    PricingError,
    ipca_factor,
    ipca_series,
    percent_of_index_series,
    pre_series,
    tesouro_series,
)


D = dt.date


def _constant_index(cal, value, start, end):
    return {d: value for d in cal.business_days(start, end)}


# --- Fixed rate (PRE) ----------------------------------------------------


def test_pre_252_business_days_at_15(cal):
    start = D(2026, 1, 2)
    target = cal.business_days(start, D(2027, 6, 1))[252]
    assert cal.bd(start, target) == 252
    series = pre_series(1000, 15, start, target, cal)
    assert series.quotes[0].price == 1000
    assert series.quotes[-1].date == target
    assert series.quotes[-1].price == pytest.approx(1150.00, abs=1e-9)


def test_pre_business_days_only(cal):
    series = pre_series(1000, 10, D(2026, 2, 12), D(2026, 2, 23), cal)
    dates = [q.date for q in series.quotes]
    assert (
        D(2026, 2, 16) not in dates and D(2026, 2, 17) not in dates
    )  # Carnaval
    assert all(d.weekday() < 5 for d in dates)
    assert dates == [
        D(2026, 2, 12),
        D(2026, 2, 13),
        D(2026, 2, 18),
        D(2026, 2, 19),
        D(2026, 2, 20),
        D(2026, 2, 23),
    ]


# --- CDI / Selic ---------------------------------------------------------


def test_percent_constant_rate(cal):
    start, end = D(2026, 3, 2), D(2026, 6, 30)
    r = 0.05  # % per day
    index = _constant_index(cal, r, start, end)
    series = percent_of_index_series(1000, 100, index, start, end, cal, "CDI")
    assert series.warnings == []
    for q in series.quotes:
        assert q.price == pytest.approx(
            1000 * (1 + r / 100) ** cal.bd(start, q.date), rel=1e-12
        )


def test_percent_applies_percentage(cal):
    start, end = D(2026, 3, 2), D(2026, 3, 31)
    index = _constant_index(cal, 0.05, start, end)
    series = percent_of_index_series(1000, 114, index, start, end, cal, "CDI")
    n = cal.bd(start, end)
    assert series.quotes[-1].price == pytest.approx(
        1000 * (1 + 0.0005 * 1.14) ** n, rel=1e-12
    )


def test_percent_without_todays_rate_ends_today(cal):
    # The price on t uses rates up to t-1: a missing rate for today does not shorten the series.
    start, today = D(2026, 9, 28), D(2026, 10, 6)
    index = _constant_index(cal, 0.05, start, D(2026, 10, 5))
    series = percent_of_index_series(
        1000, 100, index, start, today, cal, "CDI"
    )
    assert series.quotes[-1].date == today
    assert series.warnings == []


def test_percent_late_rate_ends_on_last_day_with_data(cal):
    start, today = D(2026, 9, 28), D(2026, 10, 6)
    index = _constant_index(cal, 0.05, start, D(2026, 10, 1))
    series = percent_of_index_series(
        1000, 100, index, start, today, cal, "CDI"
    )
    assert series.quotes[-1].date == D(2026, 10, 2)
    assert series.warnings


def test_percent_gap_in_the_middle_is_an_error(cal):
    start, end = D(2026, 9, 1), D(2026, 9, 30)
    index = _constant_index(cal, 0.05, start, end)
    del index[D(2026, 9, 15)]
    with pytest.raises(PricingError, match="2026-09-15"):
        percent_of_index_series(1000, 100, index, start, end, cal, "CDI")


# --- IPCA ----------------------------------------------------------------

IPCA = {D(2026, 2, 1): 0.70, D(2026, 3, 1): 0.88, D(2026, 4, 1): 0.67}


def test_ipca_full_month_zero_rate(cal):
    # 2026-03-01 is a Sunday: buying on 03-02 covers every business day of March.
    series = ipca_series(1000, 0, IPCA, 0, D(2026, 3, 2), D(2026, 4, 1), cal)
    assert series.quotes[-1].date == D(2026, 4, 1)
    assert series.quotes[-1].price == pytest.approx(1000 * 1.0088, rel=1e-12)
    assert series.warnings == []


def test_ipca_lag_shifts_the_month(cal):
    series = ipca_series(1000, 0, IPCA, 1, D(2026, 3, 2), D(2026, 4, 1), cal)
    assert series.quotes[-1].price == pytest.approx(1000 * 1.0070, rel=1e-12)


def test_ipca_pro_rata_by_business_days(cal):
    start, t = D(2026, 3, 16), D(2026, 4, 15)
    f, _ = ipca_factor(start, t, IPCA, 0, cal)
    march = 1.0088 ** (
        cal.bd(start, D(2026, 4, 1)) / cal.bd(D(2026, 3, 1), D(2026, 4, 1))
    )
    april = 1.0067 ** (
        cal.bd(D(2026, 4, 1), t) / cal.bd(D(2026, 4, 1), D(2026, 5, 1))
    )
    assert f == pytest.approx(march * april, rel=1e-12)


def test_ipca_with_rate(cal):
    start, t = D(2026, 3, 2), D(2026, 4, 1)
    series = ipca_series(1000, 8, IPCA, 0, start, t, cal)
    expected = 1000 * 1.0088 * 1.08 ** (cal.bd(start, t) / 252)
    assert series.quotes[-1].price == pytest.approx(expected, rel=1e-12)


def test_ipca_unpublished_month_uses_last(cal):
    series = ipca_series(1000, 0, IPCA, 0, D(2026, 4, 1), D(2026, 6, 1), cal)
    assert series.quotes[-1].price == pytest.approx(
        1000 * 1.0067 * 1.0067, rel=1e-12
    )
    assert series.warnings and "05/2026" in series.warnings[0]


def test_ipca_missing_old_month_is_an_error(cal):
    with pytest.raises(PricingError, match="01/2026"):
        ipca_series(1000, 0, IPCA, 0, D(2026, 1, 5), D(2026, 3, 2), cal)


def test_ipca_unpublished_months_use_focus(cal):
    focus = {D(2026, 5, 1): 0.40, D(2026, 6, 1): 0.30}
    series = ipca_series(
        1000, 0, IPCA, 0, D(2026, 4, 1), D(2026, 7, 1), cal, focus
    )
    assert series.quotes[-1].price == pytest.approx(
        1000 * 1.0067 * 1.0040 * 1.0030, rel=1e-12
    )
    assert series.warnings == [
        "IPCA projected (Focus survey median) for 05/2026, 06/2026"
    ]


def test_ipca_month_missing_from_focus_repeats_last_published(cal):
    focus = {D(2026, 5, 1): 0.40}
    series = ipca_series(
        1000, 0, IPCA, 0, D(2026, 4, 1), D(2026, 7, 1), cal, focus
    )
    assert series.quotes[-1].price == pytest.approx(
        1000 * 1.0067 * 1.0040 * 1.0067, rel=1e-12
    )
    assert series.warnings == [
        "IPCA projected (Focus survey median) for 05/2026",
        "IPCA projected (last published) for 06/2026",
    ]


def test_ipca_published_month_ignores_focus(cal):
    focus = {D(2026, 3, 1): 5.0}
    series = ipca_series(
        1000, 0, IPCA, 0, D(2026, 3, 2), D(2026, 4, 1), cal, focus
    )
    assert series.quotes[-1].price == pytest.approx(1000 * 1.0088, rel=1e-12)
    assert series.warnings == []


def test_ipca_anniversary_full_period_uses_start_month(cal):
    # 03-16 -> 04-16 is one complete period and accrues all of March's IPCA.
    start, t = D(2026, 3, 16), D(2026, 4, 16)
    f, _ = ipca_factor(start, t, IPCA, 0, cal, accrual=ACCRUAL_ANNIVERSARY)
    assert f == pytest.approx(1.0088, rel=1e-12)


def test_ipca_anniversary_pro_rata_by_business_days(cal):
    start, t = D(2026, 3, 16), D(2026, 4, 30)
    f, _ = ipca_factor(start, t, IPCA, 0, cal, accrual=ACCRUAL_ANNIVERSARY)
    a, b = D(2026, 4, 16), D(2026, 5, 16)
    assert f == pytest.approx(
        1.0088 * 1.0067 ** (cal.bd(a, t) / cal.bd(a, b)), rel=1e-12
    )


def test_ipca_anniversary_lag_shifts_the_month(cal):
    start, t = D(2026, 3, 16), D(2026, 4, 16)
    f, _ = ipca_factor(start, t, IPCA, 1, cal, accrual=ACCRUAL_ANNIVERSARY)
    assert f == pytest.approx(1.0070, rel=1e-12)


def test_ipca_anniversary_day_clamped_to_month_end(cal):
    # Anniversaries of 01-31: 02-28, 03-31, 04-30.
    ipca = {D(2026, 1, 1): 0.33, **IPCA}
    start = D(2026, 1, 31)
    t = D(2026, 3, 10)
    f, _ = ipca_factor(start, t, ipca, 0, cal, accrual=ACCRUAL_ANNIVERSARY)
    a, b = D(2026, 2, 28), D(2026, 3, 31)
    assert f == pytest.approx(
        1.0033 * 1.0070 ** (cal.bd(a, t) / cal.bd(a, b)), rel=1e-12
    )


def test_ipca_anniversary_matches_bank_statement(cal):
    # LCA IPCA + 7.33% held from 2025-05-21 to maturity on 2026-05-21; the
    # BTG statement shows 1120.434076. SGS 433 rounds IPCA to two decimals,
    # which may explain the remaining difference.
    ipca = {
        D(2025, 5, 1): 0.26,
        D(2025, 6, 1): 0.24,
        D(2025, 7, 1): 0.26,
        D(2025, 8, 1): -0.11,
        D(2025, 9, 1): 0.48,
        D(2025, 10, 1): 0.09,
        D(2025, 11, 1): 0.18,
        D(2025, 12, 1): 0.33,
        D(2026, 1, 1): 0.33,
        D(2026, 2, 1): 0.70,
        D(2026, 3, 1): 0.88,
        D(2026, 4, 1): 0.67,
        D(2026, 5, 1): 0.58,
    }
    series = ipca_series(
        1000,
        7.33,
        ipca,
        0,
        D(2025, 5, 21),
        D(2026, 5, 21),
        cal,
        accrual=ACCRUAL_ANNIVERSARY,
    )
    assert series.quotes[-1].price == pytest.approx(1120.434076, abs=0.01)
    assert series.warnings == []


def test_ipca_unknown_accrual_is_an_error(cal):
    with pytest.raises(PricingError, match="accrual"):
        ipca_factor(D(2026, 3, 2), D(2026, 4, 1), IPCA, 0, cal, accrual="x")


# --- Tesouro -------------------------------------------------------------

CSV = {
    D(2026, 1, 26): 470.23,
    D(2026, 1, 27): 471.44,
    D(2026, 1, 28): 475.12,
    D(2026, 1, 29): 478.07,
}


def test_tesouro_trims_range(cal):
    series = tesouro_series(CSV, D(2026, 1, 27), D(2026, 1, 28), cal)
    assert [(q.date, q.price) for q in series.quotes] == [
        (D(2026, 1, 27), 471.44),
        (D(2026, 1, 28), 475.12),
    ]
    assert all(q.source == SOURCE_TESOURO_CSV for q in series.quotes)


def test_tesouro_current_price_fills_in(cal):
    series = tesouro_series(
        CSV, D(2026, 1, 27), D(2026, 1, 30), cal, (D(2026, 1, 30), 480.0)
    )
    assert series.quotes[-1].date == D(2026, 1, 30)
    assert series.quotes[-1].source == SOURCE_RESGATAR


def test_tesouro_csv_beats_resgatar(cal):
    series = tesouro_series(
        CSV, D(2026, 1, 27), D(2026, 1, 29), cal, (D(2026, 1, 29), 999.0)
    )
    assert series.quotes[-1].price == 478.07
    assert series.quotes[-1].source == SOURCE_TESOURO_CSV


def test_tesouro_current_price_out_of_range_ignored(cal):
    series = tesouro_series(
        CSV, D(2026, 1, 27), D(2026, 1, 29), cal, (D(2026, 1, 30), 480.0)
    )
    assert series.quotes[-1].date == D(2026, 1, 29)


def test_tesouro_warns_about_days_without_price(cal):
    csv = dict(CSV)
    del csv[D(2026, 1, 28)]
    series = tesouro_series(csv, D(2026, 1, 26), D(2026, 1, 29), cal)
    assert len(series.quotes) == 3
    assert "2026-01-28" in series.warnings[0]


def test_tesouro_without_data_is_an_error(cal):
    with pytest.raises(PricingError):
        tesouro_series(CSV, D(2026, 3, 2), D(2026, 3, 10), cal)
