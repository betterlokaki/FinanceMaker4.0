"""Tests for shared strategy runner lifecycle behavior."""
from __future__ import annotations

import asyncio

from scheduler.strategy_runner import StrategyRunner


class _BlockingStrategy:
    def __init__(self, release: asyncio.Event) -> None:
        self._release = release
        self.initialized = False
        self.shutdown_called = False

    async def initialize(self) -> None:
        await self._release.wait()
        self.initialized = True

    async def on_tick(self, _data: object) -> None:
        return None

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.initialized = False

    @property
    def is_initialized(self) -> bool:
        return self.initialized


class _FastStrategy:
    def __init__(self, started: asyncio.Event) -> None:
        self._started = started
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True
        self._started.set()

    async def on_tick(self, _data: object) -> None:
        return None

    async def shutdown(self) -> None:
        self.initialized = False

    @property
    def is_initialized(self) -> bool:
        return self.initialized


def test_start_all_initializes_strategies_concurrently() -> None:
    async def _run() -> None:
        release_slow = asyncio.Event()
        fast_started = asyncio.Event()
        slow = _BlockingStrategy(release_slow)
        fast = _FastStrategy(fast_started)
        runner = StrategyRunner([slow, fast])

        start_task = asyncio.create_task(runner.start_all())
        await asyncio.wait_for(fast_started.wait(), timeout=0.5)

        assert fast.initialized is True
        assert slow.initialized is False
        assert start_task.done() is False

        release_slow.set()
        await asyncio.wait_for(start_task, timeout=0.5)

        assert slow.initialized is True
        assert runner._active == [fast, slow]

    asyncio.run(_run())
