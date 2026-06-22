"""Tests for the live opening range breakout strategy."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.order import OrderSide, OrderStatus, OrderType
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from strategy.opening_range_breakout_strategy import OpeningRangeBreakoutLiveStrategy

NY_TZ = ZoneInfo("America/New_York")


class FakeRealtimeProvider:
    async def subscribe(self, _tickers: list[str], _on_tick: Any) -> None:
        return None

    async def unsubscribe(self, _tickers: list[str], _on_tick: Any | None = None) -> None:
        return None

    async def disconnect(self) -> None:
        return None


class FakeMarketProvider:
    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period,
    ) -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


class FakeBroker:
    def __init__(self, portfolio: Portfolio | None = None) -> None:
        self.portfolio = portfolio or Portfolio(
            cash_balance=1_000.0,
            total_equity=1_000.0,
            buying_power=1_000.0,
        )
        self.submitted: list[OrderRequest] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        self.submitted.append(order_request)
        response = OrderResponse(
            order_id=f"orb-order-{len(self.submitted)}",
            ticker=order_request.ticker,
            quantity=order_request.quantity,
            filled_quantity=0,
            side=order_request.side,
            order_type=order_request.order_type,
            status=OrderStatus.SUBMITTED,
            limit_price=order_request.limit_price,
            stop_price=order_request.stop_price,
            time_in_force=order_request.time_in_force,
        )
        self.portfolio.open_orders.append(response)
        return response

    async def cancel_order(self, order_id: str) -> OrderResponse:
        raise NotImplementedError(order_id)

    async def get_order(self, order_id: str) -> OrderResponse:
        raise NotImplementedError(order_id)

    async def get_portfolio(self) -> Portfolio:
        return self.portfolio

    async def get_open_orders(self) -> list[OrderResponse]:
        return self.portfolio.open_orders

    async def get_orders_between(self, after: datetime, until: datetime) -> list[OrderResponse]:
        raise NotImplementedError((after, until))

    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        raise NotImplementedError(since_date)


def _strategy(broker: FakeBroker | None = None) -> OpeningRangeBreakoutLiveStrategy:
    strategy = OpeningRangeBreakoutLiveStrategy(
        realtime_provider=FakeRealtimeProvider(),  # type: ignore[arg-type]
        market_provider=FakeMarketProvider(),  # type: ignore[arg-type]
        broker=broker or FakeBroker(),  # type: ignore[arg-type]
        strategy_input=StrategyInputModel(
            portfolio_pct_per_trade=0.5,
            risk_pct=0.015,
            reward_pct=0.045,
        ),
        opening_range_minutes=15,
        confirmation_candle_minutes=5,
        max_positions=3,
    )
    strategy._tickers = ["PLTR"]
    return strategy


def _tick(price: float, hour: int, minute: int) -> PricingData:
    return PricingData(
        id="PLTR",
        price=price,
        time=datetime(2026, 5, 6, hour, minute, tzinfo=NY_TZ),
        last_size=100,
    )


async def _seed_opening_range(strategy: OpeningRangeBreakoutLiveStrategy) -> None:
    await strategy.on_tick(_tick(100.0, 9, 30))
    await strategy.on_tick(_tick(102.0, 9, 40))


def test_load_tickers_returns_requested_orb_universe() -> None:
    async def _run() -> None:
        strategy = _strategy()

        assert await strategy.load_tickers() == [
            "SPCX",
            "PLTR",
            "COIN",
            "BABA",
            "SMCI",
            "MARA",
            "NIO",
        ]

    asyncio.run(_run())


def test_bullish_confirmation_candle_places_bracket_buy() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)

        await _seed_opening_range(strategy)
        await strategy.on_tick(_tick(103.0, 9, 45))
        await strategy.on_tick(_tick(104.0, 9, 49))
        await strategy.on_tick(_tick(105.0, 9, 50))

        assert len(broker.submitted) == 1
        order = broker.submitted[0]
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.LIMIT
        assert order.quantity == 4
        assert order.limit_price == 104.0
        assert order.stop_loss_price == 102.44
        assert order.take_profit_price == 108.68

    asyncio.run(_run())


def test_bearish_confirmation_candle_places_bracket_sell() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)
        await _seed_opening_range(strategy)
        strategy._opening_states["PLTR"].low = 98.0

        await strategy.on_tick(_tick(97.0, 9, 45))
        await strategy.on_tick(_tick(96.0, 9, 49))
        await strategy.on_tick(_tick(95.0, 9, 50))

        assert len(broker.submitted) == 1
        order = broker.submitted[0]
        assert order.side == OrderSide.SELL
        assert order.limit_price == 96.0
        assert order.stop_loss_price == 97.44
        assert order.take_profit_price == 91.68

    asyncio.run(_run())


def test_existing_position_skips_orb_entry() -> None:
    async def _run() -> None:
        portfolio = Portfolio(
            positions=[Position(ticker="PLTR", quantity=1, average_cost=100.0)],
            cash_balance=1_000.0,
            total_equity=1_000.0,
            buying_power=1_000.0,
        )
        broker = FakeBroker(portfolio=portfolio)
        strategy = _strategy(broker=broker)

        await _seed_opening_range(strategy)
        await strategy.on_tick(_tick(103.0, 9, 45))
        await strategy.on_tick(_tick(104.0, 9, 49))
        await strategy.on_tick(_tick(105.0, 9, 50))

        assert broker.submitted == []

    asyncio.run(_run())


def test_duplicate_breakout_candle_does_not_submit_twice() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)

        await _seed_opening_range(strategy)
        await strategy.on_tick(_tick(103.0, 9, 45))
        await strategy.on_tick(_tick(104.0, 9, 49))
        await strategy.on_tick(_tick(105.0, 9, 50))
        await strategy.on_tick(_tick(106.0, 9, 54))
        await strategy.on_tick(_tick(107.0, 9, 55))

        assert len(broker.submitted) == 1

    asyncio.run(_run())


def test_shutdown_clears_runtime_state() -> None:
    async def _run() -> None:
        strategy = _strategy()
        await _seed_opening_range(strategy)
        strategy._reserved_cash["x"] = 1.0
        strategy._order_locks["PLTR"] = asyncio.Lock()

        await strategy.shutdown()

        assert strategy._opening_states == {}
        assert strategy._confirmation_states == {}
        assert strategy._submitted_today == set()
        assert strategy._reserved_cash == {}
        assert strategy._order_locks == {}

    asyncio.run(_run())
