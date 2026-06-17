"""Tests for the isolated pullback trading live strategy."""
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
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.settings import settings
from strategy.pullback_trading_strategy import PullbackSignal, PullbackTradingLiveStrategy
from strategy.pullback_trading_strategy.pullback_trading_live_strategy import (
    PullbackWatchContext,
)

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
            order_id=f"pullback-order-{len(self.submitted)}",
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
        raise AssertionError("Pullback strategy must not use buying power")

    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        raise NotImplementedError(since_date)


def _strategy(
    broker: FakeBroker | None = None,
    market_provider: FakeMarketProvider | None = None,
    realtime_provider: FakeRealtimeProvider | None = None,
    *,
    min_rsi: float = 50.0,
) -> PullbackTradingLiveStrategy:
    return PullbackTradingLiveStrategy(
        realtime_provider=realtime_provider or FakeRealtimeProvider(),  # type: ignore[arg-type]
        market_provider=market_provider or FakeMarketProvider(),  # type: ignore[arg-type]
        broker=broker or FakeBroker(),  # type: ignore[arg-type]
        min_rsi=min_rsi,
        now_provider=lambda: datetime(2026, 5, 6, 10, 0, tzinfo=NY_TZ),
    )


def _daily_pullback_frame(
    *,
    latest_open: float = 100.0,
    latest_high: float = 104.0,
    latest_low: float = 94.0,
    latest_close: float = 102.0,
    include_current_partial: bool = False,
) -> pd.DataFrame:
    index = pd.date_range(
        end=datetime(2026, 5, 5, 16, 0, tzinfo=NY_TZ),
        periods=80,
        freq="B",
    )
    historical_closes = [80.0 + (100.0 - 80.0) * i / 78 for i in range(79)]
    closes = pd.Series(historical_closes + [latest_close], index=index)
    frame = pd.DataFrame(
        {
            "open": closes * 0.99,
            "high": closes * 1.02,
            "low": closes * 0.98,
            "close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=index,
    )
    frame.iloc[-1, frame.columns.get_loc("open")] = latest_open
    frame.iloc[-1, frame.columns.get_loc("high")] = latest_high
    frame.iloc[-1, frame.columns.get_loc("low")] = latest_low
    frame.iloc[-1, frame.columns.get_loc("close")] = latest_close

    if include_current_partial:
        current = pd.DataFrame(
            {
                "open": [120.0],
                "high": [121.0],
                "low": [70.0],
                "close": [75.0],
                "volume": [2_000_000],
            },
            index=pd.DatetimeIndex([datetime(2026, 5, 6, 10, 0, tzinfo=NY_TZ)]),
        )
        frame = pd.concat([frame, current])

    return frame


def _intraday_pullback_recovery_frame() -> pd.DataFrame:
    rows = [
        (datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ), 99.0, 100.0, 99.0, 99.5, 1_000),
        (datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ), 99.5, 100.5, 99.0, 100.0, 1_000),
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


def _signal(ticker: str = "NVDA", entry_price: float = 102.0) -> PullbackSignal:
    return PullbackSignal(
        ticker=ticker,
        signal_date=date(2026, 5, 5),
        entry_price=entry_price,
        ema20=98.0,
        ema50=95.0,
        rsi=60.0,
        open_price=100.0,
        high_price=104.0,
        low_price=94.0,
        close_price=entry_price,
    )


def test_load_tickers_returns_full_pullback_universe() -> None:
    async def _run() -> None:
        strategy = _strategy()

        assert await strategy.load_tickers() == [
            "NVDA",
            "AMD",
            "META",
            "AMZN",
            "TSLA",
            "PLTR",
            "ASTS",
            "RKLB",
            "SMR",
            "OKLO",
        ]

    asyncio.run(_run())


def test_initialize_subscribes_hard_coded_universe_when_scan_has_no_signals() -> None:
    async def _run() -> None:
        previous_eod_enabled = settings.eod_report.enabled
        settings.eod_report.enabled = False
        realtime_provider = FakeRealtimeProvider()
        strategy = _strategy(realtime_provider=realtime_provider)
        try:
            await strategy.initialize()

            assert realtime_provider.subscribed == [list(PullbackTradingLiveStrategy.PULLBACK_TICKERS)]
            assert strategy.active_signals == {}
        finally:
            await strategy.shutdown()
            settings.eod_report.enabled = previous_eod_enabled

    asyncio.run(_run())


def test_no_signal_pullback_tick_logs_without_order(caplog: Any) -> None:
    async def _run() -> None:
        caplog.set_level(logging.INFO)
        broker = FakeBroker()
        strategy = _strategy(broker=broker)

        await strategy.on_tick(_tick(108.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))

        assert broker.submitted == []
        assert "PullbackTradingLiveStrategy live tick NVDA" in caplog.text
        assert "state=no_pullback_context" in caplog.text

    asyncio.run(_run())


def test_live_pullback_confirmation_enters_without_daily_signal() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0)
        strategy = _strategy(broker=broker)
        strategy._watch_contexts = {
            "NVDA": PullbackWatchContext(
                ticker="NVDA",
                signal_date=date(2026, 5, 5),
                previous_close=103.0,
                ema20=100.0,
                ema50=95.0,
                rsi=60.0,
            )
        }

        await strategy.on_tick(_tick(99.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(101.0, datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].ticker == "NVDA"
        assert broker.submitted[0].limit_price == 101.0

    asyncio.run(_run())


def test_late_live_pullback_confirmation_recovers_intraday_low_from_history() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0)
        market_provider = FakeMarketProvider(
            {("NVDA", Period.MINUTE): _intraday_pullback_recovery_frame()}
        )
        strategy = _strategy(broker=broker, market_provider=market_provider)
        strategy._watch_contexts = {
            "NVDA": PullbackWatchContext(
                ticker="NVDA",
                signal_date=date(2026, 5, 5),
                previous_close=103.0,
                ema20=100.0,
                ema50=95.0,
                rsi=60.0,
            )
        }

        await strategy.on_tick(_tick(101.0, datetime(2026, 5, 6, 9, 36, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].limit_price == 101.0

    asyncio.run(_run())


def test_valid_daily_pullback_signal_is_detected() -> None:
    async def _run() -> None:
        market_provider = FakeMarketProvider(
            {("NVDA", Period.DAILY): _daily_pullback_frame()}
        )
        strategy = _strategy(market_provider=market_provider)

        signals = await strategy.scan_signals()

        assert [signal.ticker for signal in signals] == ["NVDA"]
        signal = signals[0]
        assert signal.entry_price == 102.0
        assert signal.low_price <= signal.ema20
        assert signal.close_price >= signal.ema20
        assert signal.close_price > signal.ema50
        assert signal.rsi > 50.0

    asyncio.run(_run())


def test_signal_scan_ignores_current_day_partial_daily_bar() -> None:
    strategy = _strategy()

    signal = strategy._signal_from_daily_frame(
        ticker="NVDA",
        daily_df=_daily_pullback_frame(include_current_partial=True),
        as_of=datetime(2026, 5, 6, 10, 0, tzinfo=NY_TZ),
    )

    assert signal is not None
    assert signal.signal_date == date(2026, 5, 5)
    assert signal.entry_price == 102.0


def test_signal_rejections_cover_trend_touch_rsi_and_bullish_filters() -> None:
    as_of = datetime(2026, 5, 6, 10, 0, tzinfo=NY_TZ)

    assert (
        _strategy()._signal_from_daily_frame(
            ticker="NVDA",
            daily_df=_daily_pullback_frame(latest_close=80.0, latest_open=79.0, latest_low=75.0),
            as_of=as_of,
        )
        is None
    )
    assert (
        _strategy()._signal_from_daily_frame(
            ticker="NVDA",
            daily_df=_daily_pullback_frame(latest_low=101.5, latest_high=106.0),
            as_of=as_of,
        )
        is None
    )
    assert (
        _strategy(min_rsi=101.0)._signal_from_daily_frame(
            ticker="NVDA",
            daily_df=_daily_pullback_frame(),
            as_of=as_of,
        )
        is None
    )
    assert (
        _strategy()._signal_from_daily_frame(
            ticker="NVDA",
            daily_df=_daily_pullback_frame(latest_open=103.0, latest_close=102.0),
            as_of=as_of,
        )
        is None
    )


def test_first_rth_tick_uses_cash_balance_not_buying_power_and_reserves_cash() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0, buying_power=1_000_000.0)
        strategy = _strategy(broker=broker)
        strategy._active_signals = {"NVDA": _signal(entry_price=102.0)}

        await strategy.on_tick(_tick(108.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))

        assert broker.get_buying_power_calls == 0
        assert len(broker.submitted) == 1
        request = broker.submitted[0]
        assert request.ticker == "NVDA"
        assert request.side == OrderSide.BUY
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 102.0
        assert request.quantity == int((100_000.0 * 0.25) / 102.0)
        assert sum(strategy._reserved_cash.values()) == request.quantity * 102.0

    asyncio.run(_run())


def test_each_trade_uses_twenty_five_percent_of_cash_balance_with_reservations() -> None:
    async def _run() -> None:
        broker = FakeBroker(cash_balance=100_000.0, buying_power=1_000_000.0)
        strategy = _strategy(broker=broker)
        strategy._active_signals = {
            "NVDA": _signal("NVDA", entry_price=100.0),
            "AMD": _signal("AMD", entry_price=100.0),
        }

        await strategy.on_tick(_tick(105.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ), ticker="NVDA"))
        await strategy.on_tick(_tick(104.0, datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ), ticker="AMD"))

        expected_quantity = int((100_000.0 * 0.25) / 100.0)
        assert broker.get_buying_power_calls == 0
        assert [request.quantity for request in broker.submitted] == [
            expected_quantity,
            expected_quantity,
        ]
        assert sum(strategy._reserved_cash.values()) == expected_quantity * 100.0 * 2

    asyncio.run(_run())


def test_duplicate_tick_does_not_submit_second_order() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        strategy = _strategy(broker=broker)
        strategy._active_signals = {"NVDA": _signal()}

        await strategy.on_tick(_tick(108.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))
        await strategy.on_tick(_tick(109.0, datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)))

        assert len(broker.submitted) == 1

    asyncio.run(_run())


def test_existing_exposure_or_order_skips_submission() -> None:
    async def _run() -> None:
        broker = FakeBroker()
        broker.portfolio.positions.append(
            Position(ticker="NVDA", quantity=1, average_cost=100.0)
        )
        strategy = _strategy(broker=broker)
        strategy._active_signals = {"NVDA": _signal()}

        await strategy.on_tick(_tick(108.0, datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ)))

        assert broker.submitted == []

    asyncio.run(_run())


def test_entry_order_uses_one_point_five_stop_and_four_percent_target() -> None:
    strategy = _strategy()

    order_request = strategy._build_entry_order_request(
        ticker="NVDA",
        quantity=10,
        entry_price=100.0,
    )

    assert order_request.time_in_force == TimeInForce.GTC
    assert order_request.limit_price == 100.0
    assert order_request.stop_loss_price == 98.5
    assert order_request.take_profit_price == 104.0
    assert order_request.take_profit_rth is True
    assert order_request.stop_loss_rth is False
