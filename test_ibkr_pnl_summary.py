"""Unit tests for IBKR P/L summary extraction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

from common.settings import IBKRConfig
from publishers.interactive_brokers.interactive_webapi_broker import InteractiveWebapiBroker


@dataclass
class _Result:
    data: Any


class _DummyClient:
    def account_profit_and_loss(self) -> _Result:
        return _Result(
            {
                "upnl": {
                    "DU1234567.Core": {
                        "rowType": 1,
                        "dpl": 150.5,
                        "nl": 10000.0,
                        "upl": 300.0,
                    }
                }
            }
        )

    def account_performance(self, account_ids: list[str], period: str) -> _Result:
        assert account_ids == ["DU1234567"]
        assert period == "1Y"
        return _Result(
            {
                "nav": {
                    "data": [
                        {
                            "idType": "acctid",
                            "id": "DU1234567",
                            "navs": [10000.0, 10125.0, 10200.0],
                            "startNAV": {
                                "date": "20260331",
                                "val": 9950.0,
                            },
                            "baseCurrency": "USD",
                        }
                    ],
                    "freq": "D",
                    "dates": ["20260401", "20260402", "20260403"],
                }
            }
        )


def test_extract_daily_pnl_from_partitioned_payload() -> None:
    payload = {
        "upnl": {
            "U1234567.Core": {
                "rowType": 1,
                "dpl": 15.7,
            }
        }
    }

    daily = InteractiveWebapiBroker._extract_daily_pnl(payload, "U1234567")
    assert daily == 15.7


def test_extract_since_date_pnl_uses_prior_nav_baseline() -> None:
    payload = {
        "nav": {
            "data": [
                {
                    "id": "U1234567",
                    "navs": [100.0, 110.0, 120.0],
                    "startNAV": {
                        "date": "20260331",
                        "val": 95.0,
                    },
                    "baseCurrency": "USD",
                }
            ],
            "dates": ["20260401", "20260402", "20260403"],
        }
    }

    pnl, baseline_date, baseline_nav, current_nav, currency = (
        InteractiveWebapiBroker._extract_since_date_pnl(
            payload=payload,
            since_date=date(2026, 4, 1),
            account_id="U1234567",
        )
    )

    assert pnl == 25.0
    assert baseline_date == date(2026, 3, 31)
    assert baseline_nav == 95.0
    assert current_nav == 120.0
    assert currency == "USD"


def test_get_pnl_summary_combines_daily_and_since_date_values() -> None:
    async def _run() -> None:
        broker = InteractiveWebapiBroker(IBKRConfig())
        broker._connected = True
        broker._account_id = "DU1234567"
        broker._client = _DummyClient()

        summary = await broker.get_pnl_summary(since_date=date(2026, 4, 1))

        assert summary.currency == "USD"
        assert summary.daily_pnl == 150.5
        assert summary.pnl_since_date == 250.0
        assert summary.baseline_date == date(2026, 3, 31)
        assert summary.baseline_nav == 9950.0
        assert summary.current_nav == 10200.0

    asyncio.run(_run())
