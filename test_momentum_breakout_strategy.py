"""Tests for the isolated momentum breakout live strategy."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.settings import settings
from strategy.momentum_breakout_strategy import MomentumBreakoutLiveStrategy

NY_TZ = ZoneInfo("America/New_York")


class FakeRealtimeProvider:
    def __init__(self) -> None:
        self.subscribed: list[list[str]] = []
        self.unsubscribed: list[list[str]] = []

    async def subscribe(self, tickers: list[str], _on_tick: Any) -> None:
        self.subscribed.append(tickers)
        return None

    async def unsubscribe(self, tickers: list[str], _on_tick: Any | None = None) -> None:
        self.unsubscribed.append(tickers)
        return None

    async def disconnect(self) -> None:
        return None


class FakeMarketProvider:
    def __init__(self, data: dict[tuple[str, Period], pd.DataFrame] | None = None) -> None:
        self.data = data or {}

    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period,
    ) -> pd.DataFrame:
        return self.data.get(
            (ticker.upper(), period),
            pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
        )


class FakeBroker:
    def __init__(self, *, cash_balance: float = 100_000.0, buying_power: float = 999_999.0) -> None:
        self.portfolio = Portfolio(cash_balance=cash_balance, buying_power=buying_power)
        self.submitted: list[OrderRequest] = []
        self.get_buying_power_calls = 0

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
            order_id=f"momentum-order-{len(self.submitted)}",
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

    async def get_orders_between(
        self,
        after: datetime,
        until: datetime,
    ) -> list[OrderResponse]:
        raise NotImplementedError((after, until))

    async def get_buying_power(self) -> float:
        self.get_buying_power_calls += 1
        raise AssertionError("Momentum strategy must not use buying power")

    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        raise NotImplementedError(since_date)


def _strategy(
    broker: FakeBroker | None = None,
    market_provider: FakeMarketProvider | None = None,
    realtime_provider: FakeRealtimeProvider | None = None,
) -> MomentumBreakoutLiveStrategy:
    return MomentumBreakoutLiveStrategy(
        realtime_provider=realtime_provider or FakeRealtimeProvider(),  # type: ignore[arg-type]
        market_provider=market_provider or FakeMarketProvider(),  # type: ignore[arg-type]
        broker=broker or FakeBroker(),  # type: ignore[arg-type]
        now_provider=lambda: datetime(2026, 5, 6, 9, 25, tzinfo=NY_TZ),
    )


def _daily_frame(
    *,
    start_price: float = 80.0,
    previous_close: float = 100.0,
    latest_volume: int = 1_000_000,
) -> pd.DataFrame:
    index = pd.date_range(
        end=datetime(2026, 5, 5, 16, 0, tzinfo=NY_TZ),
        periods=80,
        freq="B",
    )
    closes = pd.Series(
        [start_price + (previous_close - start_price) * i / 79 for i in range(80)],
        index=index,
    )
    volumes = [1_000_000] * 79 + [latest_volume]
    return pd.DataFrame(
        {
            "open": closes * 0.99,
            "high": closes * 1.01,
            "low": closes * 0.98,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _intraday_frame(
    *,
    current_price: float,
    today_volume: int,
    prior_volume: int = 1_000,
) -> pd.DataFrame:
    rows: list[tuple[datetime, float, int]] = []
    for day in (29, 30):
        rows.append((datetime(2026, 4, day, 4, 0, tzinfo=NY_TZ), 98.0, prior_volume // 2))
        rows.append((datetime(2026, 4, day, 8, 0, tzinfo=NY_TZ), 99.0, prior_volume // 2))
    rows.extend(
        [
            (datetime(2026, 5, 6, 4, 0, tzinfo=NY_TZ), current_price - 1.0, today_volume // 2),
            (datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ), current_price, today_volume // 2),
        ]
    )
    return pd.DataFrame(
        {
            "open": [price for _, price, _ in rows],
            "high": [price for _, price, _ in rows],
            "low": [price for _, price, _ in rows],
            "close": [price for _, price, _ in rows],
            "volume": [volume for _, _, volume in rows],
        },
        index=pd.DatetimeIndex([timestamp for timestamp, _, _ in rows]),
    )


def _tick(
    price: float,
    timestamp: datetime,
    last_size: int = 100,
    ticker: str = "NVDA",
) -> PricingData:
    return PricingData(
        id=ticker,
        price=price,
        time=timestamp,
        last_size=last_size,
    )


def _opening_range_history_frame() -> pd.DataFrame:
    rows = [
        (datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ), 100.0, 101.0, 99.5, 100.5, 1_000),
        (datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ), 100.5, 101.5, 100.0, 101.0, 1_000),
        (datetime(2026, 5, 6, 9, 32, tzinfo=NY_TZ), 101.0, 102.0, 100.5, 101.5, 1_000),
        (datetime(2026, 5, 6, 9, 33, tzinfo=NY_TZ), 101.5, 102.5, 101.0, 102.0, 1_000),
        (datetime(2026, 5, 6, 9, 34, tzinfo=NY_TZ), 102.0, 103.0, 101.5, 102.5, 1_000),
    ]
    return pd.DataFrame(
        {
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
            "volume": [row[5] for row in rows],
        },
        index=pd.DatetimeIndex([row[0] for row in rows]),
    )


def test_load_tickers_returns_hard_coded_momentum_universe() -> None:
    async def _run() -> None:
        strategy = _strategy()

        assert await strategy.load_tickers() == [
            "NVDA",
            "AMD",
            "PLTR",
            "RKLB",
            "ASTS",
            "SMR",
            "OKLO",
            "HUT",
            "CRWV",
        ]

    asyncio.run(_run())


def test_initialize_subscribes_hard_coded_universe_when_scan_has_no_candidates() -> None:
    async def _run() -> None:
        previous_eod_enabled = settings.eod_report.enabled
        settings.eod_report.enabled = False
        realtime_provider = FakeRealtimeProvider()
        strategy = _strategy(realtime_provider=realtime_provider)
        try:
            await strategy.initialize()

            expected = list(MomentumBreakoutLiveStrategy.MOMENTUM_TICKERS)
            assert realtime_provider.subscribed == [expected]
            assert strategy._active_candidates == set(expected)
        finally:
            await strategy.shutdown()
            settings.eod_report.enabled = previous_eod_enabled

    asyncio.run(_run())


def test_hard_coded_momentum_tick_is_logged_when_listening(caplog: Any) -> None:
    async def _run() -> None:
        caplog.set_level(logging.INFO)
        strategy = _strategy()
        strategy._active_candidates = {"NVDA"}

        await strategy.on_tick(_tick(100.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))

        assert "MomentumBreakoutLiveStrategy live tick NVDA" in caplog.text
        assert "state=active_breakout_watch" in caplog.text

    asyncio.run(_run())


def test_scan_ranking_applies_rvol_ema_and_high_filters() -> None:
    async def _run() -> None:
        as_of = datetime(2026, 5, 6, 9, 25, tzinfo=NY_TZ)
        market_provider = FakeMarketProvider(
            {
                ("NVDA", Period.DAILY): _daily_frame(previous_close=100.0),
                ("NVDA", Period.MINUTE): _intraday_frame(
                    current_price=104.0,
                    today_volume=2_500,
                ),
                ("AMD", Period.DAILY): _daily_frame(previous_close=100.0),
                ("AMD", Period.MINUTE): _intraday_frame(
                    current_price=104.0,
                    today_volume=1_500,
                ),
            }
        )
        strategy = _strategy(market_provider=market_provider)

        candidates = await strategy.scan_candidates()

        assert [candidate.ticker for candidate in candidates] == ["NVDA"]
        assert candidates[0].rvol == 2.5
        assert candidates[0].one_day_return > 0
        assert candidates[0].distance_from_52_week_high <= 0.03
        assert candidates[0].current_price > candidates[0].ema20
        assert candidates[0].current_price > candidates[0].ema50

    asyncio.run(_run())


def test_confirmed_breakout_uses_cash_balance_not_buying_power_and_reserves_cash() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0, buying_power=1_000_000.0)
        strategy = _strategy(broker=broker)
        strategy._active_candidates = {"NVDA"}

        await strategy.on_tick(_tick(100.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(102.0, datetime(2026, 5, 6, 9, 34, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(105.0, datetime(2026, 5, 6, 9, 35, tzinfo=NY_TZ)))

        assert broker.get_buying_power_calls == 0
        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.ticker == "NVDA"
        assert request.side == OrderSide.BUY
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 105.0
        assert request.quantity == int((100_000.0 * 0.25) / 105.0)
        assert sum(strategy._reserved_cash.values()) == request.quantity * 105.0

    asyncio.run(_run())


def test_each_trade_uses_twenty_five_percent_of_cash_balance_with_reservations() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0, buying_power=1_000_000.0)
        strategy = _strategy(broker=broker)
        strategy._active_candidates = {"NVDA", "AMD"}

        for ticker in ("NVDA", "AMD"):
            await strategy.on_tick(
                _tick(100.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ), ticker=ticker)
            )
            await strategy.on_tick(
                _tick(102.0, datetime(2026, 5, 6, 9, 34, tzinfo=NY_TZ), ticker=ticker)
            )
            await strategy.on_tick(
                _tick(105.0, datetime(2026, 5, 6, 9, 35, tzinfo=NY_TZ), ticker=ticker)
            )

        expected_quantity = int((100_000.0 * 0.25) / 105.0)
        assert broker.get_buying_power_calls == 0
        assert len(broker.submitted) == 2
        assert [request.quantity for request in broker.submitted] == [
            expected_quantity,
            expected_quantity,
        ]
        assert sum(strategy._reserved_cash.values()) == expected_quantity * 105.0 * 2

    asyncio.run(_run())


def test_late_first_tick_recovers_opening_range_from_yahoo_history() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0)
        market_provider = FakeMarketProvider(
            {("NVDA", Period.MINUTE): _opening_range_history_frame()}
        )
        strategy = _strategy(broker=broker, market_provider=market_provider)
        strategy._active_candidates = {"NVDA"}

        await strategy.on_tick(_tick(104.0, datetime(2026, 5, 6, 9, 36, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].limit_price == 104.0
        state = strategy._opening_states["NVDA"]
        assert state.opening_complete is True
        assert state.opening_high == 103.0
        assert state.vwap < 104.0

    asyncio.run(_run())


def test_late_first_tick_without_opening_history_does_not_submit_order() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0)
        strategy = _strategy(broker=broker)
        strategy._active_candidates = {"NVDA"}

        await strategy.on_tick(_tick(104.0, datetime(2026, 5, 6, 9, 36, tzinfo=NY_TZ)))

        assert broker.submitted == []
        state = strategy._opening_states["NVDA"]
        assert state.opening_complete is False
        assert state.opening_volume == 0

    asyncio.run(_run())


def test_duplicate_confirmed_breakout_does_not_submit_second_order() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)
        strategy._active_candidates = {"NVDA"}

        await strategy.on_tick(_tick(100.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(102.0, datetime(2026, 5, 6, 9, 34, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(105.0, datetime(2026, 5, 6, 9, 35, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(106.0, datetime(2026, 5, 6, 9, 36, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1

    asyncio.run(_run())


def test_liquid_and_volatile_tickers_use_two_to_one_reward_risk() -> None:
    strategy = _strategy()

    liquid = strategy._build_entry_order_request("NVDA", quantity=10, entry_price=100.0)
    volatile = strategy._build_entry_order_request("RKLB", quantity=10, entry_price=100.0)

    assert liquid.time_in_force == TimeInForce.GTC
    assert liquid.stop_loss_price == 99.0
    assert liquid.take_profit_price == 102.0
    assert volatile.stop_loss_price == 98.0
    assert volatile.take_profit_price == 104.0
