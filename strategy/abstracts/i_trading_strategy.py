"""Trading strategy interface protocol."""
from typing import Protocol

from common.models.candlestick import CandleStick
from common.models.pricing_data import PricingData


class ITradingStrategy(Protocol):
    """Trading strategy interface - defines contract for all strategies.
    
    This protocol defines the interface that all trading strategy
    implementations must follow. Use this for type hints instead
    of concrete classes.
    
    Strategies are designed for real-time trading with tick-by-tick
    price updates from market data providers.
    """

    @property
    def is_initialized(self) -> bool:
        """Check if the strategy has been initialized."""
        ...

    async def initialize(self) -> None:
        """Initialize the strategy.
        
        Sets up all required resources, loads tickers to trade,
        and subscribes to real-time market data.
        
        Must be called before the strategy can receive price updates.
        """
        ...

    async def on_tick(self, data: PricingData) -> None:
        """Handle incoming price tick.
        
        Called for each real-time price update from subscribed tickers.
        This is where the core trading logic is implemented.
        
        Args:
            data: Real-time pricing data for a subscribed ticker.
        """
        ...

    async def shutdown(self) -> None:
        """Shutdown the strategy gracefully.
        
        Cleans up resources, unsubscribes from market data,
        and performs any necessary cleanup.
        """
        ...
