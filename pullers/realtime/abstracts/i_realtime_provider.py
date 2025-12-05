"""Realtime provider interface."""
from collections.abc import Awaitable, Callable
from typing import Protocol

from common.models.pricing_data import PricingData


# Type alias for async tick callback
TickCallback = Callable[[PricingData], Awaitable[None]]


class IRealtimeProvider(Protocol):
    """Protocol for real-time market data providers.
    
    Defines the contract for subscribing to live price updates.
    Supports fan-out pattern: multiple callbacks can subscribe to the same ticker.
    """

    async def subscribe(
        self,
        tickers: list[str],
        on_tick: TickCallback,
    ) -> None:
        """Subscribe to real-time updates for tickers.
        
        Args:
            tickers: List of ticker symbols to subscribe to.
            on_tick: Async callback invoked for each tick update.
        """
        ...

    async def unsubscribe(self, tickers: list[str]) -> None:
        """Unsubscribe from real-time updates for tickers.
        
        Args:
            tickers: List of ticker symbols to unsubscribe from.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the real-time data feed.
        
        Closes the connection and cleans up resources.
        """
        ...
