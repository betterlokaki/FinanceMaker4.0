"""Tests for scheduler live strategy dependency wiring."""
from __future__ import annotations

from common.di_container import container
from strategy.earning_strategy import EarningStrategy
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy


def test_scheduler_runs_mag7_and_earnings_from_shared_live_registry() -> None:
    container.reset_singletons()
    try:
        scheduler = container.trading_scheduler()
        strategies = scheduler._runner._strategies

        assert [type(strategy) for strategy in strategies] == [
            Mag7EmaSlopeRegimeLiveStrategy,
            EarningStrategy,
        ]
        assert strategies[0]._broker is strategies[1]._broker
        assert strategies[0]._broker is scheduler._broker
        assert strategies[0]._realtime_provider is strategies[1]._realtime_provider
    finally:
        container.reset_singletons()
