"""Abstract base class for real-time market data providers."""
import asyncio
import logging
from abc import ABC, abstractmethod

from common.models.pricing_data import PricingData
from pullers.realtime.abstracts.i_realtime_provider import (
    IRealtimeProvider,
    TickCallback,
)

logger = logging.getLogger(__name__)


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
        registered_tickers: list[str] = []
        
        async with self._lock:
            for ticker in tickers:
                ticker_upper: str = ticker.upper()
                if ticker_upper not in self._subscriptions:
                    self._subscriptions[ticker_upper] = set()
                    new_tickers.append(ticker_upper)
                self._subscriptions[ticker_upper].add(on_tick)
                registered_tickers.append(ticker_upper)

            callback_counts = {
                ticker: len(self._subscriptions[ticker])
                for ticker in registered_tickers
            }

        if registered_tickers:
            logger.info(
                "Registered realtime callback %s for %d ticker(s): %s | callback counts: %s",
                self._callback_name(on_tick),
                len(registered_tickers),
                registered_tickers,
                callback_counts,
            )
        
        if new_tickers:
            await self._send_subscribe_message(new_tickers)

    async def unsubscribe(
        self,
        tickers: list[str],
        on_tick: TickCallback | None = None,
    ) -> None:
        """Unsubscribe from real-time updates for tickers.
        
        Args:
            tickers: List of ticker symbols to unsubscribe from.
            on_tick: Optional callback to remove. If omitted, removes all
                callbacks for each ticker.
        """
        removed_tickers: list[str] = []
        updated_tickers: list[str] = []
        
        async with self._lock:
            for ticker in tickers:
                ticker_upper: str = ticker.upper()
                callbacks = self._subscriptions.get(ticker_upper)
                if not callbacks:
                    continue

                if on_tick is None:
                    del self._subscriptions[ticker_upper]
                    removed_tickers.append(ticker_upper)
                    continue

                callbacks.discard(on_tick)
                if callbacks:
                    updated_tickers.append(ticker_upper)
                else:
                    del self._subscriptions[ticker_upper]
                    removed_tickers.append(ticker_upper)

        if updated_tickers:
            logger.info(
                "Removed realtime callback %s from %d ticker(s); provider subscription kept: %s",
                self._callback_name(on_tick),
                len(updated_tickers),
                updated_tickers,
            )
        
        if removed_tickers:
            if on_tick is None:
                logger.info("Removed all realtime callbacks for %s", removed_tickers)
            else:
                logger.info(
                    "Removed final realtime callback %s for %s",
                    self._callback_name(on_tick),
                    removed_tickers,
                )
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
        
        results = await asyncio.gather(
            *(callback(data) for callback in callbacks_copy),
            return_exceptions=True,
        )
        for callback, result in zip(callbacks_copy, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.error(
                    "Realtime callback %s failed for %s",
                    self._callback_name(callback),
                    ticker,
                    exc_info=(type(result), result, result.__traceback__),
                )

    @staticmethod
    def _callback_name(callback: TickCallback) -> str:
        return getattr(callback, "__qualname__", repr(callback))

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
