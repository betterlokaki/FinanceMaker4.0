"""Unit tests for the MAG7 Alpaca live runner entrypoint."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from common.models.order import OrderSide, TimeInForce
import run_live_mag7_ema_slope_regime_strategy as runner
from run_live_mag7_ema_slope_regime_strategy import (
    create_mag7_strategy_input_from_settings,
    seconds_until_israel_stop,
)
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def test_seconds_until_israel_stop_at_start_time() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_israel_stop_before_stop_time() -> None:
    now = datetime(2026, 5, 6, 22, 59, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 60


def test_seconds_until_israel_stop_at_stop_time() -> None:
    now = datetime(2026, 5, 6, 23, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 0


def test_create_mag7_strategy_input_uses_alpaca_risk_reward_and_notional(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runner.settings.alpaca, "stop_loss_pct", 0.02)
    monkeypatch.setattr(runner.settings.alpaca, "take_profit_pct", 0.06)
    monkeypatch.setattr(runner.settings.alpaca, "notional_per_trade", 12_345.0)
    monkeypatch.setattr(runner.settings.portfolio_allocation, "strategy_allocation_pct", 0.5)
    monkeypatch.setattr(runner.settings.portfolio_allocation, "ticker_allocation_pct", 0.5)

    strategy_input = create_mag7_strategy_input_from_settings()

    assert strategy_input.portfolio_pct_per_trade == 0.25
    assert strategy_input.risk_pct == 0.02
    assert strategy_input.reward_pct == 0.06
    assert strategy_input.max_notional_per_trade == 12_345.0


def test_mag7_entry_order_uses_gtc_and_default_four_to_two_bracket() -> None:
    strategy = Mag7EmaSlopeRegimeLiveStrategy(
        realtime_provider=object(),
        market_provider=object(),
        broker=object(),
    )

    order_request = strategy._build_entry_order_request(
        ticker="AAPL",
        desired_side=OrderSide.BUY,
        quantity=10,
        entry_price=100.0,
    )

    assert order_request.time_in_force == TimeInForce.GTC
    assert order_request.take_profit_rth is True
    assert order_request.stop_loss_rth is False
    assert order_request.take_profit_price == 104.0
    assert order_request.stop_loss_price == 98.0


def test_mag7_sensitive_flip_uses_faster_slope_without_new_entry() -> None:
    strategy = Mag7EmaSlopeRegimeLiveStrategy(
        realtime_provider=object(),
        market_provider=object(),
        broker=object(),
    )
    strategy._close_history["AAPL"] = (
        [100.0] * 20
        + [130.0] * 10
        + [120.0] * 10
        + [112.0] * 20
    )
    signals: list[tuple[OrderSide, bool, str]] = []

    async def capture_signal(
        *,
        ticker: str,
        desired_side: OrderSide,
        entry_price: float,
        allow_new_entry: bool = True,
        signal_note: str = "signal-entry",
    ) -> None:
        assert ticker == "AAPL"
        assert entry_price == 108.0
        signals.append((desired_side, allow_new_entry, signal_note))

    strategy._process_signal = capture_signal  # type: ignore[method-assign]

    asyncio.run(strategy._evaluate_signal_with_price(ticker="AAPL", price=108.0))

    assert signals == [(OrderSide.SELL, False, "sensitive-flip")]
