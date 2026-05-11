"""Tests for the shared Alpaca live strategy menu."""
from __future__ import annotations

import asyncio

from common.settings import AIScannerConfig, OrderParamsConfig, PortfolioAllocationConfig
from run_live_strategies_menu import (
    LiveStrategySelection,
    create_live_strategies,
    initialize_live_strategies,
    parse_menu_choice,
)
from strategy.earning_strategy import EarningStrategy
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy


def test_parse_menu_choice_accepts_numeric_and_aliases() -> None:
    assert parse_menu_choice("1") == LiveStrategySelection.MAG7
    assert parse_menu_choice("earnings") == LiveStrategySelection.EARNINGS
    assert parse_menu_choice("all") == LiveStrategySelection.BOTH
    assert parse_menu_choice("q") is None


def test_create_live_strategies_shares_broker_and_realtime_provider() -> None:
    broker = object()
    realtime_provider = object()
    market_provider = object()
    earnings_scanner = object()
    ticker_cache = object()

    strategies = create_live_strategies(
        LiveStrategySelection.BOTH,
        broker=broker,  # type: ignore[arg-type]
        realtime_provider=realtime_provider,  # type: ignore[arg-type]
        market_provider=market_provider,  # type: ignore[arg-type]
        earnings_scanner=earnings_scanner,  # type: ignore[arg-type]
        ticker_cache=ticker_cache,  # type: ignore[arg-type]
        ai_scanner_config=AIScannerConfig(),
        portfolio_allocation_config=PortfolioAllocationConfig(),
        order_params_config=OrderParamsConfig(),
    )

    assert len(strategies) == 2
    assert isinstance(strategies[0], Mag7EmaSlopeRegimeLiveStrategy)
    assert isinstance(strategies[1], EarningStrategy)
    assert strategies[0]._broker is broker
    assert strategies[1]._broker is broker
    assert strategies[0]._realtime_provider is realtime_provider
    assert strategies[1]._realtime_provider is realtime_provider
    assert strategies[0]._notional_per_trade == strategies[1]._notional_per_trade


class FakeLiveStrategy:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.initialized = False
        self.shutdown_called = False

    async def initialize(self) -> None:
        if self.should_fail:
            raise RuntimeError("init failed")
        self.initialized = True

    async def on_tick(self, _data: object) -> None:
        pass

    async def shutdown(self) -> None:
        self.shutdown_called = True

    @property
    def is_initialized(self) -> bool:
        return self.initialized


def test_initialize_live_strategies_continues_after_one_failure() -> None:
    async def _run() -> None:
        failed = FakeLiveStrategy(should_fail=True)
        good = FakeLiveStrategy()

        started = await initialize_live_strategies([failed, good])  # type: ignore[list-item]

        assert started == [good]
        assert failed.shutdown_called is True
        assert good.initialized is True
        assert good.shutdown_called is False

    asyncio.run(_run())
