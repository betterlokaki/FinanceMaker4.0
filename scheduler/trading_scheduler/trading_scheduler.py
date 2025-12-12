"""Trading scheduler for managing strategy lifecycle based on market hours."""
import asyncio
import logging
from datetime import datetime

from common.cache.abstracts import ITickerCache
from common.helpers.market_calendar import MarketCalendar
from scheduler.strategy_runner import StrategyRunner

logger: logging.Logger = logging.getLogger(__name__)


class TradingScheduler:
    """Scheduler that runs strategies during market hours.
    
    Lifecycle:
    1. Wait for pre-market open (4:00 AM EST)
    2. Start all strategies
    3. Run until after-hours close (8:00 PM EST)
    4. Stop all strategies
    5. Repeat next market day
    """

    def __init__(
        self,
        strategy_runner: StrategyRunner,
        market_calendar: MarketCalendar,
        ticker_cache: ITickerCache,
    ) -> None:
        """Initialize the trading scheduler.
        
        Args:
            strategy_runner: Manages strategy lifecycle.
            market_calendar: Provides market hours info.
            ticker_cache: Cache for clearing stale ticker data.
        """
        self._runner: StrategyRunner = strategy_runner
        self._calendar: MarketCalendar = market_calendar
        self._ticker_cache: ITickerCache = ticker_cache
        self._is_running: bool = False
        self._should_stop: bool = False

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running

    async def start(self) -> None:
        """Start the scheduler main loop."""
        logger.info("🚀 Trading scheduler starting...")
        self._is_running = True
        self._should_stop = False
        
        while not self._should_stop:
            await self._run_trading_day()
        
        self._is_running = False
        logger.info("📴 Trading scheduler stopped")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        logger.info("Stopping scheduler...")
        self._should_stop = True
        await self._runner.stop_all()

    async def _run_trading_day(self) -> None:
        """Execute a single trading day cycle."""
        now: datetime = self._calendar.now()
        trading_day: datetime = self._calendar.get_next_trading_day(now)
        
        pre_market: datetime = self._calendar.get_pre_market_open(trading_day)
        after_hours: datetime = self._calendar.get_after_hours_close(trading_day)
        
        logger.info("📅 Next: %s | Pre-market: %s | Close: %s",
                    trading_day.date(), pre_market.strftime("%H:%M"), after_hours.strftime("%H:%M"))
        
        await self._wait_until(pre_market)
        if self._should_stop:
            return
        
        logger.info("🔔 Pre-market open!")
        await self._runner.start_all()
        
        await self._run_until(after_hours)
        
        logger.info("🔕 After-hours closed!")
        await self._runner.stop_all()
        
        # Clear old cache files (keep only today's cache)
        self._ticker_cache.clear_old_cache()

    async def _wait_until(self, target: datetime) -> None:
        """Wait until target time."""
        while not self._should_stop:
            remaining: float = (target - self._calendar.now()).total_seconds()
            if remaining <= 0:
                return
            
            if remaining > 60:
                logger.info("⏳ Waiting %.1f hours...", remaining / 3600)
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(min(remaining, 1))

    async def _run_until(self, target: datetime) -> None:
        """Run strategies until target time."""
        while not self._should_stop and self._calendar.now() < target:
            await self._runner.health_check()
            await asyncio.sleep(1)
