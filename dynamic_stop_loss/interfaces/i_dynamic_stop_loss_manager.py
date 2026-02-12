"""Dynamic stop loss manager interface."""
from typing import Protocol

from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData


class IDynamicStopLossManager(Protocol):
    """Monitors watched tickers and fires LIMIT SELL when trailing stop breached.

    Pulls positions from the broker every tick — broker is the source of truth.
    Does NOT hold positions internally.
    """

    @property
    def portfolio(self) -> Portfolio | None:
        """Latest portfolio snapshot — refreshed every tick."""
        ...

    async def watch(self, ticker: str, trailing_pct: float) -> None:
        """Register a ticker for stop loss monitoring."""
        ...

    async def on_tick(self, data: PricingData) -> None:
        """Process tick — refresh portfolio from broker, trail stop if position exists."""
        ...

    async def unwatch(self, ticker: str) -> None:
        """Stop watching a ticker."""
        ...

    async def shutdown(self) -> None:
        """Shutdown — clear everything."""
        ...
