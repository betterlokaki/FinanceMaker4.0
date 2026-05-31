"""Integration-style tests for the shared live strategy runner wiring."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import random
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.cache.abstracts import ITickerCache
from common.models.order import OrderSide, OrderStatus, OrderType
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.models.scanner_params import ScannerParams
from common.settings import AIScannerConfig, OrderParamsConfig, PortfolioAllocationConfig, settings
from pullers.realtime.abstracts.realtime_provider_base import RealtimeProviderBase
from run_live_strategies_menu import (
    LiveStrategySelection,
    create_live_strategies,
    initialize_live_strategies,
)
from strategy.earning_strategy import EarningStrategy
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

NY_TZ = ZoneInfo("America/New_York")


class MockRealtimeProvider(RealtimeProviderBase):
    def __init__(self) -> None:
        super().__init__()
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

    async def emit_tick(self, data: PricingData) -> None:
        await self._dispatch_tick(data)


class MockMarketProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[str, datetime, datetime, Period]] = []

    async def get_prices(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        period: Period,
    ) -> pd.DataFrame:
        self.requests.append((ticker, start_time, end_time, period))
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "period"])


class MockEarningsScanner:
    def __init__(self, tickers: list[str]) -> None:
        self._tickers = tickers
        self.scan_calls = 0

    async def scan(self, _params: ScannerParams) -> list[str]:
        self.scan_calls += 1
        return self._tickers


class EmptyTickerCache(ITickerCache):
    def load_tickers(self, _target_date: Any) -> list[str] | None:
        return None

    def save_tickers(self, _tickers: list[str], _target_date: Any) -> None:
        return None


class MockBroker:
    def __init__(self) -> None:
        self.portfolio = Portfolio(buying_power=100_000)
        self.submitted: list[OrderRequest] = []
        self.portfolio_refreshes = 0

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
            order_id=f"mock-order-{len(self.submitted)}",
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
        return OrderResponse(
            order_id=order_id,
            ticker="AAPL",
            quantity=0,
            filled_quantity=0,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
        )

    async def get_order(self, _order_id: str) -> OrderResponse:
        raise NotImplementedError

    async def get_portfolio(self) -> Portfolio:
        self.portfolio_refreshes += 1
        return self.portfolio

    async def get_open_orders(self) -> list[OrderResponse]:
        return self.portfolio.open_orders

    async def get_buying_power(self) -> float:
        return self.portfolio.buying_power

    async def get_pnl_summary(self, _since_date: Any) -> PnlSummary:
        raise NotImplementedError


def test_mag7_and_earnings_both_initialize_and_receive_mock_market_tick() -> None:
    async def _run() -> None:
        previous_eod_enabled = settings.eod_report.enabled
        settings.eod_report.enabled = False
        started = []
        try:
            broker = MockBroker()
            realtime_provider = MockRealtimeProvider()
            market_provider = MockMarketProvider()
            earnings_scanner = MockEarningsScanner(["AAPL"])

            strategies = create_live_strategies(
                LiveStrategySelection.BOTH,
                broker=broker,  # type: ignore[arg-type]
                realtime_provider=realtime_provider,  # type: ignore[arg-type]
                market_provider=market_provider,  # type: ignore[arg-type]
                earnings_scanner=earnings_scanner,  # type: ignore[arg-type]
                ticker_cache=EmptyTickerCache(),
                ai_scanner_config=AIScannerConfig(scan_passes=1),
                portfolio_allocation_config=PortfolioAllocationConfig(),
                order_params_config=OrderParamsConfig(),
            )

            started = await initialize_live_strategies(strategies)

            assert len(started) == 2
            mag7 = next(
                strategy
                for strategy in started
                if isinstance(strategy, Mag7EmaSlopeRegimeLiveStrategy)
            )
            earnings = next(strategy for strategy in started if isinstance(strategy, EarningStrategy))

            assert mag7.is_initialized is True
            assert earnings.is_initialized is True
            assert earnings_scanner.scan_calls == 1
            assert set(realtime_provider.subscribed_tickers) == {
                "AAPL",
                "MSFT",
                "NVDA",
                "AMZN",
                "META",
                "TSLA",
                "GOOGL",
            }

            aapl_callbacks = realtime_provider._subscriptions["AAPL"]
            callback_owner_names = {
                type(getattr(callback, "__self__")).__name__
                for callback in aapl_callbacks
            }
            assert callback_owner_names == {
                "Mag7EmaSlopeRegimeLiveStrategy",
                "EarningStrategy",
            }
            assert len(aapl_callbacks) == 2

            await realtime_provider.emit_tick(
                PricingData(
                    id="AAPL",
                    price=100.0,
                    time=datetime(2026, 5, 11, 9, 31, tzinfo=NY_TZ),
                    last_size=10,
                )
            )

            assert "AAPL" in mag7._hourly_states
            assert "AAPL" in earnings._seen_tick_tickers
            assert ("AAPL", datetime(2026, 5, 11, tzinfo=NY_TZ).date()) in (
                earnings._entry_candle_states
            )
        finally:
            for strategy in reversed(started):
                await strategy.shutdown()
            settings.eod_report.enabled = previous_eod_enabled

    asyncio.run(_run())


def test_mag7_and_earnings_both_process_random_mock_market_session() -> None:
    async def _run() -> None:
        previous_eod_enabled = settings.eod_report.enabled
        settings.eod_report.enabled = False
        started = []
        try:
            broker = MockBroker()
            realtime_provider = MockRealtimeProvider()
            market_provider = MockMarketProvider()
            earnings_scanner = MockEarningsScanner(["AAPL"])
            rng = random.Random(7)
            session_start = datetime(2026, 5, 11, 9, 30, tzinfo=NY_TZ)
            prices = [round(100.0 + rng.uniform(-1.5, 1.5), 2) for _ in range(6)]

            strategies = create_live_strategies(
                LiveStrategySelection.BOTH,
                broker=broker,  # type: ignore[arg-type]
                realtime_provider=realtime_provider,  # type: ignore[arg-type]
                market_provider=market_provider,  # type: ignore[arg-type]
                earnings_scanner=earnings_scanner,  # type: ignore[arg-type]
                ticker_cache=EmptyTickerCache(),
                ai_scanner_config=AIScannerConfig(scan_passes=1),
                portfolio_allocation_config=PortfolioAllocationConfig(),
                order_params_config=OrderParamsConfig(),
            )
            started = await initialize_live_strategies(strategies)
            mag7 = next(
                strategy
                for strategy in started
                if isinstance(strategy, Mag7EmaSlopeRegimeLiveStrategy)
            )
            earnings = next(strategy for strategy in started if isinstance(strategy, EarningStrategy))

            for offset, price in enumerate(prices):
                await realtime_provider.emit_tick(
                    PricingData(
                        id="AAPL",
                        price=price,
                        time=session_start + timedelta(minutes=offset),
                        last_size=10 + offset,
                    )
                )

            assert "AAPL" in mag7._hourly_states
            assert float(mag7._hourly_states["AAPL"]["close"]) == prices[-1]
            assert "AAPL" in earnings._seen_tick_tickers
            assert ("AAPL", session_start.date()) in earnings._entry_candles_processed
            assert ("AAPL", session_start.date()) not in earnings._entry_candle_states

            assert len(broker.submitted) == 1
            request = broker.submitted[0]
            assert request.ticker == "AAPL"
            assert request.side == OrderSide.BUY
            assert request.order_type == OrderType.LIMIT
            assert request.limit_price == min(prices[:5])
            assert request.quantity >= 1
        finally:
            for strategy in reversed(started):
                await strategy.shutdown()
            settings.eod_report.enabled = previous_eod_enabled

    asyncio.run(_run())
