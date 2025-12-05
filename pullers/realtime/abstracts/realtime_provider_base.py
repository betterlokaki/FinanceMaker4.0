"""Abstract base class for real-time market data providers."""
import asyncio
from abc import ABC, abstractmethod

from common.models.pricing_data import PricingData
from pullers.realtime.abstracts.i_realtime_provider import (
    IRealtimeProvider,
    TickCallback,
)


class RealtimeProviderBase(IRealtimeProvider, ABC):
    """Abstract base class for real-time market data providers.
    
    Implements fan-out pattern: multiple callbacks can subscribe to same ticker.
    Thread-safe subscription management using asyncio.Lock.
    """

    def __init__(self) -> None:
        """Initialize the realtime provider."""
        self._subscriptions: dict[str, set[TickCallback]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._is_connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if the provider is connected."""
        return self._is_connected

    @property
    def subscribed_tickers(self) -> list[str]:
        """Get list of currently subscribed tickers."""
        return list(self._subscriptions.keys())

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
        new_tickers: list[str] = []
        
        async with self._lock:
            for ticker in tickers:
                ticker_upper: str = ticker.upper()
                if ticker_upper not in self._subscriptions:
                    self._subscriptions[ticker_upper] = set()
                    new_tickers.append(ticker_upper)
                self._subscriptions[ticker_upper].add(on_tick)
        
        if new_tickers:
            await self._send_subscribe_message(new_tickers)

    async def unsubscribe(self, tickers: list[str]) -> None:
        """Unsubscribe from real-time updates for tickers.
        
        Args:
            tickers: List of ticker symbols to unsubscribe from.
        """
        removed_tickers: list[str] = []
        
        async with self._lock:
            for ticker in tickers:
                ticker_upper: str = ticker.upper()
                if ticker_upper in self._subscriptions:
                    del self._subscriptions[ticker_upper]
                    removed_tickers.append(ticker_upper)
        
        if removed_tickers:
            await self._send_unsubscribe_message(removed_tickers)

    async def _dispatch_tick(self, data: PricingData) -> None:
        """Dispatch tick to all registered callbacks for the ticker.
        
        Uses asyncio.gather for concurrent callback execution.
        
        Args:
            data: The pricing data to dispatch.
        """
        ticker: str = data.id.upper()
        
        async with self._lock:
            callbacks: set[TickCallback] | None = self._subscriptions.get(ticker)
            if not callbacks:
                return
            callbacks_copy: list[TickCallback] = list(callbacks)
        
        await asyncio.gather(
            *(callback(data) for callback in callbacks_copy),
            return_exceptions=True,
        )

    @abstractmethod
    async def _connect(self) -> None:
        """Establish connection to the data feed."""
        ...

    @abstractmethod
    async def _send_subscribe_message(self, tickers: list[str]) -> None:
        """Send subscription message to the data feed.
        
        Args:
            tickers: Tickers to subscribe to.
        """
        ...

    @abstractmethod
    async def _send_unsubscribe_message(self, tickers: list[str]) -> None:
        """Send unsubscription message to the data feed.
        
        Args:
            tickers: Tickers to unsubscribe from.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the real-time data feed."""
        ...
