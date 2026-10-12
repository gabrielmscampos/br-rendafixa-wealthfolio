"""Command line interface."""

import argparse
import datetime as dt
from pathlib import Path

from .bonds import InputError, load_bonds
from .business_days import today_in_brazil
from .generator import generate
from .logger import logger, setup_logging
from .output import table, write_csv
from .sources import default_tesouro_cache, new_client


def _date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date: {text!r} (use YYYY-MM-DD)"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate-quotes",
        description="Generates historical quote CSVs for Brazilian fixed income, for import into Wealthfolio.",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=Path("bonds.yaml"),
        help="bonds YAML file (default: %(default)s)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("quotes"),
        help="CSV output directory (default: ./%(default)s)",
    )
    p.add_argument(
        "--until",
        type=_date,
        help="last date of the series, YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--symbol",
        nargs="+",
        metavar="SYMBOL",
        help="process only the given bonds",
    )
    p.add_argument(
        "--no-tesouro-api",
        action="store_true",
        help="do not query Tesouro Direto's 'resgatar'",
    )
    p.add_argument(
        "--skip-purchase-date",
        action="store_true",
        help="omit the purchase date row",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="write no files; only print each bond's last price",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="download the Tesouro CSV even if a recent cache exists",
    )
    p.add_argument("--verbose", action="store_true", help="detailed log")
    return p


def main(argv: list[str] | None = None, today: dt.date | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    today = today or today_in_brazil()
    until = args.until or today
    if until > today:
        logger.error("--until cannot be a future date (%s)", until)
        return 2

    try:
        bonds = load_bonds(args.input, today)
    except InputError as e:
        logger.error("%s", e)
        return 2
    if args.symbol:
        unknown = sorted(set(args.symbol) - {b.symbol for b in bonds})
        if unknown:
            logger.error(
                "symbol(s) not found in %s: %s", args.input, ", ".join(unknown)
            )
            return 2
        bonds = [b for b in bonds if b.symbol in args.symbol]
    for b in bonds:
        if b.purchase_date > until:
            logger.info("%s: purchased after %s, skipped", b.symbol, until)
    bonds = [b for b in bonds if b.purchase_date <= until]
    if not bonds:
        logger.error("no bond with purchase_date up to %s", until)
        return 2

    with new_client() as client:
        results = generate(
            client,
            bonds,
            until,
            today,
            use_resgatar=not args.no_tesouro_api,
            tesouro_cache=None if args.no_cache else default_tesouro_cache(),
            skip_purchase_date=args.skip_purchase_date,
        )

    rows: list[tuple[str, ...]] = []
    for r in results:
        if r.series is None:
            rows.append(
                (r.bond.symbol, "-", "-", "-", "-", "-", f"ERROR: {r.error}")
            )
            continue
        quotes = r.series.quotes
        if not args.check:
            write_csv(
                args.output / f"{r.bond.symbol}.csv", r.bond.symbol, quotes
            )
        rows.append(
            (
                r.bond.symbol,
                str(len(quotes)),
                quotes[0].date.isoformat(),
                quotes[-1].date.isoformat(),
                f"{quotes[-1].price:.6f}",
                quotes[-1].source,
                "; ".join(r.series.warnings),
            )
        )

    print()
    print(
        table(
            (
                "symbol",
                "rows",
                "first",
                "last",
                "last_price",
                "source",
                "warnings",
            ),
            rows,
        )
    )
    if args.check:
        print("\n--check mode: no files written.")
    else:
        print(f"\nCSVs written to {args.output}/")
    return 1 if any(r.series is None for r in results) else 0
