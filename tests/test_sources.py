import datetime as dt
import json

import httpx
import pytest

from br_fixed_income.sources import (
    RedemptionPrice,
    SourceError,
    download_tesouro_csv,
    fetch_ipca,
    fetch_resgatar,
    fetch_sgs,
    find_redemption_price,
    parse_resgatar,
    parse_tesouro_csv,
)
from br_fixed_income.tesouro_map import strip_year

from .conftest import FIXTURES


D = dt.date


# --- SGS -----------------------------------------------------------------


def test_sgs_parse(server):
    server.sgs[12] = {D(2026, 10, 1): 0.050788, D(2026, 10, 2): 0.050788}
    with server.client() as c:
        values = fetch_sgs(c, 12, D(2026, 10, 1), D(2026, 10, 6))
    assert values == {D(2026, 10, 1): 0.050788, D(2026, 10, 2): 0.050788}
    assert "dataInicial=01/10/2026&dataFinal=06/10/2026" in server.calls[0]


def test_sgs_empty_window(server):
    with server.client() as c:
        assert fetch_sgs(c, 12, D(2026, 10, 6), D(2026, 10, 6)) == {}


def test_sgs_long_ranges_are_paginated(server, cal):
    server.constant_index(12, 0.04, cal, D(2014, 1, 1), D(2026, 1, 1))
    with server.client() as c:
        values = fetch_sgs(c, 12, D(2014, 1, 1), D(2026, 1, 1))
    assert server.count("sgs.12") == 3
    assert len(values) == len(cal.business_days(D(2014, 1, 1), D(2026, 1, 1)))


def test_sgs_retries_then_gives_up():
    responses = iter(
        [
            httpx.Response(502, text="<html>"),
            httpx.Response(
                200, json=[{"data": "01/10/2026", "valor": "0.05"}]
            ),
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: next(responses))
    ) as c:
        assert fetch_sgs(c, 12, D(2026, 10, 1), D(2026, 10, 1), backoff=0) == {
            D(2026, 10, 1): 0.05
        }

    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(502, text="<html>")
            )
        ) as c,
        pytest.raises(SourceError, match="SGS series 12"),
    ):
        fetch_sgs(c, 12, D(2026, 10, 1), D(2026, 10, 1), backoff=0)


def test_ipca_keyed_by_month(server):
    server.sgs[433] = {D(2026, 7, 1): 0.07, D(2026, 8, 1): -0.32}
    with server.client() as c:
        values = fetch_ipca(c, D(2026, 7, 15), D(2026, 10, 6))
    assert values == {D(2026, 7, 1): 0.07, D(2026, 8, 1): -0.32}
    assert "dataInicial=01/07/2026" in server.calls[0]


# --- Tesouro Transparente CSV -------------------------------------------


def _csv():
    return (FIXTURES / "tesouro_sample.csv").read_text(encoding="utf-8")


def test_tesouro_csv_real_values():
    prices = parse_tesouro_csv(_csv())
    ltn = prices.series("Tesouro Prefixado", D(2032, 1, 1))
    assert ltn[D(2025, 7, 30)] == 434.89
    assert ltn[D(2026, 1, 27)] == 471.44
    assert (
        prices.series("Tesouro IPCA+", D(2029, 5, 15))[D(2026, 1, 27)]
        == 3582.98
    )
    assert (
        prices.series("Tesouro Selic", D(2028, 3, 1))[D(2026, 10, 5)]
        == 20005.43
    )


def test_tesouro_csv_filters_keys():
    prices = parse_tesouro_csv(_csv(), {("Tesouro IPCA+", D(2029, 5, 15))})
    assert prices.series("Tesouro Prefixado", D(2032, 1, 1)) is None
    assert prices.series("Tesouro IPCA+", D(2029, 5, 15))
    # The index of available bonds (used for suggestions) stays complete.
    assert D(2032, 1, 1) in prices.bonds["tesouro prefixado"][1]


def test_tesouro_csv_suggestions():
    prices = parse_tesouro_csv(_csv())
    s = prices.suggestions("Tesouro Prefixado", D(2031, 1, 1))
    assert "2029-01-01" in s and "2032-01-01" in s
    assert "Tesouro IPCA+" in prices.suggestions(
        "Tesouro IPCA", D(2029, 5, 15)
    )


def test_tesouro_csv_wrong_header():
    with pytest.raises(SourceError, match="PU Venda Manha"):
        parse_tesouro_csv("Tipo Titulo;Data Vencimento;Data Base\nx;y;z\n")


def test_tesouro_csv_cache(server, tmp_path):
    cache = tmp_path / "tesouro.csv"
    with server.client() as c:
        download_tesouro_csv(c, cache)
        download_tesouro_csv(c, cache)
    assert server.count("tesourotransparente") == 1
    assert cache.read_text(encoding="utf-8") == server.tesouro_csv


# --- resgatar ------------------------------------------------------------


def _sample():
    return json.loads(
        (FIXTURES / "resgatar_sample.json").read_text(encoding="utf-8")
    )


def test_resgatar_parse_sample():
    prices = parse_resgatar(_sample())
    # The sample's Tesouro24x7 item has no price or dates: it is skipped.
    assert prices == [
        RedemptionPrice(
            "Tesouro Prefixado", D(2032, 1, 1), D(2026, 10, 6), 531.2
        )
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tesouro Prefixado 2032", "Tesouro Prefixado"),
        (
            "Tesouro Prefixado com Juros Semestrais 2035",
            "Tesouro Prefixado com Juros Semestrais",
        ),
        (
            "Tesouro Renda+ Aposentadoria Extra 2030",
            "Tesouro Renda+ Aposentadoria Extra",
        ),
        ("Tesouro IPCA+ 2029", "Tesouro IPCA+"),
        ("Tesouro Reserva", "Tesouro Reserva"),
    ],
)
def test_strip_year(name, expected):
    assert strip_year(name) == expected


def test_resgatar_walks_unknown_groups():
    item = {
        "treasuryBondName": "Tesouro IPCA+ 2029",
        "maturityDate": "2029-05-15T00:00",
        "unitaryRedemptionValue": 3986.77,
        "lastMarketPricingDate": "2026-10-05T18:00:05.677",
        "startDate": "2026-10-06T09:30:00.000",
    }
    reserva = {
        "treasuryBondName": "Tesouro Reserva 2036",
        "maturityDate": "2036-01-01T00:00",
        "unitaryRedemptionValue": 11.07,
        "lastMarketPricingDate": "2026-10-06T00:03:43.057",
    }
    data = {
        "NewGroup": [item],
        "Other": {"nested": [reserva]},
        "meta": {"version": 2},
    }
    prices = parse_resgatar(data)
    assert (
        find_redemption_price(prices, "tesouro ipca+", D(2029, 5, 15)).price
        == 3986.77
    )
    # Without startDate, the lastMarketPricingDate date applies.
    assert find_redemption_price(
        prices, "Tesouro Reserva", D(2036, 1, 1)
    ).date == D(2026, 10, 6)
    assert (
        find_redemption_price(prices, "Tesouro IPCA+", D(2035, 5, 15)) is None
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="blocked"),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={}),
    ],
)
def test_resgatar_failure_becomes_source_error(response):
    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: response)) as c,
        pytest.raises(SourceError),
    ):
        fetch_resgatar(c)


def test_resgatar_timeout_becomes_source_error():
    def time_out(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(time_out)) as c,
        pytest.raises(SourceError),
    ):
        fetch_resgatar(c)
