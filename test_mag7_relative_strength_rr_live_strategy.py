"""Tests for the live Mag7 relative-strength RR strategy."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.order import OrderSide, OrderStatus, OrderType
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from strategy.mag7_relative_strength_rr_strategy import (
    Mag7RelativeStrengthRRLiveStrategy,
    RelativeStrengthSetup,
)

NY_TZ = ZoneInfo("America/New_York")


def _daily_frame(closes: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(closes), freq="B", tz="UTC") + pd.Timedelta(
        hours=13,
        minutes=30,
    )
    close = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000,
        },
        index=index,
    )


class FakeBroker:
    def __init__(self) -> None:
        self.orders = []

    async def get_portfolio(self) -> Portfolio:
        return Portfolio(cash_balance=100_000, buying_power=200_000, total_equity=100_000)

    async def place_order(self, request):
        self.orders.append(request)
        return OrderResponse(
            order_id=f"order-{len(self.orders)}",
            ticker=request.ticker,
            quantity=request.quantity,
            filled_quantity=0,
            side=request.side,
            order_type=OrderType.LIMIT,
            status=OrderStatus.SUBMITTED,
            limit_price=request.limit_price,
        )


def test_load_tickers_returns_mag7_universe() -> None:
    strategy = Mag7RelativeStrengthRRLiveStrategy(
        realtime_provider=object(),
        market_provider=object(),
        broker=FakeBroker(),
    )

    assert asyncio.run(strategy.load_tickers()) == [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "TSLA",
        "GOOGL",
    ]


def test_build_setups_ignores_current_day_data() -> None:
    base = [100.0 + i for i in range(160)]
    data = {
        "AAPL": _daily_frame(base + [260.0]),
        "MSFT": _daily_frame([100.0] * 161),
        "NVDA": _daily_frame([150.0 - (i * 0.1) for i in range(160)] + [500.0]),
        "AMZN": _daily_frame([90.0 + (i * 0.5) for i in range(161)]),
        "META": _daily_frame([80.0 + (i * 0.4) for i in range(161)]),
        "TSLA": _daily_frame([70.0 + (i * 0.3) for i in range(161)]),
        "GOOGL": _daily_frame([60.0 + (i * 0.2) for i in range(161)]),
    }
    as_of = data["AAPL"].index[-1].tz_convert(NY_TZ).replace(hour=12).to_pydatetime()

    setups = Mag7RelativeStrengthRRLiveStrategy.build_setups_from_daily_frames(
        data,
        as_of=as_of,
    )

    assert setups
    assert all(setup.signal_date < as_of.astimezone(NY_TZ).date() for setup in setups)
    assert "NVDA" not in {setup.ticker for setup in setups}


def test_dynamic_order_input_uses_capped_one_to_two_risk_reward() -> None:
    strategy = Mag7RelativeStrengthRRLiveStrategy(
        realtime_provider=object(),
        market_provider=object(),
        broker=FakeBroker(),
        strategy_input=StrategyInputModel(
            portfolio_pct_per_trade=0.5,
            risk_pct=0.04,
            reward_pct=0.08,
        ),
    )

    order_input = strategy._order_input_for_entry(entry_price=100.0, atr=10.0)
    order = strategy._build_entry_order_request(
        ticker="AAPL",
        quantity=10,
        entry_price=100.0,
        strategy_input=order_input,
    )

    assert order_input.risk_pct == 0.2
    assert order_input.reward_pct == 0.4
    assert order.side == OrderSide.BUY
    assert order.stop_loss_price == 80.0
    assert order.take_profit_price == 140.0


def test_on_tick_submits_one_bracket_order_for_active_setup() -> None:
    broker = FakeBroker()
    strategy = Mag7RelativeStrengthRRLiveStrategy(
        realtime_provider=object(),
        market_provider=object(),
        broker=broker,
        strategy_input=StrategyInputModel(
            portfolio_pct_per_trade=0.25,
            risk_pct=0.04,
            reward_pct=0.08,
        ),
    )
    strategy._active_setups["AAPL"] = RelativeStrengthSetup(
        ticker="AAPL",
        signal_date=datetime(2024, 9, 2, tzinfo=NY_TZ).date(),
        rank=1,
        score=1.0,
        close_price=100.0,
        trend_ema=90.0,
        atr=1.0,
    )
    tick = PricingData(
        id="AAPL",
        price=100.0,
        time=datetime(2024, 9, 3, 10, 0, tzinfo=NY_TZ),
    )

    asyncio.run(strategy.on_tick(tick))
    asyncio.run(strategy.on_tick(tick))

    assert len(broker.orders) == 1
    assert broker.orders[0].ticker == "AAPL"
    assert broker.orders[0].stop_loss_price == 96.0
    assert broker.orders[0].take_profit_price == 108.0
