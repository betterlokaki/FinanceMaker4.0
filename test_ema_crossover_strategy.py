"""Tests for the live EMA crossover strategy."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from strategy.ema_crossover_strategy import EmaCrossoverLiveStrategy

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
        self.cancelled: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        self.submitted.append(order_request)
        if order_request.order_type == OrderType.MARKET:
            self.portfolio.positions = [
                position
                for position in self.portfolio.positions
                if position.ticker.upper() != order_request.ticker.upper()
            ]
        response = OrderResponse(
            order_id=f"ema-order-{len(self.submitted)}",
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
        if order_request.order_type != OrderType.MARKET:
            self.portfolio.open_orders.append(response)
        return response

    async def cancel_order(self, order_id: str) -> OrderResponse:
        self.cancelled.append(order_id)
        self.portfolio.open_orders = [
            order for order in self.portfolio.open_orders if order.order_id != order_id
        ]
        return OrderResponse(
            order_id=order_id,
            ticker="NVDA",
            quantity=1,
            filled_quantity=0,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
        )

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


def _strategy(
    broker: FakeBroker | None = None,
    strategy_input: StrategyInputModel | None = None,
) -> EmaCrossoverLiveStrategy:
    return EmaCrossoverLiveStrategy(
        realtime_provider=FakeRealtimeProvider(),  # type: ignore[arg-type]
        market_provider=FakeMarketProvider(),  # type: ignore[arg-type]
        broker=broker or FakeBroker(),  # type: ignore[arg-type]
        strategy_input=strategy_input
        or StrategyInputModel(portfolio_pct_per_trade=0.5, risk_pct=0.02, reward_pct=0.06),
        fast_period=2,
        slow_period=3,
        candle_minutes=5,
        trailing_stop_pct=0.02,
        reward_to_risk=3.0,
    )


def _candle(close: float) -> CandleStick:
    return CandleStick(
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        time=datetime(2026, 5, 6, 9, 35, tzinfo=NY_TZ),
        period=Period.MINUTE,
    )


def test_load_tickers_returns_requested_ema_universe() -> None:
    async def _run() -> None:
        strategy = _strategy()

        assert await strategy.load_tickers() == [
            "NVDA",
            "AAPL",
            "TSLA",
            "MSFT",
            "AMD",
            "AMZN",
            "META",
        ]

    asyncio.run(_run())


def test_constructor_keeps_injected_strategy_input() -> None:
    strategy_input = StrategyInputModel(
        portfolio_pct_per_trade=0.33,
        risk_pct=0.0,
        reward_pct=0.0,
    )

    strategy = _strategy(strategy_input=strategy_input)

    assert strategy._strategy_input is strategy_input


def test_bullish_crossover_places_limit_buy_using_portfolio_pct() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)
        strategy._close_history["NVDA"] = [10.0, 10.0, 10.0, 10.0]

        await strategy.on_candle("NVDA", _candle(11.0))

        assert len(broker.submitted) == 1
        order = broker.submitted[0]
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == 11.0
        assert order.quantity == 45

    asyncio.run(_run())


def test_existing_aligned_open_order_skips_duplicate_crossover_entry() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.open_orders.append(
            OrderResponse(
                order_id="existing",
                ticker="NVDA",
                quantity=10,
                filled_quantity=0,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            )
        )
        strategy = _strategy(broker=broker)
        strategy._close_history["NVDA"] = [10.0, 10.0, 10.0, 10.0]

        await strategy.on_candle("NVDA", _candle(11.0))

        assert broker.submitted == []

    asyncio.run(_run())


def test_bearish_crossover_flattens_long_before_short_entry() -> None:
    async def _run() -> None:
        portfolio = Portfolio(
            positions=[Position(ticker="NVDA", quantity=5, average_cost=10.0)],
            cash_balance=1_000.0,
            total_equity=1_000.0,
            buying_power=1_000.0,
        )
        broker = FakeBroker(portfolio=portfolio)
        strategy = _strategy(broker=broker)
        strategy._close_history["NVDA"] = [10.0, 10.0, 10.0, 10.0]

        await strategy.on_candle("NVDA", _candle(9.0))

        assert [order.order_type for order in broker.submitted] == [
            OrderType.MARKET,
            OrderType.LIMIT,
        ]
        assert [order.side for order in broker.submitted] == [
            OrderSide.SELL,
            OrderSide.SELL,
        ]

    asyncio.run(_run())


def test_open_position_gets_standalone_trailing_stop_exit() -> None:
    async def _run() -> None:
        portfolio = Portfolio(
            positions=[Position(ticker="NVDA", quantity=10, average_cost=100.0)],
            cash_balance=1_000.0,
            total_equity=1_000.0,
            buying_power=1_000.0,
        )
        broker = FakeBroker(portfolio=portfolio)
        strategy = _strategy(broker=broker)

        await strategy._ensure_trailing_stop_for_open_position("NVDA")

        assert len(broker.submitted) == 1
        order = broker.submitted[0]
        assert order.order_type == OrderType.TRAILING_STOP
        assert order.side == OrderSide.SELL
        assert order.quantity == 10
        assert order.trailing_stop_amt == 2.0
        assert order.trailing_stop_type == "%"

    asyncio.run(_run())


def test_open_position_at_three_r_target_cancels_trailing_stop_and_flattens() -> None:
    async def _run() -> None:
        portfolio = Portfolio(
            positions=[Position(ticker="NVDA", quantity=10, average_cost=100.0)],
            open_orders=[
                OrderResponse(
                    order_id="trail-1",
                    ticker="NVDA",
                    quantity=10,
                    filled_quantity=0,
                    side=OrderSide.SELL,
                    order_type=OrderType.TRAILING_STOP,
                    status=OrderStatus.SUBMITTED,
                )
            ],
            cash_balance=1_000.0,
            total_equity=1_000.0,
            buying_power=1_000.0,
        )
        broker = FakeBroker(portfolio=portfolio)
        strategy = _strategy(broker=broker)

        await strategy._manage_open_position_exits("NVDA", 106.0)

        assert broker.cancelled == ["trail-1"]
        assert len(broker.submitted) == 1
        assert broker.submitted[0].order_type == OrderType.MARKET
        assert broker.submitted[0].side == OrderSide.SELL

    asyncio.run(_run())


def test_shutdown_clears_runtime_state() -> None:
    async def _run() -> None:
        strategy = _strategy()
        strategy._candle_states["NVDA"] = object()  # type: ignore[assignment]
        strategy._close_history["NVDA"] = [1.0]
        strategy._order_locks["NVDA"] = asyncio.Lock()
        strategy._in_flight.add("NVDA")

        await strategy.shutdown()

        assert strategy._candle_states == {}
        assert strategy._close_history == {}
        assert strategy._order_locks == {}
        assert strategy._in_flight == set()

    asyncio.run(_run())
