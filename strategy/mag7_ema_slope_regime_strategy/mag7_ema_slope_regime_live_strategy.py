"""Live MAG7 EMA+slope regime strategy using Yahoo + IBKR."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common.helpers.market_calendar import MarketCalendar
from common.models.candlestick import CandleStick
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.period import Period
from common.models.position import Position
from common.models.pricing_data import PricingData
from common.models.portfolio import Portfolio
from common.models.order_response import OrderResponse
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

UTC = timezone.utc
NY_TZ = ZoneInfo("America/New_York")


class Mag7EmaSlopeRegimeLiveStrategy(RealTimeTradingBase):
    """Concrete live strategy for MAG7 EMA+slope regime on hourly candles."""

    MAG7_TICKERS: tuple[str, ...] = (
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "TSLA",
        "GOOGL",
    )
    HISTORY_LOOKBACK_DAYS: int = 60
    MAX_HISTORY_BARS: int = 600
    TRANSITION_TIMEOUT_SECONDS: float = 30.0
    TRANSITION_POLL_SECONDS: float = 1.0

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        trade_direction: str = "Both",
        notional_per_trade: float = 5_000.0,
        ema_period: int = 20,
        slope_len: int = 36,
        band: float = 0.0,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
    ) -> None:
        """Initialize strategy with tuned MAG7 defaults."""
        super().__init__(realtime_provider)
        self._market_provider: IMarketProvider = market_provider
        self._broker: IBroker = broker
        self._trade_direction: str = trade_direction
        self._notional_per_trade: float = max(0.0, float(notional_per_trade))
        self._ema_period: int = max(2, int(ema_period))
        self._slope_len: int = max(1, int(slope_len))
        self._band: float = max(0.0, float(band))
        self._stop_loss_pct: float = max(0.0, float(stop_loss_pct))
        self._take_profit_pct: float = max(0.0, float(take_profit_pct))
        self._market_calendar: MarketCalendar = MarketCalendar()

        self._hourly_states: dict[str, dict[str, Any]] = {}
        self._close_history: dict[str, list[float]] = {}
        self._candle_locks: dict[str, asyncio.Lock] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}
        self._in_flight_transitions: set[str] = set()

    async def initialize(self) -> None:
        """Initialize strategy and preload hourly history before subscribing."""
        logger.info("Initializing %s...", self.__class__.__name__)
        self._tickers = await self.load_tickers()
        if not self._tickers:
            logger.warning("No tickers loaded for %s", self.__class__.__name__)
            self._is_initialized = True
            return

        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._close_history = {ticker: [] for ticker in self._tickers}
        self._hourly_states.clear()
        self._in_flight_transitions.clear()

        await self._bootstrap_hourly_history()
        await self._realtime_provider.subscribe(self._tickers, self.on_tick)
        self._is_initialized = True
        logger.info(
            "%s initialized and subscribed to %d tickers",
            self.__class__.__name__,
            len(self._tickers),
        )

    async def load_tickers(self) -> list[str]:
        """Return fixed MAG7 ticker universe."""
        return list(self.MAG7_TICKERS)

    async def on_tick(self, data: PricingData) -> None:
        """Build pre-market+regular hourly bars and evaluate every tick."""
        ticker = data.id.upper()
        if ticker not in self._tickers:
            return
        logger.info("Received tick for %s at %s for price %.2f", ticker, data.time, data.price)
        tick_time_utc = self._ensure_utc(data.time)
        tick_time_ny = tick_time_utc.astimezone(NY_TZ)
        session_window = self._session_window_for_tick(tick_time_ny)
        if session_window is None:
            return
        pre_market_open_ny, regular_open_ny, regular_close_ny = session_window

        candle_to_process: CandleStick | None = None
        live_price: float = float(data.price)
        should_evaluate_now: bool = self._is_regular_market_time(
            tick_time_ny=tick_time_ny,
            regular_open_ny=regular_open_ny,
            regular_close_ny=regular_close_ny,
        )

        lock = self._get_lock(self._candle_locks, ticker)
        async with lock:
            state = self._hourly_states.get(ticker)
            if state is not None and tick_time_ny.date() > state["session_date"]:
                logger.warning(
                    "Dropping stale unfinished candle for %s from %s",
                    ticker,
                    state["session_date"],
                )
                self._hourly_states.pop(ticker, None)
                state = None

            if state is not None and tick_time_utc >= state["end_time_utc"]:
                candle_to_process = self._finalize_hourly_state(state)
                self._hourly_states.pop(ticker, None)
                state = None

            bucket = self._session_bucket_bounds(
                tick_time_ny=tick_time_ny,
                session_start_ny=pre_market_open_ny,
                session_end_ny=regular_close_ny,
            )
            if bucket is None:
                return
            bucket_start_ny, bucket_end_ny = bucket
            state = self._hourly_states.get(ticker)

            if state is None:
                self._hourly_states[ticker] = self._create_hourly_state(
                    data=data,
                    bucket_start_ny=bucket_start_ny,
                    bucket_end_ny=bucket_end_ny,
                )
            elif state["start_time_ny"] != bucket_start_ny:
                candle_to_process = self._finalize_hourly_state(state)
                self._hourly_states[ticker] = self._create_hourly_state(
                    data=data,
                    bucket_start_ny=bucket_start_ny,
                    bucket_end_ny=bucket_end_ny,
                )
            else:
                self._update_hourly_state(state, data)

            active_state = self._hourly_states.get(ticker)
            if active_state is not None:
                live_price = float(active_state["close"])

        if candle_to_process is not None:
            await self.on_candle(ticker, candle_to_process)

        if should_evaluate_now:
            await self._evaluate_signal_with_price(ticker=ticker, price=live_price)

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Keep finalized candle history up to date."""
        price = float(candle.close)
        if not math.isfinite(price) or price <= 0:
            return
        history = self._close_history.setdefault(ticker, [])
        history.append(price)
        if len(history) > self.MAX_HISTORY_BARS:
            del history[: len(history) - self.MAX_HISTORY_BARS]

    async def shutdown(self) -> None:
        """Shutdown strategy and clear internal state."""
        await super().shutdown()
        self._hourly_states.clear()
        self._close_history.clear()
        self._candle_locks.clear()
        self._order_locks.clear()
        self._in_flight_transitions.clear()

    async def _bootstrap_hourly_history(self) -> None:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=self.HISTORY_LOOKBACK_DAYS)

        for ticker in self._tickers:
            try:
                df = await self._market_provider.get_prices(
                    ticker=ticker,
                    start_time=start_time,
                    end_time=end_time,
                    period=Period.HOUR,
                )
                self._close_history[ticker] = self._extract_premarket_and_regular_closes(df)
                logger.info(
                    "Bootstrapped %s with %d hourly closes",
                    ticker,
                    len(self._close_history[ticker]),
                )
            except Exception as exc:
                logger.warning("Failed to bootstrap hourly history for %s: %s", ticker, exc)
                self._close_history[ticker] = []

    def _extract_premarket_and_regular_closes(self, df: pd.DataFrame | None) -> list[float]:
        if df is None or df.empty:
            return []

        frame = df.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        if "close" not in frame.columns:
            return []

        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        index = pd.to_datetime(frame.index, utc=True, errors="coerce")
        valid = ~index.isna()
        if not valid.any():
            return []

        frame = frame.loc[valid].copy()
        frame.index = index[valid]
        frame = frame.dropna(subset=["close"]).sort_index()
        if frame.empty:
            return []

        frame.index = frame.index.tz_convert(NY_TZ)
        frame = frame[frame.index.dayofweek < 5]
        if frame.empty:
            return []

        mask = [self._is_in_premarket_or_regular_session(ts.to_pydatetime()) for ts in frame.index]
        frame = frame.loc[mask]
        if frame.empty:
            return []

        closes = [float(value) for value in frame["close"].tolist()]
        if len(closes) > self.MAX_HISTORY_BARS:
            closes = closes[-self.MAX_HISTORY_BARS :]
        return closes

    @staticmethod
    def _is_regular_market_time(
        tick_time_ny: datetime,
        regular_open_ny: datetime,
        regular_close_ny: datetime,
    ) -> bool:
        return regular_open_ny <= tick_time_ny < regular_close_ny

    def _session_window_for_tick(
        self,
        tick_time_ny: datetime,
    ) -> tuple[datetime, datetime, datetime] | None:
        if not self._market_calendar.is_trading_day(tick_time_ny):
            return None

        pre_market_open_ny = self._market_calendar.get_pre_market_open(tick_time_ny)
        regular_open_ny = self._market_calendar.get_regular_market_open(tick_time_ny)
        regular_close_ny = self._market_calendar.get_regular_market_close(tick_time_ny)

        if tick_time_ny < pre_market_open_ny or tick_time_ny >= regular_close_ny:
            return None

        return pre_market_open_ny, regular_open_ny, regular_close_ny

    def _is_in_premarket_or_regular_session(self, tick_time_ny: datetime) -> bool:
        window = self._session_window_for_tick(tick_time_ny)
        return window is not None

    async def _evaluate_signal_with_price(self, ticker: str, price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            return

        history = self._close_history.setdefault(ticker, [])
        if not history:
            return
        if len(history) > self.MAX_HISTORY_BARS:
            del history[: len(history) - self.MAX_HISTORY_BARS]

        close_values = history + [float(price)]
        warmup = self._ema_period + self._slope_len + 2
        if len(close_values) < warmup:
            return

        close_series = pd.Series(close_values, dtype=float)
        ema_series = close_series.ewm(
            span=max(2, self._ema_period),
            adjust=False,
            min_periods=1,
        ).mean()
        ema_value = float(ema_series.iloc[-1])
        slope_series = ema_series - ema_series.shift(max(1, self._slope_len))
        slope_value = float(slope_series.fillna(0.0).iloc[-1])

        long_signal = (price > (ema_value * (1.0 + self._band))) and (slope_value > 0.0)
        short_signal = (price < (ema_value * (1.0 - self._band))) and (slope_value < 0.0)

        can_long = self._trade_direction in ("Both", "Long Only")
        can_short = self._trade_direction in ("Both", "Short Only")

        if long_signal and can_long:
            await self._process_signal(ticker=ticker, desired_side=OrderSide.BUY, entry_price=price)
            return

        if short_signal and can_short:
            await self._process_signal(ticker=ticker, desired_side=OrderSide.SELL, entry_price=price)

    async def _process_signal(self, ticker: str, desired_side: OrderSide, entry_price: float) -> None:
        lock = self._get_lock(self._order_locks, ticker)
        async with lock:
            if ticker in self._in_flight_transitions:
                logger.debug("Transition already in-flight for %s", ticker)
                return

            self._in_flight_transitions.add(ticker)
            try:
                portfolio = await self._broker.get_portfolio()
                open_orders = self._orders_for_ticker(portfolio, ticker)
                position = portfolio.get_position(ticker)
                position_side = self._position_side(position)
                open_order_side = self._infer_order_intent_side(open_orders)
                current_side = position_side or open_order_side

                if current_side == desired_side:
                    logger.info(
                        "Skipping %s: existing exposure/orders already aligned with %s signal",
                        ticker,
                        desired_side.value,
                    )
                    return

                if current_side is None and open_orders:
                    logger.info(
                        "Skipping %s: existing open orders have ambiguous side; keeping current orders",
                        ticker,
                    )
                    return

                if open_orders:
                    await self._cancel_orders_for_ticker(ticker, open_orders)

                portfolio = await self._broker.get_portfolio()
                position = portfolio.get_position(ticker)
                if position is not None and position.quantity != 0:
                    await self._flatten_position(ticker, position)

                is_ready = await self._wait_until_flat_and_order_free(ticker)
                if not is_ready:
                    logger.warning(
                        "Skipping %s: could not reach flat/order-free state before timeout",
                        ticker,
                    )
                    return

                portfolio = await self._broker.get_portfolio()
                if portfolio.has_open_order(ticker) or portfolio.has_position(ticker):
                    logger.info("Skipping %s: exposure still present after transition checks", ticker)
                    return

                quantity = int(min(self._notional_per_trade, await self._broker.get_buying_power()) / max(entry_price, 0.01))
                if quantity < 1:
                    logger.warning(
                        "Skipping %s: quantity < 1 (entry=%.2f, notional=%.2f)",
                        ticker,
                        entry_price,
                        self._notional_per_trade,
                    )
                    return

                order_request = self._build_entry_order_request(
                    ticker=ticker,
                    desired_side=desired_side,
                    quantity=quantity,
                    entry_price=entry_price,
                )
                response = await self._broker.place_order(order_request)
                logger.info(
                    "Placed %s bracket for %s | qty=%d entry=%.2f | order_id=%s status=%s",
                    desired_side.value,
                    ticker,
                    quantity,
                    order_request.limit_price or 0.0,
                    response.order_id,
                    response.status,
                )
            finally:
                self._in_flight_transitions.discard(ticker)

    async def _cancel_orders_for_ticker(
        self,
        ticker: str,
        orders: list[OrderResponse],
    ) -> None:
        for order in orders:
            order_id = (order.order_id or "").strip()
            if not order_id:
                continue
            try:
                await self._broker.cancel_order(order_id)
                logger.info("Cancelled order %s for %s", order_id, ticker)
            except Exception as exc:
                logger.warning("Failed cancelling order %s for %s: %s", order_id, ticker, exc)

    async def _flatten_position(self, ticker: str, position: Position) -> None:
        quantity = abs(int(position.quantity))
        if quantity < 1:
            return

        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        request = OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        await self._broker.place_order(request)
        logger.info("Flatten order sent for %s: %s %d", ticker, side.value, quantity)

    async def _wait_until_flat_and_order_free(self, ticker: str) -> bool:
        deadline = asyncio.get_running_loop().time() + self.TRANSITION_TIMEOUT_SECONDS

        while asyncio.get_running_loop().time() < deadline:
            portfolio = await self._broker.get_portfolio()
            if not portfolio.has_open_order(ticker) and not portfolio.has_position(ticker):
                return True
            await asyncio.sleep(self.TRANSITION_POLL_SECONDS)

        return False

    def _build_entry_order_request(
        self,
        ticker: str,
        desired_side: OrderSide,
        quantity: int,
        entry_price: float,
    ) -> OrderRequest:
        entry = round(entry_price, 2)
        if desired_side == OrderSide.BUY:
            stop_price = round(entry * (1.0 - self._stop_loss_pct), 2)
            take_profit_price = round(entry * (1.0 + self._take_profit_pct), 2)
        else:
            stop_price = round(entry * (1.0 + self._stop_loss_pct), 2)
            take_profit_price = round(entry * (1.0 - self._take_profit_pct), 2)

        return OrderRequest(
            ticker=ticker,
            quantity=quantity,
            side=desired_side,
            order_type=OrderType.LIMIT,
            limit_price=entry,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            time_in_force=TimeInForce.DAY,
            buy_limit_rth=True,
            take_profit_rth=True,
            stop_loss_rth=False,
        )

    def _session_bucket_bounds(
        self,
        tick_time_ny: datetime,
        session_start_ny: datetime,
        session_end_ny: datetime,
    ) -> tuple[datetime, datetime] | None:
        if tick_time_ny < session_start_ny or tick_time_ny >= session_end_ny:
            return None

        minutes_since_open = int((tick_time_ny - session_start_ny).total_seconds() // 60)
        bucket_index = minutes_since_open // 60
        bucket_start = session_start_ny + timedelta(hours=bucket_index)
        bucket_end = min(bucket_start + timedelta(hours=1), session_end_ny)
        return bucket_start, bucket_end

    def _create_hourly_state(
        self,
        data: PricingData,
        bucket_start_ny: datetime,
        bucket_end_ny: datetime,
    ) -> dict[str, Any]:
        return {
            "open": float(data.price),
            "high": float(data.price),
            "low": float(data.price),
            "close": float(data.price),
            "volume": max(0, int(data.last_size)),
            "start_time_ny": bucket_start_ny,
            "end_time_ny": bucket_end_ny,
            "start_time_utc": bucket_start_ny.astimezone(UTC),
            "end_time_utc": bucket_end_ny.astimezone(UTC),
            "session_date": bucket_start_ny.date(),
        }

    def _update_hourly_state(self, state: dict[str, Any], data: PricingData) -> None:
        price = float(data.price)
        state["high"] = max(float(state["high"]), price)
        state["low"] = min(float(state["low"]), price)
        state["close"] = price
        state["volume"] = int(state["volume"]) + max(0, int(data.last_size))

    def _finalize_hourly_state(self, state: dict[str, Any]) -> CandleStick:
        return CandleStick(
            open=float(state["open"]),
            high=float(state["high"]),
            low=float(state["low"]),
            close=float(state["close"]),
            volume=int(state["volume"]),
            time=state["start_time_utc"],
            period=Period.HOUR,
        )

    @staticmethod
    def _orders_for_ticker(portfolio: Portfolio, ticker: str) -> list[OrderResponse]:
        upper = ticker.upper()
        return [order for order in portfolio.open_orders if order.ticker.upper() == upper]

    @staticmethod
    def _position_side(position: Position | None) -> OrderSide | None:
        if position is None or position.quantity == 0:
            return None
        return OrderSide.BUY if position.quantity > 0 else OrderSide.SELL

    @staticmethod
    def _infer_order_intent_side(orders: list[OrderResponse]) -> OrderSide | None:
        if not orders:
            return None

        buy_count = sum(1 for order in orders if order.side == OrderSide.BUY)
        sell_count = sum(1 for order in orders if order.side == OrderSide.SELL)

        if buy_count and not sell_count:
            return OrderSide.BUY
        if sell_count and not buy_count:
            return OrderSide.SELL

        # Bracket orders usually have one entry order and two exit orders.
        # Infer the entry intent from the minority side when counts differ by one.
        if buy_count and sell_count and abs(buy_count - sell_count) == 1:
            return OrderSide.BUY if buy_count < sell_count else OrderSide.SELL

        return None

    @staticmethod
    def _get_lock(lock_map: dict[str, asyncio.Lock], ticker: str) -> asyncio.Lock:
        lock = lock_map.get(ticker)
        if lock is None:
            lock = asyncio.Lock()
            lock_map[ticker] = lock
        return lock

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
