import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest

from br_fixed_income.business_days import BusinessCalendar


FIXTURES = Path(__file__).parent / "fixtures"


def _parse_br_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%d/%m/%Y").date()


class FakeServer:
    """Simulates SGS, Focus, the Tesouro Transparente CSV and 'resgatar' via httpx2.MockTransport."""

    def __init__(self) -> None:
        self.sgs: dict[int, dict[dt.date, float]] = {}
        self.tesouro_csv = (FIXTURES / "tesouro_sample.csv").read_text(
            encoding="utf-8"
        )
        # dict/list = JSON response; int = HTTP error status.
        self.resgatar: Any = json.loads(
            (FIXTURES / "resgatar_sample.json").read_text(encoding="utf-8")
        )
        self.focus: Any = json.loads(
            (FIXTURES / "focus_sample.json").read_text(encoding="utf-8")
        )
        self.calls: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.calls.append(url)
        m = re.search(r"bcdata\.sgs\.(\d+)/dados", url)
        if m:
            start = _parse_br_date(request.url.params["dataInicial"])
            end = _parse_br_date(request.url.params["dataFinal"])
            values = [
                {"data": f"{d:%d/%m/%Y}", "valor": f"{v:.6f}"}
                for d, v in sorted(self.sgs.get(int(m[1]), {}).items())
                if start <= d <= end
            ]
            if not values:
                return httpx2.Response(
                    404,
                    json={
                        "erro": {
                            "statusCode": 404,
                            "detail": "Value(s) not found",
                        }
                    },
                )
            return httpx2.Response(200, json=values)
        if "tesourotransparente" in url:
            return httpx2.Response(200, text=self.tesouro_csv)
        if "resgatar" in url:
            if isinstance(self.resgatar, int):
                return httpx2.Response(self.resgatar, text="error")
            return httpx2.Response(200, json=self.resgatar)
        if "olinda" in url:
            if isinstance(self.focus, int):
                return httpx2.Response(self.focus, text="error")
            return httpx2.Response(200, json=self.focus)
        return httpx2.Response(404)

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))

    def count(self, fragment: str) -> int:
        return sum(fragment in u for u in self.calls)

    def constant_index(
        self,
        series: int,
        value: float,
        cal: BusinessCalendar,
        start: dt.date,
        end: dt.date,
    ) -> None:
        self.sgs[series] = {d: value for d in cal.business_days(start, end)}


@pytest.fixture(scope="session")
def cal() -> BusinessCalendar:
    return BusinessCalendar()


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()
