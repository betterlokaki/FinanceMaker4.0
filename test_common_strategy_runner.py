"""Tests for the injected common strategy runner."""
from __future__ import annotations

import asyncio

from common.models.strategy_input import StrategyInputModel
from common.runners import CommonStrategyRunner


class _RunnerStrategy:
    def __init__(
        self,
        *,
        release: asyncio.Event | None = None,
        fail_times: int = 0,
        shutdown_log: list[str] | None = None,
        name: str = "strategy",
    ) -> None:
        self._release = release
        self._fail_times = fail_times
        self._attempts = 0
        self._shutdown_log = shutdown_log
        self._name = name
        self.initialized = False

    async def initialize(self) -> None:
        self._attempts += 1
        if self._release is not None:
            await self._release.wait()
        if self._attempts <= self._fail_times:
            raise RuntimeError("init failed")
        self.initialized = True

    async def on_tick(self, _data: object) -> None:
        return None

    async def shutdown(self) -> None:
        self.initialized = False
        if self._shutdown_log is not None:
            self._shutdown_log.append(self._name)

    @property
    def is_initialized(self) -> bool:
        return self.initialized


def _input() -> StrategyInputModel:
    return StrategyInputModel(
        portfolio_pct_per_trade=0.25,
        risk_pct=0.03,
        reward_pct=0.05,
    )


def test_common_runner_starts_strategies_concurrently() -> None:
    async def _run() -> None:
        release_slow = asyncio.Event()
        fast = _RunnerStrategy(name="fast")
        slow = _RunnerStrategy(release=release_slow, name="slow")
        runner = CommonStrategyRunner([slow, fast], strategy_input=_input())

        start_task = asyncio.create_task(runner.start_all())
        for _ in range(10):
            if fast.initialized:
                break
            await asyncio.sleep(0)

        assert fast.initialized is True
        assert slow.initialized is False
        assert start_task.done() is False

        release_slow.set()
        await asyncio.wait_for(start_task, timeout=0.5)

        assert runner.active_strategies == [fast, slow]

    asyncio.run(_run())


def test_common_runner_retries_independently_and_stops_reverse_order() -> None:
    async def _run() -> None:
        shutdown_log: list[str] = []
        retrying = _RunnerStrategy(fail_times=1, shutdown_log=shutdown_log, name="retrying")
        stable = _RunnerStrategy(shutdown_log=shutdown_log, name="stable")
        runner = CommonStrategyRunner(
            [retrying, stable],
            strategy_input=_input(),
            max_retries=2,
            retry_delay=0,
        )

        await runner.start_all()
        assert retrying.initialized is True
        assert stable.initialized is True

        await runner.stop_all()
        assert shutdown_log == ["stable", "retrying"]

    asyncio.run(_run())
