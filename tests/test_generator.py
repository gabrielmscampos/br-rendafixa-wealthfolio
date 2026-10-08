import datetime as dt

import httpx
import pytest

from br_fixed_income.bonds import Bond
from br_fixed_income.generator import DataSources, build_series
from br_fixed_income.pricing import SOURCE_RESGATAR, PricingError
from br_fixed_income.sources import SERIES_CDI, SERIES_SELIC, SourceError


D = dt.date


def _tesouro(symbol, tesouro_bond, maturity, purchase_date):
    return Bond(
        symbol=symbol,
        type="TESOURO",
        tesouro_bond=tesouro_bond,
        maturity=maturity,
        purchase_date=purchase_date,
    )


PURCHASE, MATURITY = D(2026, 9, 1), D(2027, 9, 1)


def _bank(kind, rate=100.0, purchase_date=PURCHASE, maturity=MATURITY, **kw):
    return Bond(
        symbol=f"X{kind}",
        type=kind,
        rate=rate,
        purchase_price=1000.0,
        maturity=maturity,
        purchase_date=purchase_date,
        **kw,
    )


def _build(server, cal, bond, until, today=None, others=(), **kw):
    today = today or until
    with server.client() as c:
        data = DataSources(
            c,
            [bond, *others],
            until,
            use_resgatar=kw.pop("use_resgatar", True),
        )
        return build_series(bond, data, cal, until, today, **kw)


# --- Real Tesouro cases (spec section 10.2) ------------------------------


@pytest.mark.parametrize(
    ("tesouro_bond", "maturity", "date", "pu_venda"),
    [
        ("PRE", D(2032, 1, 1), D(2025, 7, 30), 434.89),
        ("PRE", D(2032, 1, 1), D(2026, 1, 27), 471.44),
        ("IPCA", D(2029, 5, 15), D(2026, 1, 27), 3582.98),
    ],
)
def test_tesouro_real_cases(
    server, cal, tesouro_bond, maturity, date, pu_venda
):
    b = _tesouro("T", tesouro_bond, maturity, date)
    series = _build(server, cal, b, until=date, today=D(2026, 10, 6))
    assert [(q.date, q.price) for q in series.quotes] == [(date, pu_venda)]
    assert f"{series.quotes[0].price:.6f}" == f"{pu_venda:.6f}"


def test_tesouro_not_found_suggests(server, cal):
    b = _tesouro("T", "PRE", D(2031, 1, 1), D(2026, 1, 27))
    with pytest.raises(
        PricingError,
        match=r"Closest: Tesouro Prefixado \(maturities: .*2032-01-01",
    ):
        _build(server, cal, b, until=D(2026, 1, 29))


# --- resgatar ------------------------------------------------------------


def _resgatar_ltn(date, price=480.0):
    return {
        "TesouroLegado": [
            {
                "treasuryBondName": "Tesouro Prefixado 2032",
                "maturityDate": "2032-01-01T00:00",
                "unitaryRedemptionValue": price,
                "lastMarketPricingDate": f"{date}T18:00:00",
                "startDate": f"{date}T09:30:00.000",
            }
        ]
    }


def test_resgatar_fills_in_the_current_day(server, cal):
    server.resgatar = _resgatar_ltn("2026-01-30")
    b = _tesouro("T", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    series = _build(server, cal, b, until=D(2026, 1, 30))
    assert series.quotes[-1].date == D(2026, 1, 30)
    assert series.quotes[-1].price == 480.0
    assert series.quotes[-1].source == SOURCE_RESGATAR
    assert series.quotes[-2].price == 478.07


def test_resgatar_not_called_if_csv_has_today(server, cal):
    server.resgatar = _resgatar_ltn("2026-01-29", 999.0)
    b = _tesouro("T", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    series = _build(server, cal, b, until=D(2026, 1, 29))
    assert series.quotes[-1].price == 478.07
    assert server.count("resgatar") == 0


def test_resgatar_not_called_with_until_in_the_past(server, cal):
    b = _tesouro("T", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    _build(server, cal, b, until=D(2026, 1, 28), today=D(2026, 1, 30))
    assert server.count("resgatar") == 0


def test_resgatar_disabled(server, cal):
    b = _tesouro("T", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    series = _build(server, cal, b, until=D(2026, 1, 30), use_resgatar=False)
    assert series.quotes[-1].date == D(2026, 1, 29)
    assert server.count("resgatar") == 0


@pytest.mark.parametrize("response", [503, {"unexpected": True}])
def test_resgatar_failure_tolerated(server, cal, response):
    server.resgatar = response
    b = _tesouro("T", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    series = _build(server, cal, b, until=D(2026, 1, 30))
    assert series.quotes[-1].date == D(2026, 1, 29)
    assert any("resgatar" in w for w in series.warnings)


def test_resgatar_one_call_per_run(server, cal):
    server.resgatar = _resgatar_ltn("2026-01-30")
    ltn = _tesouro("A", "PRE", D(2032, 1, 1), D(2026, 1, 27))
    ntnb = _tesouro("B", "IPCA", D(2029, 5, 15), D(2026, 1, 27))
    today = D(2026, 1, 30)
    with server.client() as c:
        data = DataSources(c, [ltn, ntnb], today)
        build_series(ltn, data, cal, today, today)
        series_b = build_series(ntnb, data, cal, today, today)
    assert server.count("resgatar") == 1
    assert server.count("tesourotransparente") == 1
    assert "bond not found in resgatar" in series_b.warnings


# --- Bank bonds ----------------------------------------------------------


def test_cdi_and_selic_read_the_right_series(server, cal):
    server.constant_index(SERIES_CDI, 0.05, cal, D(2026, 9, 1), D(2026, 10, 5))
    server.constant_index(
        SERIES_SELIC, 0.04, cal, D(2026, 9, 1), D(2026, 10, 5)
    )
    today = D(2026, 10, 6)
    n = cal.bd(D(2026, 9, 1), today)
    cdi = _build(server, cal, _bank("CDI"), until=today)
    selic = _build(server, cal, _bank("SELIC"), until=today)
    assert cdi.quotes[-1].price == pytest.approx(1000 * 1.0005**n, rel=1e-12)
    assert selic.quotes[-1].price == pytest.approx(1000 * 1.0004**n, rel=1e-12)


def test_source_failure_propagates_and_is_remembered(cal, monkeypatch):
    monkeypatch.setattr("br_fixed_income.sources.time.sleep", lambda s: None)
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(500, text="<html>")

    today = D(2026, 10, 6)
    a, b = _bank("CDI"), _bank("CDI", purchase_date=D(2026, 9, 2))
    with httpx.Client(transport=httpx.MockTransport(fail)) as c:
        data = DataSources(c, [a, b], today)
        for bond in (a, b):
            with pytest.raises(SourceError, match="SGS series 12"):
                build_series(bond, data, cal, today, today)
    assert len(calls) == 3  # 3 attempts, only once per run


@pytest.mark.parametrize(
    ("kw", "until", "last"),
    [
        ({}, D(2026, 10, 6), D(2026, 10, 6)),  # today
        (
            {"maturity": D(2026, 9, 30)},
            D(2026, 10, 6),
            D(2026, 9, 30),
        ),  # maturity
        (
            {"end_date": D(2026, 9, 15)},
            D(2026, 10, 6),
            D(2026, 9, 15),
        ),  # issuer liquidation
        ({}, D(2026, 9, 18), D(2026, 9, 18)),  # --until
    ],
)
def test_range_never_exceeds_limits(server, cal, kw, until, last):
    b = _bank("PRE", rate=12, **kw)
    series = _build(server, cal, b, until=until, today=D(2026, 10, 6))
    assert series.quotes[0].date == D(2026, 9, 1)
    assert series.quotes[-1].date == last


def test_skip_purchase_date(server, cal):
    b = _bank("PRE", rate=12)
    series = _build(
        server, cal, b, until=D(2026, 9, 4), skip_purchase_date=True
    )
    assert [q.date for q in series.quotes] == [
        D(2026, 9, 2),
        D(2026, 9, 3),
        D(2026, 9, 4),
    ]


def test_ipca_fetches_lagged_months(server, cal):
    server.sgs[433] = {D(2026, 7, 1): 0.07, D(2026, 8, 1): -0.32}
    b = _bank("IPCA", rate=8, purchase_date=D(2026, 9, 1), ipca_lag=2)
    series = _build(server, cal, b, until=D(2026, 10, 6))
    assert "dataInicial=01/07/2026" in server.calls[0]
    assert (
        series.warnings == []
    )  # Sep and Oct use Jul and Aug, already published
