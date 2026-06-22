"""Live 5-minute EMA crossover strategy using Yahoo realtime data and Alpaca."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.period import Period
from common.models.portfolio import Portfolio
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from common.trading.order_request_factory import OrderRequestFactory
from common.trading.position_sizing import PositionSizer
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger = logging.getLogger(__name__)

UTC = timezone.utc
NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_TIME = time(hour=9, minute=30)
REGULAR_CLOSE_TIME = time(hour=16, minute=0)


@dataclass
class _IntradayCandleState:
    start_time_ny: datetime
    end_time_ny: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class EmaCrossoverLiveStrategy(RealTimeTradingBase):
    """Trade 5-minute 9/21 EMA crossovers with standalone trailing exits."""

    EMA_CROSSOVER_TICKERS: tuple[str, ...] = (
        "NVDA",
        "AAPL",
        "TSLA",
        "MSFT",
        "AMD",
        "AMZN",
        "META",
    )
    HISTORY_LOOKBACK_DAYS = 5
    MAX_HISTORY_BARS = 600
    TRANSITION_TIMEOUT_SECONDS = 30.0
    TRANSITION_POLL_SECONDS = 1.0

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        strategy_input: StrategyInputModel,
        *,
        fast_period: int = 9,
        slow_period: int = 21,
        candle_minutes: int = 5,
        trailing_stop_pct: float = 0.02,
        reward_to_risk: float = 3.0,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._strategy_input = strategy_input
        self._fast_period = max(2, int(fast_period))
        self._slow_period = max(self._fast_period + 1, int(slow_period))
        self._candle_minutes = max(1, int(candle_minutes))
        self._trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self._reward_to_risk = max(0.0, float(reward_to_risk))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._candle_states: dict[str, _IntradayCandleState] = {}
        self._close_history: dict[str, list[float]] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}
        self._in_flight: set[str] = set()

    async def load_tickers(self) -> list[str]:
        """Return the fixed EMA crossover ticker universe."""
        return list(self.EMA_CROSSOVER_TICKERS)

    async def _before_subscribe(self) -> None:
        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._candle_states.clear()
        self._close_history = {ticker: [] for ticker in self._tickers}
        self._in_flight.clear()
        await self._bootstrap_history()

    async def on_tick(self, data: PricingData) -> None:
        ticker = data.id.upper()
        if ticker not in self._tickers:
            return

        tick_time_ny = self._ensure_utc(data.time).astimezone(NY_TZ)
        if not self._is_regular_market_time(tick_time_ny):
            return

        await self._manage_open_position_exits(ticker, float(data.price))
        candle = self._update_candle_from_tick(ticker, data, tick_time_ny)
        if candle is not None:
            await self.on_candle(ticker, candle)

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        price = float(candle.close)
        if not math.isfinite(price) or price <= 0:
            return

        history = self._close_history.setdefault(ticker.upper(), [])
        history.append(price)
        if len(history) > self.MAX_HISTORY_BARS:
            del history[: len(history) - self.MAX_HISTORY_BARS]

        signal = self._crossover_signal(history)
        if signal is None:
            return
        await self._process_signal(ticker.upper(), signal, price)

    async def shutdown(self) -> None:
        await super().shutdown()
        self._candle_states.clear()
        self._close_history.clear()
        self._order_locks.clear()
        self._in_flight.clear()

    async def _bootstrap_history(self) -> None:
        end_time = self._ensure_utc(self._now_provider())
        start_time = end_time - timedelta(days=self.HISTORY_LOOKBACK_DAYS)
        for ticker in self._tickers:
            try:
                frame = await self._market_provider.get_prices(
                    ticker=ticker,
                    start_time=start_time,
                    end_time=end_time,
                    period=Period.MINUTE,
                )
                self._close_history[ticker] = self._resampled_closes(frame)
            except Exception as exc:
                logger.warning("Failed to bootstrap EMA history for %s: %s", ticker, exc)
                self._close_history[ticker] = []

    def _update_candle_from_tick(
        self,
        ticker: str,
        data: PricingData,
        tick_time_ny: datetime,
    ) -> CandleStick | None:
        bucket = self._bucket_bounds(tick_time_ny)
        if bucket is None:
            return None
        start_ny, end_ny = bucket
        state = self._candle_states.get(ticker)
        finalized: CandleStick | None = None

        if state is None:
            self._candle_states[ticker] = self._create_state(data, start_ny, end_ny)
            return None
        if state.start_time_ny != start_ny:
            finalized = self._finalize_state(state)
            self._candle_states[ticker] = self._create_state(data, start_ny, end_ny)
            return finalized

        price = float(data.price)
        state.high = max(state.high, price)
        state.low = min(state.low, price)
        state.close = price
        state.volume += max(0, int(data.last_size or 0))
        return None

    async def _process_signal(
        self,
        ticker: str,
        desired_side: OrderSide,
        entry_price: float,
    ) -> None:
        lock = self._get_lock(ticker)
        async with lock:
            if ticker in self._in_flight:
                return
            self._in_flight.add(ticker)
            try:
                portfolio = await self._broker.get_portfolio()
                current_side = self._current_side(portfolio, ticker)
                if current_side == desired_side:
                    return

                open_orders = self._orders_for_ticker(portfolio, ticker)
                if open_orders:
                    await self._cancel_orders(open_orders)

                position = portfolio.get_position(ticker)
                if position is not None and position.quantity != 0:
                    await self._flatten_position(ticker, position)
                    if not await self._wait_until_order_free_and_flat(ticker):
                        return

                portfolio = await self._broker.get_portfolio()
                if portfolio.has_open_order(ticker) or portfolio.has_position(ticker):
                    return

                quantity = PositionSizer.quantity_for_entry(
                    portfolio=portfolio,
                    entry_price=entry_price,
                    strategy_input=self._strategy_input,
                )
                if quantity < 1:
                    return

                request = OrderRequestFactory.simple_limit_entry(
                    ticker=ticker,
                    quantity=quantity,
                    entry_price=entry_price,
                    side=desired_side,
                    time_in_force=TimeInForce.DAY,
                    buy_limit_rth=True,
                )
                response = await self._broker.place_order(request)
                await self._record_submitted_trade(request, response, note="ema-crossover")
            finally:
                self._in_flight.discard(ticker)

    async def _ensure_trailing_stop_for_open_position(self, ticker: str) -> None:
        if self._trailing_stop_pct <= 0:
            return
        portfolio = await self._broker.get_portfolio()
        position = portfolio.get_position(ticker)
        if position is None or position.quantity == 0:
            return
        if self._has_trailing_exit(portfolio, ticker, position):
            return
        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        request = OrderRequestFactory.trailing_stop_exit(
            ticker=ticker,
            quantity=abs(int(position.quantity)),
            side=side,
            trailing_stop_pct=self._trailing_stop_pct,
            time_in_force=TimeInForce.GTC,
        )
        response = await self._broker.place_order(request)
        await self._record_submitted_trade(request, response, note="ema-trailing-stop")

    async def _manage_open_position_exits(self, ticker: str, price: float) -> None:
        portfolio = await self._broker.get_portfolio()
        position = portfolio.get_position(ticker)
        if position is None or position.quantity == 0:
            return

        open_orders = self._orders_for_ticker(portfolio, ticker)
        if self._reward_target_hit(position, price):
            non_trailing_orders = [
                order for order in open_orders if order.order_type != OrderType.TRAILING_STOP
            ]
            if non_trailing_orders:
                return
            if open_orders:
                await self._cancel_orders(open_orders)
            await self._flatten_position(ticker, position)
            return

        if self._trailing_stop_pct <= 0 or open_orders:
            return

        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        request = OrderRequestFactory.trailing_stop_exit(
            ticker=ticker,
            quantity=abs(int(position.quantity)),
            side=side,
            trailing_stop_pct=self._trailing_stop_pct,
            time_in_force=TimeInForce.GTC,
        )
        response = await self._broker.place_order(request)
        await self._record_submitted_trade(request, response, note="ema-trailing-stop")

    def _reward_target_hit(self, position: Position, price: float) -> bool:
        if self._trailing_stop_pct <= 0 or self._reward_to_risk <= 0:
            return False
        average_cost = float(position.average_cost)
        if average_cost <= 0 or not math.isfinite(price):
            return False
        target_move = self._trailing_stop_pct * self._reward_to_risk
        if position.quantity > 0:
            return price >= average_cost * (1.0 + target_move)
        return price <= average_cost * max(0.0, 1.0 - target_move)

    def _crossover_signal(self, closes: list[float]) -> OrderSide | None:
        if len(closes) < self._slow_period + 2:
            return None
        series = pd.Series(closes, dtype=float)
        fast = series.ewm(span=self._fast_period, adjust=False, min_periods=1).mean()
        slow = series.ewm(span=self._slow_period, adjust=False, min_periods=1).mean()
        prev_fast, curr_fast = float(fast.iloc[-2]), float(fast.iloc[-1])
        prev_slow, curr_slow = float(slow.iloc[-2]), float(slow.iloc[-1])
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return OrderSide.BUY
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return OrderSide.SELL
        return None

    def _resampled_closes(self, df: pd.DataFrame | None) -> list[float]:
        frame = _normalize_ohlcv(df)
        if frame.empty:
            return []
        frame.index = frame.index.tz_convert(NY_TZ)
        frame = frame[
            (frame.index.time >= MARKET_OPEN_TIME)
            & (frame.index.time < REGULAR_CLOSE_TIME)
        ]
        if frame.empty:
            return []
        closes = frame["close"].resample(f"{self._candle_minutes}min").last().dropna()
        values = [float(value) for value in closes.tolist()]
        return values[-self.MAX_HISTORY_BARS :]

    def _bucket_bounds(self, tick_time_ny: datetime) -> tuple[datetime, datetime] | None:
        session_open = tick_time_ny.replace(
            hour=MARKET_OPEN_TIME.hour,
            minute=MARKET_OPEN_TIME.minute,
            second=0,
            microsecond=0,
        )
        if tick_time_ny < session_open:
            return None
        minutes = int((tick_time_ny - session_open).total_seconds() // 60)
        bucket_index = minutes // self._candle_minutes
        start = session_open + timedelta(minutes=bucket_index * self._candle_minutes)
        return start, start + timedelta(minutes=self._candle_minutes)

    @staticmethod
    def _create_state(
        data: PricingData,
        start_ny: datetime,
        end_ny: datetime,
    ) -> _IntradayCandleState:
        price = float(data.price)
        return _IntradayCandleState(
            start_time_ny=start_ny,
            end_time_ny=end_ny,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=max(0, int(data.last_size or 0)),
        )

    @staticmethod
    def _finalize_state(state: _IntradayCandleState) -> CandleStick:
        return CandleStick(
            open=state.open,
            high=state.high,
            low=state.low,
            close=state.close,
            volume=state.volume,
            time=state.start_time_ny.astimezone(UTC),
            period=Period.MINUTE,
        )

    async def _flatten_position(self, ticker: str, position: Position) -> None:
        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        request = OrderRequestFactory.flatten_market(
            ticker=ticker,
            quantity=abs(int(position.quantity)),
            side=side,
        )
        response = await self._broker.place_order(request)
        await self._record_submitted_trade(request, response, note="ema-reversal-flatten")

    async def _cancel_orders(self, orders: list[OrderResponse]) -> None:
        for order in orders:
            if order.order_id:
                await self._broker.cancel_order(order.order_id)

    async def _wait_until_order_free_and_flat(self, ticker: str) -> bool:
        deadline = asyncio.get_running_loop().time() + self.TRANSITION_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            portfolio = await self._broker.get_portfolio()
            if not portfolio.has_open_order(ticker) and not portfolio.has_position(ticker):
                return True
            await asyncio.sleep(self.TRANSITION_POLL_SECONDS)
        return False

    @staticmethod
    def _current_side(portfolio: Portfolio, ticker: str) -> OrderSide | None:
        position = portfolio.get_position(ticker)
        if position is not None and position.quantity != 0:
            return OrderSide.BUY if position.quantity > 0 else OrderSide.SELL
        return _infer_order_intent_side(
            [order for order in portfolio.open_orders if order.ticker.upper() == ticker.upper()]
        )

    @staticmethod
    def _orders_for_ticker(portfolio: Portfolio, ticker: str) -> list[OrderResponse]:
        upper = ticker.upper()
        return [order for order in portfolio.open_orders if order.ticker.upper() == upper]

    @staticmethod
    def _has_trailing_exit(portfolio: Portfolio, ticker: str, position: Position) -> bool:
        exit_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        return any(
            order.ticker.upper() == ticker.upper()
            and order.order_type == OrderType.TRAILING_STOP
            and order.side == exit_side
            and order.is_active
            for order in portfolio.open_orders
        )

    def _get_lock(self, ticker: str) -> asyncio.Lock:
        lock = self._order_locks.get(ticker)
        if lock is None:
            lock = asyncio.Lock()
            self._order_locks[ticker] = lock
        return lock

    @staticmethod
    def _is_regular_market_time(tick_time_ny: datetime) -> bool:
        return MARKET_OPEN_TIME <= tick_time_ny.time() < REGULAR_CLOSE_TIME

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = df.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    required = ["open", "high", "low", "close", "volume"]
    if not all(column in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame = frame[required].copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    valid = ~index.isna()
    frame = frame.loc[valid].copy()
    frame.index = index[valid]
    return frame.sort_index()


def _infer_order_intent_side(orders: list[OrderResponse]) -> OrderSide | None:
    if not orders:
        return None
    buy_count = sum(1 for order in orders if order.side == OrderSide.BUY)
    sell_count = sum(1 for order in orders if order.side == OrderSide.SELL)
    if buy_count and not sell_count:
        return OrderSide.BUY
    if sell_count and not buy_count:
        return OrderSide.SELL
    if buy_count and sell_count and abs(buy_count - sell_count) == 1:
        return OrderSide.BUY if buy_count < sell_count else OrderSide.SELL
    return None
