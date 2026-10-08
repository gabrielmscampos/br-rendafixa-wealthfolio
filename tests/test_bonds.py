import datetime as dt
from pathlib import Path

import pytest

from br_fixed_income.bonds import InputError, load_bonds, validate_bonds


TODAY = dt.date(2026, 10, 6)
EXAMPLE = Path(__file__).parent.parent / "bonds.example.yaml"


def _cdi(**extra):
    base = {
        "symbol": "CDB1",
        "type": "CDI",
        "rate": 110,
        "purchase_price": 1000,
        "maturity": dt.date(2027, 1, 4),
        "purchase_date": dt.date(2026, 1, 5),
    }
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def test_example_loads():
    bonds = load_bonds(EXAMPLE, TODAY)
    assert [b.type for b in bonds] == ["TESOURO", "TESOURO", "CDI", "IPCA"]
    assert bonds[0].tesouro_name == "Tesouro Prefixado"
    assert bonds[2].rate == 114
    assert bonds[3].ipca_lag == 1


@pytest.mark.parametrize(
    ("item", "field"),
    [
        (_cdi(rate=None), "rate"),
        (_cdi(purchase_price=None), "purchase_price"),
        (_cdi(rate=-1), "rate"),
        (_cdi(purchase_price=0), "purchase_price"),
        (_cdi(rate="114%"), "rate"),
        (_cdi(type="LCA"), "type"),
        (_cdi(purchase_date=dt.date(2026, 10, 7)), "purchase_date"),
        (_cdi(maturity=dt.date(2026, 1, 5)), "maturity"),
        (_cdi(maturity="05/01/2027"), "maturity"),
        (_cdi(end_date=dt.date(2025, 12, 1)), "end_date"),
        (_cdi(ipca_lag=1), "ipca_lag"),
        (_cdi(maturiy=dt.date(2027, 1, 4)), "maturiy"),
        (_cdi(symbol=None), "symbol"),
        (_cdi(type="TESOURO"), "tesouro_bond"),
        (_cdi(type="TESOURO", tesouro_bond="LTN"), "tesouro_bond"),
        (_cdi(type="IPCA", ipca_lag=-1), "ipca_lag"),
        (_cdi(ipca_accrual="anniversary"), "ipca_accrual"),
        (_cdi(type="IPCA", ipca_accrual="monthly"), "ipca_accrual"),
        (_cdi(use_focus_survey=True), "use_focus_survey"),
        (_cdi(type="IPCA", use_focus_survey="yes"), "use_focus_survey"),
        (_cdi(type="IPCA", use_focus_survey=1), "use_focus_survey"),
    ],
)
def test_validation_names_bond_and_field(item, field):
    with pytest.raises(InputError) as e:
        validate_bonds({"bonds": [item]}, TODAY)
    assert f"'{field}'" in str(e.value)
    assert "#1" in str(e.value)


def test_use_focus_survey():
    (b,) = validate_bonds({"bonds": [_cdi(type="IPCA")]}, TODAY)
    assert b.use_focus_survey is False
    item = _cdi(type="IPCA", use_focus_survey=True)
    (b,) = validate_bonds({"bonds": [item]}, TODAY)
    assert b.use_focus_survey is True


def test_ipca_accrual():
    (b,) = validate_bonds({"bonds": [_cdi(type="IPCA")]}, TODAY)
    assert b.ipca_accrual == "calendar"
    item = _cdi(type="IPCA", ipca_accrual="anniversary")
    (b,) = validate_bonds({"bonds": [item]}, TODAY)
    assert b.ipca_accrual == "anniversary"


def test_unique_symbol():
    with pytest.raises(InputError, match="'symbol' is duplicated"):
        validate_bonds({"bonds": [_cdi(), _cdi()]}, TODAY)


def test_tesouro_needs_no_rate_or_price():
    item = {
        "symbol": "LTN",
        "type": "tesouro",
        "tesouro_bond": "pre",
        "maturity": "2032-01-01",
        "purchase_date": "2025-07-30",
    }
    (b,) = validate_bonds({"bonds": [item]}, TODAY)
    assert (b.type, b.tesouro_bond, b.maturity) == (
        "TESOURO",
        "PRE",
        dt.date(2032, 1, 1),
    )


def test_last_date():
    (b,) = validate_bonds(
        {"bonds": [_cdi(end_date=dt.date(2026, 6, 1))]}, TODAY
    )
    assert b.last_date(TODAY) == dt.date(2026, 6, 1)
    (b,) = validate_bonds({"bonds": [_cdi()]}, TODAY)
    assert b.last_date(TODAY) == TODAY
    assert b.last_date(dt.date(2027, 3, 1)) == dt.date(2027, 1, 4)


def test_missing_file(tmp_path):
    with pytest.raises(InputError, match="not found"):
        load_bonds(tmp_path / "nothing.yaml", TODAY)


def test_no_bonds_list():
    with pytest.raises(InputError, match="bonds"):
        validate_bonds({"other": []}, TODAY)
