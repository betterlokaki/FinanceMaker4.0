"""E2E-style tests for live strategies using a random realtime provider."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from common.models.order import OrderStatus
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.settings import settings
from pullers.realtime.abstracts.realtime_provider_base import RealtimeProviderBase
from strategy.momentum_breakout_strategy import MomentumBreakoutLiveStrategy
from strategy.pullback_trading_strategy import PullbackTradingLiveStrategy

logger = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")


class RandomTickRealtimeProvider(RealtimeProviderBase):
    """Realtime provider that emits seeded random ticks to real subscribers."""

    def __init__(
        self,
        *,
        seed: int,
        min_delay_seconds: float = 0.0,
        max_delay_seconds: float = 0.001,
    ) -> None:
        super().__init__()
        self._rng = random.Random(seed)
        self._min_delay_seconds = min_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self.sent_subscribes: list[list[str]] = []
        self.sent_unsubscribes: list[list[str]] = []

    async def _connect(self) -> None:
        self._is_connected = True

    async def _send_subscribe_message(self, tickers: list[str]) -> None:
        self.sent_subscribes.append(tickers)

    async def _send_unsubscribe_message(self, tickers: list[str]) -> None:
        self.sent_unsubscribes.append(tickers)

    async def disconnect(self) -> None:
        self._is_connected = False
        self._subscriptions.clear()

    async def emit_price_bands(
        self,
        *,
        ticker: str,
        start_time: datetime,
        price_bands: Sequence[tuple[float, float]],
        step: timedelta,
        last_size_band: tuple[int, int] = (100, 300),
    ) -> list[float]:
        prices: list[float] = []
        for index, (low, high) in enumerate(price_bands):
            delay = self._rng.uniform(self._min_delay_seconds, self._max_delay_seconds)
            await asyncio.sleep(delay)
            price = round(self._rng.uniform(low, high), 2)
            timestamp = start_time + (step * index)
            prices.append(price)
            logger.info(
                "Generated random tick %s price=%.2f delay=%.4fs timestamp=%s",
                ticker,
                price,
                delay,
                timestamp.isoformat(),
            )
            await self._dispatch_tick(
                PricingData(
                    id=ticker,
                    price=price,
                    time=timestamp,
                    last_size=self._rng.randint(*last_size_band),
                )
            )
        return prices


class ScriptedMarketProvider:
    """Market provider backed by deterministic OHLCV frames."""

    def __init__(self, frames: dict[tuple[str, Period], pd.DataFrame]) -> None:
        self._frames = frames

    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period,
    ) -> pd.DataFrame:
        return self._frames.get(
            (ticker.upper(), period),
            pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
        )


class RecordingBroker:
    """In-memory broker adapter that captures orders without external calls."""

    def __init__(self, *, cash_balance: float = 100_000.0) -> None:
        self.portfolio = Portfolio(cash_balance=cash_balance, buying_power=cash_balance)
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
            order_id=f"test-order-{len(self.submitted)}",
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
        return self.portfolio.buying_power

    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        raise NotImplementedError(since_date)


@pytest.fixture(autouse=True)
def _disable_eod_report() -> Any:
    previous = settings.eod_report.enabled
    settings.eod_report.enabled = False
    try:
        yield
    finally:
        settings.eod_report.enabled = previous


def test_momentum_real_strategy_orders_from_random_realtime_ticks(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def _run() -> None:
        feed = RandomTickRealtimeProvider(seed=42)
        broker = RecordingBroker()
        strategy = MomentumBreakoutLiveStrategy(
            realtime_provider=feed,
            market_provider=ScriptedMarketProvider(
                {
                    ("NVDA", Period.DAILY): _momentum_daily_frame(),
                    ("NVDA", Period.MINUTE): _momentum_scan_intraday_frame(),
                }
            ),
            broker=broker,
            min_rvol=1.0,
            now_provider=lambda: datetime(2026, 5, 6, 9, 25, tzinfo=NY_TZ),
        )

        await strategy.initialize()
        assert "NVDA" in feed.subscribed_tickers
        assert "NVDA" in strategy._active_candidates

        prices = await feed.emit_price_bands(
            ticker="NVDA",
            start_time=datetime(2026, 5, 6, 9, 30, tzinfo=NY_TZ),
            price_bands=((99.8, 100.2), (101.7, 102.3), (105.8, 106.4)),
            step=timedelta(minutes=4, seconds=30),
        )

        assert len(broker.submitted) == 1
        assert broker.submitted[0].ticker == "NVDA"
        assert broker.submitted[0].limit_price == prices[-1]
        await strategy.shutdown()

    asyncio.run(_run())

    logs = caplog.text
    assert "Registered realtime callback MomentumBreakoutLiveStrategy.on_tick" in logs
    assert "Generated random tick NVDA price=" in logs
    assert "Placed momentum bracket for NVDA" in logs


def test_pullback_real_strategy_orders_from_random_realtime_tick(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def _run() -> None:
        feed = RandomTickRealtimeProvider(seed=7)
        broker = RecordingBroker()
        strategy = PullbackTradingLiveStrategy(
            realtime_provider=feed,
            market_provider=ScriptedMarketProvider(
                {("NVDA", Period.DAILY): _pullback_daily_frame()}
            ),
            broker=broker,
            now_provider=lambda: datetime(2026, 5, 6, 10, 0, tzinfo=NY_TZ),
        )

        await strategy.initialize()
        assert "NVDA" in feed.subscribed_tickers
        assert "NVDA" in strategy.active_signals

        await feed.emit_price_bands(
            ticker="NVDA",
            start_time=datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ),
            price_bands=((107.8, 108.6),),
            step=timedelta(minutes=1),
        )

        assert len(broker.submitted) == 1
        assert broker.submitted[0].ticker == "NVDA"
        assert broker.submitted[0].limit_price == 102.0
        await strategy.shutdown()

    asyncio.run(_run())

    logs = caplog.text
    assert "Registered realtime callback PullbackTradingLiveStrategy.on_tick" in logs
    assert "Generated random tick NVDA price=" in logs
    assert "Placed pullback bracket for NVDA" in logs


def _momentum_daily_frame() -> pd.DataFrame:
    index = pd.date_range(
        end=datetime(2026, 5, 5, 16, 0, tzinfo=NY_TZ),
        periods=80,
        freq="B",
    )
    closes = pd.Series(
        [80.0 + (100.0 - 80.0) * i / 79 for i in range(80)],
        index=index,
    )
    return pd.DataFrame(
        {
            "open": closes * 0.99,
            "high": closes * 1.01,
            "low": closes * 0.98,
            "close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=index,
    )


def _momentum_scan_intraday_frame() -> pd.DataFrame:
    rows = [
        (datetime(2026, 4, 29, 4, 0, tzinfo=NY_TZ), 100.0, 1_000),
        (datetime(2026, 4, 30, 4, 0, tzinfo=NY_TZ), 100.0, 1_000),
        (datetime(2026, 5, 6, 4, 0, tzinfo=NY_TZ), 108.0, 2_000),
        (datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ), 110.0, 2_000),
    ]
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


def _pullback_daily_frame() -> pd.DataFrame:
    index = pd.date_range(
        end=datetime(2026, 5, 5, 16, 0, tzinfo=NY_TZ),
        periods=80,
        freq="B",
    )
    closes = pd.Series(
        [80.0 + (100.0 - 80.0) * i / 78 for i in range(79)] + [102.0],
        index=index,
    )
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
    frame.iloc[-1, frame.columns.get_loc("open")] = 100.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 104.0
    frame.iloc[-1, frame.columns.get_loc("low")] = 94.0
    frame.iloc[-1, frame.columns.get_loc("close")] = 102.0
    return frame
