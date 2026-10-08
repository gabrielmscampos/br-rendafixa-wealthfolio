"""Access to the public data sources: BCB's SGS and Tesouro Direto."""

import csv
import datetime as dt
import difflib
import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .logger import logger
from .tesouro_map import normalize_name, strip_year


USER_AGENT = "br-rendafixa-wealthfolio-generator/0.1"

SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados"
SERIES_CDI = 12
SERIES_SELIC = 11
SERIES_IPCA = 433
# SGS limits daily series to 10 years per request and rejects windows that
# touch the limit; 5 years leaves some slack.
SGS_WINDOW = dt.timedelta(days=5 * 365)

TESOURO_CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
)
TESOURO_CSV_COLUMNS = (
    "Tipo Titulo",
    "Data Vencimento",
    "Data Base",
    "PU Venda Manha",
)

RESGATAR_URL = "https://www.tesourodireto.com.br/o/rentabilidade/resgatar"


class SourceError(Exception):
    pass


def new_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    )


def _parse_br_date(text: str) -> dt.date:
    return dt.datetime.strptime(text.strip(), "%d/%m/%Y").date()


# --------------------------------------------------------------------------
# Banco Central (SGS)
# --------------------------------------------------------------------------


def fetch_sgs(
    client: httpx.Client,
    series: int,
    start: dt.date,
    end: dt.date,
    attempts: int = 3,
    backoff: float = 1.0,
) -> dict[dt.date, float]:
    """Series values in [start, end], keyed by date."""
    values: dict[dt.date, float] = {}
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + SGS_WINDOW)
        for item in _fetch_sgs_window(
            client, series, window_start, window_end, attempts, backoff
        ):
            try:
                values[_parse_br_date(item["data"])] = float(item["valor"])
            except (KeyError, TypeError, ValueError):
                raise SourceError(
                    f"SGS {series}: unexpected item in response: {item!r}"
                ) from None
        window_start = window_end + dt.timedelta(days=1)
    logger.debug(
        "SGS %s: %d values between %s and %s", series, len(values), start, end
    )
    return values


def _fetch_sgs_window(
    client: httpx.Client,
    series: int,
    start: dt.date,
    end: dt.date,
    attempts: int,
    backoff: float,
) -> list[dict[str, Any]]:
    # The query is built by hand to keep the slashes in the dates unescaped.
    url = (
        SGS_URL.format(series=series)
        + f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
    )
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url)
            body = resp.json()
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}: {e}"
        except ValueError:
            last_error = f"HTTP {resp.status_code}, response is not JSON"
        else:
            if resp.status_code == 200 and isinstance(body, list):
                return body
            # Window without data: 404 with {"erro": {... "Value(s) not found"}}.
            if resp.status_code == 404 and "not found" in str(body).lower():
                return []
            last_error = f"HTTP {resp.status_code}: {str(body)[:200]}"
        logger.debug(
            "SGS %s (%s to %s), attempt %d: %s",
            series,
            start,
            end,
            attempt,
            last_error,
        )
        if attempt < attempts:
            time.sleep(backoff * attempt)
    raise SourceError(
        f"failed to fetch SGS series {series} from {start} to {end}: {last_error}"
    )


def fetch_ipca(
    client: httpx.Client, start: dt.date, end: dt.date, **kwargs: Any
) -> dict[dt.date, float]:
    """Monthly IPCA (% per month), keyed by the first day of the reference month."""
    start = start.replace(day=1)
    return {
        d.replace(day=1): v
        for d, v in fetch_sgs(
            client, SERIES_IPCA, start, end, **kwargs
        ).items()
    }


# --------------------------------------------------------------------------
# Tesouro Transparente (price and rate CSV)
# --------------------------------------------------------------------------


@dataclass
class TesouroPrices:
    # (normalized name, maturity) -> {base date: PU Venda Manha}
    prices: dict[tuple[str, dt.date], dict[dt.date, float]] = field(
        default_factory=dict
    )
    # normalized name -> (original name, available maturities)
    bonds: dict[str, tuple[str, set[dt.date]]] = field(default_factory=dict)

    def series(
        self, name: str, maturity: dt.date
    ) -> dict[dt.date, float] | None:
        return self.prices.get((normalize_name(name), maturity))

    def suggestions(self, name: str, maturity: dt.date, limit: int = 3) -> str:
        key = normalize_name(name)
        if key in self.bonds:
            candidates = [key]
        else:
            candidates = difflib.get_close_matches(
                key, list(self.bonds), n=limit, cutoff=0.4
            )
        if not candidates:
            return "no similar bond in the CSV"
        parts = []
        for c in candidates:
            original, maturities = self.bonds[c]
            nearest = sorted(
                maturities, key=lambda m: abs((m - maturity).days)
            )[:limit]
            parts.append(
                f"{original} (maturities: {', '.join(m.isoformat() for m in sorted(nearest))})"
            )
        return "; ".join(parts)


def download_tesouro_csv(
    client: httpx.Client,
    cache: Path | None = None,
    max_age: dt.timedelta = dt.timedelta(hours=6),
) -> str:
    if cache is not None and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < max_age.total_seconds():
            logger.info(
                "Using cached Tesouro CSV (%s, %.0f min old)", cache, age / 60
            )
            return cache.read_text(encoding="utf-8")

    logger.info("Downloading the Tesouro Transparente price CSV...")
    try:
        resp = client.get(
            TESOURO_CSV_URL, timeout=httpx.Timeout(30.0, read=180.0)
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceError(
            f"failed to download the Tesouro Transparente CSV: {e}"
        ) from None
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = resp.content.decode("latin-1")
    _check_tesouro_header(text.partition("\n")[0])

    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, cache)
        except OSError as e:
            logger.warning("Could not write the Tesouro CSV cache: %s", e)
    return text


def _check_tesouro_header(line: str) -> list[str]:
    header = [c.strip() for c in line.strip().split(";")]
    missing = [c for c in TESOURO_CSV_COLUMNS if c not in header]
    if missing:
        raise SourceError(
            f"Tesouro CSV is missing the expected columns: {', '.join(missing)}"
        )
    return header


def _parse_br_number(text: str) -> float:
    return float(text.strip().replace(".", "").replace(",", "."))


def parse_tesouro_csv(
    text: str, keys: set[tuple[str, dt.date]] | None = None
) -> TesouroPrices:
    """Parses the CSV. Keeps prices only for the requested (name, maturity) keys, or all if None."""
    wanted = (
        None if keys is None else {(normalize_name(n), m) for n, m in keys}
    )
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        header = _check_tesouro_header(";".join(next(reader)))
    except StopIteration:
        raise SourceError("Tesouro CSV is empty") from None
    i_name, i_maturity, i_base, i_price = (
        header.index(c) for c in TESOURO_CSV_COLUMNS
    )

    result = TesouroPrices()
    for n, row in enumerate(reader, start=2):
        if not row:
            continue
        try:
            name = row[i_name].strip()
            key = (normalize_name(name), _parse_br_date(row[i_maturity]))
            if key[0] not in result.bonds:
                result.bonds[key[0]] = (name, set())
            result.bonds[key[0]][1].add(key[1])
            if wanted is not None and key not in wanted:
                continue
            price = _parse_br_number(row[i_price])
            base = _parse_br_date(row[i_base])
        except (IndexError, ValueError):
            logger.debug("Tesouro CSV: skipped line %d: %r", n, row)
            continue
        if price > 0:
            result.prices.setdefault(key, {})[base] = price
    return result


# --------------------------------------------------------------------------
# Tesouro Direto ("resgatar" endpoint, current price only)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RedemptionPrice:
    name: str  # name without the trailing year, e.g. "Tesouro Prefixado"
    maturity: dt.date
    date: dt.date
    price: float


def fetch_resgatar(
    client: httpx.Client, timeout: float = 10.0
) -> list[RedemptionPrice]:
    try:
        resp = client.get(RESGATAR_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SourceError(
            f"failed to query Tesouro Direto's 'resgatar': {e}"
        ) from None
    prices = parse_resgatar(data)
    if not prices:
        raise SourceError("'resgatar' response has no recognizable bond")
    return prices


def parse_resgatar(data: Any) -> list[RedemptionPrice]:
    """Extracts prices from every group in the response, including unknown groups."""
    prices: list[RedemptionPrice] = []
    for item in _named_items(data):
        try:
            price = float(item["unitaryRedemptionValue"])
            # The price is for the session in startDate: on 2026-10-06 Tesouro Selic
            # already carried one day of interest over the CSV's PU Venda for 10-05.
            # Tesouro24x7 items have no startDate; there lastMarketPricingDate is the current day.
            date = dt.datetime.fromisoformat(
                item.get("startDate") or item["lastMarketPricingDate"]
            ).date()
            maturity = dt.datetime.fromisoformat(item["maturityDate"]).date()
        except (KeyError, TypeError, ValueError):
            logger.debug("resgatar: skipped item: %r", item)
            continue
        if price > 0:
            prices.append(
                RedemptionPrice(
                    strip_year(item["treasuryBondName"]), maturity, date, price
                )
            )
    return prices


def _named_items(data: Any):
    if isinstance(data, dict):
        if isinstance(data.get("treasuryBondName"), str):
            yield data
            return
        for value in data.values():
            yield from _named_items(value)
    elif isinstance(data, list):
        for value in data:
            yield from _named_items(value)


def find_redemption_price(
    prices: list[RedemptionPrice], name: str, maturity: dt.date
) -> RedemptionPrice | None:
    target = normalize_name(name)
    for p in prices:
        if normalize_name(p.name) == target and p.maturity == maturity:
            return p
    return None
