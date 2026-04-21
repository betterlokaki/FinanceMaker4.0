"""Trading scheduler for managing strategy lifecycle based on market hours."""
import asyncio
import logging
import time
from datetime import datetime

from common.cache.abstracts import ITickerCache
from common.helpers.market_calendar import MarketCalendar
from publishers.abstracts import IBroker
from scheduler.strategy_runner import StrategyRunner
from scheduler.trading_scheduler.end_of_day_email_reporter import EndOfDayEmailReporter

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
        broker: IBroker,
        end_of_day_reporter: EndOfDayEmailReporter,
    ) -> None:
        """Initialize the trading scheduler.
        
        Args:
            strategy_runner: Manages strategy lifecycle.
            market_calendar: Provides market hours info.
            ticker_cache: Cache for clearing stale ticker data.
            broker: Broker instance for connection and buying power checks.
            end_of_day_reporter: Sends end-of-day portfolio report emails.
        """
        self._runner: StrategyRunner = strategy_runner
        self._calendar: MarketCalendar = market_calendar
        self._ticker_cache: ITickerCache = ticker_cache
        self._broker: IBroker = broker
        self._end_of_day_reporter: EndOfDayEmailReporter = end_of_day_reporter
        self._is_running: bool = False
        self._should_stop: bool = False

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running

    async def start(self) -> None:
        """Start the scheduler main loop."""
        logger.info("🚀 Trading scheduler starting...")
        
        # Connect to broker first - if this fails, don't proceed
        try:
            await self._broker.connect()
            buying_power = await self._broker.get_buying_power()
            logger.info("💰 Buying Power: $%.2f", buying_power)
        except ConnectionError as e:
            logger.error("❌ Failed to connect to Interactive Brokers: %s", e)
            logger.error("Scheduler will not start without broker connection.")
            return
        
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
        await self._end_of_day_reporter.send_report_for_trading_day(trading_day)
        
        # Clear old cache files (keep only today's cache)
        self._ticker_cache.clear_old_cache()

    async def _wait_until(self, target: datetime) -> None:
        """Wait until target time.
        
        Logs keepalive messages every 10 minutes to prevent GCP Cloud Run
        from timing out the service during long waits for market open.
        """
        last_keepalive_log: float = 0.0
        KEEPALIVE_INTERVAL_SECONDS: float = 600.0  # 10 minutes
        
        while not self._should_stop:
            remaining: float = (target - self._calendar.now()).total_seconds()
            if remaining <= 0:
                return
            
            # Log keepalive every 10 minutes to prevent GCP timeout
            current_time = time.time()
            if current_time - last_keepalive_log >= KEEPALIVE_INTERVAL_SECONDS:
                hours_remaining = remaining / 3600
                logger.info(
                    "💓 Keepalive: Service is alive. Waiting for market open. "
                    "%.1f hours remaining until pre-market (%.1f minutes)",
                    hours_remaining,
                    remaining / 60
                )
                last_keepalive_log = current_time
            
            if remaining > 60:
                # Only log the initial wait message once, keepalive handles the rest
                if last_keepalive_log == 0.0:
                    logger.info("⏳ Waiting %.1f hours...", remaining / 3600)
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(min(remaining, 1))

    async def _run_until(self, target: datetime) -> None:
        """Run strategies until target time."""
        while not self._should_stop and self._calendar.now() < target:
            await self._runner.health_check()
            await asyncio.sleep(1)
