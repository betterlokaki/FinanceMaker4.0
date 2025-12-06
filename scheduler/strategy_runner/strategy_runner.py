"""Strategy runner with retry logic."""
import asyncio
import logging

from strategy.abstracts.i_trading_strategy import ITradingStrategy

logger: logging.Logger = logging.getLogger(__name__)


class StrategyRunner:
    """Manages strategy lifecycle with retry logic.
    
    Handles initialization, health monitoring, and graceful shutdown
    of trading strategies with configurable retry attempts.
    """

    def __init__(
        self,
        strategies: list[ITradingStrategy],
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        """Initialize strategy runner.
        
        Args:
            strategies: List of strategies to manage.
            max_retries: Max retry attempts for failed strategies.
            retry_delay: Delay between retry attempts (seconds).
        """
        self._strategies: list[ITradingStrategy] = strategies
        self._max_retries: int = max_retries
        self._retry_delay: float = retry_delay
        self._active: list[ITradingStrategy] = []
        self._failures: dict[int, int] = {}

    async def start_all(self) -> None:
        """Initialize all strategies with retry logic."""
        self._active = []
        self._failures = {}
        
        for idx, strategy in enumerate(self._strategies):
            await self._start_strategy(idx, strategy)

    async def stop_all(self) -> None:
        """Shutdown all active strategies."""
        for strategy in self._active:
            try:
                await strategy.shutdown()
            except Exception as e:
                logger.error("Error shutting down %s: %s", type(strategy).__name__, e)
        
        self._active = []

    async def health_check(self) -> None:
        """Check and restart crashed strategies."""
        for idx, strategy in enumerate(self._strategies):
            if strategy not in self._active:
                continue
            
            if not strategy.is_initialized:
                await self._handle_crash(idx, strategy)

    async def _start_strategy(self, idx: int, strategy: ITradingStrategy) -> None:
        """Start a strategy with retry logic."""
        name: str = type(strategy).__name__
        
        for attempt in range(self._max_retries):
            try:
                logger.info("Starting %s (attempt %d/%d)...", name, attempt + 1, self._max_retries)
                await strategy.initialize()
                self._active.append(strategy)
                self._failures[idx] = 0
                logger.info("✅ %s started", name)
                return
            except Exception as e:
                logger.error("❌ %s failed: %s", name, e)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay)
        
        logger.error("🚫 %s disabled after %d failures", name, self._max_retries)

    async def _handle_crash(self, idx: int, strategy: ITradingStrategy) -> None:
        """Handle a crashed strategy."""
        name: str = type(strategy).__name__
        failures: int = self._failures.get(idx, 0) + 1
        self._failures[idx] = failures
        
        logger.warning("⚠️ %s crashed (failure %d/%d)", name, failures, self._max_retries)
        self._active.remove(strategy)
        
        if failures < self._max_retries:
            await self._start_strategy(idx, strategy)
        else:
            logger.error("🚫 %s disabled", name)
