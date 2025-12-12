"""Abstract base class for real-time trading strategies."""
import logging
from abc import ABC, abstractmethod

from common.models.candlestick import CandleStick
from common.models.period import Period
from common.models.pricing_data import PricingData
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.i_trading_strategy import ITradingStrategy

logger: logging.Logger = logging.getLogger(__name__)


class RealTimeTradingBase(ITradingStrategy, ABC):
    """Abstract base class for real-time trading strategies.
    
    Provides common functionality for strategies that trade based on
    real-time market data. Handles ticker loading, subscription management,
    tick dispatching, and candle building.
    
    Subclasses must implement:
        - load_tickers(): Return list of tickers to trade
        - on_candle(ticker, candle): Handle confirmed candles
    """

    CANDLE_TICKS: int = 1  # Number of ticks per candle

    def __init__(self, realtime_provider: IRealtimeProvider) -> None:
        """Initialize the real-time trading strategy.
        
        Args:
            realtime_provider: Real-time market data provider for subscriptions.
        """
        self._realtime_provider: IRealtimeProvider = realtime_provider
        self._tickers: list[str] = []
        self._is_initialized: bool = False
        self._building_candles: dict[str, dict] = {}

    @property
    def tickers(self) -> list[str]:
        """Get the list of tickers this strategy is trading."""
        return self._tickers.copy()

    @property
    def is_initialized(self) -> bool:
        """Check if the strategy has been initialized."""
        return self._is_initialized

    async def initialize(self) -> None:
        """Initialize the strategy.
        
        Loads tickers via load_tickers() and subscribes to real-time
        market data for all loaded tickers.
        
        Raises:
            Exception: If ticker loading or subscription fails.
        """
        logger.info("Initializing %s...", self.__class__.__name__)
        
        self._tickers = await self.load_tickers()
        
        if not self._tickers:
            logger.warning("No tickers loaded for %s", self.__class__.__name__)
            self._is_initialized = True
            return
        
        logger.info("Loaded %d tickers: %s", len(self._tickers), self._tickers)
        
        await self._realtime_provider.subscribe(self._tickers, self.on_tick)
        
        self._is_initialized = True
        logger.info(
            "%s initialized and subscribed to %d tickers",
            self.__class__.__name__,
            len(self._tickers),
        )

    @abstractmethod
    async def load_tickers(self) -> list[str]:
        """Load tickers to trade.
        
        Subclasses must implement this to return the list of ticker
        symbols the strategy will trade.
        
        Returns:
            List of ticker symbols to subscribe to.
        """
        ...

    @abstractmethod
    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Handle confirmed candle.
        
        Called when a candle period completes. Subclasses implement
        their trading logic here.
        
        Args:
            ticker: The ticker symbol.
            candle: The confirmed candlestick.
        """
        ...

    async def on_tick(self, data: PricingData) -> None:
        """Handle incoming price tick and build candles.
        
        Accumulates ticks into candles. When a candle period closes,
        calls on_candle() with the confirmed candle.
        
        Args:
            data: Real-time pricing data from the subscribed ticker.
        """
        ticker: str = data.id.upper()
        
        if ticker not in self._building_candles:
            self._building_candles[ticker] = self._create_candle_state(data)
            return
        
        state: dict = self._building_candles[ticker]
        elapsed: float = (data.time - state["start_time"]).total_seconds()
        print("Tick received for %s: price=%.2f, time=%s",
                     ticker, data.price, data.time  )
        if elapsed >= self.CANDLE_TICKS:
            candle: CandleStick = self._finalize_candle(state)
            await self.on_candle(ticker, candle)
            self._building_candles[ticker] = self._create_candle_state(data)
            return
        
        self._update_candle_state(state, data)

    def _create_candle_state(self, data: PricingData) -> dict:
        """Create initial candle state from first tick."""
        return {
            "open": data.price,
            "high": data.price,
            "low": data.price,
            "close": data.price,
            "volume": data.last_size,
            "start_time": data.time,
        }

    def _update_candle_state(self, state: dict, data: PricingData) -> None:
        """Update candle state with new tick."""
        state["high"] = max(state["high"], data.price)
        state["low"] = min(state["low"], data.price)
        state["close"] = data.price
        state["volume"] += data.last_size

    def _finalize_candle(self, state: dict) -> CandleStick:
        """Create CandleStick from accumulated state."""
        return CandleStick(
            open=state["open"],
            high=state["high"],
            low=state["low"],
            close=state["close"],
            volume=state["volume"],
            time=state["start_time"],
            period=Period.MINUTE,
        )

    async def shutdown(self) -> None:
        """Shutdown the strategy gracefully.
        
        Unsubscribes from all tickers and cleans up resources.
        """
        logger.info("Shutting down %s...", self.__class__.__name__)
        
        if self._tickers:
            await self._realtime_provider.unsubscribe(self._tickers)
        
        self._tickers = []
        self._is_initialized = False
        
        logger.info("%s shutdown complete", self.__class__.__name__)
