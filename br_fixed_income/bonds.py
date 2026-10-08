"""Loading and validation of the bonds YAML file."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .pricing import ACCRUAL_CALENDAR, IPCA_ACCRUALS
from .tesouro_map import TESOURO_BONDS


BANK_TYPES = ("PRE", "CDI", "SELIC", "IPCA")
TYPES = (*BANK_TYPES, "TESOURO")

FIELDS = {
    "symbol",
    "description",
    "type",
    "rate",
    "purchase_price",
    "tesouro_bond",
    "maturity",
    "purchase_date",
    "end_date",
    "ipca_lag",
    "ipca_accrual",
    "use_focus_survey",
}


class InputError(Exception):
    pass


@dataclass(frozen=True)
class Bond:
    symbol: str
    type: str
    maturity: dt.date
    purchase_date: dt.date
    description: str = ""
    rate: float | None = None
    purchase_price: float | None = None
    tesouro_bond: str | None = None
    end_date: dt.date | None = None
    ipca_lag: int = 0
    ipca_accrual: str = ACCRUAL_CALENDAR
    use_focus_survey: bool = False

    @property
    def tesouro_name(self) -> str:
        if self.tesouro_bond is None:
            raise ValueError(f"{self.symbol} is not a TESOURO bond")
        return TESOURO_BONDS[self.tesouro_bond]

    def last_date(self, until: dt.date) -> dt.date:
        """Last possible date of the series: min(until, maturity, end_date)."""
        return min(
            d for d in (until, self.maturity, self.end_date) if d is not None
        )


def load_bonds(path: Path, today: dt.date) -> list[Bond]:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise InputError(f"input file not found: {path}") from None
    except yaml.YAMLError as e:
        raise InputError(f"invalid YAML in {path}: {e}") from None
    return validate_bonds(data, today)


def validate_bonds(data: Any, today: dt.date) -> list[Bond]:
    if not isinstance(data, dict) or not isinstance(data.get("bonds"), list):
        raise InputError("the input file must have a 'bonds' list")

    bonds: list[Bond] = []
    seen: set[str] = set()
    for i, item in enumerate(data["bonds"], start=1):
        b = _validate_item(item, i, today)
        if b.symbol in seen:
            raise InputError(
                f"bond #{i} ({b.symbol}): field 'symbol' is duplicated"
            )
        seen.add(b.symbol)
        bonds.append(b)
    return bonds


def _validate_item(item: Any, i: int, today: dt.date) -> Bond:
    if not isinstance(item, dict):
        raise InputError(f"bond #{i}: each bond must be a mapping of fields")

    label = str(item.get("symbol") or f"#{i}")

    def error(field: str, msg: str) -> InputError:
        return InputError(f"bond #{i} ({label}): field '{field}' {msg}")

    unknown = sorted(set(item) - FIELDS)
    if unknown:
        raise error(unknown[0], "is unknown")

    def required(field: str) -> Any:
        if item.get(field) is None:
            raise error(field, "is required")
        return item[field]

    def date_field(field: str) -> dt.date:
        v = required(field)
        if isinstance(v, dt.datetime):
            return v.date()
        if isinstance(v, dt.date):
            return v
        try:
            return dt.date.fromisoformat(str(v))
        except ValueError:
            raise error(
                field, f"must be a YYYY-MM-DD date (got: {v!r})"
            ) from None

    def positive(field: str) -> float:
        v = required(field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise error(
                field,
                f"must be numeric, with a dot as decimal separator (got: {v!r})",
            )
        if v <= 0:
            raise error(field, f"must be positive (got: {v!r})")
        return float(v)

    symbol = str(required("symbol")).strip()
    if not symbol:
        raise error("symbol", "is required")

    kind = str(required("type")).strip().upper()
    if kind not in TYPES:
        raise error(
            "type",
            f"must be one of {', '.join(TYPES)} (got: {item['type']!r})",
        )

    maturity = date_field("maturity")
    purchase_date = date_field("purchase_date")
    if purchase_date > today:
        raise error(
            "purchase_date", f"cannot be in the future ({purchase_date})"
        )
    if maturity <= purchase_date:
        raise error(
            "maturity",
            f"must be after purchase_date ({maturity} <= {purchase_date})",
        )

    end_date = None
    if item.get("end_date") is not None:
        end_date = date_field("end_date")
        if end_date < purchase_date:
            raise error(
                "end_date", f"must be on or after purchase_date ({end_date})"
            )

    kwargs: dict[str, Any] = {}
    if kind == "TESOURO":
        code = str(required("tesouro_bond")).strip().upper()
        if code not in TESOURO_BONDS:
            raise error(
                "tesouro_bond",
                f"must be one of {', '.join(TESOURO_BONDS)} (got: {item['tesouro_bond']!r})",
            )
        kwargs["tesouro_bond"] = code
    else:
        kwargs["rate"] = positive("rate")
        kwargs["purchase_price"] = positive("purchase_price")

    if item.get("ipca_lag") is not None:
        if kind != "IPCA":
            raise error("ipca_lag", "only applies to bonds of type IPCA")
        v = item["ipca_lag"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise error("ipca_lag", f"must be an integer >= 0 (got: {v!r})")
        kwargs["ipca_lag"] = v

    if item.get("ipca_accrual") is not None:
        if kind != "IPCA":
            raise error("ipca_accrual", "only applies to bonds of type IPCA")
        v = item["ipca_accrual"]
        if v not in IPCA_ACCRUALS:
            raise error(
                "ipca_accrual",
                f"must be one of {', '.join(IPCA_ACCRUALS)} (got: {v!r})",
            )
        kwargs["ipca_accrual"] = v

    if item.get("use_focus_survey") is not None:
        if kind != "IPCA":
            raise error(
                "use_focus_survey", "only applies to bonds of type IPCA"
            )
        v = item["use_focus_survey"]
        if not isinstance(v, bool):
            raise error(
                "use_focus_survey", f"must be true or false (got: {v!r})"
            )
        kwargs["use_focus_survey"] = v

    return Bond(
        symbol=symbol,
        type=kind,
        maturity=maturity,
        purchase_date=purchase_date,
        description=str(item.get("description") or ""),
        end_date=end_date,
        **kwargs,
    )
