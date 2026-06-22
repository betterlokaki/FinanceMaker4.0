"""Common lifecycle runner for injected live strategy instances."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from common.models.strategy_input import StrategyInputModel
from strategy.abstracts.i_trading_strategy import ITradingStrategy

logger: logging.Logger = logging.getLogger(__name__)


class CommonStrategyRunner:
    """Start, retry, and stop injected strategies without constructing them."""

    def __init__(
        self,
        strategies: Sequence[ITradingStrategy],
        strategy_input: StrategyInputModel,
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        self._strategies: list[ITradingStrategy] = list(strategies)
        self._strategy_input = strategy_input
        self._max_retries = max(1, int(max_retries))
        self._retry_delay = max(0.0, float(retry_delay))
        self._active: list[ITradingStrategy] = []
        self._failures: dict[int, int] = {}

    @property
    def active_strategies(self) -> list[ITradingStrategy]:
        return list(self._active)

    async def start_all(self) -> None:
        """Initialize all strategies concurrently with isolated retry handling."""
        self._active = []
        self._failures = {}
        logger.info(
            "Starting %d strategy instance(s) with input=%s",
            len(self._strategies),
            self._strategy_input,
        )
        await asyncio.gather(
            *(
                self._start_strategy(index, strategy)
                for index, strategy in enumerate(self._strategies)
            )
        )
        logger.info("Started %d/%d strategy instance(s)", len(self._active), len(self._strategies))

    async def stop_all(self) -> None:
        """Shutdown active strategies in reverse startup order."""
        for strategy in reversed(self._active):
            try:
                await strategy.shutdown()
            except Exception as exc:
                logger.error(
                    "Error shutting down %s: %s",
                    type(strategy).__name__,
                    exc,
                    exc_info=True,
                )
        self._active = []

    async def health_check(self) -> None:
        """Restart active strategies that report an uninitialized state."""
        for index, strategy in enumerate(self._strategies):
            if strategy in self._active and not strategy.is_initialized:
                await self._handle_crash(index, strategy)

    async def _start_strategy(self, index: int, strategy: ITradingStrategy) -> None:
        name = type(strategy).__name__
        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info("Starting %s (attempt %d/%d)", name, attempt, self._max_retries)
                await strategy.initialize()
                self._active.append(strategy)
                self._failures[index] = 0
                logger.info("Started %s", name)
                return
            except Exception as exc:
                logger.error(
                    "%s failed on attempt %d/%d: %s",
                    name,
                    attempt,
                    self._max_retries,
                    exc,
                    exc_info=True,
                )
                if attempt < self._max_retries and self._retry_delay > 0:
                    await asyncio.sleep(self._retry_delay)
        logger.error("%s disabled after %d failed attempt(s)", name, self._max_retries)

    async def _handle_crash(self, index: int, strategy: ITradingStrategy) -> None:
        name = type(strategy).__name__
        failures = self._failures.get(index, 0) + 1
        self._failures[index] = failures
        logger.warning("%s crashed (failure %d/%d)", name, failures, self._max_retries)
        self._active.remove(strategy)
        if failures < self._max_retries:
            await self._start_strategy(index, strategy)
        else:
            logger.error("%s disabled", name)
