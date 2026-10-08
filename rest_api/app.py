"""FastAPI app: a Wealthfolio custom market data source (CSV format).

Wealthfolio custom provider, historical source:
    URL:          http://<host>:8000/quotes/{SYMBOL}.csv?from={FROM}&to={TO}
    Format:       CSV
    Price column: close
    Date column:  date
"""

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from br_fixed_income.output import format_csv

from .config import Settings
from .service import BondState, QuoteService, RefreshScheduler


def create_app(
    settings: Settings | None = None,
    service: QuoteService | None = None,
    run_scheduler: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or QuoteService(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler = RefreshScheduler(service, settings.refresh_interval)
        if run_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(
        title="Brazilian fixed income quotes",
        description="Wealthfolio custom market data source for the bonds in bonds.yaml.",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok" if service.last_refresh else "starting",
            "last_refresh": service.last_refresh,
            "last_error": service.last_error,
            "bonds": len(service.states()),
        }

    @app.get("/bonds")
    def bonds() -> list[dict[str, Any]]:
        return [
            _summary(s)
            for s in sorted(service.states(), key=lambda s: s.symbol)
        ]

    @app.post("/refresh")
    def refresh() -> list[dict[str, Any]]:
        # Sync endpoint: FastAPI runs it in a worker thread, so the blocking
        # refresh does not stall the event loop.
        if not service.refresh(wait=False):
            raise HTTPException(409, "a refresh is already in progress")
        return bonds()

    @app.get("/quotes/{symbol}.csv")
    def quotes_csv(
        symbol: str,
        start: Annotated[dt.date | None, Query(alias="from")] = None,
        end: Annotated[dt.date | None, Query(alias="to")] = None,
    ) -> Response:
        if start and end and start > end:
            raise HTTPException(422, "'from' must not be after 'to'")
        state = service.get(symbol)
        if state is None:
            if service.last_refresh is None:
                raise HTTPException(
                    503,
                    "quotes are still being computed",
                    headers={"Retry-After": "30"},
                )
            raise HTTPException(404, f"unknown symbol: {symbol}")
        quotes = [
            q
            for q in state.quotes
            if (start is None or q.date >= start)
            and (end is None or q.date <= end)
        ]
        return Response(format_csv(symbol, quotes), media_type="text/csv")

    return app


def _summary(state: BondState) -> dict[str, Any]:
    last = state.quotes[-1] if state.quotes else None
    return {
        "symbol": state.symbol,
        "rows": len(state.quotes),
        "first_date": state.quotes[0].date if state.quotes else None,
        "last_date": last.date if last else None,
        "last_price": round(last.price, 6) if last else None,
        "source": last.source if last else None,
        "warnings": list(state.warnings),
        "error": state.error,
        "updated_at": state.updated_at,
    }
