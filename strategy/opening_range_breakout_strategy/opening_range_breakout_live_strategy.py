"""Live opening range breakout strategy using Yahoo realtime data and Alpaca."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from common.models.candlestick import CandleStick
from common.models.order import OrderSide, TimeInForce
from common.models.order_response import OrderResponse
from common.models.period import Period
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
class _OpeningRangeState:
    session_date: date
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    complete: bool = False


@dataclass
class _ConfirmationCandleState:
    start_time_ny: datetime
    end_time_ny: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class OpeningRangeBreakoutLiveStrategy(RealTimeTradingBase):
    """Trade breakouts after a confirmed first-session opening range."""

    ORB_TICKERS: tuple[str, ...] = (
        "SPCX",
        "PLTR",
        "COIN",
        "BABA",
        "SMCI",
        "MARA",
        "NIO",
    )

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        strategy_input: StrategyInputModel,
        *,
        opening_range_minutes: int = 15,
        confirmation_candle_minutes: int = 5,
        max_positions: int = 3,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(realtime_provider, broker=broker)
        self._market_provider = market_provider
        self._broker = broker
        self._strategy_input = strategy_input
        self._opening_range_minutes = max(1, int(opening_range_minutes))
        self._confirmation_candle_minutes = max(1, int(confirmation_candle_minutes))
        self._max_positions = max(1, int(max_positions))
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._opening_states: dict[str, _OpeningRangeState] = {}
        self._confirmation_states: dict[str, _ConfirmationCandleState] = {}
        self._submitted_today: set[tuple[str, date]] = set()
        self._reserved_cash: dict[str, float] = {}
        self._order_locks: dict[str, asyncio.Lock] = {}

    async def load_tickers(self) -> list[str]:
        """Return the fixed ORB ticker universe."""
        return list(self.ORB_TICKERS)

    async def _before_subscribe(self) -> None:
        self._tickers = [ticker.upper() for ticker in self._tickers]
        self._opening_states.clear()
        self._confirmation_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()

    async def on_tick(self, data: PricingData) -> None:
        ticker = data.id.upper()
        if ticker not in self._tickers:
            return

        tick_time_ny = self._ensure_utc(data.time).astimezone(NY_TZ)
        if not self._is_regular_market_time(tick_time_ny):
            return

        session_date = tick_time_ny.date()
        opening_state = await self._opening_state_for_tick(ticker, session_date, tick_time_ny)
        if opening_state is None:
            return

        opening_end = self._opening_range_end(tick_time_ny)
        if tick_time_ny < opening_end:
            self._update_opening_range(opening_state, data)
            return

        opening_state.complete = opening_state.volume > 0
        if not opening_state.complete:
            return

        candle = self._update_confirmation_candle(ticker, data, tick_time_ny)
        if candle is not None:
            await self.on_candle(ticker, candle)

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        state = self._opening_states.get(ticker.upper())
        if state is None or not state.complete:
            return
        signal = self._breakout_signal(candle, state)
        if signal is None:
            return
        await self._submit_entry(
            ticker=ticker.upper(),
            side=signal,
            entry_price=float(candle.close),
            session_date=state.session_date,
        )

    async def shutdown(self) -> None:
        await super().shutdown()
        self._opening_states.clear()
        self._confirmation_states.clear()
        self._submitted_today.clear()
        self._reserved_cash.clear()
        self._order_locks.clear()

    async def _opening_state_for_tick(
        self,
        ticker: str,
        session_date: date,
        tick_time_ny: datetime,
    ) -> _OpeningRangeState | None:
        state = self._opening_states.get(ticker)
        if state is None or state.session_date != session_date:
            state = _OpeningRangeState(session_date=session_date)
            self._opening_states[ticker] = state

        if tick_time_ny >= self._opening_range_end(tick_time_ny) and state.volume == 0:
            recovered = await self._recover_opening_range(ticker, session_date, tick_time_ny)
            if recovered is not None:
                self._opening_states[ticker] = recovered
                return recovered
        return state

    async def _submit_entry(
        self,
        ticker: str,
        side: OrderSide,
        entry_price: float,
        session_date: date,
    ) -> None:
        lock = self._get_lock(ticker)
        async with lock:
            key = (ticker, session_date)
            if key in self._submitted_today:
                return
            if self._submitted_count_for_session(session_date) >= self._max_positions:
                return

            portfolio = await self._broker.get_portfolio()
            if portfolio.has_position(ticker) or portfolio.has_open_order(ticker):
                self._submitted_today.add(key)
                return

            quantity = PositionSizer.quantity_for_entry(
                portfolio=portfolio,
                entry_price=entry_price,
                strategy_input=self._strategy_input,
                reserved_notional=sum(self._reserved_cash.values()),
            )
            if quantity < 1:
                return

            request = OrderRequestFactory.bracket_entry(
                ticker=ticker,
                quantity=quantity,
                side=side,
                entry_price=entry_price,
                strategy_input=self._strategy_input,
                time_in_force=TimeInForce.GTC,
                buy_limit_rth=True,
                take_profit_rth=True,
                stop_loss_rth=False,
            )
            response = await self._broker.place_order(request)
            self._submitted_today.add(key)
            self._reserved_cash[response.order_id or f"{ticker}-{session_date.isoformat()}"] = (
                float(request.quantity) * float(request.limit_price or 0.0)
            )
            await self._record_submitted_trade(request, response, note="opening-range-breakout")

    def _update_opening_range(self, state: _OpeningRangeState, data: PricingData) -> None:
        price = float(data.price)
        if state.volume == 0:
            state.high = price
            state.low = price
        else:
            state.high = max(state.high, price)
            state.low = min(state.low, price)
        state.volume += max(1, int(data.last_size or 0))

    def _update_confirmation_candle(
        self,
        ticker: str,
        data: PricingData,
        tick_time_ny: datetime,
    ) -> CandleStick | None:
        bucket = self._confirmation_bucket_bounds(tick_time_ny)
        if bucket is None:
            return None
        start_ny, end_ny = bucket
        state = self._confirmation_states.get(ticker)
        if state is None:
            self._confirmation_states[ticker] = self._create_confirmation_state(
                data,
                start_ny,
                end_ny,
            )
            return None
        if state.start_time_ny != start_ny:
            finalized = self._finalize_confirmation_state(state)
            self._confirmation_states[ticker] = self._create_confirmation_state(
                data,
                start_ny,
                end_ny,
            )
            return finalized

        price = float(data.price)
        state.high = max(state.high, price)
        state.low = min(state.low, price)
        state.close = price
        state.volume += max(0, int(data.last_size or 0))
        return None

    def _breakout_signal(
        self,
        candle: CandleStick,
        opening_state: _OpeningRangeState,
    ) -> OrderSide | None:
        if not math.isfinite(candle.close):
            return None
        if candle.low > opening_state.high and candle.close > opening_state.high:
            return OrderSide.BUY
        if candle.high < opening_state.low and candle.close < opening_state.low:
            return OrderSide.SELL
        return None

    async def _recover_opening_range(
        self,
        ticker: str,
        session_date: date,
        tick_time_ny: datetime,
    ) -> _OpeningRangeState | None:
        start_ny = datetime.combine(session_date, MARKET_OPEN_TIME, tzinfo=NY_TZ)
        end_ny = self._opening_range_end(tick_time_ny)
        try:
            minute_df = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start_ny.astimezone(UTC),
                end_time=end_ny.astimezone(UTC),
                period=Period.MINUTE,
            )
        except Exception as exc:
            logger.warning("Could not recover opening range for %s: %s", ticker, exc)
            return None

        frame = _normalize_ohlcv(minute_df)
        if frame.empty:
            return None
        frame.index = frame.index.tz_convert(NY_TZ)
        opening = frame[
            (frame.index.date == session_date)
            & (frame.index.time >= MARKET_OPEN_TIME)
            & (frame.index < end_ny)
        ]
        if opening.empty:
            return None
        volume = int(opening["volume"].clip(lower=0).sum())
        if volume <= 0:
            return None
        return _OpeningRangeState(
            session_date=session_date,
            high=float(opening["high"].max()),
            low=float(opening["low"].min()),
            volume=volume,
            complete=True,
        )

    def _opening_range_end(self, value_ny: datetime) -> datetime:
        return value_ny.replace(
            hour=MARKET_OPEN_TIME.hour,
            minute=MARKET_OPEN_TIME.minute,
            second=0,
            microsecond=0,
        ) + timedelta(minutes=self._opening_range_minutes)

    def _confirmation_bucket_bounds(
        self,
        tick_time_ny: datetime,
    ) -> tuple[datetime, datetime] | None:
        opening_end = self._opening_range_end(tick_time_ny)
        if tick_time_ny < opening_end:
            return None
        minutes = int((tick_time_ny - opening_end).total_seconds() // 60)
        bucket_index = minutes // self._confirmation_candle_minutes
        start = opening_end + timedelta(
            minutes=bucket_index * self._confirmation_candle_minutes,
        )
        return start, start + timedelta(minutes=self._confirmation_candle_minutes)

    @staticmethod
    def _create_confirmation_state(
        data: PricingData,
        start_ny: datetime,
        end_ny: datetime,
    ) -> _ConfirmationCandleState:
        price = float(data.price)
        return _ConfirmationCandleState(
            start_time_ny=start_ny,
            end_time_ny=end_ny,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=max(0, int(data.last_size or 0)),
        )

    @staticmethod
    def _finalize_confirmation_state(state: _ConfirmationCandleState) -> CandleStick:
        return CandleStick(
            open=state.open,
            high=state.high,
            low=state.low,
            close=state.close,
            volume=state.volume,
            time=state.start_time_ny.astimezone(UTC),
            period=Period.MINUTE,
        )

    def _submitted_count_for_session(self, session_date: date) -> int:
        return sum(1 for _, submitted_date in self._submitted_today if submitted_date == session_date)

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
