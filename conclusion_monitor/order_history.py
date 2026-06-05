"""Order-history service for conclusion reports."""

from __future__ import annotations

from datetime import date

from common.models.order_response import OrderResponse
from publishers.abstracts import IBroker

from conclusion_monitor.time_windows import local_date_range_bounds_utc, local_day_bounds_utc


class BrokerOrderHistoryProvider:
    """Fetch order history through the broker abstraction."""

    def __init__(self, broker: IBroker) -> None:
        self._broker = broker

    async def get_orders_for_day(self, trading_day: date) -> list[OrderResponse]:
        """Return all broker orders for a New York trading date."""
        after, until = local_day_bounds_utc(trading_day)
        return await self._broker.get_orders_between(after=after, until=until)

    async def get_orders_for_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[OrderResponse]:
        """Return all broker orders for an inclusive New York date range."""
        after, until = local_date_range_bounds_utc(start_date, end_date)
        return await self._broker.get_orders_between(after=after, until=until)
