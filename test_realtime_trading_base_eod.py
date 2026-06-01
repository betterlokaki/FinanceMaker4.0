"""Unit tests for RealTimeTradingBase end-of-day reporting helpers."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.pnl_summary import PnlSummary
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase
from strategy.abstracts.realtime_trading_base import StrategyTradeRecord


class _DummyRealtimeProvider:
    async def subscribe(self, tickers, callback):  # noqa: ANN001
        return None

    async def unsubscribe(self, tickers, callback=None):  # noqa: ANN001
        return None


class _DummyStrategy(RealTimeTradingBase):
    async def load_tickers(self) -> list[str]:
        return []

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:  # noqa: ARG002
        return None


class _RecordingRealtimeProvider:
    def __init__(self) -> None:
        self.subscribed_tickers: list[str] = []

    async def subscribe(self, tickers, callback):  # noqa: ANN001
        self.subscribed_tickers = list(tickers)

    async def unsubscribe(self, tickers, callback=None):  # noqa: ANN001
        return None


class _HookStrategy(RealTimeTradingBase):
    def __init__(self, realtime_provider: _RecordingRealtimeProvider) -> None:
        super().__init__(realtime_provider)
        self.events: list[tuple[str, list[str]]] = []

    async def load_tickers(self) -> list[str]:
        return ["aapl"]

    async def _before_subscribe(self) -> None:
        self.events.append(("before_subscribe", self._tickers.copy()))
        self._tickers = [ticker.upper() for ticker in self._tickers]

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:  # noqa: ARG002
        return None


def test_resolve_since_date_for_current_year() -> None:
    strategy = _DummyStrategy(_DummyRealtimeProvider())
    resolved = strategy._resolve_since_date(date(2026, 4, 23))
    assert resolved == date(2026, 4, 1)


def test_initialize_runs_pre_subscribe_hook_before_registering_callback() -> None:
    async def _run() -> None:
        provider = _RecordingRealtimeProvider()
        strategy = _HookStrategy(provider)

        await strategy.initialize()

        assert strategy.events == [("before_subscribe", ["aapl"])]
        assert provider.subscribed_tickers == ["AAPL"]
        assert strategy.is_initialized is True

    asyncio.run(_run())


def test_resolve_since_date_rolls_back_if_before_april() -> None:
    strategy = _DummyStrategy(_DummyRealtimeProvider())
    resolved = strategy._resolve_since_date(date(2026, 3, 15))
    assert resolved == date(2025, 4, 1)


def test_build_end_of_day_email_includes_trade_lines() -> None:
    strategy = _DummyStrategy(_DummyRealtimeProvider())
    summary = PnlSummary(
        as_of_date=date(2026, 4, 23),
        since_date=date(2026, 4, 1),
        currency="USD",
        daily_pnl=125.4,
        pnl_since_date=982.1,
        baseline_date=date(2026, 3, 31),
        baseline_nav=10000.0,
        current_nav=10982.1,
    )
    trades = [
        StrategyTradeRecord(
            timestamp_utc=datetime(2026, 4, 23, 15, 20, tzinfo=timezone.utc),
            ticker="AAPL",
            side="BUY",
            quantity=10,
            order_type="LIMIT",
            requested_price=200.5,
            order_id="12345",
            status="SUBMITTED",
            note="signal-entry",
        )
    ]

    subject, body = strategy._build_end_of_day_email(summary, trades)

    assert "EOD Report" in subject
    assert "Today's P&L: +USD 125.40" in body
    assert "P&L since 2026-04-01: +USD 982.10" in body
    assert "AAPL BUY qty=10" in body


def test_record_submitted_trade_saved_for_day() -> None:
    async def _run() -> None:
        strategy = _DummyStrategy(_DummyRealtimeProvider())
        request = OrderRequest(
            ticker="NVDA",
            quantity=3,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=110.0,
            time_in_force=TimeInForce.DAY,
        )
        response = OrderResponse(
            order_id="abc123",
            ticker="NVDA",
            quantity=3,
            filled_quantity=0,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.SUBMITTED,
            limit_price=110.0,
        )
        await strategy._record_submitted_trade(request, response, note="signal-entry")

        now_ny_date = datetime.now(timezone.utc).astimezone(strategy._market_calendar.timezone).date()
        records = await strategy._trade_records_for_day(now_ny_date)
        assert len(records) == 1
        assert records[0].ticker == "NVDA"
        assert records[0].order_id == "abc123"

    asyncio.run(_run())
