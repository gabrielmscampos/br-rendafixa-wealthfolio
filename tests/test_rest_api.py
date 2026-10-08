import datetime as dt
import threading
from pathlib import Path

import pytest
import yaml


pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from br_fixed_income.sources import default_tesouro_cache
from rest_api.app import create_app
from rest_api.config import ConfigError, Settings
from rest_api.service import QuoteService, RefreshScheduler


TODAY = dt.date(2026, 1, 30)

PRE = {
    "symbol": "CDB_PRE",
    "type": "PRE",
    "rate": 12,
    "purchase_price": 1000,
    "maturity": dt.date(2027, 1, 4),
    "purchase_date": dt.date(2026, 1, 26),
}
LTN = {
    "symbol": "LTN_20320101",
    "type": "TESOURO",
    "tesouro_bond": "PRE",
    "maturity": dt.date(2032, 1, 1),
    "purchase_date": dt.date(2026, 1, 27),
}
# Not in the Tesouro CSV sample, so pricing fails.
BROKEN = dict(LTN, symbol="LTN_WRONG", maturity=dt.date(2031, 1, 1))


@pytest.fixture
def bonds_file(tmp_path):
    path = tmp_path / "bonds.yaml"

    def write(*bonds):
        path.write_text(yaml.safe_dump({"bonds": list(bonds)}), "utf-8")

    write(PRE, LTN)
    return path, write


@pytest.fixture
def service(server, bonds_file, tmp_path):
    server.resgatar = 503
    settings = Settings(bonds_file=bonds_file[0], output_dir=tmp_path / "out")
    return QuoteService(settings, server.client, lambda: TODAY)


@pytest.fixture
def api(service):
    with TestClient(create_app(service.settings, service, False)) as client:
        yield client


# --- Service -------------------------------------------------------------


def test_refresh_builds_every_bond_and_writes_csvs(service, tmp_path):
    assert service.refresh()
    states = {s.symbol: s for s in service.states()}
    assert set(states) == {"CDB_PRE", "LTN_20320101"}
    assert states["LTN_20320101"].quotes[0].price == 471.44
    assert states["CDB_PRE"].quotes[-1].date == TODAY
    assert (tmp_path / "out" / "CDB_PRE.csv").exists()
    assert service.last_refresh is not None
    assert service.last_error is None


def test_refresh_without_output_dir_writes_nothing(
    server, bonds_file, tmp_path
):
    settings = Settings(bonds_file=bonds_file[0], output_dir=None)
    QuoteService(settings, server.client, lambda: TODAY).refresh()
    assert not (tmp_path / "out").exists()


def test_failed_bond_keeps_previous_quotes(service, server, bonds_file):
    service.refresh()
    before = service.get("LTN_20320101")
    server.tesouro_csv = "Tipo Titulo;Data Vencimento;Data Base\n"
    service.refresh()
    after = service.get("LTN_20320101")
    assert after.error and "PU Venda Manha" in after.error
    assert after.quotes == before.quotes
    assert after.updated_at == before.updated_at


def test_failed_bond_without_history(service, bonds_file):
    bonds_file[1](PRE, BROKEN)
    service.refresh()
    state = service.get("LTN_WRONG")
    assert state.quotes == ()
    assert "not found in the Tesouro CSV" in state.error


def test_invalid_bonds_file_keeps_previous_state(service, bonds_file):
    service.refresh()
    bonds_file[0].write_text("bonds: [", "utf-8")
    service.refresh()
    assert service.get("CDB_PRE") is not None
    assert "invalid YAML" in service.last_error


def test_bonds_file_is_reread(service, bonds_file):
    service.refresh()
    bonds_file[1](PRE)
    service.refresh()
    assert [s.symbol for s in service.states()] == ["CDB_PRE"]


def test_concurrent_refresh_is_refused(service):
    service._refresh_lock.acquire()
    try:
        assert service.refresh(wait=False) is False
    finally:
        service._refresh_lock.release()


def test_scheduler_refreshes_and_stops(service):
    refreshed = threading.Event()
    original = service.refresh

    def refresh(wait=True):
        result = original(wait)
        refreshed.set()
        return result

    service.refresh = refresh
    scheduler = RefreshScheduler(service, dt.timedelta(hours=1))
    scheduler.start()
    assert refreshed.wait(5)
    scheduler.stop()
    assert not scheduler._thread.is_alive()


def test_scheduler_survives_unexpected_errors(service):
    calls = []

    def boom(wait=True):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return True

    service.refresh = boom
    scheduler = RefreshScheduler(service, dt.timedelta(milliseconds=10))
    scheduler.start()
    deadline = dt.datetime.now() + dt.timedelta(seconds=5)
    while len(calls) < 2 and dt.datetime.now() < deadline:
        threading.Event().wait(0.01)
    scheduler.stop()
    assert len(calls) >= 2


# --- API -----------------------------------------------------------------


def test_quotes_before_first_refresh(api):
    assert api.get("/health").json()["status"] == "starting"
    response = api.get("/quotes/CDB_PRE.csv")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"


def test_quotes_csv_in_wealthfolio_format(api, service):
    service.refresh()
    response = api.get("/quotes/LTN_20320101.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.splitlines()
    # Wealthfolio's custom provider reads the "close" and "date" columns.
    assert lines[0] == "symbol,date,open,high,low,close,volume,currency"
    assert lines[1] == (
        "LTN_20320101,2026-01-27,471.440000,471.440000,471.440000,"
        "471.440000,0,BRL"
    )
    assert lines[-1].startswith("LTN_20320101,2026-01-29,478.070000")


def test_quotes_filtered_by_wealthfolio_from_to(api, service):
    service.refresh()
    response = api.get(
        "/quotes/CDB_PRE.csv",
        params={"from": "2026-01-27", "to": "2026-01-28"},
    )
    dates = [line.split(",")[1] for line in response.text.splitlines()[1:]]
    assert dates == ["2026-01-27", "2026-01-28"]


def test_quotes_empty_range_returns_header_only(api, service):
    service.refresh()
    response = api.get(
        "/quotes/CDB_PRE.csv",
        params={"from": "2025-01-01", "to": "2025-01-31"},
    )
    assert response.status_code == 200
    assert response.text.splitlines() == [
        "symbol,date,open,high,low,close,volume,currency"
    ]


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"from": "2026-01-29", "to": "2026-01-27"}, 422),
        ({"from": "29/01/2026"}, 422),
    ],
)
def test_quotes_invalid_dates(api, service, params, status):
    service.refresh()
    assert api.get("/quotes/CDB_PRE.csv", params=params).status_code == status


def test_unknown_symbol(api, service):
    service.refresh()
    assert api.get("/quotes/NOPE.csv").status_code == 404


def test_health_and_bonds(api, service):
    service.refresh()
    health = api.get("/health").json()
    assert health["status"] == "ok"
    assert health["bonds"] == 2
    bonds = api.get("/bonds").json()
    assert [b["symbol"] for b in bonds] == ["CDB_PRE", "LTN_20320101"]
    ltn = bonds[1]
    assert ltn["first_date"] == "2026-01-27"
    assert ltn["last_price"] == 478.07
    assert ltn["error"] is None


def test_manual_refresh(api):
    response = api.post("/refresh")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_manual_refresh_while_running(api, service):
    service._refresh_lock.acquire()
    try:
        assert api.post("/refresh").status_code == 409
    finally:
        service._refresh_lock.release()


def test_lifespan_starts_scheduler(service):
    with TestClient(create_app(service.settings, service)) as client:
        deadline = dt.datetime.now() + dt.timedelta(seconds=5)
        while service.last_refresh is None and dt.datetime.now() < deadline:
            threading.Event().wait(0.01)
        assert client.get("/health").json()["status"] == "ok"


# --- Settings ------------------------------------------------------------


def test_settings_defaults():
    s = Settings.from_env({})
    assert s.bonds_file == Path("bonds.yaml")
    assert s.output_dir == Path("quotes")
    assert s.refresh_interval == dt.timedelta(hours=6)
    assert s.use_tesouro_api is True
    assert s.tesouro_cache == default_tesouro_cache()
    assert (s.host, s.port) == ("127.0.0.1", 8000)


def test_settings_from_env():
    s = Settings.from_env(
        {
            "BONDS_FILE": "/data/bonds.yaml",
            "OUTPUT_DIR": "/data/quotes",
            "REFRESH_INTERVAL_MINUTES": "30",
            "USE_TESOURO_API": "false",
            "HOST": "0.0.0.0",
            "XDG_CACHE_HOME": "/tmp/cache",
            "PORT": "9000",
        }
    )
    assert str(s.bonds_file) == "/data/bonds.yaml"
    assert str(s.output_dir) == "/data/quotes"
    assert s.refresh_interval == dt.timedelta(minutes=30)
    assert s.use_tesouro_api is False
    assert (s.host, s.port) == ("0.0.0.0", 9000)
    assert str(s.tesouro_cache).startswith("/tmp/cache/")


def test_settings_empty_output_dir_disables_csvs():
    assert Settings.from_env({"OUTPUT_DIR": ""}).output_dir is None


@pytest.mark.parametrize(
    "env",
    [
        {"REFRESH_INTERVAL_MINUTES": "soon"},
        {"REFRESH_INTERVAL_MINUTES": "0"},
        {"PORT": "-1"},
        {"USE_TESOURO_API": "maybe"},
    ],
)
def test_settings_invalid(env):
    with pytest.raises(ConfigError, match=next(iter(env))):
        Settings.from_env(env)
