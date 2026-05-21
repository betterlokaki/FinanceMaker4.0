"""Tests for the Alpaca earnings strategy order flow."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.cache.abstracts import ITickerCache
from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.models.scanner_params import ScannerParams
from common.settings import AIScannerConfig, OrderParamsConfig, PortfolioAllocationConfig
from strategy.earning_strategy.earning_strategy import EarningStrategy

NY_TZ = ZoneInfo("America/New_York")


class FakeRealtimeProvider:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[list[str], Any]] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, tickers: list[str], on_tick: Any) -> None:
        self.subscriptions.append((tickers, on_tick))

    async def unsubscribe(self, tickers: list[str]) -> None:
        self.unsubscribed.extend(tickers)

    async def disconnect(self) -> None:
        pass


class FakeScanner:
    async def scan(self, _params: ScannerParams) -> list[str]:
        return ["AAPL"]


class FakeTickerCache(ITickerCache):
    def load_tickers(self, _target_date: Any) -> list[str] | None:
        return ["AAPL"]

    def save_tickers(self, _tickers: list[str], _target_date: Any) -> None:
        pass


class FakeMarketProvider:
    def __init__(self, prices: pd.DataFrame | None = None) -> None:
        self.prices = prices
        self.requests: list[tuple[str, datetime, datetime, Period]] = []

    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period,
    ) -> pd.DataFrame:
        self.requests.append((ticker, start_time, end_time, period))
        if self.prices is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "period"])
        return self.prices


class FakeBroker:
    def __init__(self) -> None:
        self.portfolio = Portfolio(buying_power=10_000)
        self.submitted: list[OrderRequest] = []
        self.cancelled_order_ids: list[str] = []
        self._order_sequence = 0

    @property
    def is_connected(self) -> bool:
        return True

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        self._order_sequence += 1
        self.submitted.append(order_request)
        response = OrderResponse(
            order_id=f"order-{self._order_sequence}",
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
        self.cancelled_order_ids.append(order_id)
        for order in list(self.portfolio.open_orders):
            if order.order_id == order_id:
                self.portfolio.open_orders.remove(order)
                return OrderResponse(
                    order_id=order.order_id,
                    ticker=order.ticker,
                    quantity=order.quantity,
                    filled_quantity=order.filled_quantity,
                    side=order.side,
                    order_type=order.order_type,
                    status=OrderStatus.CANCELLED,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    time_in_force=order.time_in_force,
                )
        return OrderResponse(
            order_id=order_id,
            ticker="AAPL",
            quantity=0,
            filled_quantity=0,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
        )

    async def get_order(self, order_id: str) -> OrderResponse:
        for order in self.portfolio.open_orders:
            if order.order_id == order_id:
                return order
        raise ValueError(order_id)

    async def get_portfolio(self) -> Portfolio:
        return self.portfolio

    async def get_open_orders(self) -> list[OrderResponse]:
        return [order for order in self.portfolio.open_orders if order.is_active]

    async def get_buying_power(self) -> float:
        return self.portfolio.buying_power

    async def get_pnl_summary(self, _since_date: Any) -> PnlSummary:
        raise NotImplementedError


def _strategy(
    broker: FakeBroker,
    market_provider: FakeMarketProvider | None = None,
) -> EarningStrategy:
    return EarningStrategy(
        realtime_provider=FakeRealtimeProvider(),
        earnings_scanner=FakeScanner(),  # type: ignore[arg-type]
        broker=broker,
        market_provider=market_provider or FakeMarketProvider(),  # type: ignore[arg-type]
        ai_scanner_config=AIScannerConfig(),
        ticker_cache=FakeTickerCache(),
        portfolio_allocation_config=PortfolioAllocationConfig(),
        order_params_config=OrderParamsConfig(),
        notional_per_trade=1_000,
    )


def _candle(low: float = 100.0) -> CandleStick:
    return CandleStick(
        open=101.0,
        high=102.0,
        low=low,
        close=101.5,
        volume=100,
        time=datetime.now(timezone.utc),
        period=Period.MINUTE,
    )


def _tick(price: float) -> PricingData:
    return PricingData(
        id="AAPL",
        price=price,
        time=datetime.now(timezone.utc),
        last_size=10,
    )


def _tick_at(price: float, timestamp: datetime) -> PricingData:
    return PricingData(
        id="AAPL",
        price=price,
        time=timestamp,
        last_size=10,
    )


def _first_rth_prices() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            datetime(2026, 1, 2, 9, 30, tzinfo=NY_TZ),
            datetime(2026, 1, 2, 9, 31, tzinfo=NY_TZ),
            datetime(2026, 1, 2, 9, 34, tzinfo=NY_TZ),
        ]
    )
    return pd.DataFrame(
        {
            "open": [101.0, 100.0, 99.0],
            "high": [102.0, 101.0, 100.0],
            "low": [100.0, 98.5, 99.0],
            "close": [100.5, 99.0, 99.5],
            "volume": [10, 20, 30],
            "period": [Period.MINUTE, Period.MINUTE, Period.MINUTE],
        },
        index=index,
    )


def test_earnings_entry_is_plain_extended_hours_limit_day_order() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker)

        await strategy.on_candle("AAPL", _candle(low=100.0))

        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.ticker == "AAPL"
        assert request.side == OrderSide.BUY
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 100.0
        assert request.quantity == 10
        assert request.time_in_force == TimeInForce.DAY
        assert request.extended_hours is True
        assert request.take_profit_price is None
        assert request.stop_loss_price is None
        assert request.stop_price is None

    asyncio.run(_run())


def test_earnings_on_tick_waits_for_real_first_five_minute_rth_candle() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker)

        await strategy.on_tick(_tick_at(100.0, datetime(2026, 1, 2, 9, 31, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick_at(98.0, datetime(2026, 1, 2, 9, 33, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick_at(101.0, datetime(2026, 1, 2, 9, 34, tzinfo=NY_TZ)))

        assert broker.submitted == []

        await strategy.on_tick(_tick_at(102.0, datetime(2026, 1, 2, 9, 35, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.side == OrderSide.BUY
        assert request.limit_price == 98.0

    asyncio.run(_run())


def test_earnings_on_tick_loads_missed_first_rth_candle_from_market_provider() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        market_provider = FakeMarketProvider(_first_rth_prices())
        strategy = _strategy(broker, market_provider=market_provider)

        await strategy.on_tick(_tick_at(100.0, datetime(2026, 1, 2, 9, 36, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].limit_price == 98.5
        assert market_provider.requests

    asyncio.run(_run())


def test_earnings_load_tickers_adds_existing_positions_and_orders_for_restart_monitoring() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.positions = [Position(ticker="MSFT", quantity=5, average_cost=200.0)]
        broker.portfolio.open_orders = [
            OrderResponse(
                order_id="entry-1",
                ticker="NVDA",
                quantity=2,
                filled_quantity=0,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
                limit_price=100.0,
                time_in_force=TimeInForce.DAY,
            )
        ]
        strategy = _strategy(broker)

        tickers = await strategy.load_tickers()

        assert tickers == ["AAPL", "MSFT", "NVDA"]
        assert strategy._orders_placed == {"MSFT", "NVDA"}
        assert strategy._exit_monitoring_tickers == {"MSFT", "NVDA"}

    asyncio.run(_run())


def test_earnings_entry_is_not_duplicated() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker)

        await strategy.on_candle("AAPL", _candle(low=100.0))
        await strategy.on_candle("AAPL", _candle(low=99.0))

        assert len(broker.submitted) == 1

    asyncio.run(_run())


def test_earnings_entry_is_skipped_when_buying_power_cannot_buy_one_share() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.buying_power = 50
        strategy = _strategy(broker)

        await strategy.on_candle("AAPL", _candle(low=100.0))

        assert broker.submitted == []

    asyncio.run(_run())


def test_earnings_places_take_profit_after_position_is_detected() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.positions = [Position(ticker="AAPL", quantity=10, average_cost=100.0)]
        strategy = _strategy(broker)

        await strategy.on_tick(_tick(price=101.0))

        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.side == OrderSide.SELL
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 108.0
        assert request.quantity == 10
        assert request.time_in_force == TimeInForce.DAY
        assert request.extended_hours is True

    asyncio.run(_run())


def test_earnings_synthetic_stop_cancels_tp_and_places_extended_hours_limit_sell() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.positions = [Position(ticker="AAPL", quantity=10, average_cost=100.0)]
        broker.portfolio.open_orders = [
            OrderResponse(
                order_id="tp-1",
                ticker="AAPL",
                quantity=10,
                filled_quantity=0,
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
                limit_price=108.0,
                time_in_force=TimeInForce.DAY,
            )
        ]
        strategy = _strategy(broker)
        strategy._take_profit_order_ids["AAPL"] = "tp-1"

        await strategy.on_tick(_tick(price=95.5))

        assert broker.cancelled_order_ids == ["tp-1"]
        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.side == OrderSide.SELL
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 95.5
        assert request.quantity == 10
        assert request.time_in_force == TimeInForce.DAY
        assert request.extended_hours is True
        assert strategy._realtime_provider.unsubscribed == ["AAPL"]  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_earnings_unsubscribes_recovered_ticker_after_position_and_orders_are_gone() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker)
        strategy._exit_monitoring_tickers.add("AAPL")
        strategy._orders_placed.add("AAPL")

        await strategy.on_tick(_tick_at(100.0, datetime(2026, 1, 2, 8, 0, tzinfo=NY_TZ)))

        assert strategy._realtime_provider.unsubscribed == ["AAPL"]  # type: ignore[attr-defined]
        assert "AAPL" not in strategy._exit_monitoring_tickers

    asyncio.run(_run())
