"""Builds each bond's series, fetching each source at most once per run."""

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from .bonds import Bond
from .business_days import BusinessCalendar
from .logger import logger
from .pricing import (
    PricingError,
    Series,
    add_months,
    ipca_series,
    percent_of_index_series,
    pre_series,
    tesouro_series,
)
from .sources import (
    SERIES_CDI,
    SERIES_SELIC,
    RedemptionPrice,
    SourceError,
    TesouroPrices,
    download_tesouro_csv,
    fetch_ipca,
    fetch_resgatar,
    fetch_sgs,
    find_redemption_price,
    parse_tesouro_csv,
)


class DataSources:
    """Loads sources on demand, covering the range of all bonds in one go."""

    def __init__(
        self,
        client: httpx.Client,
        bonds: Sequence[Bond],
        until: dt.date,
        use_resgatar: bool = True,
        tesouro_cache: Path | None = None,
    ) -> None:
        self.client = client
        self.bonds = bonds
        self.until = until
        self.use_resgatar = use_resgatar
        self.tesouro_cache = tesouro_cache
        self._memo: dict[Any, tuple[Any, SourceError | None]] = {}

    def _once(self, key: Any, fetch: Callable[[], Any]) -> Any:
        if key not in self._memo:
            try:
                self._memo[key] = (fetch(), None)
            except SourceError as e:
                self._memo[key] = (None, e)
        value, error = self._memo[key]
        if error is not None:
            raise error
        return value

    def _start(self, kind: str) -> dt.date:
        return min(b.purchase_date for b in self.bonds if b.type == kind)

    def index(self, kind: str) -> dict[dt.date, float]:
        series = {"CDI": SERIES_CDI, "SELIC": SERIES_SELIC}[kind]
        return self._once(
            series,
            lambda: fetch_sgs(
                self.client, series, self._start(kind), self.until
            ),
        )

    def ipca(self) -> dict[dt.date, float]:
        start = min(
            add_months(b.purchase_date, -b.ipca_lag)
            for b in self.bonds
            if b.type == "IPCA"
        )
        return self._once(
            "IPCA", lambda: fetch_ipca(self.client, start, self.until)
        )

    def tesouro(self) -> TesouroPrices:
        keys = {
            (b.tesouro_name, b.maturity)
            for b in self.bonds
            if b.type == "TESOURO"
        }

        def fetch() -> TesouroPrices:
            text = download_tesouro_csv(self.client, self.tesouro_cache)
            return parse_tesouro_csv(text, keys)

        return self._once("TESOURO", fetch)

    def resgatar(self) -> list[RedemptionPrice] | None:
        """Current prices; any failure is tolerated (returns None)."""
        if "RESGATAR" not in self._memo:
            try:
                value = fetch_resgatar(self.client)
            except SourceError as e:
                logger.warning(
                    "%s. Continuing without today's Tesouro price.", e
                )
                value = None
            self._memo["RESGATAR"] = (value, None)
        return self._memo["RESGATAR"][0]


def _bank_terms(bond: Bond) -> tuple[float, float]:
    """(purchase_price, rate) of a bank bond; validation guarantees both."""
    if bond.purchase_price is None or bond.rate is None:
        raise PricingError(f"{bond.type} bond without purchase_price or rate")
    return bond.purchase_price, bond.rate


def build_series(
    bond: Bond,
    data: DataSources,
    cal: BusinessCalendar,
    until: dt.date,
    today: dt.date,
    skip_purchase_date: bool = False,
) -> Series:
    start = bond.purchase_date
    end = bond.last_date(until)

    if bond.type == "PRE":
        price, rate = _bank_terms(bond)
        series = pre_series(price, rate, start, end, cal)
    elif bond.type in ("CDI", "SELIC"):
        price, rate = _bank_terms(bond)
        code = SERIES_CDI if bond.type == "CDI" else SERIES_SELIC
        source = f"SGS {code} ({bond.type}) x {rate:g}%"
        series = percent_of_index_series(
            price,
            rate,
            data.index(bond.type),
            start,
            end,
            cal,
            source,
        )
    elif bond.type == "IPCA":
        price, rate = _bank_terms(bond)
        series = ipca_series(
            price,
            rate,
            data.ipca(),
            bond.ipca_lag,
            start,
            end,
            cal,
        )
    elif bond.type == "TESOURO":
        series = _tesouro_series(bond, data, cal, start, end, today)
    else:
        raise PricingError(f"unsupported type: {bond.type}")

    if skip_purchase_date:
        series.quotes = [
            q for q in series.quotes if q.date != bond.purchase_date
        ]
    if not series.quotes:
        raise PricingError(f"no quotes between {start} and {end}")
    return series


def _tesouro_series(
    bond: Bond,
    data: DataSources,
    cal: BusinessCalendar,
    start: dt.date,
    end: dt.date,
    today: dt.date,
) -> Series:
    name = bond.tesouro_name
    prices = data.tesouro()
    csv = prices.series(name, bond.maturity)
    if csv is None:
        raise PricingError(
            f"{name} maturing on {bond.maturity} not found in the Tesouro CSV. "
            f"Closest: {prices.suggestions(name, bond.maturity)}"
        )

    warnings = []
    current_price = None
    if end == today and today not in csv and data.use_resgatar:
        found = data.resgatar()
        if found is None:
            warnings.append("no price for today (resgatar unavailable)")
        else:
            p = find_redemption_price(found, name, bond.maturity)
            if p is None:
                warnings.append("bond not found in resgatar")
            else:
                current_price = (p.date, p.price)

    series = tesouro_series(csv, start, end, cal, current_price)
    series.warnings[:0] = warnings
    return series
