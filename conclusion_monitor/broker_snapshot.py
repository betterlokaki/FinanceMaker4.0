"""Broker state collection for conclusion reports."""

from __future__ import annotations

from datetime import date
from typing import Any

from publishers.abstracts import IBroker

from conclusion_monitor.serialization import serialize_pnl, serialize_portfolio


class BrokerSnapshotCollector:
    """Collect normalized account, portfolio, and P/L state."""

    def __init__(self, broker: IBroker) -> None:
        self._broker = broker

    async def collect(self, since_date: date) -> dict[str, Any]:
        """Collect a broker snapshot for the report payload."""
        portfolio = await self._broker.get_portfolio()
        buying_power = await self._broker.get_buying_power()
        pnl_summary = await self._broker.get_pnl_summary(since_date=since_date)

        return {
            "provider": type(self._broker).__name__,
            "is_connected": self._broker.is_connected,
            "buying_power": buying_power,
            "pnl": serialize_pnl(pnl_summary),
            "portfolio": serialize_portfolio(portfolio),
            "raw_portfolio": portfolio,
        }
