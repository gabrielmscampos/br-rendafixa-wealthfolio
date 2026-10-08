"""Periodic quote refresh and the in-memory state the API serves."""

import datetime as dt
import threading
from collections.abc import Callable
from dataclasses import dataclass

import httpx2

from br_fixed_income.bonds import InputError, load_bonds
from br_fixed_income.business_days import today_in_brazil
from br_fixed_income.generator import generate
from br_fixed_income.logger import logger
from br_fixed_income.output import write_csv
from br_fixed_income.pricing import Quote
from br_fixed_income.sources import new_client

from .config import Settings


@dataclass(frozen=True)
class BondState:
    symbol: str
    quotes: tuple[Quote, ...]
    warnings: tuple[str, ...]
    # Error from the latest refresh; `quotes` then still holds the last
    # successful series, so a failing source does not blank out prices.
    error: str | None
    updated_at: dt.datetime | None


class QuoteService:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], httpx2.Client] = new_client,
        today: Callable[[], dt.date] = today_in_brazil,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._today = today
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._states: dict[str, BondState] = {}
        self.last_refresh: dt.datetime | None = None
        self.last_error: str | None = None

    def get(self, symbol: str) -> BondState | None:
        with self._state_lock:
            return self._states.get(symbol)

    def states(self) -> list[BondState]:
        with self._state_lock:
            return list(self._states.values())

    def refresh(self, wait: bool = True) -> bool:
        """Recomputes every bond. Returns False if another refresh was
        already running and `wait` is False."""
        if not self._refresh_lock.acquire(blocking=wait):
            return False
        try:
            self._refresh()
        finally:
            self._refresh_lock.release()
        return True

    def _refresh(self) -> None:
        today = self._today()
        now = dt.datetime.now(dt.UTC)
        try:
            # Re-read on every refresh, so editing bonds.yaml needs no restart.
            bonds = load_bonds(self.settings.bonds_file, today)
        except InputError as e:
            logger.error("Refresh skipped, keeping previous quotes: %s", e)
            self.last_error = str(e)
            return

        logger.info("Refreshing quotes for %d bond(s)", len(bonds))
        with self._client_factory() as client:
            results = generate(
                client,
                bonds,
                today,
                today,
                use_resgatar=self.settings.use_tesouro_api,
                tesouro_cache=self.settings.tesouro_cache,
            )

        states: dict[str, BondState] = {}
        for r in results:
            symbol = r.bond.symbol
            if r.series is not None:
                states[symbol] = BondState(
                    symbol,
                    tuple(r.series.quotes),
                    tuple(r.series.warnings),
                    None,
                    now,
                )
                if self.settings.output_dir is not None:
                    write_csv(
                        self.settings.output_dir / f"{symbol}.csv",
                        symbol,
                        r.series.quotes,
                    )
            else:
                previous = self.get(symbol)
                states[symbol] = BondState(
                    symbol,
                    previous.quotes if previous else (),
                    previous.warnings if previous else (),
                    r.error,
                    previous.updated_at if previous else None,
                )

        with self._state_lock:
            self._states = states
        self.last_refresh = now
        self.last_error = None
        failed = sum(1 for s in states.values() if s.error)
        logger.info("Refresh done: %d bond(s), %d failed", len(states), failed)


class RefreshScheduler:
    """Refreshes right away, then every `interval`, in a daemon thread."""

    def __init__(self, service: QuoteService, interval: dt.timedelta) -> None:
        self._service = service
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="quote-refresh", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._service.refresh()
            # Deliberately broad: an uncaught error would end this thread and
            # leave the API serving stale prices forever; the next tick retries.
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error while refreshing quotes")
            if self._stop.wait(self._interval.total_seconds()):
                return
