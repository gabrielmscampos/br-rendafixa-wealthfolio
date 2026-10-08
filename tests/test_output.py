import datetime as dt

from br_fixed_income.output import table, write_csv
from br_fixed_income.pricing import Quote


def test_csv_format(tmp_path):
    path = tmp_path / "out" / "CDB1.csv"
    write_csv(
        path,
        "CDB1",
        [
            Quote(dt.date(2025, 11, 26), 1000, "x"),
            Quote(dt.date(2025, 11, 27), 1234567.8912345678, "x"),
        ],
    )
    assert path.read_bytes().decode("utf-8") == (
        "symbol,date,open,high,low,close,volume,currency\n"
        "CDB1,2025-11-26,1000.000000,1000.000000,1000.000000,1000.000000,0,BRL\n"
        "CDB1,2025-11-27,1234567.891235,1234567.891235,1234567.891235,1234567.891235,0,BRL\n"
    )
    assert not list(path.parent.glob("*.tmp"))


def test_table_aligns_columns():
    text = table(("a", "bb"), [("xxx", 1), ("y", 22)])
    assert text.splitlines() == ["a    bb", "---  --", "xxx  1", "y    22"]
