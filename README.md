# Brazilian fixed income quotes for Wealthfolio

Command line script that generates historical quote CSVs for Brazilian fixed income bonds, to be imported manually into [Wealthfolio](https://wealthfolio.app) for assets with manual quotes.

- **Tesouro Direto**: market price (`PU Venda Manha`) from the official Tesouro Transparente CSV, plus the current day's price from the `resgatar` endpoint of the Tesouro Direto website.
- **Bank bonds priced on the curve** (CDB, LCI, LCA etc.): fixed rate (`PRE`), % of CDI (`CDI`), % of Selic (`SELIC`) and IPCA + rate (`IPCA`), computed from the purchase price with Banco Central data (SGS, and optionally the Focus survey for unpublished IPCA months).

All prices are gross of income tax. Bonds with periodic interest payments are not supported.

## Installation

Requires Python 3.12+.

As a tool, with pip (inside a virtual environment) or with [uv](https://docs.astral.sh/uv/), from a clone of this repository:

```sh
pip install .
# or
uv tool install .
```

Either one installs the `generate-quotes` command.

For development:

```sh
uv sync --all-groups
```

## Filling in `bonds.yaml`

Copy the example and adjust it:

```sh
cp bonds.example.yaml bonds.yaml
```

| Field | Required | Description |
|---|---|---|
| `symbol` | yes | Asset symbol in Wealthfolio. Becomes the file name and the CSV `symbol` column. |
| `description` | no | Free text. |
| `type` | yes | `PRE`, `CDI`, `SELIC`, `IPCA` or `TESOURO`. |
| `rate` | bank bonds | PRE: % p.a. · CDI: % of CDI · SELIC: % of Selic · IPCA: spread % p.a. Always the rate of your purchase. |
| `purchase_price` | bank bonds | Unit price paid (1000 for purchases at issuance). |
| `tesouro_bond` | `TESOURO` | `PRE`, `PREJ`, `IPCA`, `IPCAJ`, `SELIC`, `IGPMJ`, `RENDA`, `EDUCA` or `RESERVA`. |
| `maturity` | yes | Actual maturity date. For Educa+ and Renda+ the year in the name is not the maturity. |
| `purchase_date` | yes | First purchase; the series starts here. |
| `end_date` | no | Ends the series before maturity (e.g. issuer liquidation, payout by the FGC). |
| `ipca_lag` | no | `IPCA` only: index lag in months (default 0). With lag N, each period earns the IPCA of N months earlier. See [How the IPCA is applied](#how-the-ipca-is-applied). |
| `ipca_accrual` | no | `IPCA` only: how the IPCA is split into monthly periods, `calendar` (default) or `anniversary`. See [How the IPCA is applied](#how-the-ipca-is-applied). |
| `use_focus_survey` | no | `IPCA` only: `true` to project unpublished IPCA months with the BCB Focus survey median instead of repeating the last published IPCA (default `false`). See [Calibrating IPCA](#calibrating-ipca). |

Tesouro codes and broker acronyms: `PRE` = LTN, `PREJ` = NTN-F, `IPCA` = NTN-B Principal, `IPCAJ` = NTN-B, `SELIC` = LFT, `IGPMJ` = NTN-C.

For Tesouro, use one symbol per bond, even with several purchases. For bank bonds, each purchase with a different rate or date is a different curve and needs its own symbol, both in Wealthfolio and in the YAML.

### How the IPCA is applied

IBGE publishes one IPCA figure per month (for example, 0.88% for March 2026). The bond's IPCA factor is the product of those monthly figures over the time you held it. Two details vary between banks: where each monthly period starts and ends (`ipca_accrual`), and which month's IPCA a period earns (`ipca_lag`).

**Partial periods.** When only part of a period has passed, the bond earns only that part of the month's IPCA, measured in business days. If 10 of a period's 21 business days have passed and the IPCA is 0.88%, the bond has earned `1.0088 ^ (10 / 21)`, about 0.42%. A complete period earns the whole 0.88%.

**`ipca_accrual: calendar`** (default): the periods are calendar months, from the 1st of one month to the 1st of the next. Each month earns its own IPCA. A bond bought mid-month therefore starts with a partial month and, on any date other than the 1st, ends with one.

**`ipca_accrual: anniversary`**: the periods start on the day of the month you bought the bond (its "anniversary"), so a bond bought on the 21st has periods from the 21st of one month to the 21st of the next. Each period earns the IPCA of the month it starts in. Only the current period is partial. When the purchase day does not exist in a month (the 31st, say), that month's anniversary falls on its last day.

Example: an LCA bought on 2025-05-21 and held to maturity on 2026-05-21, with `ipca_lag: 0`:

| | `calendar` | `anniversary` |
|---|---|---|
| Periods | May 21-31 (partial), June to April (whole months), May 1-21 2026 (partial) | May 21-June 21, June 21-July 21, ..., April 21-May 21 2026: 12 whole periods |
| IPCA earned | part of May 2025, June 2025 to April 2026, part of May 2026 | May 2025 to April 2026, each one whole |

**`ipca_lag: N`** shifts the IPCA back N months. With `calendar` and lag 1, October earns September's IPCA. With `anniversary` and lag 1, the period starting on October 21 earns September's IPCA.

### Calibrating IPCA

Banks use different IPCA conventions. Run with `--check --until <statement date>` and compare the last price with the "Preço R$" column of your broker statement, varying `ipca_accrual`, `ipca_lag` and `use_focus_survey`. Start with `ipca_accrual: anniversary` and `ipca_lag: 0`.

A bond that has already matured is the cleanest test, because every IPCA month it needs is published and nothing is projected. For the LCA above, BTG reports R$ 1,120.434076 at maturity:

| Setting | Price | Difference |
|---|---|---|
| `calendar`, `ipca_lag: 0` | 1,122.849736 | +2.415660 |
| `calendar`, `ipca_lag: 1` | 1,119.649390 | -0.784686 |
| `anniversary`, `ipca_lag: 0` | 1,120.436327 | +0.002251 |
| `anniversary`, `ipca_lag: 1` | 1,117.765177 | -2.668899 |

The remaining R$ 0.002 may come from rounding, since the BCB series used here gives the IPCA with two decimals.

For a bond still running, the recent months are projected (see below), so differences of a few reais are expected. For the Pine CDB in the example, against the BTG statement of 2026-10-05 (R$ 1,037.88):

| Setting | Price | Difference |
|---|---|---|
| `calendar`, `ipca_lag: 0` | 1,026.90 | -10.98 |
| `calendar`, `ipca_lag: 0`, `use_focus_survey: true` | 1,037.01 | -0.87 |
| `calendar`, `ipca_lag: 1` | 1,036.45 | -1.43 |
| `calendar`, `ipca_lag: 1`, `use_focus_survey: true` | 1,037.36 | -0.52 |
| `anniversary`, `ipca_lag: 0` | 1,033.27 | -4.61 |
| `anniversary`, `ipca_lag: 0`, `use_focus_survey: true` | 1,037.35 | -0.53 |
| `anniversary`, `ipca_lag: 1` | 1,043.53 | +5.65 |

IPCA projection: IBGE publishes each month's IPCA around the 10th of the following month, so the most recent months are usually not published yet. Those months are projected in one of two ways, and the summary table lists them in a warning:

- **Default:** repeat the **last published IPCA**. This is a plain repeat, not a forecast. Warning: `IPCA projected (last published) for 09/2026, 10/2026`.
- **`use_focus_survey: true`:** use the median IPCA expectation for that month from the BCB [Focus survey](https://www.bcb.gov.br/publicacoes/focus) (30-day respondent base, as in the weekly Focus report), taken from the latest survey on or before `--until`. So with an old `--until` you get the forecast as it stood on that date. Warning: `IPCA projected (Focus survey median) for 09/2026, 10/2026`. If the survey cannot be fetched or has no figure for a month, that month falls back to repeating the last published IPCA, with a log warning.

Keep in mind:

- With `ipca_lag: 0`, the current month and often the previous one are projected. A higher lag needs fewer projected months.
- Prices on projected months are provisional. Running the script again after IBGE publishes replaces the projection with the real figure, including in the history.
- Without the Focus survey, an unusual last print distorts recent prices. For example, repeating a deflation month (such as -0.32%) makes the price lower than a bank would show.
- Banks and brokers usually use a forecast for the current month, such as ANBIMA's IPCA projection or the Focus survey median, which is why `use_focus_survey: true` tends to get closer to the statement.

## Running

```sh
generate-quotes            # reads bonds.yaml and writes ./quotes/{symbol}.csv
generate-quotes --check    # only prints each bond's last price
```

`python -m br_fixed_income` is equivalent to `generate-quotes`. In a development checkout, prefix either with `uv run`.

| Option | Description |
|---|---|
| `--input` | Bonds YAML file (default `bonds.yaml`). |
| `--output` | CSV output directory (default `./quotes`). |
| `--until YYYY-MM-DD` | Last date of the series (default: today). Useful to compare with an older statement. |
| `--symbol S1 S2 ...` | Process only the given bonds. |
| `--no-tesouro-api` | Do not query Tesouro Direto's `resgatar`. |
| `--skip-purchase-date` | Omit the purchase date row. |
| `--check` | Write no files; print last date, last price and source. |
| `--no-cache` | Download the Tesouro CSV even if a recent cache exists. |
| `--verbose` | Detailed log. |

At the end the script prints a table with each bond's row count, first and last date, last price, source and warnings. The exit code is 1 if any bond failed and 2 on input errors. A failed bond writes no file; the others proceed normally.

Behavior:

- The series runs from `purchase_date` to `min(today, maturity, end_date)`, on business days only (ANBIMA/B3 calendar). There are never future dates: to update, run it again.
- CDI and Selic: a day's price uses the rates up to the previous business day. If the BCB has not yet published yesterday's rate, the series ends on the last day that can be computed.
- IPCA: a month without a published IPCA is projected, by repeating the last published IPCA or, with `use_focus_survey: true`, from the Focus survey, with a warning (see [Calibrating IPCA](#calibrating-ipca)).
- Tesouro: the Tesouro Transparente CSV (about 14 MB) is cached for 6 hours in `~/.cache/br-rendafixa-wealthfolio-generator/`. Today's price comes from `resgatar` (one call per run); any failure there is tolerated and the CSV always takes precedence. Tesouro Direto publishes no prices on 12-24 and 12-31, hence the warning about days without a price on those dates.

## Importing into Wealthfolio

1. In **Settings > Market data**, choose **Import prices from CSV**.
2. Select one of the files in `./quotes/`, validate and import.

The generated format is Wealthfolio's quote import format:

```
symbol,date,open,high,low,close,volume,currency
CDB626FG7PK,2026-06-30,1000.000000,1000.000000,1000.000000,1000.000000,0,BRL
```

`open`, `high` and `low` repeat `close`; `volume` is 0. The `currency` column is needed: without it Wealthfolio assumes USD. The `symbol` must match the symbol of the registered asset. If a Wealthfolio version requires another layout, adjust `COLUMNS` and `_row()` in `br_fixed_income/output.py`.

Wealthfolio already creates a manual quote for every buy and sell with a price. For Tesouro, the CSV quote on the purchase date (`PU Venda`) is slightly lower than the price paid, because of the spread. On import, quotes with the same symbol and date overwrite existing ones. Use `--skip-purchase-date` if you prefer to keep the trade price.

## Tests

```sh
uv run pytest                   # unit tests, no network
uv run pytest -m integration    # real calls to SGS, Focus, Tesouro Transparente and resgatar
```

The integration tests check the Tesouro prices against the official CSV and the CDI CDBs against the BTG statement of 2026-10-05 (R$ 0.01 tolerance).

## Date of the `resgatar` price

The `resgatar` price is stored on the `startDate` date. This was confirmed against the official CSV: the prices `resgatar` returned on 2026-10-06 (LTN 2032 R$ 533.66, Tesouro IPCA+ 2029 R$ 3,988.69, Tesouro Selic 2028 R$ 20,015.51) are exactly the `PU Venda Manha` the CSV later published for 2026-10-06.

The Tesouro Direto website states in `robots.txt` that it does not allow automated access. The script makes at most one call per run; use `--no-tesouro-api` to turn it off.

## License

Copyright (C) 2026 Gabriel Moreira

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [GNU General Public License](LICENSE) for more details.
