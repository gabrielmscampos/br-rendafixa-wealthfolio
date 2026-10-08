import datetime as dt

import pytest
import yaml

from br_fixed_income.cli import main
from br_fixed_income.sources import SERIES_CDI


TODAY = dt.date(2026, 1, 30)

BONDS = [
    {
        "symbol": "LTN_20320101",
        "type": "TESOURO",
        "tesouro_bond": "PRE",
        "maturity": dt.date(2032, 1, 1),
        "purchase_date": dt.date(2026, 1, 27),
    },
    {
        "symbol": "CDB_CDI",
        "type": "CDI",
        "rate": 100,
        "purchase_price": 1000,
        "maturity": dt.date(2027, 1, 4),
        "purchase_date": dt.date(2026, 1, 26),
    },
]


@pytest.fixture
def env(server, cal, tmp_path, monkeypatch):
    server.constant_index(
        SERIES_CDI, 0.05, cal, dt.date(2026, 1, 1), dt.date(2026, 1, 29)
    )
    server.resgatar = 503
    monkeypatch.setattr("br_fixed_income.cli.new_client", server.client)

    def run(*args, bonds=BONDS):
        input_file = tmp_path / "bonds.yaml"
        input_file.write_text(
            yaml.safe_dump({"bonds": bonds}), encoding="utf-8"
        )
        return main(
            [
                "--input",
                str(input_file),
                "--output",
                str(tmp_path / "out"),
                "--no-cache",
                *args,
            ],
            today=TODAY,
        )

    return run, tmp_path / "out"


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_writes_one_csv_per_bond(env, capsys):
    run, out_dir = env
    assert run() == 0
    ltn = _lines(out_dir / "LTN_20320101.csv")
    assert ltn[0] == "symbol,date,open,high,low,close,volume,currency"
    assert (
        ltn[1]
        == "LTN_20320101,2026-01-27,471.440000,471.440000,471.440000,471.440000,0,BRL"
    )
    assert ltn[-1].startswith("LTN_20320101,2026-01-29,478.070000")
    cdi = _lines(out_dir / "CDB_CDI.csv")
    assert cdi[1].startswith("CDB_CDI,2026-01-26,1000.000000")
    assert cdi[-1].split(",")[1] == "2026-01-30"
    out = capsys.readouterr().out
    assert "LTN_20320101" in out and "CDB_CDI" in out


def test_check_writes_nothing(env, capsys):
    run, out_dir = env
    assert run("--check") == 0
    assert not out_dir.exists()
    out = capsys.readouterr().out
    assert "478.070000" in out and "Tesouro Transparente CSV" in out


def test_skip_purchase_date(env):
    run, out_dir = env
    assert run("--skip-purchase-date") == 0
    assert (
        _lines(out_dir / "LTN_20320101.csv")[1].split(",")[1] == "2026-01-28"
    )


def test_symbol_filters(env, server):
    run, out_dir = env
    assert run("--symbol", "CDB_CDI") == 0
    assert [p.name for p in out_dir.iterdir()] == ["CDB_CDI.csv"]
    assert server.count("tesourotransparente") == 0


def test_unknown_symbol(env):
    run, _ = env
    assert run("--symbol", "DOES_NOT_EXIST") == 2


def test_failed_bond_writes_no_file_and_others_continue(env, capsys):
    run, out_dir = env
    broken = dict(BONDS[0], symbol="LTN_WRONG", maturity=dt.date(2031, 1, 1))
    assert run(bonds=[*BONDS, broken]) == 1
    assert (out_dir / "CDB_CDI.csv").exists()
    assert (out_dir / "LTN_20320101.csv").exists()
    assert not (out_dir / "LTN_WRONG.csv").exists()
    assert "ERROR" in capsys.readouterr().out


def test_until_in_the_past(env):
    run, out_dir = env
    assert run("--until", "2026-01-28") == 0
    assert (
        _lines(out_dir / "LTN_20320101.csv")[-1].split(",")[1] == "2026-01-28"
    )
    assert _lines(out_dir / "CDB_CDI.csv")[-1].split(",")[1] == "2026-01-28"


def test_future_until_is_an_error(env):
    run, _ = env
    assert run("--until", "2026-02-02") == 2


def test_invalid_input(env):
    run, _ = env
    assert run(bonds=[dict(BONDS[1], rate=None)]) == 2
