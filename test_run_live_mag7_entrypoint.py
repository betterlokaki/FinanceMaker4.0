"""Unit tests for the MAG7 Alpaca live runner entrypoint."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from common.models.order import OrderSide, TimeInForce
from run_live_mag7_ema_slope_regime_strategy import seconds_until_israel_stop
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


def test_mag7_entry_order_uses_gtc_and_default_five_to_three_bracket() -> None:
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
    assert order_request.take_profit_price == 105.0
    assert order_request.stop_loss_price == 97.0
