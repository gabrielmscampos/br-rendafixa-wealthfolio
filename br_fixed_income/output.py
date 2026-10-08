"""Writing quote CSVs in Wealthfolio's import format."""

import csv
import io
import os
from collections.abc import Sequence
from pathlib import Path

from .pricing import Quote


# Wealthfolio's quote import format (Settings > Market data > Import). Required:
# symbol, date, close. Without `currency` Wealthfolio assumes USD. To change the
# format, change only COLUMNS and _row().
COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "currency",
)
CURRENCY = "BRL"
DECIMALS = 6


def _row(symbol: str, q: Quote) -> dict[str, str]:
    price = f"{q.price:.{DECIMALS}f}"
    return {
        "symbol": symbol,
        "date": q.date.isoformat(),
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": "0",
        "currency": CURRENCY,
    }


def format_csv(symbol: str, quotes: Sequence[Quote]) -> str:
    buffer = io.StringIO()
    w = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for q in quotes:
        w.writerow(_row(symbol, q))
    return buffer.getvalue()


def write_csv(path: Path, symbol: str, quotes: Sequence[Quote]) -> None:
    """Writes atomically: a failed bond never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(format_csv(symbol, quotes), encoding="utf-8", newline="")
    os.replace(tmp, path)


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [
        max(len(str(x)) for x in col)
        for col in zip(header, *rows, strict=True)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*map(str, row)) for row in rows]
    return "\n".join(s.rstrip() for s in lines)
