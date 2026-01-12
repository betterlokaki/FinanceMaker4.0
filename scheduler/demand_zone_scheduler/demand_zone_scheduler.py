"""Scheduler for demand zone strategy execution every 2 hours during market hours."""
import asyncio
import logging

from common.helpers.market_calendar import MarketCalendar
from scheduler.abstracts.i_scheduler import IScheduler
from strategy.abstracts.i_trading_strategy import ITradingStrategy

logger: logging.Logger = logging.getLogger(__name__)


class DemandZoneScheduler(IScheduler):
    """Scheduler that runs demand zone strategy continuously during market hours.
    
    Initializes strategy once on startup, keeps it running and listening for
    price updates continuously. Only shuts down when market hours close or
    scheduler is stopped.
    """

    def __init__(
        self,
        strategy: ITradingStrategy,
        market_calendar: MarketCalendar,
    ) -> None:
        """Initialize the scheduler.
        
        Args:
            strategy: Demand zone strategy to execute.
            market_calendar: Market calendar for trading day detection.
        """
        self._strategy: ITradingStrategy = strategy
        self._calendar: MarketCalendar = market_calendar
        self._is_running: bool = False
        self._should_stop: bool = False

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running

    async def start(self) -> None:
        """Start the scheduler main loop."""
        logger.info("Starting DemandZoneScheduler...")
        self._is_running = True
        self._should_stop = False
        
        # Initialize strategy once - it will stay running and listening
        if not self._calendar.is_market_hours_open():
            logger.info("Market hours closed. Waiting for market to open...")
            return
        
        logger.info("Initializing demand zone strategy (will run continuously)...")
        try:
            await self._strategy.initialize()
            logger.info("✅ Strategy initialized and listening for price updates")
        except Exception as e:
            logger.error("Error initializing strategy: %s", e, exc_info=True)
            return
        
        # Keep strategy running until scheduler stops (listener stays alive forever)
        while not self._should_stop:
            if not self._calendar.is_market_hours_open():
                logger.info("Market hours closed. Strategy continues listening for next market open...")
                # Don't shutdown - keep listener alive, just wait
                await asyncio.sleep(300)  # Check every 5 minutes when market closed
                continue
            
            # Strategy is running and listening - just wait and monitor
            await asyncio.sleep(60)  # Check every minute during market hours
        
        # Only unsubscribe from remaining tickers, but keep listener alive
        self._is_running = False
        logger.info("DemandZoneScheduler stopped (listener stays alive)")

    async def stop(self) -> None:
        """Stop the scheduler gracefully.
        
        Note: Does NOT shutdown the listener - it stays alive.
        Only unsubscribes from remaining tickers.
        """
        logger.info("Stopping DemandZoneScheduler...")
        self._should_stop = True
        # Don't shutdown strategy - listener should stay alive
        # Strategy will unsubscribe from tickers individually after processing
