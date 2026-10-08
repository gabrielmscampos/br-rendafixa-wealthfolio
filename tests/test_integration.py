"""Real calls to the data sources. Run with: uv run pytest -m integration"""

import datetime as dt

import pytest

from br_fixed_income.pricing import percent_of_index_series
from br_fixed_income.sources import (
    SERIES_CDI,
    SERIES_SELIC,
    download_tesouro_csv,
    fetch_ipca,
    fetch_resgatar,
    fetch_sgs,
    find_redemption_price,
    new_client,
    parse_tesouro_csv,
)


pytestmark = pytest.mark.integration

D = dt.date


@pytest.fixture(scope="module")
def client():
    with new_client() as c:
        yield c


@pytest.fixture(scope="module")
def cdi(client):
    return fetch_sgs(client, SERIES_CDI, D(2025, 11, 1), D(2026, 10, 6))


@pytest.fixture(scope="module")
def tesouro_csv(client):
    return download_tesouro_csv(client)


def test_calendar_matches_cdi_days(client, cal):
    values = fetch_sgs(client, SERIES_CDI, D(2016, 1, 4), D(2026, 9, 30))
    assert set(values) == set(cal.business_days(D(2016, 1, 4), D(2026, 9, 30)))


def test_sgs_selic_and_ipca(client):
    assert fetch_sgs(client, SERIES_SELIC, D(2026, 9, 1), D(2026, 9, 30))
    ipca = fetch_ipca(client, D(2026, 1, 1), D(2026, 6, 30))
    assert ipca[D(2026, 3, 1)] == 0.88


@pytest.mark.parametrize(
    ("name", "maturity", "date", "pu_venda"),
    [
        ("Tesouro Prefixado", D(2032, 1, 1), D(2025, 7, 30), 434.89),
        ("Tesouro Prefixado", D(2032, 1, 1), D(2026, 1, 27), 471.44),
        ("Tesouro IPCA+", D(2029, 5, 15), D(2026, 1, 27), 3582.98),
    ],
)
def test_real_tesouro_csv(tesouro_csv, name, maturity, date, pu_venda):
    assert (
        parse_tesouro_csv(tesouro_csv, {(name, maturity)}).series(
            name, maturity
        )[date]
        == pu_venda
    )


def test_real_resgatar(client):
    prices = fetch_resgatar(client)
    assert (
        find_redemption_price(prices, "Tesouro Prefixado", D(2032, 1, 1))
        is not None
    )


# Spec section 10.3: "Preço R$" from the BTG position statement of 2026-10-05.
@pytest.mark.parametrize(
    ("percent", "purchase_date", "date", "expected"),
    [
        (
            114,
            D(2025, 11, 26),
            D(2026, 10, 5),
            1139.68426,
        ),  # CDBB25BDVA0, Pefisa
        (113, D(2025, 12, 30), D(2026, 10, 5), 1122.18826),  # CDBC25EV6VP, BRB
        (
            105.5,
            D(2026, 6, 30),
            D(2026, 10, 5),
            1037.88856,
        ),  # CDB626FG7PK, Neon
        (
            105.5,
            D(2026, 6, 30),
            D(2026, 10, 6),
            1038.4447,
        ),  # CDB626FG7PK, Neon (reported on 2026-10-06)
    ],
)
def test_bank_bonds_against_btg_statement(
    cdi, cal, percent, purchase_date, date, expected
):
    series = percent_of_index_series(
        1000, percent, cdi, purchase_date, date, cal, "CDI"
    )
    assert series.quotes[-1].date == date
    assert series.quotes[-1].price == pytest.approx(expected, abs=0.01)
